"""Cancellation reconciler and lost-callback completion backfill (M4-02 A2).

The worker is the only actor that can finish what no callback will:

1. the lifecycle reaper (A1) owns the five execution timeouts; its counts are
   merged into this pass so one worker loop covers both;
2. a ``cancelling`` run needs the GitHub workflow run cancelled and, once its
   in-flight attempts settle (or the grace period expires), the final
   ``cancelled`` transition;
3. an attempt stuck in ``uploading`` while its score object already exists means
   the completion callback was lost — the artifacts are re-verified and the
   completion is backfilled through the same core the callback uses.

Idempotency rules: every side effect is keyed on a persisted row or a deduped
run/attempt event, so running the reconciler twice (or from two workers) never
cancels twice, never duplicates an event and never creates an attempt. GitHub
is only called for ``cancelling`` runs, and only until the cancel request is
confirmed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import EvalCaseAttempt, EvalCaseRun, EvalRun
from . import case_attempts, evaluations, lifecycle, reaper
from .github_dispatch import GitHubDispatchError, cancel_workflow_run, find_workflow_run
from .object_store import object_store_from_settings

CANCEL_REQUESTED_EVENT = "eval.cancel_requested"
CANCEL_FAILED_EVENT = "eval.cancel_failed"
CANCEL_LOOKUP_FAILED_EVENT = "eval.cancel_lookup_failed"
RECONCILED_COMPLETION_EVENT = "case.reconciled_completed"

COUNT_KEYS = (*lifecycle.TIMEOUT_CODES, "cancelled", "reconciled")


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC for arithmetic."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _active_attempts(session: Session, run: EvalRun) -> list[tuple[EvalCaseAttempt, EvalCaseRun]]:
    rows = session.execute(
        select(EvalCaseAttempt, EvalCaseRun)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .where(
            EvalCaseRun.eval_run_id == run.id,
            EvalCaseAttempt.status.in_(evaluations.ACTIVE_ATTEMPT_STATUSES),
        )
        .order_by(EvalCaseAttempt.created_at, EvalCaseAttempt.id)
    ).all()
    return [(attempt, case) for attempt, case in rows]


def reconcile(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    transport=None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """Run one reconcile pass; returns the number of transitions per class."""

    now = now or datetime.now(UTC)
    limit = max(
        1,
        int(batch_size if batch_size is not None else settings.eval_lifecycle_batch_size),
    )
    counts = dict(
        reaper.sweep_lifecycle(session, settings=settings, now=now, batch_size=batch_size)
    )
    counts["cancelled"] = 0
    counts["reconciled"] = 0

    cancelling = list(
        session.scalars(
            select(EvalRun)
            .where(EvalRun.status == "cancelling")
            .order_by(EvalRun.created_at, EvalRun.id)
            .limit(limit)
        )
    )
    for run in cancelling:
        if _reconcile_cancel(
            session,
            run,
            settings=settings,
            now=now,
            transport=transport,
        ):
            counts["cancelled"] += 1

    for attempt, case, run in _uploading_attempts(session, limit=limit):
        if _backfill_completion(session, case, attempt, run, settings=settings, now=now):
            counts["reconciled"] += 1

    return {key: int(counts.get(key, 0)) for key in COUNT_KEYS}


def _reconcile_cancel(
    session: Session,
    run: EvalRun,
    *,
    settings: Settings,
    now: datetime,
    transport,
) -> bool:
    """Drive one ``cancelling`` run forward; returns True when it finalized."""

    if run.github_run_id is None:
        try:
            github_run_id = find_workflow_run(run.id, settings=settings, transport=transport)
        except GitHubDispatchError as exc:
            evaluations.append_run_event(
                session,
                run,
                CANCEL_LOOKUP_FAILED_EVENT,
                {"error": exc.message[:500], "status": exc.status},
            )
            session.commit()
        else:
            if github_run_id:
                run.github_run_id = github_run_id
                session.commit()

    if run.github_run_id is not None and not evaluations.run_event_exists(
        session, run, CANCEL_REQUESTED_EVENT
    ):
        try:
            cancel_workflow_run(
                run.github_run_id, settings=settings, transport=transport
            )
        except GitHubDispatchError as exc:
            evaluations.append_run_event(
                session,
                run,
                CANCEL_FAILED_EVENT,
                {
                    "github_run_id": run.github_run_id,
                    "error": exc.message[:500],
                    "status": exc.status,
                },
            )
            session.commit()
        else:
            # Event-based confirmation: once this row exists no second GitHub
            # cancel is sent, even if the worker restarts mid-cycle.
            evaluations.append_run_event(
                session,
                run,
                CANCEL_REQUESTED_EVENT,
                {"github_run_id": run.github_run_id},
            )
            session.commit()

    active = _active_attempts(session, run)
    requested_at = _aware(run.cancel_requested_at) or _aware(run.created_at) or now
    grace = int(settings.eval_cancel_grace_seconds)
    if active and requested_at + timedelta(seconds=grace) < now:
        # Grace expired: running/scoring/uploading attempts had their chance to
        # settle. Force them cancelled so the run can never hang in cancelling.
        for attempt, case in active:
            lifecycle.mark_status(attempt, "cancelled", now)
            attempt.ended_at = now
            attempt.error_code = evaluations.CANCEL_GRACE_EXCEEDED
            lifecycle.release_runner_binding(session, attempt)
            if case.execution_status not in evaluations.TERMINAL_CASE_STATUSES:
                case.execution_status = "cancelled"
                case.error_code = evaluations.CANCEL_GRACE_EXCEEDED
        session.flush()
        active = []

    if active:
        return False
    return evaluations.finalize_cancelled(session, run, now=now)


def _uploading_attempts(
    session: Session, *, limit: int
) -> list[tuple[EvalCaseAttempt, EvalCaseRun, EvalRun]]:
    rows = session.execute(
        select(EvalCaseAttempt, EvalCaseRun, EvalRun)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .join(EvalRun, EvalRun.id == EvalCaseRun.eval_run_id)
        .where(
            EvalCaseAttempt.status == "uploading",
            # Cancellation runs settle through _reconcile_cancel first; a
            # backfilled success must not resurrect a cancelled case.
            EvalRun.status.not_in(("cancelling", "cancelled")),
        )
        .order_by(EvalCaseAttempt.phase_changed_at, EvalCaseAttempt.id)
        .limit(limit)
    ).all()
    return [(attempt, case, run) for attempt, case, run in rows]


def _object_of_kind(objects: list, artifact_kind: str) -> dict | None:
    for item in objects:
        if not isinstance(item, dict):
            continue
        if item.get("artifact_kind") != artifact_kind:
            continue
        if not item.get("object_key"):
            continue
        return item
    return None


def _backfill_completion(
    session: Session,
    case: EvalCaseRun,
    attempt: EvalCaseAttempt,
    run: EvalRun,
    *,
    settings: Settings,
    now: datetime,
) -> bool:
    """Finalize an uploading attempt whose completion callback was lost."""

    if case_attempts.attempt_event_exists(
        session,
        case_run_id=case.id,
        attempt_no=attempt.attempt_no,
        event_type=RECONCILED_COMPLETION_EVENT,
    ):
        return False

    objects = list((attempt.artifact_manifest_json or {}).get("objects") or [])
    score = _object_of_kind(objects, "score")
    if score is None:
        return False
    trace = _object_of_kind(objects, "trace")

    store = object_store_from_settings(settings)
    if store is None:
        # Nothing to verify against: leave the attempt to the upload reaper.
        return False
    try:
        for item in [score] + ([trace] if trace is not None else []):
            store.verify_object(item["object_key"], expected_size=item.get("size"))
    except Exception:  # noqa: BLE001 - retry on the next pass
        # Missing/mismatched size is not a completion; the upload timeout keeps
        # the attempt diagnosable if the object never lands.
        return False

    # Reading the report is best-effort: a readable JSON gives the exact
    # result status, an unreadable one still finalizes as not_scored. The
    # SWE-bench report is keyed by case_key; a flat ``{"resolved": bool}`` form
    # is accepted too so the backfill is not tied to one exporter shape.
    result_status = "not_scored"
    try:
        report = json.loads(store.get_bytes(score["object_key"]))
    except Exception:  # noqa: BLE001 - completion must not wait on a GET
        report = None
    entry = report.get(case.case_key) if isinstance(report, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("resolved"), bool):
        result_status = "resolved" if entry["resolved"] else "unresolved"
    elif isinstance(report, dict) and isinstance(report.get("resolved"), bool):
        result_status = "resolved" if report["resolved"] else "unresolved"

    duration_ms = None
    started = _aware(attempt.started_at)
    if started is not None:
        duration_ms = max(0, int((now - started).total_seconds() * 1000))

    case_attempts.finalize_completion(
        session,
        case_run=case,
        attempt=attempt,
        execution_status="succeeded",
        result_status=result_status,
        score_manifest=score,
        trace_manifest=trace,
        summary={"duration_ms": duration_ms},
        now=now,
        settings=settings,
        event_type=RECONCILED_COMPLETION_EVENT,
        event_payload={
            "source": "reconciler",
            "attempt_id": attempt.id,
            "score_object_key": score["object_key"],
        },
    )
    return True
