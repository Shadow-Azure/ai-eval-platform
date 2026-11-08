from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..datasets import service
from ..datasets.adapter import AdapterCaseError, get_adapter
from ..db import get_session
from ..models import Dataset, DatasetImport, DatasetVersion
from ..object_store import LocalObjectStore
from ..seed import ensure_default_scope

router = APIRouter(tags=["datasets"])


def get_object_store() -> LocalObjectStore:
    return LocalObjectStore(get_settings().object_store_root)


def _scope(session: Session) -> tuple[str, str]:
    tenant, _, project = ensure_default_scope(session)
    return tenant.id, project.id


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "request_id": None}},
    )


class DatasetCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    adapter_key: str = Field(default="swebench", min_length=1, max_length=64)
    metadata: dict = Field(default_factory=dict)


class SourceConfig(BaseModel):
    kind: Literal["local_path", "http", "https"]
    path: str | None = None
    url: str | None = None
    revision: str | None = Field(default=None, max_length=200)


class ImportCreate(BaseModel):
    dataset_id: str | None = None
    slug: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    adapter_key: str | None = Field(default=None, max_length=64)
    source: SourceConfig


def _dataset_or_404(session: Session, tenant_id: str, dataset_id: str) -> Dataset:
    dataset = session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
    )
    if dataset is None:
        raise _error(404, "dataset_not_found", "数据集不存在")
    return dataset


def _version_or_404(session: Session, tenant_id: str, version_id: str) -> DatasetVersion:
    version = session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id, DatasetVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise _error(404, "dataset_version_not_found", "数据集版本不存在")
    return version


@router.get("/api/v1/datasets")
def list_datasets(session: Session = Depends(get_session)) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    datasets = service.list_datasets(session, tenant_id, project_id)
    items = []
    for dataset in datasets:
        payload = service.dataset_to_dict(dataset)
        payload["version_count"] = int(
            session.scalar(
                select(func.count())
                .select_from(DatasetVersion)
                .where(DatasetVersion.dataset_id == dataset.id)
            )
            or 0
        )
        items.append(payload)
    return {"items": items, "page_info": {"next_cursor": None, "has_more": False}}


@router.post("/api/v1/datasets", status_code=201)
def create_dataset(
    body: DatasetCreate, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, project_id = _scope(session)
    try:
        get_adapter(body.adapter_key)
    except AdapterCaseError as error:
        raise _error(422, error.code, error.message) from error
    dataset = service.create_dataset(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        slug=body.slug,
        name=body.name,
        adapter_key=body.adapter_key,
        metadata=body.metadata,
    )
    session.commit()
    return service.dataset_to_dict(dataset)


@router.get("/api/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    dataset = _dataset_or_404(session, tenant_id, dataset_id)
    versions = service.list_versions(session, dataset.id)
    payload = service.dataset_to_dict(dataset)
    payload["versions"] = [service.version_to_dict(version) for version in versions]
    payload["metrics"] = (
        [service.metric_to_dict(metric) for metric in service.version_metrics(session, versions[0].id)]
        if versions
        else []
    )
    return payload


@router.get("/api/v1/datasets/{dataset_id}/versions")
def list_dataset_versions(
    dataset_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    dataset = _dataset_or_404(session, tenant_id, dataset_id)
    versions = service.list_versions(session, dataset.id)
    return {
        "items": [service.version_to_dict(version) for version in versions],
        "page_info": {"next_cursor": None, "has_more": False},
    }


@router.get("/api/v1/dataset-versions/{version_id}")
def get_dataset_version(
    version_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    version = _version_or_404(session, tenant_id, version_id)
    dataset = _dataset_or_404(session, tenant_id, version.dataset_id)
    payload = service.version_to_dict(version)
    payload["dataset"] = service.dataset_to_dict(dataset)
    payload["metrics"] = [
        service.metric_to_dict(metric) for metric in service.version_metrics(session, version.id)
    ]
    return payload


@router.get("/api/v1/dataset-versions/{version_id}/cases")
def list_dataset_cases(
    version_id: str,
    q: str | None = Query(default=None, max_length=200),
    cursor: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    version = _version_or_404(session, tenant_id, version_id)
    cases, next_cursor = service.list_cases(
        session, version.id, q=q, cursor=cursor, limit=limit
    )
    return {
        "items": [service.case_to_dict(case) for case in cases],
        "page_info": {
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "total": version.case_count,
        },
    }


@router.get("/api/v1/dataset-versions/{version_id}/metrics")
def list_dataset_metrics(
    version_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    version = _version_or_404(session, tenant_id, version_id)
    return {
        "items": [
            service.metric_to_dict(metric)
            for metric in service.version_metrics(session, version.id)
        ],
        "page_info": {"next_cursor": None, "has_more": False},
    }


@router.post("/api/v1/dataset-imports", status_code=202)
def create_dataset_import(
    body: ImportCreate,
    session: Session = Depends(get_session),
    store: LocalObjectStore = Depends(get_object_store),
) -> dict[str, object]:
    del store  # source validation happens in the service; the worker needs the store
    tenant_id, project_id = _scope(session)
    if body.dataset_id:
        dataset = _dataset_or_404(session, tenant_id, body.dataset_id)
    else:
        if not body.slug or not body.name:
            raise _error(422, "dataset_required", "dataset_id 或 slug/name 至少提供一组")
        try:
            dataset = service.create_dataset(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                slug=body.slug,
                name=body.name,
                adapter_key=body.adapter_key or "swebench",
            )
        except AdapterCaseError as error:
            raise _error(422, error.code, error.message) from error
    try:
        record = service.create_import(
            session,
            tenant_id=tenant_id,
            dataset=dataset,
            source=body.source.model_dump(exclude_none=True),
            adapter_key=body.adapter_key,
        )
    except AdapterCaseError as error:
        raise _error(422, error.code, error.message) from error
    except service.DatasetImportError as error:
        raise _error(422, error.code, error.message) from error
    session.commit()
    return {
        "import": service.import_to_dict(record),
        "dataset": service.dataset_to_dict(dataset),
    }


@router.get("/api/v1/dataset-imports/{import_id}")
def get_dataset_import(
    import_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    record = session.scalar(
        select(DatasetImport).where(
            DatasetImport.id == import_id, DatasetImport.tenant_id == tenant_id
        )
    )
    if record is None:
        raise _error(404, "dataset_import_not_found", "导入记录不存在")
    return service.import_to_dict(record)


@router.post("/api/v1/dataset-imports/{import_id}/confirm")
def confirm_dataset_import(
    import_id: str,
    session: Session = Depends(get_session),
    store: LocalObjectStore = Depends(get_object_store),
) -> dict[str, object]:
    tenant_id, _ = _scope(session)
    record = session.scalar(
        select(DatasetImport).where(
            DatasetImport.id == import_id, DatasetImport.tenant_id == tenant_id
        )
    )
    if record is None:
        raise _error(404, "dataset_import_not_found", "导入记录不存在")
    try:
        record = service.run_import(session, import_id, store)
    except service.DatasetImportError as error:
        raise _error(422, error.code, error.message) from error
    version = (
        session.get(DatasetVersion, record.dataset_version_id)
        if record.dataset_version_id
        else None
    )
    return {
        "import": service.import_to_dict(record),
        "version": service.version_to_dict(version) if version else None,
    }


@router.post("/api/v1/dataset-imports/{import_id}/retry")
def retry_dataset_import(
    import_id: str,
    session: Session = Depends(get_session),
    store: LocalObjectStore = Depends(get_object_store),
) -> dict[str, object]:
    return confirm_dataset_import(import_id, session, store)
