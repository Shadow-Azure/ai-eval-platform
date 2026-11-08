"""Dataset import application service.

Imports are re-entrant: a failed or repeated confirm rebuilds only the missing
version, and a completed import short-circuits. Task bundles are content
addressed and the version manifest is the frozen reference used by later
evaluation snapshots.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Dataset,
    DatasetCase,
    DatasetImport,
    DatasetVersion,
    DatasetVersionMetric,
    MetricDefinition,
)
from ..object_store import ObjectStore
from .adapter import AdapterCaseError, BundleFile, DatasetAdapter, MetricSpec, get_adapter

_MAX_INVALID_ROWS = 100


class DatasetImportError(RuntimeError):
    """The import cannot proceed for a structural reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SourcePayload:
    text: str
    revision: str
    uri: str


def _source_root() -> Path:
    root = Path(get_settings().dataset_source_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_source(source: dict[str, object]) -> SourcePayload:
    kind = source.get("kind")
    revision = source.get("revision")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise DatasetImportError("invalid_revision", "source.revision 必须是非空字符串")
    if kind == "local_path":
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise DatasetImportError("invalid_source", "source.path 必须是非空字符串")
        target = Path(raw_path).expanduser().resolve()
        root = _source_root()
        if not target.is_relative_to(root):
            raise DatasetImportError(
                "source_outside_root",
                f"本地来源必须位于 {root} 下",
            )
        if not target.is_file():
            raise DatasetImportError("source_missing", f"来源文件不存在：{target}")
        if target.stat().st_size > get_settings().dataset_import_max_bytes:
            raise DatasetImportError("source_too_large", "来源超过允许的导入大小")
        text = target.read_text(encoding="utf-8")
        return SourcePayload(text=text, revision=revision or _digest(text), uri=str(target))
    if kind in ("http", "https"):
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise DatasetImportError("invalid_source", "source.url 必须是 http(s) 地址")
        max_bytes = get_settings().dataset_import_max_bytes
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
                raw = response.read(max_bytes + 1)
        except OSError as error:
            raise DatasetImportError("source_unreachable", f"下载来源失败：{error}") from error
        if len(raw) > max_bytes:
            raise DatasetImportError("source_too_large", "来源超过允许的导入大小")
        text = raw.decode("utf-8")
        return SourcePayload(text=text, revision=revision or _digest(text), uri=url)
    raise DatasetImportError("invalid_source_kind", "source.kind 必须是 local_path 或 http")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_dataset(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    slug: str,
    name: str,
    adapter_key: str = "swebench",
    task_family: str | None = None,
    environment_kind: str | None = None,
    metadata: dict | None = None,
) -> Dataset:
    adapter = get_adapter(adapter_key)
    existing = session.scalar(
        select(Dataset).where(
            Dataset.tenant_id == tenant_id,
            Dataset.project_id == project_id,
            Dataset.slug == slug,
        )
    )
    if existing is not None:
        return existing
    dataset = Dataset(
        tenant_id=tenant_id,
        project_id=project_id,
        slug=slug,
        name=name,
        adapter_key=adapter.key,
        task_family=task_family or adapter.task_family,
        environment_kind=environment_kind or adapter.environment_kind,
        metadata_json=metadata or {},
    )
    session.add(dataset)
    session.flush()
    return dataset


def create_import(
    session: Session,
    *,
    tenant_id: str,
    dataset: Dataset,
    source: dict[str, object],
    created_by: str = "system",
    adapter_key: str | None = None,
) -> DatasetImport:
    adapter = get_adapter(adapter_key or dataset.adapter_key)
    load_source(source)  # validate early; the worker re-reads on confirm
    record = DatasetImport(
        tenant_id=tenant_id,
        dataset_id=dataset.id,
        adapter_key=adapter.key,
        status="pending",
        source_config_json=source,
        created_by=created_by,
    )
    session.add(record)
    session.flush()
    return record


def run_import(
    session: Session,
    import_id: str,
    store: ObjectStore,
) -> DatasetImport:
    record = session.get(DatasetImport, import_id)
    if record is None:
        raise DatasetImportError("import_not_found", "导入记录不存在")
    if record.status == "completed":
        return record
    dataset = session.get(Dataset, record.dataset_id)
    if dataset is None:
        raise DatasetImportError("dataset_not_found", "数据集不存在")
    adapter = get_adapter(record.adapter_key)

    record.status = "running"
    record.error_summary = None
    record.error_report_key = None
    session.flush()
    try:
        _run_import(session, record, dataset, adapter, store)
        record.status = "completed"
        session.commit()
    except Exception as error:  # keep the failure diagnosable for retries
        session.rollback()
        record = session.get(DatasetImport, import_id)
        if record is None:
            raise
        record.status = "failed"
        record.error_summary = _summarize_error(error)
        if isinstance(error, DatasetImportError):
            record.error_summary = error.message
        session.commit()
        raise
    return record


def _run_import(
    session: Session,
    record: DatasetImport,
    dataset: Dataset,
    adapter: DatasetAdapter,
    store: ObjectStore,
) -> None:
    source = load_source(record.source_config_json)
    existing = session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.tenant_id == record.tenant_id,
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.source_revision == source.revision,
            DatasetVersion.adapter_version == adapter.adapter_version,
        )
    )
    if existing is not None and existing.status == "ready":
        record.dataset_version_id = existing.id
        record.total_count = record.valid_count = existing.case_count
        record.invalid_count = 0
        return

    cases, invalid_rows = _parse_cases(adapter, source.text)
    record.total_count = len(cases) + len(invalid_rows)
    record.invalid_count = len(invalid_rows)
    if not cases:
        raise DatasetImportError("no_valid_cases", "来源中没有可导入的合法用例")

    if existing is not None:
        # A previous attempt stopped mid-way: rebuild the same immutable
        # source/adaptor slot instead of creating a duplicate version.
        session.execute(
            delete(DatasetCase).where(DatasetCase.dataset_version_id == existing.id)
        )
        session.execute(
            delete(DatasetVersionMetric).where(
                DatasetVersionMetric.dataset_version_id == existing.id
            )
        )
        version = existing
        version.status = "importing"
        version.error_summary = None
        version.source_uri = source.uri
        version.case_count = 0
        version.task_manifest_key = None
        version.task_manifest_digest = None
        session.flush()
    else:
        version = DatasetVersion(
            tenant_id=record.tenant_id,
            dataset_id=dataset.id,
            version=_next_version(session, dataset.id),
            source_uri=source.uri,
            source_revision=source.revision,
            adapter_version=adapter.adapter_version,
            status="importing",
            case_count=0,
        )
        session.add(version)
        session.flush()

    metric_definitions = _ensure_metrics(session, dataset, adapter.metric_specs())
    for definition in metric_definitions:
        session.add(
            DatasetVersionMetric(
                tenant_id=record.tenant_id,
                dataset_version_id=version.id,
                metric_definition_id=definition.id,
            )
        )
    session.flush()

    case_summaries: list[dict[str, object]] = []
    for ordinal, case in enumerate(cases):
        bundle = adapter.bundle(case)
        keys = _store_case_bundle(store, dataset.id, version.id, case.case_key, bundle)
        manifest = {
            "version": 1,
            "adapter": adapter.key,
            "adapter_version": adapter.adapter_version,
            "case_key": case.case_key,
            "files": keys,
        }
        manifest_key = f"datasets/{dataset.id}/{version.id}/cases/{case.case_key}/manifest.json"
        stored_manifest = store.put_json(manifest_key, manifest)
        session.add(
            DatasetCase(
                tenant_id=record.tenant_id,
                dataset_version_id=version.id,
                case_key=case.case_key,
                ordinal=ordinal,
                repo=case.repo,
                base_commit=case.base_commit,
                problem_statement=case.problem_statement,
                reference_patch_key=_file_key(dataset.id, version.id, case.case_key, "solution/solve.patch"),
                test_patch_key=_file_key(dataset.id, version.id, case.case_key, "tests/test.patch"),
                expected_metrics_json={
                    "FAIL_TO_PASS": list(case.fail_to_pass),
                    "PASS_TO_PASS": list(case.pass_to_pass),
                },
                extra_json=case.extra,
            )
        )
        case_summaries.append(
            {
                "case_key": case.case_key,
                "manifest_key": stored_manifest.key,
                "manifest_digest": stored_manifest.sha256,
            }
        )

    version_manifest = {
        "version": 1,
        "dataset_id": dataset.id,
        "dataset_version_id": version.id,
        "adapter": adapter.key,
        "adapter_version": adapter.adapter_version,
        "source_revision": source.revision,
        "case_count": len(cases),
        "invalid_count": len(invalid_rows),
        "metrics": [
            {"metric_key": definition.metric_key, "version": definition.version}
            for definition in metric_definitions
        ],
        "cases": case_summaries,
    }
    manifest_key = f"datasets/{dataset.id}/{version.id}/source-manifest.json"
    stored = store.put_json(manifest_key, version_manifest)
    version.status = "ready"
    version.case_count = len(cases)
    version.task_manifest_key = stored.key
    version.task_manifest_digest = stored.sha256

    record.dataset_version_id = version.id
    record.valid_count = len(cases)
    if invalid_rows:
        report_key = f"datasets/{dataset.id}/imports/{record.id}/errors.json"
        report = store.put_json(report_key, {"invalid_count": len(invalid_rows), "rows": invalid_rows})
        record.error_report_key = report.key


