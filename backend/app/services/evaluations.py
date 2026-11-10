"""Minimal synthetic evaluation lifecycle (M2-05).

Creation is one transaction that builds run/case/attempt rows AND the shard
dispatch outbox rows (transactional outbox, design 05 §6.4 / 06 §5). Outbox
payloads carry the contract-valid repository_dispatch client_payload plus the
byte-stable manifest text and its sha256 digest, so dispatch/manifest serving
and post-crash reconciliation never regenerate state.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import (
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    ModelConfig,
    ModelUsage,
    Outbox,
)
from ..runner_contracts import ContractInvalid, validate_payload

SHARD_DISPATCH_TOPIC = "eval.shard.dispatch"
MANIFEST_PATH_TEMPLATE = "/runner/v1/shards/{shard_id}/manifest"

TERMINAL_RUN_STATUSES = ("completed", "partial", "failed", "cancelled")
# M4-05: a budget pause is deliberately non-terminal. While either status is
# set the scheduler/mutators must not create new work and the run must not be
# reclassified as completed/partial/failed.
PAUSED_RUN_STATUSES = ("pausing_budget", "paused_budget")
BUDGET_PAUSED = "budget_paused"
BUDGET_EXCEEDED = "budget_exceeded"
TERMINAL_CASE_STATUSES = ("succeeded", "failed", "timeout", "cancelled")
TERMINAL_ATTEMPT_STATUSES = (
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "artifact_missing",
    "dispatch_failed",
)
# Attempt phases that still own work. ``cancelling`` keeps the started ones
# (running/scoring/uploading) alive so their artifacts settle; only the ones
# that never started an agent are cancelled outright.
ACTIVE_ATTEMPT_STATUSES = (
    "pending",
    "dispatching",
    "queued",
    "leased",
    "running",
    "scoring",
    "uploading",
)
NOT_STARTED_ATTEMPT_STATUSES = ("pending", "dispatching", "queued")

# Stable cancellation error codes (diagnosable on attempt/case rows).
RUN_CANCELLED = "run_cancelled"
CANCEL_GRACE_EXCEEDED = "cancel_grace_exceeded"


class EvaluationNotFound(Exception):
    pass


class EvaluationAlreadyFinal(Exception):
    pass


class EvaluationInvalid(Exception):
    """Request violates the synthetic-evaluation creation contract."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _clean_case_keys(case_keys: list[str], settings: Settings) -> list[str]:
    if not isinstance(case_keys, list) or not case_keys:
        raise EvaluationInvalid("case_keys 必须是非空字符串列表", field="case_keys")
    if len(case_keys) > settings.eval_max_cases:
        raise EvaluationInvalid(
            f"case_keys 数量不能超过 {settings.eval_max_cases}", field="case_keys"
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for key in case_keys:
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise EvaluationInvalid("case_key 必须是 1..200 字符的非空字符串", field="case_keys")
        if key in seen:
            raise EvaluationInvalid(f"case_key 重复：{key}", field="case_keys")
        seen.add(key)
        cleaned.append(key)
    return cleaned


def create_synthetic_evaluation(
    session: Session,
    *,
    name: str,
    case_keys: list[str],
    selected_case_keys: list[str] | None = None,
    shard_size: int | None = None,
    requested_concurrency: int | None = None,
    case_timeout_seconds: int | None = None,
    max_attempts: int | None = None,
    idempotency_key: str | None = None,
    created_by: str,
    model_config_id: str | None = None,
    model_name: str | None = None,
    model_budget_usd: Decimal | float | str | None = None,
    dataset_version_id: str | None = None,
    runtime_key: str | None = None,
    runtime_version: str | None = None,
    resource_pool_id: str | None = None,
    scoring_image_digest: str | None = None,
    execution_image_digest: str | None = None,
    asset_snapshot_id: str | None = None,
    asset_snapshot_json: dict | None = None,
    consistency_json: dict | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, bool]:
    """Create the evaluation + shard dispatch outbox in one transaction.

    Replays with the same (tenant, created_by, idempotency_key) return the
    original run untouched.
    """

    settings = settings or Settings()
    now = _now(now)
    shard_size = shard_size if shard_size is not None else settings.eval_shard_size_default
    # GitHub runs at most 256 jobs per workflow run; the prepare job plus one
    # matrix job per case must stay inside that envelope (255 + 1 = 256).
    if not isinstance(shard_size, int) or not 1 <= shard_size <= 255:
        raise EvaluationInvalid("shard_size 必须在 1..255 之间", field="shard_size")
    requested = (
        requested_concurrency
        if requested_concurrency is not None
        else settings.eval_default_requested_concurrency
    )
    if not isinstance(requested, int) or not 1 <= requested <= 256:
        raise EvaluationInvalid("requested_concurrency 必须在 1..256 之间", field="requested_concurrency")
    timeout = case_timeout_seconds if case_timeout_seconds is not None else 3600
    if not isinstance(timeout, int) or not (
        settings.eval_min_case_timeout_seconds
        <= timeout
        <= settings.eval_max_case_timeout_seconds
    ):
        raise EvaluationInvalid(
            "case_timeout_seconds 超出允许范围", field="case_timeout_seconds"
        )
    attempts = max_attempts if max_attempts is not None else 2
    if not isinstance(attempts, int) or not 1 <= attempts <= settings.eval_max_case_attempts:
        raise EvaluationInvalid("max_attempts 超出允许范围", field="max_attempts")
    idempotency_key = idempotency_key or str(uuid.uuid4())

    tenant_id = str(DEFAULT_TENANT_ID)
    config = None
    if model_config_id:
        config = session.get(ModelConfig, model_config_id)
        if config is None or config.tenant_id != tenant_id:
            raise EvaluationInvalid("模型配置不存在或不可用", field="model_config_id")
        if not model_name:
            model_name = config.model_name
        if model_name != config.model_name:
            raise EvaluationInvalid("model_name 必须与模型配置一致", field="model_name")
    elif model_name:
        raise EvaluationInvalid("指定 model_name 时必须同时指定 model_config_id", field="model_name")
    budget = None
    if model_budget_usd is not None:
        budget = Decimal(str(model_budget_usd))
        if budget < 0:
            raise EvaluationInvalid("model_budget_usd 不能为负", field="model_budget_usd")
    project_id = str(DEFAULT_PROJECT_ID)
    existing = session.scalar(
        select(EvalRun).where(
            EvalRun.tenant_id == tenant_id,
            EvalRun.created_by == created_by,
            EvalRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing, False

    case_specs: list[dict] = []
    if dataset_version_id:
        version = session.get(DatasetVersion, dataset_version_id)
        if version is None or version.tenant_id != tenant_id:
            raise EvaluationInvalid("数据集版本不存在", field="dataset_version_id")
        dataset = session.get(Dataset, version.dataset_id)
        if dataset is None or dataset.tenant_id != tenant_id:
            raise EvaluationInvalid("数据集不存在", field="dataset_version_id")
        if version.status != "active" or version.case_count <= 0:
            raise EvaluationInvalid("数据集版本不可用", field="dataset_version_id")
        cases = list(
            session.scalars(
                select(DatasetCase)
                .where(DatasetCase.dataset_version_id == version.id)
                .order_by(DatasetCase.ordinal)
            )
        )
        if selected_case_keys is not None:
            wanted = set(selected_case_keys)
            selected = [case for case in cases if case.case_key in wanted]
            if len(selected) != len(wanted):
                raise EvaluationInvalid(
                    "case_keys 包含不属于该数据集版本的用例", field="case_keys"
                )
            cases = selected
        if not cases:
            raise EvaluationInvalid("数据集版本没有用例", field="dataset_version_id")
        if len(cases) > settings.eval_max_cases:
            raise EvaluationInvalid(
                f"单个评测最多 {settings.eval_max_cases} 个用例", field="case_keys"
            )
        case_specs = [
            {
                "case_key": case.case_key,
                "dataset_case_id": case.id,
                "ordinal": case.ordinal or index + 1,
                "task_json": {
                    "case_key": case.case_key,
                    "problem_statement": case.problem_statement,
                    "repo": case.repo,
                    "base_commit": case.base_commit,
                    "test_patch": case.test_patch,
                    "expected_metrics": case.expected_metrics_json,
                },
            }
            for index, case in enumerate(cases)
        ]
        name = name or dataset.name
    else:
        cleaned = _clean_case_keys(case_keys, settings)
        case_specs = [
            {"case_key": key, "dataset_case_id": key, "ordinal": index + 1, "task_json": {}}
            for index, key in enumerate(cleaned)
        ]
    total_cases = len(case_specs)

    run = EvalRun(
        tenant_id=tenant_id,
        project_id=project_id,
        name=(name or "合成评测")[:200],
        status="queued",
        requested_concurrency=requested,
        effective_concurrency=None,
        queue_reason=None,
        shard_size=shard_size,
        total_cases=total_cases,
        queued_count=total_cases,
        model_config_id=model_config_id,
        model_name=model_name,
        model_budget_usd=budget,
        dataset_version_id=dataset_version_id,
        runtime_key=runtime_key,
        runtime_version=runtime_version,
        resource_pool_id=resource_pool_id,
        scoring_image_digest=scoring_image_digest,
        execution_image_digest=execution_image_digest,
        asset_snapshot_id=asset_snapshot_id,
        asset_snapshot_json=asset_snapshot_json or {},
        consistency_json=consistency_json or {},
        idempotency_key=idempotency_key[:128],
        created_by=created_by[:128],
        case_timeout_seconds=timeout,
        max_attempts=attempts,
    )
    session.add(run)
    session.flush()

    shard_payloads = build_shard_dispatches(
        session,
        run=run,
        case_specs=case_specs,
        shard_size=shard_size,
        settings=settings,
        now=now,
    )
    session.add_all(shard_payloads.outbox_rows)
    session.add_all(shard_payloads.attempts)

    session.add(
        EvalEvent(
            tenant_id=tenant_id,
            eval_run_id=run.id,
            event_type="eval.created",
            payload_json={
                "name": run.name,
                "case_count": run.total_cases,
                "shard_count": len(shard_payloads.outbox_rows),
                "shard_size": shard_size,
                "dataset_version_id": dataset_version_id,
            },
        )
    )
    session.commit()
    return run, True


class ShardDispatchBundle:
    def __init__(self) -> None:
        self.outbox_rows: list[Outbox] = []
        self.attempts: list[EvalCaseAttempt] = []


def build_shard_dispatches(
    session: Session,
    *,
    run: EvalRun,
    case_specs: list[dict],
    shard_size: int,
    settings: Settings,
    now: datetime,
) -> ShardDispatchBundle:
    """Build one outbox row per shard + one pending attempt per case.

    All rows stay uncommitted; the caller owns the transaction.
    """

    for start in range(0, len(case_specs), shard_size):
        for offset, spec in enumerate(case_specs[start : start + shard_size]):
            session.add(
                EvalCaseRun(
                    tenant_id=str(DEFAULT_TENANT_ID),
                    eval_run_id=run.id,
                    dataset_case_id=str(spec.get("dataset_case_id") or spec["case_key"])[:128],
                    case_key=str(spec["case_key"])[:200],
                    ordinal=int(spec.get("ordinal") or start + offset + 1),
                    task_json=spec.get("task_json") or {},
                )
            )
        session.flush()

    case_runs = list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    return build_case_dispatches(
        session,
        run=run,
        case_runs=case_runs,
        attempt_no=1,
        shard_size=shard_size,
        shard_prefix="shard",
        settings=settings,
        now=now,
    )


def build_case_dispatches(
    session: Session,
    *,
    run: EvalRun,
    case_runs: list[EvalCaseRun],
    attempt_no: int,
    shard_size: int,
    shard_prefix: str,
    settings: Settings,
    now: datetime,
) -> ShardDispatchBundle:
    """Build dispatch outbox rows + attempts for EXISTING case rows.

    Used by creation (attempt 1) and by ``resume-budget`` (a new attempt for
    every unfinished case). The caller owns the transaction; case rows are
    only mutated through ``latest_attempt_no``.
    """

    bundle = ShardDispatchBundle()
    tenant_id = str(DEFAULT_TENANT_ID)
    deadline = now + timedelta(minutes=settings.eval_shard_deadline_minutes)
    expires_at = now + timedelta(seconds=settings.eval_shard_ttl_seconds)

    for shard_index, start in enumerate(range(0, len(case_runs), shard_size)):
        chunk = case_runs[start : start + shard_size]
        shard_id = f"{shard_prefix}_{shard_index:03d}"[:64]
        dispatch_key = f"{run.id}:{shard_id}:{attempt_no}"

        manifest = {
            "schema_version": "1.0",
            "eval_run_id": run.id,
            "shard_id": shard_id,
            "attempt": attempt_no,
            "dispatch_key": dispatch_key,
            "callback_base_url": settings.runner_callback_base_url,
            "deadline": deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "case_timeout_seconds": run.case_timeout_seconds,
            "max_attempts": run.max_attempts,
            "cases": [
                {
                    "case_run_id": case_run.id,
                    "case_key": case_run.case_key,
                    "dataset_case_id": case_run.dataset_case_id,
                }
                for case_run in chunk
            ],
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        manifest_digest = "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
        query = urlencode(
            {
                "eval_run_id": run.id,
                "attempt": attempt_no,
                "dispatch_key": dispatch_key,
            }
        )
        manifest_url = (
            f"{settings.runner_callback_base_url.rstrip('/')}"
            f"{MANIFEST_PATH_TEMPLATE.format(shard_id=shard_id)}?{query}"
        )
        client_payload = {
            "schema_version": "1.0",
            "eval_run_id": run.id,
            "shard_id": shard_id,
            "attempt": attempt_no,
            "dispatch_key": dispatch_key,
            "manifest_url": manifest_url,
            "manifest_digest": manifest_digest,
            "expires_at": expires_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }
        try:
            validate_payload("dispatch", client_payload)
            validate_payload("manifest", manifest)
        except ContractInvalid as exc:
            raise EvaluationInvalid(f"分片契约校验失败：{exc}") from exc

        bundle.outbox_rows.append(
            Outbox(
                tenant_id=tenant_id,
                topic=SHARD_DISPATCH_TOPIC,
                aggregate_type="eval_run",
                aggregate_id=run.id,
                dispatch_key=dispatch_key,
                operation="repository_dispatch",
                payload_json={
                    "client_payload": client_payload,
                    "manifest_text": manifest_text,
                    "manifest_digest": manifest_digest,
                    "attempt": attempt_no,
                    "shard_id": shard_id,
                },
                status="pending",
            )
        )
        for case_run in chunk:
            case_run.latest_attempt_no = attempt_no
            bundle.attempts.append(
                EvalCaseAttempt(
                    tenant_id=tenant_id,
                    eval_case_run_id=case_run.id,
                    attempt_no=attempt_no,
                    status="pending",
                    dispatch_status="pending",
                    shard_id=shard_id,
                    dispatch_key=f"{dispatch_key}:{case_run.id}",
                    resource_pool_id=run.resource_pool_id,
                )
            )
    return bundle


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def spent_usd(session: Session, run_id: str) -> Decimal:
    """Authoritative model spend for one run (design 06 §8: ModelUsage is the
    source of truth; ``eval_run.budget_spent_usd`` is a reconciled snapshot)."""

    value = session.scalar(
        select(func.coalesce(func.sum(ModelUsage.cost_usd), 0)).where(
            ModelUsage.eval_run_id == run_id
        )
    )
    return Decimal(str(value or 0))


def budget_remaining_usd(run: EvalRun, spent: Decimal | None = None) -> Decimal | None:
    if run.model_budget_usd is None:
        return None
    return max(Decimal("0"), Decimal(str(run.model_budget_usd)) - (spent or Decimal("0")))


def get_evaluation(session: Session, run_id: str) -> EvalRun:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise EvaluationNotFound(run_id)
    return run


def list_evaluations(session: Session, *, limit: int = 50) -> tuple[list[EvalRun], int]:
    query = (
        select(EvalRun)
        .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
        .limit(max(1, min(limit, 200)))
    )
    items = list(session.scalars(query))
    total = int(
        session.scalar(select(func.count()).select_from(EvalRun)) or 0
    )
    return items, total


def evaluation_summary(run: EvalRun, *, spent_usd: Decimal | None = None) -> dict:
    spent = spent_usd if spent_usd is not None else run.budget_spent_usd
    unfinished = int(run.queued_count or 0) + int(run.running_count or 0)
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "total_cases": run.total_cases,
        "queued_count": run.queued_count,
        "running_count": run.running_count,
        "succeeded_count": run.succeeded_count,
        "failed_count": run.failed_count,
        "finished_count": max(0, int(run.total_cases or 0) - unfinished),
        "unfinished_count": unfinished,
        "shard_size": run.shard_size,
        "requested_concurrency": run.requested_concurrency,
        "effective_concurrency": run.effective_concurrency,
        "queue_reason": run.queue_reason,
        "model_config_id": run.model_config_id,
        "model_name": run.model_name,
        "model_budget_usd": str(run.model_budget_usd) if run.model_budget_usd is not None else None,
        "budget_spent_usd": str(spent) if spent is not None else None,
        "budget_remaining_usd": (
            str(budget_remaining_usd(run, spent if spent is not None else Decimal("0")))
            if run.model_budget_usd is not None
            else None
        ),
        "budget_version": run.budget_version,
        "pause_reason": run.pause_reason,
        "dataset_version_id": run.dataset_version_id,
        "case_timeout_seconds": run.case_timeout_seconds,
        "max_attempts": run.max_attempts,
        "runtime_key": run.runtime_key,
        "runtime_version": run.runtime_version,
        "execution_image_digest": run.execution_image_digest,
        "scoring_image_digest": run.scoring_image_digest,
        "asset_snapshot_id": run.asset_snapshot_id,
        "asset_resolved_commit": (run.asset_snapshot_json or {}).get("resolved_commit"),
        "asset_count": (run.asset_snapshot_json or {}).get("asset_count"),
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "idempotency_key": run.idempotency_key,
        "created_by": run.created_by,
    }


def evaluation_detail(session: Session, run: EvalRun) -> dict:
    cases = list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    # The detail view reconciles the spend snapshot against the gateway usage
    # rows so the pause dialog always shows the authoritative figure.
    live_spent = spent_usd(session, run.id) if run.model_budget_usd is not None else None
    shards = [
        {
            "shard_id": row.payload_json.get("shard_id"),
            "dispatch_key": row.dispatch_key,
            "attempt": row.payload_json.get("attempt", 1),
            "status": row.status,
            "manifest_digest": row.payload_json.get("manifest_digest"),
            "dispatched_at": row.payload_json.get("dispatched_at"),
            "github_status": row.payload_json.get("github_status"),
            "attempts": row.attempts,
            "last_error": row.payload_json.get("last_error"),
        }
        for row in session.scalars(
            select(Outbox)
            .where(Outbox.topic == SHARD_DISPATCH_TOPIC, Outbox.aggregate_id == run.id)
            .order_by(Outbox.id)
        )
    ]
    return {
        **evaluation_summary(run, spent_usd=live_spent),
        "asset_snapshot": run.asset_snapshot_json or {},
        # M5-04: the frozen preparation snapshot (execution policy, case
        # subset summary, digest) is part of the run record for traceability.
        "consistency": run.consistency_json or {},
        "cases": [
            {
                "id": case.id,
                "case_key": case.case_key,
                "ordinal": case.ordinal,
                "execution_status": case.execution_status,
                "result_status": case.result_status,
                "latest_attempt_no": case.latest_attempt_no,
                "duration_ms": case.duration_ms,
                "error_code": case.error_code,
                "error_summary": case.error_summary,
                "updated_at": _iso(case.updated_at),
            }
            for case in cases
        ],
        "shards": shards,
    }


def list_events(
    session: Session,
    run_id: str,
    *,
    after_id: int | None = None,
    limit: int = 50,
) -> tuple[list[dict], int | None]:
    get_evaluation(session, run_id)
    query = select(EvalEvent).where(EvalEvent.eval_run_id == run_id).order_by(EvalEvent.id)
    if after_id is not None:
        query = query.where(EvalEvent.id > after_id)
    rows = list(session.scalars(query.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1].id if has_more and rows else None
    items = [
        {
            "id": row.id,
            "event_type": row.event_type,
            "case_run_id": row.eval_case_run_id,
            "attempt_no": row.attempt_no,
            "sequence": row.sequence,
            "payload": row.payload_json,
            "occurred_at": _iso(row.occurred_at),
        }
        for row in rows
    ]
    return items, next_cursor


# ---------------------------------------------------------------------------
# Mutations: cancel + run progress rollup
# ---------------------------------------------------------------------------


def cancel_evaluation(session: Session, *, run_id: str, now: datetime | None = None) -> tuple[EvalRun, bool]:
    """Request cancellation of a non-terminal evaluation.

    State machine (M4-02 A2):

    - replay on ``cancelled`` is a no-op; any other terminal state means the
      client was too late (``EvaluationAlreadyFinal``);
    - ``queued``/``dispatching``/``pending`` attempts never started an agent and
      are cancelled immediately together with their cases and pending shard
      dispatches; ``running``/``scoring``/``uploading`` attempts stay alive and
      settle through their callbacks (their artifacts are already paid for);
    - no active attempt left → the run is finalized here, otherwise it stays
      ``cancelling`` and ``reconciler.reconcile`` finalizes it (also after
      ``eval_cancel_grace_seconds``).
    """

    now = _now(now)
    run = get_evaluation(session, run_id)
    if run.status == "cancelled":
        return run, False
    if run.status in TERMINAL_RUN_STATUSES:
        raise EvaluationAlreadyFinal(run.status)

    # Local import: ``lifecycle`` imports this module at import time (its retry
    # guard needs TERMINAL_RUN_STATUSES), so the back-edge must stay lazy.
    from . import lifecycle

    was_paused = run.status in PAUSED_RUN_STATUSES
    run.status = "cancelling"
    if run.cancel_requested_at is None:
        run.cancel_requested_at = now

    attempts = session.scalars(
        select(EvalCaseAttempt)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .where(
            EvalCaseRun.eval_run_id == run.id,
            EvalCaseAttempt.status.in_(NOT_STARTED_ATTEMPT_STATUSES),
        )
    ).all()
    for attempt in attempts:
        lifecycle.mark_status(attempt, "cancelled", now)
        attempt.ended_at = now
        attempt.error_code = RUN_CANCELLED
        case = session.get(EvalCaseRun, attempt.eval_case_run_id)
        if case is not None and case.execution_status not in TERMINAL_CASE_STATUSES:
            case.execution_status = "cancelled"
            case.error_code = RUN_CANCELLED

    outbox_rows = session.scalars(
        select(Outbox).where(
            Outbox.topic == SHARD_DISPATCH_TOPIC,
            Outbox.aggregate_id == run.id,
            Outbox.status.in_(("pending", "processing")),
        )
    ).all()
    for row in outbox_rows:
        # Keep client_payload/manifest for reconciliation; only annotate.
        row.payload_json = {**row.payload_json, "last_error": RUN_CANCELLED}
        row.status = "cancelled"

    if was_paused:
        # A paused run has no active attempt left to settle, so the queued
        # unfinished cases would otherwise keep the run in ``cancelling``
        # forever. Cases with a still-active attempt are left for that attempt.
        active_cases = {attempt.eval_case_run_id for attempt in active_attempts(session, run)}
        for case in session.scalars(
            select(EvalCaseRun).where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseRun.execution_status.in_(("queued", "running")),
            )
        ):
            if case.id in active_cases:
                continue
            case.execution_status = "cancelled"
            case.error_code = RUN_CANCELLED
        run.pause_reason = None

    append_run_event(session, run, "eval.cancelling", {"reason": "client_requested"})
    session.flush()
    if active_attempts(session, run):
        # In-flight attempts keep the run in ``cancelling``; still refresh the
        # counters so the console never shows the pre-cancel queue size.
        refresh_run_progress(session, run, now=now)
        session.commit()
        return run, True

    finalize_cancelled(session, run, now=now)
    return run, True


def active_attempts(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
            )
        )
    )


def _active_attempts(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    return active_attempts(session, run)


def finalize_cancelled(session: Session, run: EvalRun, *, now: datetime) -> bool:
    """Move a ``cancelling`` run to ``cancelled`` exactly once.

    Idempotent entry point shared by ``cancel_evaluation`` and the reconciler:
    counters are refreshed from the case rows (never accumulated), the terminal
    status is only written for a run that is still ``cancelling`` and the
    ``eval.cancelled`` event is deduped per run/event type.
    """

    if run.status != "cancelling":
        return False
    run.status = "cancelled"
    if run.finished_at is None:
        run.finished_at = now
    session.flush()
    refresh_run_progress(session, run, now=now)
    append_run_event(session, run, "eval.cancelled", {"reason": "client_requested"})
    session.commit()
    return True


def run_event_exists(session: Session, run: EvalRun, event_type: str) -> bool:
    """Whether a run-level event of ``event_type`` was already recorded."""

    return (
        session.scalar(
            select(EvalEvent.id)
            .where(
                EvalEvent.eval_run_id == run.id,
                EvalEvent.event_type == event_type,
                EvalEvent.eval_case_run_id.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def append_run_event(session: Session, run: EvalRun, event_type: str, payload: dict) -> None:
    """Run-level event; deduped per (run, event_type) since the natural unique
    key only covers case-level (case_run, attempt, sequence) rows."""

    if run_event_exists(session, run, event_type):
        return
    session.add(
        EvalEvent(
            tenant_id=run.tenant_id,
            eval_run_id=run.id,
            event_type=event_type,
            payload_json=payload,
        )
    )


def refresh_run_progress(session: Session, run: EvalRun, *, now: datetime | None = None) -> EvalRun:
    """Recompute counters/status from the case rows (design 05 §6.1: the
    authoritative result is recomputed in-transaction, not accumulated)."""

    now = _now(now)
    # Sessions are created with autoflush=False; pending case mutations must
    # hit the database before the authoritative group-by.
    session.flush()
    statuses = dict(
        session.execute(
            select(EvalCaseRun.execution_status, func.count()).where(
                EvalCaseRun.eval_run_id == run.id
            ).group_by(EvalCaseRun.execution_status)
        ).all()
    )
    total = sum(statuses.values())
    run.total_cases = total
    run.queued_count = statuses.get("queued", 0)
    run.running_count = statuses.get("running", 0)
    run.succeeded_count = statuses.get("succeeded", 0)
    run.failed_count = sum(
        statuses.get(status, 0)
        for status in ("failed", "timeout", "artifact_missing")
    )

    if run.status in TERMINAL_RUN_STATUSES:
        return run

    if run.status in PAUSED_RUN_STATUSES:
        # A budget pause is non-terminal: counters stay authoritative but the
        # run must never be reclassified while waiting for the operator.
        return run

    if run.status == "cancelling":
        # A cancelling run must never be reclassified as completed/partial/
        # failed; it becomes cancelled once no case is still queued/running
        # (running includes scoring/uploading attempts, which keep the case open).
        if run.queued_count == 0 and run.running_count == 0:
            run.status = "cancelled"
            if run.finished_at is None:
                run.finished_at = now
            append_run_event(session, run, "eval.cancelled", {"reason": "client_requested"})
        return run

    if run.queued_count == 0 and run.running_count == 0:
        if run.succeeded_count == total:
            run.status = "completed"
        elif run.succeeded_count > 0:
            run.status = "partial"
        elif total and statuses.get("cancelled", 0) == total:
            run.status = "cancelled"
        else:
            run.status = "failed"
        if run.started_at is None:
            run.started_at = now
        run.finished_at = now
        return run

    if run.status == "queued" and run.running_count > 0:
        run.status = "running"
        if run.started_at is None:
            run.started_at = now
    return run


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds")
