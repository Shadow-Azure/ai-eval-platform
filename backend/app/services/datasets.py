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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..datasets import (
    AdapterNotFound,
    CaseIssue,
    DatasetAdapter,
    adapter_metadata,
    get_adapter,
    registered_adapter_keys,
)
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import Dataset, DatasetCase, DatasetImport, DatasetVersion, DatasetVersionMetric, MetricDefinition
from .dataset_images import DatasetImageUnavailable, effective_sources, image_for_case, probe_case_images
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

    settings = get_settings()
    image_arch = getattr(settings, "dataset_image_arch", "x86_64")
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
        # M5-13: fall back to the adapter registry for task family, environment
        # shape and description so catalog cards and import defaults share one
        # source of truth.
        meta = adapter_metadata(adapter)
        dataset_metadata = dict(metadata or {})
        dataset_metadata.setdefault("description", meta["description"] or "")
        dataset = Dataset(
            tenant_id=tenant_id,
            project_id=project_id,
            slug=dataset_slug,
            name=dataset_name,
            adapter_key=adapter_key,
            task_family=task_family or meta["task_family"],
            environment_kind=environment_kind or meta["environment_kind"],
            metadata_json=dataset_metadata,
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
                environment_image=image_for_case(
                    adapter_key,
                    normalized.case_key,
                    raw,
                    arch=image_arch,
                ),
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

    settings = get_settings()
    arch = getattr(settings, "dataset_image_arch", "x86_64")
    case_images: dict[str, str] = {}
    for raw in records:
        normalized = adapter.normalize_case(raw)
        image = image_for_case(adapter_key, normalized.case_key, raw, arch=arch)
        if image:
            case_images[normalized.case_key] = image
    if case_images:
        check = probe_case_images(
            case_images,
            settings=settings,
            sources=effective_sources(session, settings),
        )
        if check.get("status") == "blocked":
            raise DatasetImageUnavailable(check)

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


def _escape_like(term: str) -> str:
    """Escape LIKE metacharacters so a user search is always a literal substring."""

    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


DIFFICULTY_LEVELS = ("easy", "medium", "hard")
_DURATION_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)"
)


def difficulty_level(label: str | None) -> str | None:
    """Normalize an adapter difficulty label to the prototype's three levels.

    SWE-bench Verified labels difficulty as an estimated human solve-time band
    (``15 min - 1 hour``, ``1-4 hours``, ...), while the UI product language is
    easy/medium/hard. The band stays untouched in ``difficulty_label``; this
    presentation level uses the upper bound (≤1h easy, ≤4h medium, >4h hard)
    so no new data is invented. Labels already using easy/medium/hard pass
    through, and an unrecognized label stays ``None`` instead of guessing.
    """

    if not label:
        return None
    text = " ".join(label.strip().lower().split())
    if not text:
        return None
    if text in DIFFICULTY_LEVELS:
        return text
    for level in DIFFICULTY_LEVELS:
        if text.startswith(f"{level} ") or text.startswith(f"{level}(") or text.startswith(f"{level}（"):
            return level
    matches = _DURATION_PATTERN.findall(text)
    if not matches:
        return None
    minutes = [float(amount) * (60 if unit.startswith("h") else 1) for amount, unit in matches]
    upper = max(minutes)
    if any(marker in text for marker in (">", "≥", "more than", "+")):
        upper += 1
    if upper <= 60:
        return "easy"
    if upper <= 240:
        return "medium"
    return "hard"


def case_difficulty_label(case: DatasetCase) -> str | None:
    """Raw adapter-provided difficulty label carried in ``extra_json``."""

    value = (case.extra_json or {}).get("difficulty")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def case_expected_test_count(case: DatasetCase) -> int:
    """Number of expected checks frozen with the case (F2P + P2P for SWE-bench)."""

    metrics = case.expected_metrics_json or {}
    return sum(len(value) for value in metrics.values() if isinstance(value, list))


def _version_difficulty_labels(session: Session, version_id: str) -> list[tuple[str, int]]:
    label_expr = func.lower(func.trim(DatasetCase.extra_json["difficulty"].as_string()))
    rows = session.execute(
        select(label_expr, func.count())
        .where(
            DatasetCase.dataset_version_id == version_id,
            label_expr.is_not(None),
            label_expr != "",
        )
        .group_by(label_expr)
    ).all()
    return [(str(label), int(count)) for label, count in rows if label]


