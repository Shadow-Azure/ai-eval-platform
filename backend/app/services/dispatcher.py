"""Outbox dispatcher: claim → repository_dispatch → reconciliation (M2-05).

Loop rules (design 06 §5):
- Only pending rows past ``available_at`` are claimed; the claim is an
  optimistic ``pending → processing`` flip inside the caller's transaction.
- A GitHub failure parks the row back to ``pending`` with attempts+1 and an
  exponential backoff; exhausting ``dispatch_max_attempts`` moves the shard,
  its cases and the run into diagnosable failure states.
- Rows stuck in ``processing`` past the lease are recovered (worker restart)
  and re-dispatched; attempt rows are only ever created by evaluation
  creation/retry, so recovery cannot duplicate attempts.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalEvent, EvalRun, Outbox
from . import capacity, evaluations, lifecycle
from .evaluations import SHARD_DISPATCH_TOPIC, refresh_run_progress
from .github_dispatch import GitHubDispatchError, post_repository_dispatch


@dataclass
class DispatchOutcome:
    dispatched: int = 0
    retried: int = 0
    failed: int = 0
    recovered: int = 0
    skipped: int = 0
    queued: int = 0


def _capacity_decision(
    session: Session, run: EvalRun, *, settings: Settings, now: datetime
) -> tuple[int, str] | None:
    """Return ``(cap, queue_reason)`` when a shard must stay queued.

    M4-01: the platform, not the GitHub queue, decides whether a shard may be
    released. A shard consumes at most ``run.shard_size`` parallel jobs, so the
    number of shards that may be in flight is ``ceil(cap / shard_size)``.
    ``None`` means "release the row".
    """

    cap = capacity.effective_concurrency(session, run, settings=settings, now=now)
    run.effective_concurrency = cap
    if cap <= 0:
        return cap, capacity.QUEUE_REASON_CAPACITY_WAITING

    released_shards = int(
        session.scalar(
            select(func.count(func.distinct(EvalCaseAttempt.shard_id)))
            .select_from(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.status.in_(capacity.RELEASED_ATTEMPT_STATUSES),
            )
        )
        or 0
    )
    max_in_flight = max(1, math.ceil(cap / max(1, int(run.shard_size or 1))))
    if released_shards >= max_in_flight:
        return cap, capacity.QUEUE_REASON_CONCURRENCY_CAP
    return None


def dispatch_pending_outbox(
    session: Session,
    *,
    settings: Settings | None = None,
    transport=None,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> DispatchOutcome:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    outcome = DispatchOutcome()

    outcome.recovered = recover_stuck_leases(
        session, lease_seconds=settings.dispatch_lease_seconds, now=now
    )

    rows = list(
        session.scalars(
            select(Outbox)
            .where(
                Outbox.topic == SHARD_DISPATCH_TOPIC,
                Outbox.status == "pending",
                Outbox.available_at <= now,
            )
            .order_by(Outbox.id)
            .limit(batch_size or settings.dispatch_batch_size)
        )
    )
    dirty = False
    for row in rows:
        run = session.get(EvalRun, row.aggregate_id)
        if (
            settings.dispatch_capacity_gate
            and run is not None
            and run.resource_pool_id
            and run.status in ("queued", "running")
        ):
            decision = _capacity_decision(session, run, settings=settings, now=now)
            if decision is not None:
                _cap, reason = decision
                if run.queue_reason != reason:
                    run.queue_reason = reason
                    dirty = True
                row.available_at = now + timedelta(
                    seconds=settings.dispatch_capacity_retry_seconds
                )
                dirty = True
                outcome.queued += 1
                continue
            if run.queue_reason is not None:
                run.queue_reason = None
                dirty = True
        _dispatch_row(session, row, settings=settings, transport=transport, now=now, outcome=outcome)
    if dirty:
        session.commit()
    return outcome


def recover_stuck_leases(session: Session, *, lease_seconds: float, now: datetime) -> int:
    stale_before = now - timedelta(seconds=lease_seconds)
    stuck = list(
        session.scalars(
            select(Outbox).where(
                Outbox.topic == SHARD_DISPATCH_TOPIC,
                Outbox.status == "processing",
                Outbox.updated_at < stale_before,
            )
        )
    )
    for row in stuck:
        row.status = "pending"
        row.payload_json = {**row.payload_json, "last_error": "lease_recovered"}
        # The previous dispatcher may have flipped attempts to ``dispatching``
        # before dying. Those rows would otherwise count as "released" forever
        # and the capacity gate would never release the shard again.
        _reset_dispatching_attempts(session, row)
    if stuck:
        session.commit()
    return len(stuck)


def _reset_dispatching_attempts(session: Session, row: Outbox) -> int:
    shard_id = row.payload_json.get("shard_id")
    attempt_no = int(row.payload_json.get("attempt", 1))
    if not shard_id:
        return 0
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == row.aggregate_id,
                EvalCaseAttempt.shard_id == shard_id,
                EvalCaseAttempt.attempt_no == attempt_no,
                EvalCaseAttempt.status == "dispatching",
            )
        )
    )
    for attempt in attempts:
        attempt.status = "pending"
        attempt.dispatch_status = "pending"
    return len(attempts)


def _refresh_dispatch_window(
    row: Outbox, run: EvalRun, *, settings: Settings, now: datetime
) -> None:
    """Stamp expiry/deadline relative to the actual release time (M4-01).

    The manifest is built at creation, but the capacity gate releases shards
    later, one shard at a time. A creation-time deadline would expire before
    the later shards are ever dispatched (500 cases / 8 shards easily run for
    hours). Regenerate the deadline from ``now`` with enough waves for the
    shard at the current effective concurrency; the case list itself is frozen.
    """

    payload = row.payload_json
    manifest_text = payload.get("manifest_text")
    client_payload = payload.get("client_payload")
    if not manifest_text or not client_payload:
        return
    manifest = json.loads(manifest_text)
    cases = manifest.get("cases") or []
    cap = run.effective_concurrency or run.requested_concurrency or 1
    waves = max(1, math.ceil(len(cases) / max(1, int(cap))))
    deadline = now + timedelta(seconds=run.case_timeout_seconds * waves + 300)
    manifest["deadline"] = deadline.isoformat(timespec="microseconds").replace("+00:00", "Z")
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    expires_at = now + timedelta(seconds=settings.eval_shard_ttl_seconds)
    row.payload_json = {
        **payload,
        "manifest_text": text,
        "manifest_digest": digest,
        "client_payload": {
            **client_payload,
            "manifest_digest": digest,
            "expires_at": expires_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        },
    }


def _shard_attempts(session: Session, run: EvalRun, shard_id: str, attempt_no: int) -> list[EvalCaseAttempt]:
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.shard_id == shard_id,
                EvalCaseAttempt.attempt_no == attempt_no,
            )
        )
    )


def _dispatch_row(
    session: Session,
    row: Outbox,
    *,
    settings: Settings,
    transport,
    now: datetime,
    outcome: DispatchOutcome,
) -> None:
    run = session.get(EvalRun, row.aggregate_id)
    if run is None:
        _fail_row_permanently(session, row, reason="run_missing", now=now)
        outcome.failed += 1
        return
    if run.status in evaluations.PAUSED_RUN_STATUSES:
        # A budget pause is expected to last until an operator resumes the run;
        # the row must stay dispatchable instead of becoming a permanent failure.
        row.payload_json = {**row.payload_json, "last_error": "run_budget_paused"}
        row.available_at = now + timedelta(seconds=settings.dispatch_capacity_retry_seconds)
        session.commit()
        outcome.queued += 1
        return
    if run.status not in ("queued", "running"):
        row.payload_json = {**row.payload_json, "last_error": f"run_not_dispatchable:{run.status}"}
        row.status = "failed"
        session.commit()
        outcome.skipped += 1
        return

    shard_id = row.payload_json.get("shard_id")
    attempt_no = int(row.payload_json.get("attempt", 1))
    attempts = _shard_attempts(session, run, shard_id, attempt_no)
    if attempts and all(a.status in evaluations.TERMINAL_ATTEMPT_STATUSES for a in attempts):
        row.payload_json = {**row.payload_json, "last_error": "attempts_terminal"}
        row.status = "failed"
        session.commit()
        outcome.skipped += 1
        return

    if row.attempts >= settings.dispatch_max_attempts:
        _exhaust_dispatch(session, row, run=run, attempts=attempts, now=now)
        outcome.failed += 1
        return

    # Optimistic claim: only one dispatcher wins per row.
    claimed = (
        session.query(Outbox)
        .filter(Outbox.id == row.id, Outbox.status == "pending")
        .update({"status": "processing", "attempts": Outbox.attempts + 1})
    )
    if not claimed:
        outcome.skipped += 1
        return
    session.refresh(row)
    for attempt in attempts:
        attempt.dispatch_status = "dispatching"
    session.commit()

    _refresh_dispatch_window(row, run, settings=settings, now=now)
    session.commit()

    try:
        result = post_repository_dispatch(
            event_type=settings.github_dispatch_event_type,
            client_payload=row.payload_json["client_payload"],
            settings=settings,
            transport=transport,
            now=now,
        )
    except GitHubDispatchError as exc:
        _handle_dispatch_error(session, row, run=run, attempts=attempts, exc=exc, settings=settings, now=now)
        outcome.retried += 1
        return

    row.status = "sent"
    row.payload_json = {
        **row.payload_json,
        "dispatched_at": result["dispatched_at"],
        "github_status": result["status"],
        "last_error": None,
    }
    for attempt in attempts:
        attempt.dispatch_status = "sent"
        # The queue timeout budget starts when the shard actually reaches
        # GitHub, not at creation (capacity gate can hold shards for hours).
        lifecycle.mark_status(attempt, "queued", now)
    session.add(
        _shard_event(
            session,
            run,
            "eval.dispatched",
            {
                "dedupe_key": row.dispatch_key,
                "shard_id": shard_id,
                "dispatch_key": row.dispatch_key,
                "attempt": attempt_no,
                "github_status": result["status"],
            },
        )
    )
    session.commit()
    outcome.dispatched += 1


def _handle_dispatch_error(
    session: Session,
    row: Outbox,
    *,
    run: EvalRun,
    attempts: list[EvalCaseAttempt],
    exc: GitHubDispatchError,
    settings: Settings,
    now: datetime,
) -> None:
    last_error = f"github_dispatch_failed:{exc.status}:{exc.message}"[:500]
    if row.attempts >= settings.dispatch_max_attempts:
        _exhaust_dispatch(session, row, run=run, attempts=attempts, now=now, last_error=last_error)
        return
    backoff = min(
        settings.dispatch_backoff_seconds * (2 ** (row.attempts - 1)),
        settings.dispatch_backoff_cap_seconds,
    )
    row.status = "pending"
    row.available_at = now + timedelta(seconds=backoff)
    row.payload_json = {**row.payload_json, "last_error": last_error}
    for attempt in attempts:
        attempt.dispatch_status = "pending"
    session.commit()


def _exhaust_dispatch(
    session: Session,
    row: Outbox,
    *,
    run: EvalRun,
    attempts: list[EvalCaseAttempt],
    now: datetime,
    last_error: str | None = None,
) -> None:
    """Shard dispatch permanently failed: diagnosable failure, never queued."""

    last_error = last_error or row.payload_json.get("last_error") or "dispatch_max_attempts_exceeded"
    row.status = "failed"
    row.payload_json = {**row.payload_json, "last_error": last_error}
    shard_id = row.payload_json.get("shard_id")
    attempt_no = int(row.payload_json.get("attempt", 1))

    for attempt in attempts:
        attempt.status = "dispatch_failed"
        attempt.dispatch_status = "failed"
        attempt.ended_at = now
        attempt.error_code = "dispatch_failed"
        attempt.error_summary = last_error[:1000]
        case = session.get(EvalCaseRun, attempt.eval_case_run_id)
        if case is not None and case.execution_status not in evaluations.TERMINAL_CASE_STATUSES:
            case.execution_status = "failed"
            case.error_code = "dispatch_failed"
            case.error_summary = f"分片派发失败：{last_error}"[:500]
    session.add(
        _shard_event(
            session,
            run,
            "eval.dispatch_failed",
            {
                "dedupe_key": row.dispatch_key,
                "shard_id": shard_id,
                "dispatch_key": row.dispatch_key,
                "attempt": attempt_no,
                "error": last_error,
            },
        )
    )
    refresh_run_progress(session, run, now=now)
    session.commit()


def _fail_row_permanently(session: Session, row: Outbox, *, reason: str, now: datetime) -> None:
    row.status = "failed"
    row.payload_json = {**row.payload_json, "last_error": reason}
    session.commit()


def _shard_event(session: Session, run: EvalRun, event_type: str, payload: dict):
    """Run-level shard event deduped on payload['dedupe_key'] (one per shard
    dispatch_key; eval_event's natural unique key only covers case rows)."""

    dedupe_key = payload.get("dedupe_key")
    existing = session.scalars(
        select(EvalEvent).where(
            EvalEvent.eval_run_id == run.id, EvalEvent.event_type == event_type
        )
    ).all()
    for event in existing:
        if event.eval_case_run_id is None and event.payload_json.get("dedupe_key") == dedupe_key:
            return event
    return EvalEvent(
        tenant_id=run.tenant_id,
        eval_run_id=run.id,
        event_type=event_type,
        payload_json=payload,
    )
