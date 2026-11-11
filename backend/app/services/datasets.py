"""Dataset version import + query service（M3-01，design 07 §1/§2，05 §4）。

导入流程（验收 1/3/4）：

1. 解析适配器（注册表，未知 key 记失败分类 ``adapter_not_found``）；
2. 逐条 ``validate_case`` 校验，失败收集可诊断 ``CaseIssue``；
3. 版本内 ``case_key`` 查重（``duplicate_case_key``）；
4. 幂等：同 dataset+version 且来源/修订/适配器版本一致 → ``duplicate``，
   不产生第二个版本或脏任务；不一致 → ``version_conflict``；
5. 校验失败 → 落一条 ``failed`` 导入记录（含错误分类报告）后抛
   ``ImportValidationFailed``，不产生任何版本/用例行；
6. 成功 → 单事务写 dataset（如缺）、指标注册、版本、用例（不可变视图）、
   版本指标集合与 ``succeeded`` 导入记录。

任务视图不可变：成功后服务层没有任何更新路径。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..datasets import AdapterNotFound, CaseIssue, DatasetAdapter, get_adapter
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import Dataset, DatasetCase, DatasetImport, DatasetVersion, DatasetVersionMetric, MetricDefinition
from .metrics import register_adapter_metrics


class DatasetNotFound(Exception):
    pass


class DatasetVersionNotFound(Exception):
    pass


class DatasetCaseNotFound(Exception):
    pass


class DatasetImportNotFound(Exception):
    pass


class ImportValidationFailed(Exception):
    """导入校验失败；``outcome`` 携带已落库的失败记录与错误分类。"""

    def __init__(self, outcome: ImportOutcome) -> None:
        self.outcome = outcome
        super().__init__(f"数据集导入校验失败：{len(outcome.errors)} 个错误，import_id={outcome.import_record.id}")


class DatasetSourceChanged(Exception):
    """The validated source no longer matches what is being materialized."""


class DatasetVersionConflict(Exception):
    """同版本标签但来源/修订/适配器版本不一致。"""

    def __init__(self, message: str, *, existing_version: DatasetVersion) -> None:
        self.existing_version = existing_version
        super().__init__(message)


@dataclass
class ImportOutcome:
    status: str  # succeeded | failed | duplicate
    created: bool
    import_record: DatasetImport
    dataset: Dataset | None = None
    version: DatasetVersion | None = None
    errors: list[CaseIssue] = field(default_factory=list)


def _error_report(
    *,
    adapter_key: str,
    dataset_slug: str,
    version: str,
    errors: list[CaseIssue],
) -> dict[str, Any]:
    return {
        "adapter_key": adapter_key,
        "dataset_slug": dataset_slug,
        "version": version,
        "error_count": len(errors),
        "codes": sorted({issue.code for issue in errors}),
        "errors": [issue.as_dict() for issue in errors],
    }


def _record_failed_import(
    session: Session,
    *,
    tenant_id: str,
    adapter_key: str,
    dataset_slug: str,
    version: str,
    source_uri: str,
    source_revision: str | None,
    total: int,
    errors: list[CaseIssue],
    created_by: str,
) -> DatasetImport:
    record = DatasetImport(
        tenant_id=tenant_id,
        dataset_slug=dataset_slug,
        adapter_key=adapter_key,
        version=version,
        source_uri=source_uri,
        source_revision=source_revision,
        status="failed",
        total_count=total,
        valid_count=max(0, total - len(errors)),
        invalid_count=len(errors),
        error_report_json=_error_report(
            adapter_key=adapter_key,
            dataset_slug=dataset_slug,
            version=version,
            errors=errors,
        ),
        created_by=created_by,
    )
    session.add(record)
    session.commit()
    return record


def _validate_records(adapter: DatasetAdapter, records: list[Mapping[str, Any]]) -> list[CaseIssue]:
    errors: list[CaseIssue] = []
    seen_keys: dict[str, int] = {}
    for index, raw in enumerate(records):
        issues = adapter.validate_case(raw)
        # case_key 来自适配器标准映射（不假设上游字段名，如 SWE-bench 的
        # instance_id）。尽力提取：记录缺关键字段导致无法归一化时置空，
        # 定位回退 case_index，不影响校验结论。
        case_key: str | None = None
        try:
            case_key = adapter.normalize_case(raw).case_key
        except (KeyError, TypeError):
            case_key = None
        for issue in issues:
            issue.case_index = index
            issue.case_key = case_key
            errors.append(issue)
        if case_key is not None:
            if case_key in seen_keys:
                errors.append(
                    CaseIssue(
                        code="duplicate_case_key",
                        message=f"case_key 重复：{case_key}（第 {seen_keys[case_key]} 条与第 {index} 条）",
                        case_index=index,
                        case_key=case_key,
                    )
                )
            else:
                seen_keys[case_key] = index
    return errors


def _effective_digests(
    execution_image_digest: str | None, scoring_image_digest: str | None
) -> tuple[str | None, str | None]:
    settings = get_settings()
    execution = (
        (execution_image_digest or "").strip()
        or (settings.default_execution_image_digest or "").strip()
        or None
    )
    scoring = (
        (scoring_image_digest or "").strip()
        or (settings.default_scoring_image_digest or "").strip()
        or None
    )
    return execution, scoring


def _set_import_status(
    record: DatasetImport,
    *,
    status: str,
    total_count: int,
    valid_count: int,
    invalid_count: int,
    errors: list[CaseIssue] | None = None,
    adapter_key: str | None = None,
) -> None:
    record.status = status
    record.total_count = total_count
    record.valid_count = valid_count
    record.invalid_count = invalid_count
    record.status_updated_at = datetime.now(UTC)
    if errors is not None:
        record.error_report_json = _error_report(
            adapter_key=adapter_key or record.adapter_key,
            dataset_slug=record.dataset_slug,
            version=record.version,
            errors=errors,
        )


def materialize_dataset_version(
    session: Session,
    *,
    dataset_slug: str,
    dataset_name: str,
    adapter_key: str,
    version: str,
    source_uri: str,
    source_revision: str,
    records: Iterable[Mapping[str, Any]],
    total_count: int,
    environment_image: str | None = None,
    execution_image_digest: str | None = None,
    scoring_image_digest: str | None = None,
    created_by: str = "console",
    task_family: str | None = None,
    environment_kind: str | None = None,
    metadata: dict[str, Any] | None = None,
    import_record: DatasetImport | None = None,
    source_digest: str | None = None,
) -> ImportOutcome:
    """Write one immutable version from already-validated records.

    ``records`` is consumed lazily so the upload flow can stream a large file;
    ``total_count`` is the validated record count recorded during validation.
    Related history (version reuse / conflict) is resolved before any case is
    written, and a mismatch between the validated count and the stream aborts
    the transaction instead of silently creating a partial version.
    """

    tenant_id = str(DEFAULT_TENANT_ID)
    project_id = str(DEFAULT_PROJECT_ID)
    execution_image_digest, scoring_image_digest = _effective_digests(
        execution_image_digest, scoring_image_digest
    )
    adapter: DatasetAdapter = get_adapter(adapter_key)

    dataset = session.scalar(
        select(Dataset).where(Dataset.project_id == project_id, Dataset.slug == dataset_slug)
    )

    if dataset is not None:
        existing = session.scalar(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == dataset.id,
                DatasetVersion.version == version,
            )
        )
        if existing is not None:
            same_source = (
                existing.source_revision == source_revision
                and existing.adapter_version == adapter.adapter_version
                # Uploaded files carry a content digest. Legacy imports have
                # none, so they keep the historical revision-only comparison.
                and (
                    source_digest is None
                    or existing.task_manifest_digest in (None, source_digest)
                )
            )
            if same_source:
                if import_record is None:
                    record = DatasetImport(
                        tenant_id=tenant_id,
                        dataset_id=dataset.id,
                        dataset_slug=dataset_slug,
                        adapter_key=adapter_key,
                        version=version,
                        source_uri=source_uri,
                        source_revision=source_revision,
                        status="duplicate",
                        total_count=total_count,
                        valid_count=total_count,
                        invalid_count=0,
                        created_by=created_by,
                    )
                    session.add(record)
                else:
                    record = import_record
                    _set_import_status(
                        record,
                        status="duplicate",
                        total_count=total_count,
                        valid_count=total_count,
                        invalid_count=0,
                    )
                record.dataset_id = dataset.id
                session.commit()
                return ImportOutcome(
                    status="duplicate",
                    created=False,
                    import_record=record,
                    dataset=dataset,
                    version=existing,
                )

            conflict = CaseIssue(
                code="version_conflict",
                field="source_revision",
                message=(
                    f"版本 {version} 已存在且来源不一致：已有 revision="
                    f"{existing.source_revision} adapter={existing.adapter_version}，"
                    f"本次 revision={source_revision} adapter={adapter.adapter_version}"
                ),
            )
            if import_record is None:
                _record_failed_import(
                    session,
                    tenant_id=tenant_id,
                    adapter_key=adapter_key,
                    dataset_slug=dataset_slug,
                    version=version,
                    source_uri=source_uri,
                    source_revision=source_revision,
                    total=total_count,
                    errors=[conflict],
                    created_by=created_by,
                )
            else:
                _set_import_status(
                    import_record,
                    status="failed",
                    total_count=total_count,
                    valid_count=0,
                    invalid_count=1,
                    errors=[conflict],
                )
                # The async wizard polls the validation report, so a conflict
                # must be diagnosable there as well as in error_report_json.
                report = dict(import_record.validation_report_json or {})
                report.update(
                    {
                        "fatal": True,
                        "fatal_code": "version_conflict",
                        "fatal_message": conflict.message,
                    }
                )
                import_record.validation_report_json = report
                session.commit()
            raise DatasetVersionConflict(
                f"版本 {version} 已绑定 revision={existing.source_revision}，与本次 revision={source_revision} 冲突",
                existing_version=existing,
            )

    # Success path: all validation happened before this call, so one
    # transaction creates the dataset (if missing), metrics, version, cases
    # and the succeeded import record (or finalizes the uploaded one).
    if dataset is None:
        dataset = Dataset(
            tenant_id=tenant_id,
            project_id=project_id,
            slug=dataset_slug,
            name=dataset_name,
            adapter_key=adapter_key,
            task_family=task_family,
            environment_kind=environment_kind,
            metadata_json=metadata or {},
        )
        session.add(dataset)
        session.flush()

    definitions = register_adapter_metrics(session, dataset=dataset, adapter=adapter)

    dataset_version = DatasetVersion(
        tenant_id=tenant_id,
        dataset_id=dataset.id,
        version=version,
        source_uri=source_uri,
        source_revision=source_revision,
        adapter_version=adapter.adapter_version,
        case_count=total_count,
        status="active",
        environment_image=environment_image,
        execution_image_digest=execution_image_digest,
        scoring_image_digest=scoring_image_digest,
        task_manifest_digest=source_digest,
    )
    session.add(dataset_version)
    session.flush()

    written = 0
    for index, raw in enumerate(records):
        normalized = adapter.normalize_case(raw)
        session.add(
            DatasetCase(
                tenant_id=tenant_id,
                dataset_version_id=dataset_version.id,
                case_key=normalized.case_key,
                ordinal=index + 1,
                repo=normalized.repo,
                base_commit=normalized.base_commit,
                problem_statement=normalized.problem_statement,
                reference_patch=normalized.reference_patch,
                test_patch=normalized.test_patch,
                expected_metrics_json=normalized.expected_metrics,
                extra_json=normalized.extra,
            )
        )
        written += 1
        if written % 500 == 0:
            session.flush()

    if written != total_count:
        raise DatasetSourceChanged(
            f"校验记录数 {total_count} 与实际读取记录数 {written} 不一致，拒绝生成部分版本"
        )

    for definition in definitions:
        session.add(
            DatasetVersionMetric(
                tenant_id=tenant_id,
                dataset_version_id=dataset_version.id,
                metric_definition_id=definition.id,
            )
        )

    if import_record is None:
        record = DatasetImport(
            tenant_id=tenant_id,
            dataset_id=dataset.id,
            dataset_slug=dataset_slug,
            adapter_key=adapter_key,
            version=version,
            source_uri=source_uri,
            source_revision=source_revision,
            status="succeeded",
            total_count=total_count,
            valid_count=total_count,
            invalid_count=0,
            created_by=created_by,
        )
        session.add(record)
    else:
        record = import_record
        record.dataset_id = dataset.id
        _set_import_status(
            record,
            status="succeeded",
            total_count=total_count,
            valid_count=total_count,
            invalid_count=0,
        )
        record.error_report_json = {}
    record.status_updated_at = datetime.now(UTC)
    session.commit()
    return ImportOutcome(
        status="succeeded",
        created=True,
        import_record=record,
        dataset=dataset,
        version=dataset_version,
        errors=[],
    )


def import_dataset_version(
    session: Session,
    *,
    dataset_slug: str,
    dataset_name: str,
    adapter_key: str,
    version: str,
    source_uri: str,
    source_revision: str,
    environment_image: str | None = None,
    execution_image_digest: str | None = None,
    scoring_image_digest: str | None = None,
    records: list[Mapping[str, Any]],
    created_by: str = "console",
    task_family: str | None = None,
    environment_kind: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ImportOutcome:
    """导入一个数据集版本：校验 → 入库；幂等可重放。

    The legacy JSON-body endpoint passes records in memory; the upload flow
    validates the file first and calls :func:`materialize_dataset_version`
    directly with a stream.
    """

    tenant_id = str(DEFAULT_TENANT_ID)

    def failed(errors: list[CaseIssue]) -> ImportValidationFailed:
        record = _record_failed_import(
            session,
            tenant_id=tenant_id,
            adapter_key=adapter_key,
            dataset_slug=dataset_slug,
            version=version,
            source_uri=source_uri,
            source_revision=source_revision,
            total=len(records),
            errors=errors,
            created_by=created_by,
        )
        return ImportValidationFailed(
            ImportOutcome(
                status="failed",
                created=False,
                import_record=record,
                errors=errors,
            )
        )

    try:
        adapter: DatasetAdapter = get_adapter(adapter_key)
    except AdapterNotFound as exc:
        raise failed([CaseIssue(code="adapter_not_found", message=str(exc), field="adapter_key")]) from exc

    errors = _validate_records(adapter, records)
    if errors:
        raise failed(errors)

    return materialize_dataset_version(
        session,
        dataset_slug=dataset_slug,
        dataset_name=dataset_name,
        adapter_key=adapter_key,
        version=version,
        source_uri=source_uri,
        source_revision=source_revision,
        environment_image=environment_image,
        execution_image_digest=execution_image_digest,
        scoring_image_digest=scoring_image_digest,
        records=records,
        total_count=len(records),
        created_by=created_by,
        task_family=task_family,
        environment_kind=environment_kind,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Queries（服务端分页：offset/limit + total）
# ---------------------------------------------------------------------------


def paginate(
    session: Session,
    query,
    *,
    limit: int,
    offset: int,
) -> tuple[list[Any], int]:
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    items = list(session.scalars(query.limit(limit).offset(offset)))
    return items, total


def page_info(*, limit: int, offset: int, total: int) -> dict[str, Any]:
    has_more = offset + limit < total
    return {
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


def get_dataset(session: Session, dataset_id: str) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise DatasetNotFound(dataset_id)
    return dataset


def list_datasets(session: Session, *, limit: int = 20, offset: int = 0) -> tuple[list[Dataset], int]:
    query = select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc())
    return paginate(session, query, limit=limit, offset=offset)


def get_dataset_version(session: Session, version_id: str) -> DatasetVersion:
    version = session.get(DatasetVersion, version_id)
    if version is None:
        raise DatasetVersionNotFound(version_id)
    return version


def list_dataset_versions(
    session: Session, dataset_id: str, *, limit: int = 20, offset: int = 0
) -> tuple[list[DatasetVersion], int]:
    query = (
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id.desc())
    )
    return paginate(session, query, limit=limit, offset=offset)


def get_dataset_case(session: Session, case_id: str) -> DatasetCase:
    case = session.get(DatasetCase, case_id)
    if case is None:
        raise DatasetCaseNotFound(case_id)
    return case


def list_version_cases(
    session: Session, version_id: str, *, limit: int = 20, offset: int = 0
) -> tuple[list[DatasetCase], int]:
    query = select(DatasetCase).where(DatasetCase.dataset_version_id == version_id).order_by(DatasetCase.ordinal)
    return paginate(session, query, limit=limit, offset=offset)


def list_version_metrics(session: Session, version_id: str) -> list[MetricDefinition]:
    """版本固化的指标定义集合，按 metric_key 排序。"""

    return list(
        session.scalars(
            select(MetricDefinition)
            .join(
                DatasetVersionMetric,
                DatasetVersionMetric.metric_definition_id == MetricDefinition.id,
            )
            .where(DatasetVersionMetric.dataset_version_id == version_id)
            .order_by(MetricDefinition.metric_key, MetricDefinition.version)
        )
    )


def get_import(session: Session, import_id: str) -> DatasetImport:
    record = session.get(DatasetImport, import_id)
    if record is None:
        raise DatasetImportNotFound(import_id)
    return record
