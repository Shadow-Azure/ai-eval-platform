"""Self-evolution bad-case reporting (M9, issue #263).

Two halves share this module:

- ``enqueue_self_evolution_reports`` runs inside the terminal-status
  transaction of ``refresh_run_progress``: it freezes one
  ``BadCaseIngest``-shaped payload per bad case into ``payload_json`` so
  retries never recompute the mapping (a re-classified case or a rotated
  signing key cannot silently change what was already accepted upstream).
- ``process_pending_self_evolution_reports`` is the worker pass that POSTs
  the frozen payloads to the harness-evolution workspace with a retry
  ladder; credential/quota/contract errors are terminal, transient ones
  back off.

The short-lived HMAC helpers build the per-report ``sessionSource`` URL
template and are reused by the envelope endpoint for verification.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    Dataset,
    DatasetVersion,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    ModelUsage,
    SelfEvolutionReport,
    TestCaseResult,
    TraceSession,
)
from ..secrets import SecretBoxError, get_secret_box
from .evaluations import append_run_event, iso_utc

logger = logging.getLogger(__name__)

SOURCE = "ai-eval-platform"
BAD_CASE_TYPE = "discovery"
OBJECT_TYPE = "agent"
TITLE_MAX_LENGTH = 255
REASON_MAX_LENGTH = 4000
TEST_NAME_LIMIT = 20
SESSION_ID_PATTERN_LIMIT = 36  # HE sessionId contract: 1..36 chars

# Short-lived session-envelope grant embedded in sessionUrlTemplate. The HMAC
# binds the endpoint path PREFIX (not the concrete session id): harness-
# evolution substitutes every traversal session id — the reported root plus
# each child — into the same template, so a per-session signature would break
# the BFS pull after the first hop.
SESSION_ENVELOPE_PATH_PREFIX = "/api/v1/self-evolution/sessions/"
SESSION_TTL_SECONDS = 7 * 24 * 3600
SIGNATURE_ALGORITHM = "GET\n{path_prefix}\n{exp}"

# Worker retry policy: 1min, 5min, 15min, then 1h forever; eight failed
# attempts (the initial one included) give up permanently.
RETRY_LADDER_SECONDS = (60, 300, 900, 3600)
MAX_REPORT_ATTEMPTS = 8
REPORT_TIMEOUT_SECONDS = 15.0
FAILED_TEST_OUTCOMES = ("failed", "error")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ---------------------------------------------------------------------------
# Session-envelope signing (shared with api/self_evolution.py)
# ---------------------------------------------------------------------------


def session_signature(key: str, exp: int) -> str:
    """HMAC-SHA256 over ``GET\\n{path_prefix}\\n{exp}`` for one grant window."""

    message = SIGNATURE_ALGORITHM.format(path_prefix=SESSION_ENVELOPE_PATH_PREFIX, exp=exp)
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def session_signature_matches(key: str, exp: int, signature: str) -> bool:
    """Constant-time comparison of a presented signature."""

    return hmac.compare_digest(session_signature(key, exp), signature or "")


def build_session_url_template(
    public_base_url: str, session_id: str, *, exp: int, key: str
) -> str:
    """``sessionUrlTemplate`` with the ``{sessionId}`` placeholder exactly once.

    The signature is bound to the endpoint prefix and the expiry, so the
    reported root session and every child session the pull side substitutes
    into ``{sessionId}`` verify against the same HMAC.
    """

    signature = session_signature(key, exp)
    return (
        f"{public_base_url.rstrip('/')}"
        f"{SESSION_ENVELOPE_PATH_PREFIX}{{sessionId}}"
        f"?exp={exp}&sig={signature}"
    )


def _maybe_session_source(
    run: EvalRun, session_id: str, *, now: datetime, settings: Settings
) -> dict | None:
    if run.runtime_key != "claude-code":
        return None
    key = settings.self_evolution_session_signing_key
    base_url = settings.self_evolution_public_base_url
    if not key or not base_url:
        logger.warning(
            "self-evolution sessionSource omitted: SELF_EVO_SESSION_SIGNING_KEY or "
            "SELF_EVO_PUBLIC_BASE_URL is not configured (run %s)",
            run.id,
        )
        return None
    exp = int((now + timedelta(seconds=SESSION_TTL_SECONDS)).timestamp())
    return {
        "runtime": "claude-code",
        "sessionUrlTemplate": build_session_url_template(base_url, session_id, exp=exp, key=key),
    }


# ---------------------------------------------------------------------------
# Payload assembly (frozen once at enqueue time)
# ---------------------------------------------------------------------------


def bad_case_identifier(tenant_id: str, eval_run_id: str, case_key: str) -> str:
    digest = hashlib.sha1(f"{tenant_id}:{eval_run_id}:{case_key}".encode()).hexdigest()
    return f"swe-{digest[:20]}"


def _main_session_id(session: Session, attempt_id: str) -> str | None:
    """Native id of the attempt's root (main) Claude session, if parsed."""

    row = session.scalar(
        select(TraceSession)
        .where(
            TraceSession.eval_case_attempt_id == attempt_id,
            TraceSession.parent_session_id.is_(None),
            TraceSession.status != "unlinked",
        )
        .order_by(TraceSession.ordinal, TraceSession.id)
        .limit(1)
    )
    if row is None or not row.native_session_id:
        return None
    # The HE sessionId contract caps at 36 chars. A longer native id cannot be
    # reported faithfully (truncation would 404 the envelope), so treat it as
    # unusable and fall back to the attempt id without a sessionSource.
    if len(row.native_session_id) > SESSION_ID_PATTERN_LIMIT:
        logger.warning(
            "native session id longer than %s chars; envelope link omitted",
            SESSION_ID_PATTERN_LIMIT,
        )
        return None
    return row.native_session_id


