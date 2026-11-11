"""Evaluation results workbench queries and recovery actions (M5-05).

The console detail page is a read-mostly workbench: filter/search cases,
inspect every attempt and its test-level results, explain the frozen run
configuration, page through the event log and retry failed cases without
touching the successful ones. Everything here is scoped to one run; write
paths reuse the M4 case-attempt state machine instead of duplicating it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    TRACE_STATUSES,
    Dataset,
    DatasetVersion,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    ModelConfig,
    ResourcePool,
    TestCaseResult,
)
from ..money import money_str
from . import datasets as dataset_service
from . import evaluations

CASE_LIST_STATUSES = ("all", "resolved", "unresolved", "running", "failed", "queued")
FAILED_CASE_STATUSES = ("failed", "timeout")
RETRYABLE_CASE_STATUSES = ("failed", "timeout")
TEST_SUMMARY_KEYS = ("passed", "failed", "skipped", "error", "f2p_failed", "p2p_failed")
SORTABLE_CASE_COLUMNS = {
    "ordinal": EvalCaseRun.ordinal,
    "case_key": EvalCaseRun.case_key,
    "duration_ms": EvalCaseRun.duration_ms,
    "updated_at": EvalCaseRun.updated_at,
    "execution_status": EvalCaseRun.execution_status,
    "result_status": EvalCaseRun.result_status,
}


class WorkbenchNotFound(Exception):
    """Requested run/case row does not exist."""

    def __init__(self, message: str, *, code: str = "eval_case_run_not_found") -> None:
        self.code = code
        super().__init__(message)


class WorkbenchRejected(Exception):
    """The requested recovery action is not valid for the current state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "eval_action_not_allowed",
        status_code: int = 409,
    ) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Case list + filters
# ---------------------------------------------------------------------------


def status_condition(status: str):
    """Translate the prototype's single status chip into a SQL predicate."""

    if status == "resolved":
        return EvalCaseRun.result_status == "resolved"
    if status == "unresolved":
        return EvalCaseRun.result_status == "unresolved"
    if status == "running":
        return EvalCaseRun.execution_status == "running"
    if status == "failed":
        return EvalCaseRun.execution_status.in_(FAILED_CASE_STATUSES)
    if status == "queued":
        return EvalCaseRun.execution_status == "queued"
    raise WorkbenchRejected(f"未知的用例状态筛选：{status}", code="invalid_case_filter", status_code=422)


def case_counts(session: Session, run_id: str) -> dict[str, int]:
    """Counts for the filter chips; never affected by the current page."""

    rows = session.execute(
        select(
            EvalCaseRun.execution_status,
            EvalCaseRun.result_status,
            func.count(),
        )
        .where(EvalCaseRun.eval_run_id == run_id)
        .group_by(EvalCaseRun.execution_status, EvalCaseRun.result_status)
    ).all()
    counts = {
        "all": 0,
        "resolved": 0,
        "unresolved": 0,
        "running": 0,
        "failed": 0,
        "queued": 0,
    }
    for execution_status, result_status, amount in rows:
        counts["all"] += amount
        if result_status == "resolved":
            counts["resolved"] += amount
        if result_status == "unresolved":
            counts["unresolved"] += amount
        if execution_status == "running":
            counts["running"] += amount
        if execution_status in FAILED_CASE_STATUSES:
            counts["failed"] += amount
        if execution_status == "queued":
            counts["queued"] += amount
    return counts


def empty_test_summary() -> dict[str, int]:
    return {key: 0 for key in TEST_SUMMARY_KEYS}


def _summaries_for_latest_attempts(
    session: Session, cases: list[EvalCaseRun]
) -> dict[str, dict[str, int]]:
    """One batch query for the page's latest-attempt test summaries."""

    if not cases:
        return {}
    attempts = session.scalars(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id.in_([case.id for case in cases])
        )
    ).all()
    latest_attempt_id: dict[str, str] = {}
    by_key = {(attempt.eval_case_run_id, attempt.attempt_no): attempt for attempt in attempts}
    for case in cases:
        attempt = by_key.get((case.id, case.latest_attempt_no))
        if attempt is not None:
            latest_attempt_id[case.id] = attempt.id

    summaries = {case.id: empty_test_summary() for case in cases}
    if not latest_attempt_id:
        return summaries
    rows = session.execute(
        select(
            TestCaseResult.eval_case_attempt_id,
            TestCaseResult.test_group,
            TestCaseResult.outcome,
            func.count(),
        )
        .where(TestCaseResult.eval_case_attempt_id.in_(list(latest_attempt_id.values())))
        .group_by(
            TestCaseResult.eval_case_attempt_id,
            TestCaseResult.test_group,
            TestCaseResult.outcome,
        )
    ).all()
    by_attempt = {attempt_id: case_id for case_id, attempt_id in latest_attempt_id.items()}
    for attempt_id, group, outcome, amount in rows:
        case_id = by_attempt.get(attempt_id)
        if case_id is None:
            continue
        summary = summaries[case_id]
        if outcome in summary:
            summary[outcome] += amount
        group_key = f"{group.lower()}_{outcome}"
        if group_key in summary:
            summary[group_key] += amount
    return summaries


