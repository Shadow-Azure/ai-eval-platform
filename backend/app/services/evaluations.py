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
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    Outbox,
)
from ..runner_contracts import ContractInvalid, validate_payload

SHARD_DISPATCH_TOPIC = "eval.shard.dispatch"
MANIFEST_PATH_TEMPLATE = "/runner/v1/shards/{shard_id}/manifest"

TERMINAL_RUN_STATUSES = ("completed", "partial", "failed", "cancelled")
TERMINAL_CASE_STATUSES = ("succeeded", "failed", "timeout", "cancelled")
TERMINAL_ATTEMPT_STATUSES = (
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "artifact_missing",
    "dispatch_failed",
)


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
    shard_size: int | None = None,
    idempotency_key: str | None = None,
    created_by: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, bool]:
    """Create the evaluation + shard dispatch outbox in one transaction.

    Replays with the same (tenant, created_by, idempotency_key) return the
    original run untouched.
    """

    settings = settings or Settings()
    now = _now(now)
    cleaned = _clean_case_keys(case_keys, settings)
    shard_size = shard_size if shard_size is not None else 64
    if not isinstance(shard_size, int) or not 1 <= shard_size <= 256:
        raise EvaluationInvalid("shard_size 必须在 1..256 之间", field="shard_size")
    idempotency_key = idempotency_key or str(uuid.uuid4())

    tenant_id = str(DEFAULT_TENANT_ID)
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

    run = EvalRun(
        tenant_id=tenant_id,
        project_id=project_id,
        name=(name or "合成评测")[:200],
        status="queued",
        requested_concurrency=1,
        shard_size=shard_size,
        total_cases=len(cleaned),
        queued_count=len(cleaned),
        idempotency_key=idempotency_key[:128],
        created_by=created_by[:128],
        max_attempts=2,
    )
    session.add(run)
    session.flush()

    shard_payloads = build_shard_dispatches(
        session,
        run=run,
        case_keys=cleaned,
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
    case_keys: list[str],
    shard_size: int,
    settings: Settings,
    now: datetime,
) -> ShardDispatchBundle:
    """Build one outbox row per shard + one pending attempt per case.

    All rows stay uncommitted; the caller owns the transaction.
    """

    bundle = ShardDispatchBundle()
    tenant_id = str(DEFAULT_TENANT_ID)
    deadline = now + timedelta(minutes=settings.eval_shard_deadline_minutes)
    expires_at = now + timedelta(seconds=settings.eval_shard_ttl_seconds)

    for shard_index, start in enumerate(range(0, len(case_keys), shard_size)):
        chunk = case_keys[start : start + shard_size]
        shard_id = f"shard_{shard_index:03d}"
        dispatch_key = f"{run.id}:{shard_id}:1"

        case_runs: list[EvalCaseRun] = []
        for offset, case_key in enumerate(chunk):
            case_run = EvalCaseRun(
                tenant_id=tenant_id,
                eval_run_id=run.id,
                dataset_case_id=case_key[:128],
                case_key=case_key[:200],
                ordinal=start + offset + 1,
            )
            session.add(case_run)
            case_runs.append(case_run)
        session.flush()

        manifest = {
            "schema_version": "1.0",
            "eval_run_id": run.id,
            "shard_id": shard_id,
            "attempt": 1,
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
                for case_run in case_runs
            ],
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        manifest_digest = "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
        query = urlencode(
            {
                "eval_run_id": run.id,
                "attempt": 1,
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
            "attempt": 1,
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
                    "attempt": 1,
                    "shard_id": shard_id,
                },
                status="pending",
            )
        )
        for case_run in case_runs:
            case_run.latest_attempt_no = 1
            bundle.attempts.append(
                EvalCaseAttempt(
                    tenant_id=tenant_id,
                    eval_case_run_id=case_run.id,
                    attempt_no=1,
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


def evaluation_summary(run: EvalRun) -> dict:
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "total_cases": run.total_cases,
        "queued_count": run.queued_count,
        "running_count": run.running_count,
        "succeeded_count": run.succeeded_count,
        "failed_count": run.failed_count,
        "shard_size": run.shard_size,
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
        **evaluation_summary(run),
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
    """Cancel a non-terminal evaluation; replaying on cancelled is a no-op."""

    now = _now(now)
    run = get_evaluation(session, run_id)
    if run.status == "cancelled":
        return run, False
    if run.status in TERMINAL_RUN_STATUSES:
        raise EvaluationAlreadyFinal(run.status)

    run.status = "cancelling"
    attempts = session.scalars(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id.in_(
                select(EvalCaseRun.id).where(EvalCaseRun.eval_run_id == run.id)
            ),
            EvalCaseAttempt.status.not_in(TERMINAL_ATTEMPT_STATUSES),
        )
    ).all()
    for attempt in attempts:
        attempt.status = "cancelled"
        attempt.ended_at = now
        attempt.error_code = "run_cancelled"

    cancelled_case_ids = session.scalars(
        select(EvalCaseAttempt.eval_case_run_id).where(
            EvalCaseAttempt.status == "cancelled",
            EvalCaseAttempt.eval_case_run_id.in_(
                select(EvalCaseRun.id).where(EvalCaseRun.eval_run_id == run.id)
            ),
        )
    ).all()
    if cancelled_case_ids:
        session.query(EvalCaseRun).filter(EvalCaseRun.id.in_(cancelled_case_ids)).update(
            {"execution_status": "cancelled"}, synchronize_session=False
        )

    outbox_rows = session.scalars(
        select(Outbox).where(
            Outbox.topic == SHARD_DISPATCH_TOPIC,
            Outbox.aggregate_id == run.id,
            Outbox.status.in_(("pending", "processing")),
        )
    ).all()
    for row in outbox_rows:
        # Keep client_payload/manifest for reconciliation; only annotate.
        row.payload_json = {**row.payload_json, "last_error": "run_cancelled"}
        row.status = "cancelled"

    run.queued_count = 0
    run.running_count = 0
    run.status = "cancelled"
    run.finished_at = now
    append_run_event(session, run, "eval.cancelled", {"reason": "client_requested"})
    session.commit()
    return run, True


def append_run_event(session: Session, run: EvalRun, event_type: str, payload: dict) -> None:
    """Run-level event; deduped per (run, event_type) since the natural unique
    key only covers case-level (case_run, attempt, sequence) rows."""

    exists = session.scalar(
        select(EvalEvent.id)
        .where(
            EvalEvent.eval_run_id == run.id,
            EvalEvent.event_type == event_type,
            EvalEvent.eval_case_run_id.is_(None),
        )
        .limit(1)
    )
    if exists is not None:
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