def list_version_cases(
    session: Session,
    version_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
    q: str | None = None,
    difficulty: str | None = None,
) -> tuple[list[DatasetCase], int]:
    query = select(DatasetCase).where(DatasetCase.dataset_version_id == version_id)
    term = (q or "").strip()
    if term:
        # Case-insensitive substring over the instance ID and repository. The
        # search stays in SQL so the server never loads the whole version.
        pattern = f"%{_escape_like(term.lower())}%"
        query = query.where(
            or_(
                func.lower(DatasetCase.case_key).like(pattern, escape="\\"),
                func.lower(func.coalesce(DatasetCase.repo, "")).like(pattern, escape="\\"),
            )
        )
    difficulty_value = (difficulty or "").strip().lower()
    if difficulty_value:
        # Map the requested level back to the raw labels that represent it, so
        # the filter remains a single SQL predicate over the paginated query.
        labels = [
            label
            for label, _count in _version_difficulty_labels(session, version_id)
            if difficulty_level(label) == difficulty_value
        ]
        label_expr = func.lower(func.trim(DatasetCase.extra_json["difficulty"].as_string()))
        query = (
            query.where(label_expr.in_(labels))
            if labels
            else query.where(DatasetCase.id.is_(None))
        )
    query = query.order_by(DatasetCase.ordinal, DatasetCase.id)
    return paginate(session, query, limit=limit, offset=offset)


def version_difficulty_facets(session: Session, version_id: str) -> list[dict[str, Any]]:
    """Distinct difficulty levels of a whole version, with case counts.

    Facets come from all cases (not the current search page) so the filter
    options stay stable while the user searches or pages. Labels that cannot
    be mapped to a level are omitted rather than misclassified.
    """

    counts = dict.fromkeys(DIFFICULTY_LEVELS, 0)
    for label, count in _version_difficulty_labels(session, version_id):
        level = difficulty_level(label)
        if level is not None:
            counts[level] += count
    return [
        {"value": level, "count": counts[level]}
        for level in DIFFICULTY_LEVELS
        if counts[level] > 0
    ]


def version_import_validation(session: Session, version: DatasetVersion) -> dict[str, Any] | None:
    """Import-time validation record that produced this immutable version.

    The card must show a real record, so this reuses the succeeded/duplicate
    ``dataset_import`` row and its persisted report; nothing is recomputed or
    guessed. Legacy JSON imports predate the report and therefore expose only
    their counts with ``completeness_percent=None``.
    """

    record = session.scalar(
        select(DatasetImport)
        .where(
            DatasetImport.dataset_id == version.dataset_id,
            DatasetImport.version == version.version,
            DatasetImport.status.in_(("succeeded", "duplicate")),
        )
        .order_by(DatasetImport.created_at.desc(), DatasetImport.id.desc())
        .limit(1)
    )
    if record is None:
        return None

    report = record.validation_report_json or {}
    completeness = report.get("field_completeness")
    if not isinstance(completeness, dict):
        completeness = {}
    percents = [
        entry["percent"]
        for entry in completeness.values()
        if isinstance(entry, dict) and isinstance(entry.get("percent"), (int, float))
    ]
    error_codes = report.get("error_codes")
    return {
        "import_id": record.id,
        "status": record.status,
        "passed": record.invalid_count == 0 and not report.get("fatal"),
        "total_count": record.total_count,
        "valid_count": record.valid_count,
        "invalid_count": record.invalid_count,
        "field_completeness": completeness,
        "completeness_percent": min(percents) if percents else None,
        "report_available": bool(report),
        "error_codes": error_codes if isinstance(error_codes, dict) else {},
        "created_at": _iso(record.created_at),
    }


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
            .order_by(MetricDefinition.metric_key, MetricDefinition.version.desc())
        )
    )


def get_import(session: Session, import_id: str) -> DatasetImport:
    record = session.get(DatasetImport, import_id)
    if record is None:
        raise DatasetImportNotFound(import_id)
    return record