def _parse_cases(
    adapter: DatasetAdapter, text: str
) -> tuple[list, list[dict[str, object]]]:
    cases = []
    invalid: list[dict[str, object]] = []
    max_cases = get_settings().dataset_import_max_cases
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
            if not isinstance(raw, dict):
                raise AdapterCaseError("invalid_row", "每一行必须是 JSON 对象")
            case = adapter.parse(raw)
        except json.JSONDecodeError as error:
            if len(invalid) < _MAX_INVALID_ROWS:
                invalid.append({"line": line_no, "code": "invalid_json", "message": str(error)})
            continue
        except AdapterCaseError as error:
            if len(invalid) < _MAX_INVALID_ROWS:
                invalid.append({"line": line_no, "code": error.code, "message": error.message})
            continue
        cases.append(case)
        if len(cases) > max_cases:
            raise DatasetImportError(
                "source_too_large", f"单次导入用例数超过上限 {max_cases}"
            )
    return cases, invalid


def _next_version(session: Session, dataset_id: str) -> int:
    current = session.scalar(
        select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset_id)
    )
    return int(current or 0) + 1


def _ensure_metrics(
    session: Session, dataset: Dataset, specs: tuple[MetricSpec, ...]
) -> list[MetricDefinition]:
    definitions = []
    for spec in specs:
        definition = session.scalar(
            select(MetricDefinition).where(
                MetricDefinition.tenant_id == dataset.tenant_id,
                MetricDefinition.dataset_id == dataset.id,
                MetricDefinition.metric_key == spec.metric_key,
                MetricDefinition.version == spec.version,
            )
        )
        if definition is None:
            definition = MetricDefinition(
                tenant_id=dataset.tenant_id,
                dataset_id=dataset.id,
                metric_key=spec.metric_key,
                version=spec.version,
                display_name=spec.display_name,
                description=spec.description,
                aggregator_key=spec.aggregator_key,
                formula_text=spec.formula_text,
                display_kind=spec.display_kind,
                unit=spec.unit,
                direction=spec.direction,
                denominator_policy=spec.denominator_policy,
                config_json=spec.config,
            )
            session.add(definition)
            session.flush()
        definitions.append(definition)
    return definitions