def case_row(case: EvalCaseRun, summary: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": case.id,
        "case_key": case.case_key,
        "ordinal": case.ordinal,
        "repo": (case.task_json or {}).get("repo"),
        "execution_status": case.execution_status,
        "result_status": case.result_status,
        "trace_status": case.trace_status,
        "latest_attempt_no": case.latest_attempt_no,
        "duration_ms": case.duration_ms,
        "error_code": case.error_code,
        "error_summary": case.error_summary,
        "metrics": case.metrics_json or {},
        "test_summary": summary or empty_test_summary(),
        "updated_at": evaluations._iso(case.updated_at),
    }


def list_cases(
    session: Session,
    run: EvalRun,
    *,
    status: str = "all",
    q: str | None = None,
    execution_status: str | None = None,
    result_status: str | None = None,
    trace_status: str | None = None,
    sort: str = "ordinal",
    order: str = "asc",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Filtered, paged case rows plus chip counts."""

    conditions = [EvalCaseRun.eval_run_id == run.id]
    if status and status != "all":
        conditions.append(status_condition(status))
    if q and q.strip():
        conditions.append(func.lower(EvalCaseRun.case_key).like(f"%{q.strip().lower()}%"))
    for value, column, allowed, label in (
        (
            execution_status,
            EvalCaseRun.execution_status,
            ("queued", "running", "succeeded", "failed", "timeout", "cancelled"),
            "execution_status",
        ),
        (
            result_status,
            EvalCaseRun.result_status,
            ("resolved", "unresolved", "not_scored"),
            "result_status",
        ),
        (
            trace_status,
            EvalCaseRun.trace_status,
            TRACE_STATUSES,
            "trace_status",
        ),
    ):
        if value:
            if value not in allowed:
                raise WorkbenchRejected(
                    f"未知的 {label} 筛选：{value}",
                    code="invalid_case_filter",
                    status_code=422,
                )
            conditions.append(column == value)
    column = SORTABLE_CASE_COLUMNS.get(sort)
    if column is None:
        raise WorkbenchRejected(f"未知的排序字段：{sort}", code="invalid_case_sort", status_code=422)
    if order not in ("asc", "desc"):
        raise WorkbenchRejected(f"未知的排序方向：{order}", code="invalid_case_sort", status_code=422)
    direction = column.desc() if order == "desc" else column.asc()
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    total = int(
        session.scalar(
            select(func.count()).select_from(EvalCaseRun).where(*conditions)
        )
        or 0
    )
    cases = list(
        session.scalars(
            select(EvalCaseRun)
            .where(*conditions)
            .order_by(direction, EvalCaseRun.id)
            .offset(safe_offset)
            .limit(safe_limit)
        )
    )
    summaries = _summaries_for_latest_attempts(session, cases)
    has_more = safe_offset + len(cases) < total
    return {
        "run_id": run.id,
        "items": [case_row(case, summaries.get(case.id)) for case in cases],
        "total": total,
        "counts": case_counts(session, run.id),
        "page_info": {
            "limit": safe_limit,
            "offset": safe_offset,
            "total": total,
            "has_more": has_more,
            "next_offset": safe_offset + len(cases) if has_more else None,
        },
    }


# ---------------------------------------------------------------------------
# Single case + attempts
# ---------------------------------------------------------------------------


def _get_case(session: Session, case_run_id: str, *, lock: bool = False) -> EvalCaseRun:
    statement = select(EvalCaseRun).where(EvalCaseRun.id == case_run_id)
    if lock:
        # Serialize concurrent retry requests on Postgres; SQLite (tests/local)
        # ignores the clause and relies on the unique attempt constraint below.
        statement = statement.with_for_update()
    case = session.scalar(statement)
    if case is None:
        raise WorkbenchNotFound(f"评测用例不存在：{case_run_id}")
    return case


def _attempt_rows(session: Session, case: EvalCaseRun) -> list[dict[str, Any]]:
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .where(EvalCaseAttempt.eval_case_run_id == case.id)
            .order_by(EvalCaseAttempt.attempt_no)
        )
    )
    return [_attempt_row(session, attempt) for attempt in attempts]


def _attempt_row(session: Session, attempt: EvalCaseAttempt) -> dict[str, Any]:
    from ..scoring.service import case_test_summary

    duration_ms: int | None = None
    if attempt.started_at is not None and attempt.ended_at is not None:
        duration_ms = int((attempt.ended_at - attempt.started_at).total_seconds() * 1000)
    return {
        "id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "phase": attempt.phase,
        "dispatch_status": attempt.dispatch_status,
        "runner_id": attempt.runner_id,
        "started_at": evaluations._iso(attempt.started_at),
        "ended_at": evaluations._iso(attempt.ended_at),
        "duration_ms": duration_ms,
        "error_code": attempt.error_code,
        "error_summary": attempt.error_summary,
        "retry_reason": attempt.retry_reason,
        "test_summary": {
            **empty_test_summary(),
            **case_test_summary(session, attempt.id),
        },
    }


def _latest_attempt(session: Session, case: EvalCaseRun) -> EvalCaseAttempt | None:
    return session.scalar(
        select(EvalCaseAttempt)
        .where(
            EvalCaseAttempt.eval_case_run_id == case.id,
            EvalCaseAttempt.attempt_no == case.latest_attempt_no,
        )
    )


def retry_state(session: Session, run: EvalRun, case: EvalCaseRun) -> dict[str, Any]:
    """Explain whether the case may be retried and why not."""

    if run.status != "running":
        return {"retryable": False, "reason": "run_not_running"}
    if case.execution_status not in RETRYABLE_CASE_STATUSES:
        return {"retryable": False, "reason": "case_not_failed"}
    if case.latest_attempt_no >= run.max_attempts:
        return {"retryable": False, "reason": "attempts_exhausted"}
    latest = _latest_attempt(session, case)
    if latest is None:
        return {"retryable": False, "reason": "attempt_missing"}
    if latest.status not in evaluations.TERMINAL_ATTEMPT_STATUSES:
        return {"retryable": False, "reason": "attempt_active"}
    return {"retryable": True, "reason": None}


def get_case(session: Session, case_run_id: str) -> dict[str, Any]:
    case = _get_case(session, case_run_id)
    run = session.get(EvalRun, case.eval_run_id)
    if run is None:
        raise WorkbenchNotFound(f"评测不存在：{case.eval_run_id}", code="eval_run_not_found")
    attempts = _attempt_rows(session, case)
    latest = _latest_attempt(session, case)
    return {
        "case": case_row(case),
        "run": {
            "id": run.id,
            "name": run.name,
            "status": run.status,
            "max_attempts": run.max_attempts,
            "case_timeout_seconds": run.case_timeout_seconds,
            "requested_concurrency": run.requested_concurrency,
            "effective_concurrency": run.effective_concurrency,
            "total_cases": run.total_cases,
        },
        "attempts": attempts,
        "retry": retry_state(session, run, case),
        "tests_attempt": latest.attempt_no if latest is not None else None,
    }


def _create_retry_attempt(
    session: Session,
    *,
    run: EvalRun,
    case: EvalCaseRun,
    latest: EvalCaseAttempt,
    actor: str,
    now: datetime,
) -> EvalCaseAttempt:
    """One new attempt + dispatch outbox row, preserving the failed attempt."""

    from . import case_attempts

    new_attempt_no = case.latest_attempt_no + 1
    outbox = case_attempts.build_retry_dispatch(
        session,
        case_attempts.CaseContext(session, case, run, latest),
        new_attempt_no=new_attempt_no,
        settings=get_settings(),
        now=now,
        retry_reason="manual_retry",
    )
    session.add(outbox)
    case.execution_status = "queued"
    case.result_status = "not_scored"
    case.error_code = None
    case.error_summary = None
    session.add(
        EvalEvent(
            tenant_id=run.tenant_id,
            eval_run_id=run.id,
            eval_case_run_id=case.id,
            attempt_no=new_attempt_no,
            event_type="case.retry_requested",
            payload_json={
                "requested_by": actor,
                "previous_attempt_no": latest.attempt_no,
                "dispatch_key": outbox.dispatch_key,
            },
        )
    )
    session.flush()
    attempt = session.scalar(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id == case.id,
            EvalCaseAttempt.attempt_no == new_attempt_no,
        )
    )
    if attempt is not None:
        from .trace_ingestion import set_case_trace_status

        set_case_trace_status(
            session, case_run=case, attempt=attempt, status="pending"
        )
    return attempt


def retry_case(session: Session, *, case_run_id: str, actor: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    case = _get_case(session, case_run_id, lock=True)
    run = session.get(EvalRun, case.eval_run_id)
    if run is None:
        raise WorkbenchNotFound(f"评测不存在：{case.eval_run_id}", code="eval_run_not_found")
    state = retry_state(session, run, case)
    if not state["retryable"]:
        raise WorkbenchRejected(
            _retry_reason_message(state["reason"]),
            code=f"case_retry_{state['reason']}",
        )
    try:
        latest = _latest_attempt(session, case)
        assert latest is not None  # retry_state guarantees it
        attempt = _create_retry_attempt(
            session, run=run, case=case, latest=latest, actor=actor, now=now
        )
        evaluations.refresh_run_progress(session, run, now=now)
        session.commit()
    except IntegrityError as exc:
        # Two operators/tabs may race on the same case; the unique
        # (case_run_id, attempt_no) constraint is the final arbiter.
        session.rollback()
        raise WorkbenchRejected(
            "该用例已被并发重试或状态已变化，请刷新后重试",
            code="case_retry_conflict",
        ) from exc
    return {
        "case": case_row(case),
        "attempt": _attempt_row(session, attempt),
        "retry": retry_state(session, run, case),
    }


def retry_failed_cases(
    session: Session, *, run_id: str, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Retry every failed/timed-out case that still has an attempt left."""

    now = now or datetime.now(UTC)
    run = session.get(EvalRun, run_id)
    if run is None:
        raise WorkbenchNotFound(f"评测不存在：{run_id}", code="eval_run_not_found")
    if run.status != "running":
        raise WorkbenchRejected(
            "只有运行中的评测可以重试失败用例",
            code="run_not_running",
        )
    cases = list(
        session.scalars(
            select(EvalCaseRun)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseRun.execution_status.in_(RETRYABLE_CASE_STATUSES),
            )
            .order_by(EvalCaseRun.ordinal)
            .with_for_update()
        )
    )
    retried = 0
    skipped = 0
    try:
        for case in cases:
            state = retry_state(session, run, case)
            latest = _latest_attempt(session, case)
            if not state["retryable"] or latest is None:
                skipped += 1
                continue
            _create_retry_attempt(session, run=run, case=case, latest=latest, actor=actor, now=now)
            retried += 1
        if retried:
            session.add(
                EvalEvent(
                    tenant_id=run.tenant_id,
                    eval_run_id=run.id,
                    event_type="eval.retry_failed",
                    payload_json={
                        "requested_by": actor,
                        "retried": retried,
                        "skipped": skipped,
                    },
                )
            )
            evaluations.refresh_run_progress(session, run, now=now)
            session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise WorkbenchRejected(
            "批量重试与另一请求冲突，请刷新后重试",
            code="eval_retry_conflict",
        ) from exc
    return {"run_id": run.id, "retried": retried, "skipped": skipped}