def _failed_tests(session: Session, attempt_id: str, group: str) -> list[str]:
    rows = session.execute(
        select(TestCaseResult.test_name)
        .where(
            TestCaseResult.eval_case_attempt_id == attempt_id,
            TestCaseResult.test_group == group,
            TestCaseResult.outcome.in_(FAILED_TEST_OUTCOMES),
        )
        .order_by(TestCaseResult.test_name)
    ).scalars()
    return list(rows)[:TEST_NAME_LIMIT]


def _usage_totals(session: Session, case_run_id: str) -> tuple[int | None, float | None]:
    """Aggregate model usage for one case; omitted entirely when absent."""

    tokens, cost = session.execute(
        select(
            func.sum(func.coalesce(ModelUsage.total_tokens, 0)),
            func.sum(ModelUsage.cost_cny),
        ).where(ModelUsage.case_run_id == case_run_id)
    ).one()
    tokens_total = int(tokens or 0)
    cost_total = float(cost) if cost is not None else None
    return (tokens_total or None), cost_total


def build_bad_case_payload(
    session: Session,
    *,
    run: EvalRun,
    case: EvalCaseRun,
    final_attempt: EvalCaseAttempt,
    now: datetime,
    settings: Settings,
) -> dict:
    """Assemble the frozen ``BadCaseIngest`` body for one bad case."""

    session_id = _main_session_id(session, final_attempt.id)
    has_native_session = session_id is not None
    if session_id is None:
        # Attempt-id fallback: the envelope endpoint resolves sessions by
        # native id, so this id would 404 upstream — sessionSource stays off.
        session_id = final_attempt.id
    object_version = run.runtime_version or (run.asset_snapshot_json or {}).get("resolved_commit")
    title = f"[SWE-bench] {case.case_key} · {case.result_status}"[:TITLE_MAX_LENGTH]

    fail_to_pass = _failed_tests(session, final_attempt.id, "F2P")
    pass_to_pass = _failed_tests(session, final_attempt.id, "P2P")

    reason_parts = [f"result_status={case.result_status}"]
    if case.result_status == "not_scored":
        reason_parts.append("执行失败（未进入评分）")
    if case.error_summary:
        reason_parts.append(f"error: {case.error_summary}")
    if fail_to_pass:
        reason_parts.append(f"F2P 失败: {', '.join(fail_to_pass)}")
    if pass_to_pass:
        reason_parts.append(f"P2P 回退: {', '.join(pass_to_pass)}")
    reason = "\n".join(reason_parts)[:REASON_MAX_LENGTH]

    meta: dict = {
        "instanceId": case.case_key,
        "evalRunId": run.id,
        "evalCaseRunId": case.id,
        "attemptId": final_attempt.id,
        "resolved": False,
        "failToPassFailed": fail_to_pass,
        "passToPassRegressed": pass_to_pass,
    }
    if case.duration_ms is not None:
        meta["durationMs"] = case.duration_ms
    if run.dataset_version_id:
        version = session.get(DatasetVersion, run.dataset_version_id)
        dataset = session.get(Dataset, version.dataset_id) if version is not None else None
        if version is not None:
            meta["datasetVersion"] = version.version
        if dataset is not None:
            meta["datasetSlug"] = dataset.slug
    tokens_total, cost_total = _usage_totals(session, case.id)
    if tokens_total is not None:
        meta["tokensTotal"] = tokens_total
    if cost_total is not None:
        meta["costCny"] = round(cost_total, 6)

    created_at = _as_utc(final_attempt.ended_at) or now

    payload: dict = {
        "badCaseId": bad_case_identifier(run.tenant_id, run.id, case.case_key),
        "sessionId": session_id,
        "badCaseType": BAD_CASE_TYPE,
        "objectName": run.runtime_key or "claude-code",
        "objectVersion": object_version,
        "objectType": OBJECT_TYPE,
        "title": title,
        "reason": reason,
        "metaData": meta,
        "source": SOURCE,
        "createdAt": iso_utc(created_at),
    }
    session_source = (
        _maybe_session_source(run, session_id, now=now, settings=settings)
        if has_native_session
        else None
    )
    if session_source is not None:
        payload["sessionSource"] = session_source
    return payload