def _file_key(dataset_id: str, version_id: str, case_key: str, path: str) -> str:
    return f"datasets/{dataset_id}/{version_id}/cases/{case_key}/{path}"


def _store_case_bundle(
    store: ObjectStore,
    dataset_id: str,
    version_id: str,
    case_key: str,
    bundle: tuple[BundleFile, ...],
) -> list[dict[str, object]]:
    files = []
    for item in bundle:
        stored = store.put_bytes(_file_key(dataset_id, version_id, case_key, item.path), item.content)
        files.append({**stored.as_dict(), "path": item.path, "visibility": item.visibility})
    return files


def _summarize_error(error: Exception) -> str:
    if isinstance(error, DatasetImportError):
        return error.message
    return f"{type(error).__name__}: {error}"[:1000]


def list_datasets(session: Session, tenant_id: str, project_id: str) -> list[Dataset]:
    return list(
        session.scalars(
            select(Dataset)
            .where(Dataset.tenant_id == tenant_id, Dataset.project_id == project_id)
            .order_by(Dataset.created_at, Dataset.id)
        )
    )


def list_versions(session: Session, dataset_id: str) -> list[DatasetVersion]:
    return list(
        session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version.desc())
        )
    )


def version_metrics(session: Session, version_id: str) -> list[MetricDefinition]:
    return list(
        session.scalars(
            select(MetricDefinition)
            .join(
                DatasetVersionMetric,
                DatasetVersionMetric.metric_definition_id == MetricDefinition.id,
            )
            .where(DatasetVersionMetric.dataset_version_id == version_id)
            .order_by(MetricDefinition.metric_key)
        )
    )