def _retry_reason_message(reason: str | None) -> str:
    return {
        "run_not_running": "只有运行中的评测可以重试失败用例",
        "case_not_failed": "只有失败或超时的用例可以重试",
        "attempts_exhausted": "该用例已达到最大 attempts 次数",
        "attempt_missing": "该用例没有可追溯的 attempt",
        "attempt_active": "该用例仍有进行中的 attempt",
    }.get(reason or "", "当前状态不允许重试该用例")


# ---------------------------------------------------------------------------
# Frozen configuration snapshot
# ---------------------------------------------------------------------------


def _metric_definition_row(definition) -> dict[str, Any]:
    return {
        "id": definition.id,
        "metric_key": definition.metric_key,
        "version": definition.version,
        "display_name": definition.display_name,
        "description": definition.description,
        "formula_text": definition.formula_text,
        "display_kind": definition.display_kind,
        "unit": definition.unit,
        "direction": definition.direction,
        "denominator_policy": definition.denominator_policy,
        "config": definition.config_json,
    }


def config_snapshot(session: Session, run: EvalRun) -> dict[str, Any]:
    """Everything needed to explain what this run executed, without secrets."""

    dataset: dict[str, Any] | None = None
    metrics: list[dict[str, Any]] = []
    if run.dataset_version_id:
        version = session.get(DatasetVersion, run.dataset_version_id)
        if version is not None:
            dataset_record = session.get(Dataset, version.dataset_id)
            dataset = {
                "dataset_id": version.dataset_id,
                "dataset_name": dataset_record.name if dataset_record is not None else None,
                "dataset_slug": dataset_record.slug if dataset_record is not None else None,
                "adapter_key": dataset_record.adapter_key if dataset_record is not None else None,
                "task_family": dataset_record.task_family if dataset_record is not None else None,
                "environment_kind": dataset_record.environment_kind if dataset_record is not None else None,
                "version_id": version.id,
                "version": version.version,
                "status": version.status,
                "case_count": version.case_count,
                "source_uri": version.source_uri,
                "source_revision": version.source_revision,
                "adapter_version": version.adapter_version,
                "environment_image": version.environment_image,
                "execution_image_digest": version.execution_image_digest,
                "scoring_image_digest": version.scoring_image_digest,
                "created_at": evaluations._iso(version.created_at),
            }
            metrics = [
                _metric_definition_row(definition)
                for definition in dataset_service.list_version_metrics(session, version.id)
            ]

    model: dict[str, Any] | None = None
    if run.model_config_id:
        config = session.get(ModelConfig, run.model_config_id)
        if config is not None:
            model = {
                "id": config.id,
                "display_name": config.display_name,
                "provider": config.provider,
                "model_name": config.model_name,
                "api_style": config.api_style,
                "base_url": config.base_url,
                "is_temporary": config.is_temporary,
            }

    pool: dict[str, Any] | None = None
    if run.resource_pool_id:
        resource_pool = session.get(ResourcePool, run.resource_pool_id)
        if resource_pool is not None:
            pool = {
                "id": resource_pool.id,
                "name": resource_pool.name,
                "provider_key": resource_pool.provider_key,
                "provisioning_mode": resource_pool.provisioning_mode,
                "min_capacity": resource_pool.min_capacity,
                "max_capacity": resource_pool.max_capacity,
                "price_cny": money_str(resource_pool.price_cny),
                "price_threshold_cny": money_str(resource_pool.price_threshold_cny),
                "capacity_reason": resource_pool.capacity_reason,
                "status": resource_pool.status,
            }

    assets = run.asset_snapshot_json or {}
    consistency = run.consistency_json or {}
    # Keep the config snapshot consistent with the detail endpoint: gateway
    # usage rows are authoritative once a budget is bound.
    spent = (
        evaluations.spent_cny(session, run.id)
        if run.model_budget_cny is not None
        else run.budget_spent_cny
    )
    return {
        "run_id": run.id,
        "name": run.name,
        "status": run.status,
        "created_by": run.created_by,
        "created_at": evaluations._iso(run.created_at),
        "started_at": evaluations._iso(run.started_at),
        "finished_at": evaluations._iso(run.finished_at),
        "dataset": dataset,
        "runtime": {
            "key": run.runtime_key,
            "version": run.runtime_version,
        },
        "assets": {
            "snapshot_id": assets.get("snapshot_id") or run.asset_snapshot_id,
            "source_id": assets.get("source_id"),
            "requested_ref": assets.get("requested_ref"),
            "resolved_commit": assets.get("resolved_commit"),
            "tree_digest": assets.get("tree_digest"),
            "bundle_digest": assets.get("bundle_digest"),
            "bundle_size": assets.get("bundle_size"),
            "file_count": assets.get("file_count"),
            "asset_count": assets.get("asset_count"),
            "runtime_key": assets.get("runtime_key"),
            "items": assets.get("assets") or [],
        },
        "model": model,
        "resource_pool": pool,
        "execution": {
            "shard_size": run.shard_size,
            "requested_concurrency": run.requested_concurrency,
            "effective_concurrency": run.effective_concurrency,
            "queue_reason": run.queue_reason,
            "case_timeout_seconds": run.case_timeout_seconds,
            "max_attempts": run.max_attempts,
            "execution_image_digest": run.execution_image_digest,
            "scoring_image_digest": run.scoring_image_digest,
            "task_manifest_digest": run.task_manifest_digest,
        },
        "consistency": {
            "effective_concurrency": consistency.get("effective_concurrency"),
            "warnings": consistency.get("warnings") or [],
            "blockers": consistency.get("blockers") or [],
        },
        "budget": {
            "model_budget_cny": money_str(run.model_budget_cny),
            "budget_spent_cny": money_str(spent),
            "budget_remaining_cny": (
                money_str(evaluations.budget_remaining_cny(run, spent))
                if run.model_budget_cny is not None
                else None
            ),
            "budget_version": run.budget_version,
            "pause_reason": run.pause_reason,
        },
        "metrics": metrics,
    }