# ---------------------------------------------------------------------------
# Terminal enqueue
# ---------------------------------------------------------------------------


def enqueue_self_evolution_reports(
    session: Session, run: EvalRun, *, now: datetime | None = None, settings: Settings | None = None
) -> int:
    """Freeze one pending report row per bad case of a terminal run.

    A bad case is a case whose final ``result_status`` is not ``resolved``
    (``unresolved``, or ``not_scored`` when the agent actually executed) and
    which has at least one attempt with a non-null ``started_at`` — purely
    queued-then-cancelled cases never reached an agent and are not reported.
    ``(tenant_id, bad_case_id)`` keeps re-running the terminal transition
    idempotent.
    """

    if not run.self_evolution_enabled:
        return 0
    now = _now(now)
    settings = settings or get_settings()

    cases = list(
        session.scalars(
            select(EvalCaseRun)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseRun.result_status != "resolved",
            )
            .order_by(EvalCaseRun.ordinal, EvalCaseRun.id)
        )
    )
    created = 0
    for case in cases:
        attempts = list(
            session.scalars(
                select(EvalCaseAttempt)
                .where(EvalCaseAttempt.eval_case_run_id == case.id)
                .order_by(EvalCaseAttempt.attempt_no)
            )
        )
        if not any(attempt.started_at is not None for attempt in attempts):
            continue
        final_attempt = attempts[-1]
        payload = build_bad_case_payload(
            session, run=run, case=case, final_attempt=final_attempt, now=now, settings=settings
        )
        bad_case_id = payload["badCaseId"]
        exists = (
            session.scalar(
                select(SelfEvolutionReport.id).where(
                    SelfEvolutionReport.tenant_id == run.tenant_id,
                    SelfEvolutionReport.bad_case_id == bad_case_id,
                )
            )
            is not None
        )
        if exists:
            continue
        session.add(
            SelfEvolutionReport(
                tenant_id=run.tenant_id,
                eval_run_id=run.id,
                eval_case_run_id=case.id,
                bad_case_id=bad_case_id,
                status="pending",
                payload_json=payload,
            )
        )
        created += 1
    if created:
        append_run_event(
            session,
            run,
            "eval.self_evolution.enqueued",
            {"count": created, "workspace_id": run.self_evolution_workspace_id},
        )
    return created


# ---------------------------------------------------------------------------
# Worker reporting pass
# ---------------------------------------------------------------------------


def _post_bad_case(
    url: str,
    token: str,
    payload: dict,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = REPORT_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    with httpx.Client(transport=transport, timeout=timeout) as client:
        response = client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "ai-eval-platform-self-evolution",
            },
        )
        return response.status_code, response.text