def list_imports(session: Session, *, limit: int = 20, offset: int = 0) -> tuple[list[DatasetImport], int]:
    query = select(DatasetImport).order_by(DatasetImport.created_at.desc(), DatasetImport.id.desc())
    return paginate(session, query, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Catalog（M5-13）：目录卡片 + 适配器注册表元数据
# ---------------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat(timespec="microseconds") if value is not None else None


def _metric_card(definition: MetricDefinition) -> dict[str, Any]:
    return {
        "metric_key": definition.metric_key,
        "version": definition.version,
        "display_name": definition.display_name,
        "display_kind": definition.display_kind,
        "unit": definition.unit,
        "direction": definition.direction,
    }


def dataset_catalog(session: Session, *, limit: int = 200, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """数据集目录条目：数据集行 + 最新版本 + 主指标 + 适配器元数据。

    目录卡片所需字段全部来自数据库与适配器注册表，不在前端硬编码某个数据集。
    版本/指标按当前页批量查询，避免逐卡片 N+1。
    """

    datasets, total = list_datasets(session, limit=limit, offset=offset)
    ids = [dataset.id for dataset in datasets]
    versions: dict[str, list[DatasetVersion]] = {}
    metrics: dict[str, list[MetricDefinition]] = {}
    if ids:
        for version in session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id.in_(ids))
            .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id.desc())
        ):
            versions.setdefault(version.dataset_id, []).append(version)
        for definition in session.scalars(
            select(MetricDefinition)
            .where(MetricDefinition.dataset_id.in_(ids))
            .order_by(MetricDefinition.metric_key, MetricDefinition.version.desc())
        ):
            metrics.setdefault(definition.dataset_id, []).append(definition)

    items: list[dict[str, Any]] = []
    for dataset in datasets:
        try:
            adapter = get_adapter(dataset.adapter_key)
        except AdapterNotFound:
            adapter = None
        meta = adapter_metadata(adapter) if adapter is not None else {}
        dataset_versions = versions.get(dataset.id, [])
        latest = dataset_versions[0] if dataset_versions else None
        definitions = metrics.get(dataset.id, [])
        primary_key = meta.get("primary_metric_key")
        primary = next((item for item in definitions if item.metric_key == primary_key), None)
        if primary is None and definitions:
            primary = definitions[0]
        # DATASET_VERSION_STATUSES 目前只有 active/archived，且没有归档 API；
        # 非 active 的最新版本保守按“待导入”处理，不发明第 4 种产品状态。
        # 引入归档工作流时再定义“已归档”的目录语义与 CTA。
        status = "ready" if latest is not None and latest.status == "active" else "draft"
        items.append(
            {
                "id": dataset.id,
                "slug": dataset.slug,
                "name": dataset.name,
                "adapter_key": dataset.adapter_key,
                "task_family": dataset.task_family or meta.get("task_family"),
                "environment_kind": dataset.environment_kind or meta.get("environment_kind"),
                "description": (dataset.metadata_json or {}).get("description")
                or meta.get("description")
                or "",
                "created_at": _iso(dataset.created_at),
                "updated_at": _iso(latest.created_at if latest is not None else dataset.updated_at),
                "version_count": len(dataset_versions),
                "latest_version": (
                    {
                        "id": latest.id,
                        "version": latest.version,
                        "status": latest.status,
                        "case_count": latest.case_count,
                        "created_at": _iso(latest.created_at),
                    }
                    if latest is not None
                    else None
                ),
                "primary_metric": _metric_card(primary) if primary is not None else None,
                "adapter": {
                    "key": dataset.adapter_key,
                    "display_name": meta.get("display_name") or dataset.adapter_key,
                    "registered": adapter is not None,
                },
                "status": status,
            }
        )
    return items, total


def adapter_catalog(session: Session) -> list[dict[str, Any]]:
    """已注册适配器的自描述元数据 + 已挂在目录下的数据集数量。"""

    counts = {
        key: int(count)
        for key, count in session.execute(
            select(Dataset.adapter_key, func.count(Dataset.id)).group_by(Dataset.adapter_key)
        ).all()
    }
    items: list[dict[str, Any]] = []
    for key in registered_adapter_keys():
        adapter = get_adapter(key)
        metadata = adapter_metadata(adapter)
        metadata["dataset_count"] = int(counts.get(key, 0))
        items.append(metadata)
    return items