def list_cases(
    session: Session,
    version_id: str,
    *,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> tuple[list[DatasetCase], int | None]:
    statement = select(DatasetCase).where(DatasetCase.dataset_version_id == version_id)
    if cursor is not None:
        statement = statement.where(DatasetCase.ordinal > cursor)
    if q:
        pattern = f"%{q}%"
        statement = statement.where(
            DatasetCase.case_key.ilike(pattern) | DatasetCase.repo.ilike(pattern)
        )
    rows = list(
        session.scalars(statement.order_by(DatasetCase.ordinal, DatasetCase.id).limit(limit + 1))
    )
    next_cursor = rows[limit - 1].ordinal if len(rows) > limit else None
    return rows[:limit], next_cursor


def metric_to_dict(metric: MetricDefinition) -> dict[str, object]:
    return {
        "id": metric.id,
        "metric_key": metric.metric_key,
        "version": metric.version,
        "display_name": metric.display_name,
        "description": metric.description,
        "aggregator_key": metric.aggregator_key,
        "formula_text": metric.formula_text,
        "display_kind": metric.display_kind,
        "unit": metric.unit,
        "direction": metric.direction,
        "denominator_policy": metric.denominator_policy,
        "config": metric.config_json,
    }


def dataset_to_dict(dataset: Dataset) -> dict[str, object]:
    return {
        "id": dataset.id,
        "slug": dataset.slug,
        "name": dataset.name,
        "adapter_key": dataset.adapter_key,
        "task_family": dataset.task_family,
        "environment_kind": dataset.environment_kind,
        "metadata": dataset.metadata_json,
        "created_at": dataset.created_at,
    }


def version_to_dict(version: DatasetVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "dataset_id": version.dataset_id,
        "version": version.version,
        "source_uri": version.source_uri,
        "source_revision": version.source_revision,
        "adapter_version": version.adapter_version,
        "status": version.status,
        "case_count": version.case_count,
        "task_manifest_key": version.task_manifest_key,
        "task_manifest_digest": version.task_manifest_digest,
        "error_summary": version.error_summary,
        "created_at": version.created_at,
    }


def case_to_dict(case: DatasetCase) -> dict[str, object]:
    return {
        "id": case.id,
        "case_key": case.case_key,
        "ordinal": case.ordinal,
        "repo": case.repo,
        "base_commit": case.base_commit,
        "problem_statement": case.problem_statement,
        "reference_patch_key": case.reference_patch_key,
        "test_patch_key": case.test_patch_key,
        "expected_metrics": case.expected_metrics_json,
    }


def import_to_dict(record: DatasetImport) -> dict[str, object]:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "dataset_version_id": record.dataset_version_id,
        "adapter_key": record.adapter_key,
        "status": record.status,
        "total_count": record.total_count,
        "valid_count": record.valid_count,
        "invalid_count": record.invalid_count,
        "error_report_key": record.error_report_key,
        "error_summary": record.error_summary,
        "created_at": record.created_at,
    }