def _mark_terminal_failure(row: SelfEvolutionReport, *, status_code: int | None, message: str) -> None:
    row.status = "failed"
    row.last_status_code = status_code
    row.last_error = message[:1000]
    row.next_attempt_at = None


def _mark_retry(
    row: SelfEvolutionReport, *, status_code: int | None, message: str, now: datetime
) -> None:
    row.attempts += 1
    row.last_status_code = status_code
    row.last_error = message[:1000]
    if row.attempts >= MAX_REPORT_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
        return
    delay = RETRY_LADDER_SECONDS[min(row.attempts - 1, len(RETRY_LADDER_SECONDS) - 1)]
    row.next_attempt_at = now + timedelta(seconds=delay)


def process_pending_self_evolution_reports(
    session: Session,
    *,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """One worker pass over due pending reports (POST + ladder backoff)."""

    now = _now(now)
    settings = settings or get_settings()
    rows = list(
        session.scalars(
            select(SelfEvolutionReport)
            .where(
                SelfEvolutionReport.status == "pending",
                or_(
                    SelfEvolutionReport.next_attempt_at.is_(None),
                    SelfEvolutionReport.next_attempt_at <= now,
                ),
            )
            .order_by(SelfEvolutionReport.next_attempt_at, SelfEvolutionReport.id)
            .limit(max(1, settings.self_evolution_batch_size))
        )
    )
    outcome = {"scanned": len(rows), "succeeded": 0, "failed": 0, "retried": 0}
    for row in rows:
        run = session.get(EvalRun, row.eval_run_id)
        if (
            run is None
            or not run.self_evolution_base_url
            or not run.self_evolution_workspace_id
            or not run.self_evolution_token_encrypted
        ):
            _mark_terminal_failure(row, status_code=None, message="评测运行缺少自进化上报配置")
            outcome["failed"] += 1
            continue
        try:
            box = get_secret_box()
            token = box.decrypt(run.self_evolution_token_encrypted, box.version)
        except SecretBoxError as exc:
            _mark_terminal_failure(row, status_code=None, message=f"上报令牌解密失败：{exc}")
            outcome["failed"] += 1
            continue
        url = (
            f"{run.self_evolution_base_url.rstrip('/')}"
            f"/api/workspaces/{run.self_evolution_workspace_id}/bad-cases"
        )
        try:
            status, text = _post_bad_case(url, token, row.payload_json, transport=transport)
        except Exception as exc:  # network failure: retryable
            _mark_retry(
                row, status_code=None, message=f"网络错误：{exc}", now=now
            )
            outcome["failed" if row.status == "failed" else "retried"] += 1
            continue

        summary = " ".join(text.split())[:200]
        if 200 <= status < 300:
            row.status = "succeeded"
            row.reported_at = now
            row.last_status_code = status
            row.last_error = None
            row.next_attempt_at = None
            outcome["succeeded"] += 1
        elif status in (401, 403):
            _mark_terminal_failure(
                row, status_code=status, message=f"凭据问题（HTTP {status}），不重试：{summary}"
            )
            outcome["failed"] += 1
        elif status == 409:
            _mark_terminal_failure(
                row,
                status_code=status,
                message=f"工作区配额耗尽或 badCaseId 归属冲突（HTTP 409）：{summary}",
            )
            outcome["failed"] += 1
        elif status == 422:
            _mark_terminal_failure(
                row, status_code=status, message=f"上报契约违规（HTTP 422）：{summary}"
            )
            outcome["failed"] += 1
        elif status == 429 or status >= 500:
            _mark_retry(
                row,
                status_code=status,
                message=f"暂时性失败（HTTP {status}），将退避重试：{summary}",
                now=now,
            )
            outcome["failed" if row.status == "failed" else "retried"] += 1
        else:
            _mark_terminal_failure(
                row, status_code=status, message=f"非重试客户端错误（HTTP {status}）：{summary}"
            )
            outcome["failed"] += 1
    session.commit()
    return outcome


__all__ = [
    "MAX_REPORT_ATTEMPTS",
    "RETRY_LADDER_SECONDS",
    "SESSION_TTL_SECONDS",
    "SOURCE",
    "bad_case_identifier",
    "build_bad_case_payload",
    "build_session_url_template",
    "enqueue_self_evolution_reports",
    "process_pending_self_evolution_reports",
    "session_signature",
    "session_signature_matches",
]
