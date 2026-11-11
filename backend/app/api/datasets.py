"""Dataset console API（M3-01，design 04 §2 Dataset/Import/Metrics 行）。

- ``POST /api/v1/dataset-imports``：导入→校验→入库（幂等重放 200/新建 201）。
- ``GET`` 目录/版本/用例/指标定义查询，全部服务端分页（items + page_info）。
- 错误统一 ``{"error": {code, message, ...}}`` 封套；导入校验失败 422 携带
  可诊断错误分类清单。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..datasets import AdapterNotFound, get_adapter
from ..db import get_session
from ..defaults import DEFAULT_TENANT_ID
from ..models import Dataset, DatasetCase, DatasetImport, DatasetVersion, MetricDefinition
from ..services import dataset_imports as import_service
from ..services import datasets as dataset_service
from ..services import identity
from ..services import metrics as metric_service
from .dependencies import require_admin
from .errors import error_detail

router = APIRouter(tags=["datasets"])


class DatasetImportRequest(BaseModel):
    dataset_slug: str = Field(min_length=1, max_length=128)
    dataset_name: str = Field(min_length=1, max_length=200)
    adapter_key: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    source_uri: str = Field(default="", max_length=500)
    source_revision: str = Field(min_length=1, max_length=128)
    environment_image: str | None = Field(default=None, max_length=300)
    execution_image_digest: str | None = Field(default=None, max_length=128)
    scoring_image_digest: str | None = Field(default=None, max_length=128)
    records: list[dict[str, Any]] = Field(min_length=1)
    created_by: str = Field(default="console", max_length=128)


def dataset_summary(dataset) -> dict:
    return {
        "id": dataset.id,
        "slug": dataset.slug,
        "name": dataset.name,
        "adapter_key": dataset.adapter_key,
        "task_family": dataset.task_family,
        "environment_kind": dataset.environment_kind,
        "created_at": dataset.created_at.isoformat(timespec="microseconds"),
    }


def version_summary(version: DatasetVersion) -> dict:
    return {
        "id": version.id,
        "dataset_id": version.dataset_id,
        "version": version.version,
        "status": version.status,
        "case_count": version.case_count,
        "source_uri": version.source_uri,
        "source_revision": version.source_revision,
        "adapter_version": version.adapter_version,
        "environment_image": version.environment_image,
        "execution_image_digest": version.execution_image_digest,
        "scoring_image_digest": version.scoring_image_digest,
        "created_at": version.created_at.isoformat(timespec="microseconds"),
    }


def case_summary(case: DatasetCase) -> dict:
    # M5-14: the adapter's raw difficulty label is preserved, and the display
    # level (easy/medium/hard) comes from a documented time-band normalization.
    # No difficulty value is invented for datasets without the field.
    difficulty_label = dataset_service.case_difficulty_label(case)
    return {
        "id": case.id,
        "dataset_version_id": case.dataset_version_id,
        "case_key": case.case_key,
        "ordinal": case.ordinal,
        "repo": case.repo,
        "base_commit": case.base_commit,
        "difficulty": dataset_service.difficulty_level(difficulty_label),
        "difficulty_label": difficulty_label,
        "expected_test_count": dataset_service.case_expected_test_count(case),
        "created_at": case.created_at.isoformat(timespec="microseconds"),
    }


def case_detail(case: DatasetCase, visibility: dict[str, str]) -> dict:
    return {
        **case_summary(case),
        "problem_statement": case.problem_statement,
        "reference_patch": case.reference_patch,
        "test_patch": case.test_patch,
        "expected_metrics": case.expected_metrics_json,
        "extra": case.extra_json,
        "visibility": visibility,
    }


def metric_summary(definition: MetricDefinition) -> dict:
    return {
        "id": definition.id,
        "dataset_id": definition.dataset_id,
        "metric_key": definition.metric_key,
        "version": definition.version,
        "display_name": definition.display_name,
        "description": definition.description,
        "aggregator_key": definition.aggregator_key,
        "formula_text": definition.formula_text,
        "display_kind": definition.display_kind,
        "unit": definition.unit,
        "direction": definition.direction,
        "denominator_policy": definition.denominator_policy,
        "config": definition.config_json,
        "created_at": definition.created_at.isoformat(timespec="microseconds"),
    }


def import_summary(record: DatasetImport) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "dataset_slug": record.dataset_slug,
        "adapter_key": record.adapter_key,
        "version": record.version,
        "source_uri": record.source_uri,
        "source_revision": record.source_revision,
        "status": record.status,
        "total_count": record.total_count,
        "valid_count": record.valid_count,
        "invalid_count": record.invalid_count,
        "processed_count": record.processed_count,
        "file_name": record.file_name,
        "file_size": record.file_size,
        "file_digest": record.file_digest,
        "report": record.validation_report_json or {},
        "error_report": record.error_report_json or {},
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat(timespec="microseconds"),
        "status_updated_at": (
            record.status_updated_at.isoformat(timespec="microseconds")
            if record.status_updated_at
            else None
        ),
    }


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=error_detail(code, message))


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@router.post("/api/v1/dataset-imports")
def create_dataset_import(
    payload: DatasetImportRequest,
    response: Response,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        outcome = dataset_service.import_dataset_version(
            session,
            dataset_slug=payload.dataset_slug,
            dataset_name=payload.dataset_name,
            adapter_key=payload.adapter_key,
            version=payload.version,
            source_uri=payload.source_uri,
            source_revision=payload.source_revision,
            environment_image=payload.environment_image,
            execution_image_digest=payload.execution_image_digest,
            scoring_image_digest=payload.scoring_image_digest,
            records=payload.records,
            created_by=context.user.email,
        )
    except dataset_service.ImportValidationFailed as exc:
        failed = exc.outcome
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "dataset_import_validation_failed",
                str(exc),
                details={
                    "import_id": failed.import_record.id,
                    "errors": [issue.as_dict() for issue in failed.errors],
                },
            ),
        ) from exc
    except dataset_service.DatasetVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail("dataset_version_conflict", str(exc)),
        ) from exc
    # 幂等重放答 200，新建 201（与 evaluations 路由一致）。
    response.status_code = 201 if outcome.created else 200
    return {
        "created": outcome.created,
        "status": outcome.status,
        "import": import_summary(outcome.import_record),
        "dataset": dataset_summary(outcome.dataset) if outcome.dataset else None,
        "version": version_summary(outcome.version) if outcome.version else None,
    }
    return response


@router.get("/api/v1/dataset-imports")
def list_dataset_imports(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> dict:
    items, total = dataset_service.list_imports(session, limit=limit, offset=offset)
    return {
        "items": [import_summary(record) for record in items],
        "page_info": dataset_service.page_info(limit=limit, offset=offset, total=total),
    }


@router.get("/api/v1/dataset-imports/{import_id}")
def get_dataset_import(import_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        record = dataset_service.get_import(session, import_id)
    except dataset_service.DatasetImportNotFound as exc:
        raise _not_found("dataset_import_not_found", "导入记录不存在") from exc
    summary = import_summary(record)
    if record.dataset_id:
        dataset = session.get(Dataset, record.dataset_id)
        if dataset is not None:
            summary["dataset"] = dataset_summary(dataset)
            version = session.scalar(
                select(DatasetVersion).where(
                    DatasetVersion.dataset_id == dataset.id,
                    DatasetVersion.version == record.version,
                )
            )
            # Keep `version` as the import label; the full immutable version
            # summary travels as `version_detail` and never overwrites it.
            summary["version_detail"] = version_summary(version) if version else None
    return summary


def _database_url(session: Session) -> str:
    bind = session.get_bind()
    url = bind.url if hasattr(bind, "url") else bind.engine.url
    return url.render_as_string(hide_password=False)


def _upload_error(code: str, message: str, status_code: int = 422, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=error_detail(code, message, details=details or None),
    )


UPLOAD_SUFFIXES = (".json", ".jsonl", ".ndjson")
# Multipart framing adds a little to the file size; reject clearly oversized
# bodies from the request header before Starlette spools them to disk.
UPLOAD_HEADER_SLACK_BYTES = 1024 * 1024


def _upload_size_guard(request: Request) -> None:
    header = request.headers.get("content-length")
    if not header:
        return
    try:
        length = int(header)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=error_detail("invalid_content_length", "无效的 Content-Length"),
        ) from exc
    limit = int(get_settings().dataset_import_max_bytes) + UPLOAD_HEADER_SLACK_BYTES
    if length > limit:
        raise HTTPException(
            status_code=413,
            detail=error_detail(
                "dataset_import_too_large",
                f"数据文件超过 {get_settings().dataset_import_max_bytes // (1024 * 1024)} MB 上限",
            ),
        )


@router.post("/api/v1/dataset-imports/uploads", status_code=201)
async def upload_dataset_import(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    dataset_slug: Annotated[str, Form(min_length=1, max_length=128)],
    dataset_name: Annotated[str, Form(min_length=1, max_length=200)],
    adapter_key: Annotated[str, Form(min_length=1, max_length=64)],
    version: Annotated[str, Form(min_length=1, max_length=64)],
    source_revision: Annotated[str, Form(min_length=1, max_length=128)],
    execution_image_digest: Annotated[str | None, Form()] = None,
    scoring_image_digest: Annotated[str | None, Form()] = None,
    source_uri: Annotated[str | None, Form()] = None,
    _guard: None = Depends(_upload_size_guard),
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    """Stage an uploaded JSON/JSONL file and start isolated validation."""

    settings = get_settings()
    try:
        get_adapter(adapter_key)
    except AdapterNotFound as exc:
        raise _upload_error("adapter_not_found", f"未注册的数据集适配器：{adapter_key}") from exc

    original_name = Path(file.filename or "").name
    if not original_name or len(original_name) > 255:
        raise _upload_error("invalid_file_name", "请选择 JSON 或 JSONL 数据文件")
    suffix = Path(original_name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise _upload_error("unsupported_file_type", "仅支持 .json、.jsonl 或 .ndjson 数据文件")

    max_bytes = int(settings.dataset_import_max_bytes)
    root = import_service.import_root(settings)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage_key = f"{uuid.uuid4()}{suffix}"
    destination = root / storage_key

    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise _upload_error(
                        "dataset_import_too_large",
                        f"数据文件超过 {max_bytes // (1024 * 1024)} MB 上限",
                        status_code=413,
                    )
                digest.update(chunk)
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise _upload_error(
            "dataset_import_storage_failed", "数据文件暂存失败，请稍后重试", status_code=503
        ) from exc
    except BaseException:
        # Never leave an unreferenced staged file on cancellation or a crash.
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if size == 0:
        destination.unlink(missing_ok=True)
        raise _upload_error("dataset_import_empty_file", "数据文件为空")

    now = import_service._now()
    record = DatasetImport(
        # Console imports are scoped to the default project, matching the
        # legacy JSON import service and the dataset foreign key.
        tenant_id=str(DEFAULT_TENANT_ID),
        dataset_slug=dataset_slug,
        dataset_name=dataset_name,
        adapter_key=adapter_key,
        version=version,
        source_uri=(source_uri or f"upload://{original_name}")[:500],
        source_revision=source_revision,
        status="queued",
        total_count=0,
        valid_count=0,
        invalid_count=0,
        file_name=original_name,
        file_size=size,
        file_digest="sha256:" + digest.hexdigest(),
        storage_key=storage_key,
        execution_image_digest=(execution_image_digest or "").strip() or None,
        scoring_image_digest=(scoring_image_digest or "").strip() or None,
        validation_report_json={},
        processed_count=0,
        status_updated_at=now,
        error_report_json={},
        created_by=context.user.email,
    )
    session.add(record)
    try:
        session.commit()
    except Exception:
        # Do not leave a staged file behind when the import row cannot exist.
        destination.unlink(missing_ok=True)
        raise

    database_url = _database_url(session)
    background_tasks.add_task(
        import_service.run_import_job,
        record.id,
        "validate",
        database_url=database_url,
    )
    return {"import": import_summary(record)}


@router.post("/api/v1/dataset-imports/{import_id}/commit", status_code=202)
def commit_dataset_import(
    import_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        record = import_service.begin_commit(session, import_id)
    except import_service.DatasetImportNotFound as exc:
        raise _not_found("dataset_import_not_found", "导入记录不存在") from exc
    except import_service.DatasetImportStateConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "dataset_import_state_conflict",
                "只有校验通过的导入可以生成版本，请重新校验数据",
            ),
        ) from exc
    database_url = _database_url(session)
    background_tasks.add_task(
        import_service.run_import_job,
        record.id,
        "materialize",
        database_url=database_url,
    )
    return {"import": import_summary(record)}


@router.post("/api/v1/dataset-imports/{import_id}/cancel")
def cancel_dataset_import(
    import_id: str,
    session: Session = Depends(get_session),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        record = import_service.cancel_import(session, import_id)
    except import_service.DatasetImportNotFound as exc:
        raise _not_found("dataset_import_not_found", "导入记录不存在") from exc
    except import_service.DatasetImportStateConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "dataset_import_state_conflict",
                "导入正在生成版本或已经结束，无法取消",
            ),
        ) from exc
    return import_summary(record)


# ---------------------------------------------------------------------------
# Catalog + versions + cases + metrics（服务端分页）
# ---------------------------------------------------------------------------


@router.get("/api/v1/datasets")
def list_datasets(
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> dict:
    items, total = dataset_service.dataset_catalog(session, limit=limit, offset=offset)
    return {
        "items": items,
        "page_info": dataset_service.page_info(limit=limit, offset=offset, total=total),
    }


@router.get("/api/v1/dataset-adapters")
def list_dataset_adapters(session: Session = Depends(get_session)) -> dict:
    return {"items": dataset_service.adapter_catalog(session)}


@router.get("/api/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        dataset = dataset_service.get_dataset(session, dataset_id)
    except dataset_service.DatasetNotFound as exc:
        raise _not_found("dataset_not_found", "数据集不存在") from exc
    _, version_total = dataset_service.list_dataset_versions(session, dataset.id, limit=1, offset=0)
    return {**dataset_summary(dataset), "version_count": version_total}


@router.get("/api/v1/datasets/{dataset_id}/versions")
def list_dataset_versions(
    dataset_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> dict:
    try:
        dataset_service.get_dataset(session, dataset_id)
    except dataset_service.DatasetNotFound as exc:
        raise _not_found("dataset_not_found", "数据集不存在") from exc
    items, total = dataset_service.list_dataset_versions(
        session, dataset_id, limit=limit, offset=offset
    )
    return {
        "items": [version_summary(version) for version in items],
        "page_info": dataset_service.page_info(limit=limit, offset=offset, total=total),
    }


@router.get("/api/v1/dataset-versions/{version_id}")
def get_dataset_version(version_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        version = dataset_service.get_dataset_version(session, version_id)
    except dataset_service.DatasetVersionNotFound as exc:
        raise _not_found("dataset_version_not_found", "数据集版本不存在") from exc
    metrics = dataset_service.list_version_metrics(session, version.id)
    return {
        **version_summary(version),
        "metrics": [metric_summary(definition) for definition in metrics],
        "validation": dataset_service.version_import_validation(session, version),
    }


@router.get("/api/v1/dataset-versions/{version_id}/cases")
def list_version_cases(
    version_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    difficulty: Annotated[str | None, Query(max_length=32)] = None,
    session: Session = Depends(get_session),
) -> dict:
    try:
        dataset_service.get_dataset_version(session, version_id)
    except dataset_service.DatasetVersionNotFound as exc:
        raise _not_found("dataset_version_not_found", "数据集版本不存在") from exc
    items, total = dataset_service.list_version_cases(
        session,
        version_id,
        limit=limit,
        offset=offset,
        q=q,
        difficulty=difficulty,
    )
    return {
        "items": [case_summary(case) for case in items],
        "page_info": dataset_service.page_info(limit=limit, offset=offset, total=total),
        # An adapter may not provide a difficulty label; the UI uses facets to
        # decide whether the filter is usable instead of assuming easy/hard.
        "facets": {"difficulties": dataset_service.version_difficulty_facets(session, version_id)},
    }


@router.get("/api/v1/dataset-versions/{version_id}/metrics")
def list_version_metrics(version_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        dataset_service.get_dataset_version(session, version_id)
    except dataset_service.DatasetVersionNotFound as exc:
        raise _not_found("dataset_version_not_found", "数据集版本不存在") from exc
    metrics = dataset_service.list_version_metrics(session, version_id)
    return {"items": [metric_summary(definition) for definition in metrics]}


@router.get("/api/v1/dataset-cases/{case_id}")
def get_dataset_case(case_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        case = dataset_service.get_dataset_case(session, case_id)
    except dataset_service.DatasetCaseNotFound as exc:
        raise _not_found("dataset_case_not_found", "用例不存在") from exc
    version = dataset_service.get_dataset_version(session, case.dataset_version_id)
    visibility = get_adapter(version_dataset_adapter_key(session, version)).case_visibility()
    return case_detail(case, visibility)


def version_dataset_adapter_key(session: Session, version: DatasetVersion) -> str:
    dataset = dataset_service.get_dataset(session, version.dataset_id)
    return dataset.adapter_key


# 注册时校验用的白名单查询（供前端/调用方展示，D10）。
@router.get("/api/v1/metric-aggregators")
def list_metric_aggregators() -> dict:
    return {
        "aggregator_keys": list(metric_service.AGGREGATOR_WHITELIST),
        "case_result_keys": list(metric_service.CASE_RESULT_KEYS),
        "case_set_keys": list(metric_service.CASE_SET_KEYS),
    }
