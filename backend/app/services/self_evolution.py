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
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
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
)
from ..secrets import SecretBoxError, get_secret_box
from .evaluations import append_run_event, iso_utc
from .trace_sessions import main_session_id

logger = logging.getLogger(__name__)

SOURCE = "ai-eval-platform"
BAD_CASE_TYPE = "discovery"
OBJECT_TYPE = "agent"
TITLE_MAX_LENGTH = 255
REASON_MAX_LENGTH = 4000
TEST_NAME_LIMIT = 20
REASON_TEST_LIMIT = 8
PROBLEM_EXCERPT_LIMIT = 600
SESSION_ID_PATTERN_LIMIT = 36  # HE sessionId contract: 1..36 chars
EXECUTION_LABELS = {
    "empty_patch": "未生成有效补丁",
    "agent_failed": "Agent 执行失败",
    "scoring_failed": "评分阶段失败",
}
# M8 (#375): the structured failure code is the remote platform's only clue to
# whether a run failed on provider quota, auth, timeout or Agent crash.
FAILURE_LABELS = {
    "model_rate_limited": "模型供应商限流",
    "model_quota_exhausted": "模型配额耗尽",
    "model_authentication_failed": "模型鉴权失败",
    "model_unavailable": "模型供应商不可用",
    "model_overloaded": "模型供应商过载",
    "agent_timeout": "Agent 执行超时",
    "agent_crashed": "Agent 崩溃",
    "asset_source_install_failed": "资产安装失败",
    "asset_source_init_failed": "资产初始化失败",
    "asset_source_inject_failed": "资产注入失败",
    "asset_source_setup_failed": "资产构建失败",
    "source_archive_invalid": "源码包无效",
    "manifest_invalid": "任务清单无效",
    "scoring_failed": "评分阶段失败",
    "empty_patch": "未生成有效补丁",
}

# Short-lived session-envelope grant embedded in sessionUrlTemplate. The HMAC
# binds the endpoint path PREFIX (not the concrete session id) plus the
# reporting eval run id: harness-evolution substitutes every traversal session
# id — the reported root plus each child — into the same template, so a
# per-session signature would break the BFS pull after the first hop, while
# the run scope keeps a leaked URL from reaching sessions of other runs.
SESSION_ENVELOPE_PATH_PREFIX = "/api/v1/self-evolution/sessions/"
SESSION_TTL_SECONDS = 7 * 24 * 3600
SIGNATURE_ALGORITHM = "GET\n{path_prefix}\n{run_id}\n{exp}"

# Worker retry policy: 1min, 5min, 15min, then 1h forever; eight failed
# attempts (the initial one included) give up permanently.
RETRY_LADDER_SECONDS = (60, 300, 900, 3600)
MAX_REPORT_ATTEMPTS = 8
REPORT_TIMEOUT_SECONDS = 15.0
FAILED_TEST_OUTCOMES = ("failed", "error")

# Per-case early enqueue (issue #288): only a final, immutable result may be
# reported before the run ends. ``succeeded`` cannot be retried at all; a
# ``failed``/``timeout`` case is final only after its automatic and manual
# retry budget is exhausted. Terminal runs keep the terminal batch enqueue;
# cancelling/cancelled runs only stop new enqueues.
SETTLED_CASE_STATUSES = ("succeeded", "failed", "timeout")
SETTLED_TRACE_STATUSES = ("ready", "incomplete", "degraded", "failed")
SETTLED_SKIPPED_RUN_STATUSES = (
    "completed",
    "partial",
    "failed",
    "cancelling",
    "cancelled",
)


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


def session_signature(key: str, run_id: str, exp: int) -> str:
    """HMAC-SHA256 over ``GET\\n{path_prefix}\\n{run_id}\\n{exp}`` for one grant."""

    message = SIGNATURE_ALGORITHM.format(
        path_prefix=SESSION_ENVELOPE_PATH_PREFIX, run_id=run_id, exp=exp
    )
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def session_signature_matches(key: str, run_id: str, exp: int, signature: str) -> bool:
    """Constant-time comparison of a presented signature."""

    return hmac.compare_digest(session_signature(key, run_id, exp), signature or "")


def build_session_url_template(
    public_base_url: str, session_id: str, *, run_id: str, exp: int, key: str
) -> str:
    """``sessionUrlTemplate`` with the ``{sessionId}`` placeholder exactly once.

    The signature is bound to the endpoint prefix, the reporting run id and
    the expiry, so the reported root session and every child session the pull
    side substitutes into ``{sessionId}`` verify against the same HMAC while
    the grant stays scoped to this single eval run.
    """

    signature = session_signature(key, run_id, exp)
    return (
        f"{public_base_url.rstrip('/')}"
        f"{SESSION_ENVELOPE_PATH_PREFIX}{{sessionId}}"
        f"?run={run_id}&exp={exp}&sig={signature}"
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
        "sessionUrlTemplate": build_session_url_template(
            base_url, session_id, run_id=run.id, exp=exp, key=key
        ),
    }


# ---------------------------------------------------------------------------
# Payload assembly (frozen once at enqueue time)
# ---------------------------------------------------------------------------


def _report_session_id(session: Session, attempt_id: str) -> str | None:
    """Native root session id that HE's 1..36-char sessionId contract accepts.

    The case list may display a longer native id, but a report must fall back
    to the attempt id (without ``sessionSource``) when truncation would make
    the envelope URL unresolvable upstream.
    """

    session_id = main_session_id(session, attempt_id)
    if session_id is None:
        return None
    if len(session_id) > SESSION_ID_PATTERN_LIMIT:
        logger.warning(
            "native session id longer than %s chars; envelope link omitted",
            SESSION_ID_PATTERN_LIMIT,
        )
        return None
    return session_id


def bad_case_identifier(tenant_id: str, eval_run_id: str, case_key: str) -> str:
    digest = hashlib.sha1(f"{tenant_id}:{eval_run_id}:{case_key}".encode()).hexdigest()
    return f"swe-{digest[:20]}"


def _format_test_id(suite_name: str, test_name: str) -> str:
    """Render one test as ``file.py::test_name`` when the suite is known."""

    return f"{suite_name}::{test_name}" if suite_name else test_name


def _failed_tests(session: Session, attempt_id: str, group: str) -> list[tuple[str, str]]:
    rows = session.execute(
        select(TestCaseResult.suite_name, TestCaseResult.test_name)
        .where(
            TestCaseResult.eval_case_attempt_id == attempt_id,
            TestCaseResult.test_group == group,
            TestCaseResult.outcome.in_(FAILED_TEST_OUTCOMES),
        )
        .order_by(TestCaseResult.suite_name, TestCaseResult.test_name)
    ).all()
    return [(suite_name, test_name) for suite_name, test_name in rows][:TEST_NAME_LIMIT]


def _failed_test_count(session: Session, attempt_id: str, group: str) -> int:
    """Exact failed count, independent of the display limit on names."""

    count = session.scalar(
        select(func.count())
        .select_from(TestCaseResult)
        .where(
            TestCaseResult.eval_case_attempt_id == attempt_id,
            TestCaseResult.test_group == group,
            TestCaseResult.outcome.in_(FAILED_TEST_OUTCOMES),
        )
    )
    return int(count or 0)


def _failed_test_files(session: Session, attempt_id: str) -> list[str]:
    rows = session.scalars(
        select(TestCaseResult.suite_name)
        .where(
            TestCaseResult.eval_case_attempt_id == attempt_id,
            TestCaseResult.test_group.in_(("F2P", "P2P")),
            TestCaseResult.outcome.in_(FAILED_TEST_OUTCOMES),
        )
        .distinct()
        .order_by(TestCaseResult.suite_name)
    )
    return [suite_name for suite_name in rows if suite_name]


def _expected_test_total(task: dict, key: str) -> int:
    """Number of expected F2P/P2P tests frozen with the task view."""

    metrics = task.get("expected_metrics")
    values = metrics.get(key) if isinstance(metrics, dict) else None
    return len(values) if isinstance(values, list) else 0


def _problem_excerpt(problem_statement: object, *, limit: int = PROBLEM_EXCERPT_LIMIT) -> str:
    """First readable problem-statement paragraph for the report headline.

    SWE-bench bodies contain GitHub-template comments, headings and code;
    strip those, then take the first paragraph long enough to describe the
    task. Keeping the excerpt to one paragraph avoids turning issue content
    into unintended block structure when HE renders the Markdown.
    """

    if not isinstance(problem_statement, str):
        return ""
    text = re.sub(r"<!--.*?-->", " ", problem_statement, flags=re.S)
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    excerpt = ""
    for paragraph in paragraphs:
        normalized = re.sub(r"\s+", " ", paragraph).strip()
        if len(normalized) >= 40:
            excerpt = normalized
            break
    if not excerpt:
        excerpt = re.sub(r"\s+", " ", text).strip()
    if len(excerpt) > limit:
        excerpt = excerpt[:limit].rsplit(" ", 1)[0].rstrip(" ,;:，；：") + "…"
    return excerpt


def _execution_label(error_summary: str) -> str:
    return EXECUTION_LABELS.get(error_summary, error_summary)


def _failure_text(failure: dict | None, error_summary: str | None) -> str:
    """Human-readable cause shared by the console and the HE reason."""

    if isinstance(failure, dict) and isinstance(failure.get("code"), str):
        code = failure["code"]
        label = FAILURE_LABELS.get(code, code)
        message = failure.get("message")
        text = label
        if isinstance(message, str) and message.strip() and message.strip() != code:
            if label not in message:
                text = f"{label}：{message.strip()}"
            else:
                text = message.strip()
        request_id = failure.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            text += f"（request_id: {request_id.strip()}）"
        return text
    return _execution_label(error_summary) if error_summary else ""


def _build_reason(
    *,
    case_key: str,
    repo: str | None,
    base_commit: str | None,
    result_status: str,
    error_summary: str | None,
    failure: dict | None,
    problem: str,
    fail_to_pass: list[tuple[str, str]],
    pass_to_pass: list[tuple[str, str]],
    fail_to_pass_count: int,
    pass_to_pass_count: int,
    fail_to_pass_total: int,
    test_files: list[str],
) -> str:
    """Compose the Markdown ``reason`` rendered as the HE problem description."""

    subject = f"`{case_key}`"
    if repo:
        subject += f"（{repo}"
        if base_commit:
            subject += f" @ {base_commit[:8]}"
        subject += "）"
    elif base_commit:
        subject += f"（@ {base_commit[:8]}）"

    sections: list[str] = []
    cause = _failure_text(failure, error_summary)
    if result_status == "not_scored":
        conclusion = f"**结论**：{subject}执行失败（未进入评分）"
        if cause:
            conclusion += f"——{cause}"
        sections.append(conclusion + "。")
    else:
        parts: list[str] = []
        if fail_to_pass_count:
            if fail_to_pass_total:
                parts.append(
                    f"{fail_to_pass_count}/{fail_to_pass_total} 个目标测试（F2P）未通过"
                )
            else:
                parts.append(f"{fail_to_pass_count} 个目标测试（F2P）未通过")
        elif fail_to_pass_total:
            parts.append("目标测试（F2P）无失败")
        if pass_to_pass_count:
            parts.append(f"{pass_to_pass_count} 个既有测试（P2P）回退")
        elif fail_to_pass_count:
            parts.append("未发现既有测试（P2P）回退")
        conclusion = f"**结论**：{subject}未解决"
        if parts:
            conclusion += "——" + "，".join(parts)
        if cause:
            conclusion += f"；执行信息：{cause}"
        elif not parts:
            conclusion += "——未报告失败测试"
        elif fail_to_pass_count or pass_to_pass_count:
            conclusion += "；测试已执行，评分阶段未报告错误"
        sections.append(conclusion + "。")

    if problem:
        sections.append(f"**题面**（原文摘录）：{problem}")

    for label, group, rows, count, total in (
        ("失败", "F2P", fail_to_pass, fail_to_pass_count, fail_to_pass_total),
        ("回退", "P2P", pass_to_pass, pass_to_pass_count, 0),
    ):
        if count == 0:
            continue
        header = f"**{label}（{group}，{count}"
        if total:
            header += f"/{total}"
        header += "）**"
        lines = [header]
        for suite_name, test_name in rows[:REASON_TEST_LIMIT]:
            lines.append(f"- `{_format_test_id(suite_name, test_name)}`")
        remaining = count - min(count, REASON_TEST_LIMIT)
        if remaining > 0:
            lines.append(f"- 其余 {remaining} 个未展开")
        sections.append("\n".join(lines))

    if test_files:
        shown = "、".join(f"`{path}`" for path in test_files[:3])
        if len(test_files) > 3:
            shown += f" 等 {len(test_files)} 个文件"
        sections.append(f"**测试文件**：{shown}")
    sections.append("**排查**：查看关联会话轨迹与 Agent 补丁。")
    return "\n\n".join(sections)[:REASON_MAX_LENGTH]


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

    session_id = _report_session_id(session, final_attempt.id)
    has_native_session = session_id is not None
    if session_id is None:
        # Attempt-id fallback: the envelope endpoint resolves sessions by
        # native id, so this id would 404 upstream — sessionSource stays off.
        session_id = final_attempt.id
    object_version = run.runtime_version or (run.asset_snapshot_json or {}).get("resolved_commit")
    title = f"[SWE-bench] {case.case_key} · {case.result_status}"[:TITLE_MAX_LENGTH]

    task = case.task_json or {}
    fail_to_pass = _failed_tests(session, final_attempt.id, "F2P")
    pass_to_pass = _failed_tests(session, final_attempt.id, "P2P")
    fail_to_pass_count = _failed_test_count(session, final_attempt.id, "F2P")
    pass_to_pass_count = _failed_test_count(session, final_attempt.id, "P2P")
    fail_to_pass_total = _expected_test_total(task, "fail_to_pass")
    test_files = _failed_test_files(session, final_attempt.id)

    reason = _build_reason(
        case_key=case.case_key,
        repo=task.get("repo"),
        base_commit=task.get("base_commit"),
        result_status=case.result_status,
        error_summary=case.error_summary,
        failure=case.failure_detail_json,
        problem=_problem_excerpt(task.get("problem_statement")),
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        fail_to_pass_count=fail_to_pass_count,
        pass_to_pass_count=pass_to_pass_count,
        fail_to_pass_total=fail_to_pass_total,
        test_files=test_files,
    )

    meta: dict = {
        "instanceId": case.case_key,
        "evalRunId": run.id,
        "evalCaseRunId": case.id,
        "attemptId": final_attempt.id,
        "resolved": False,
        "failToPassFailed": [
            _format_test_id(suite_name, test_name) for suite_name, test_name in fail_to_pass
        ],
        "passToPassRegressed": [
            _format_test_id(suite_name, test_name) for suite_name, test_name in pass_to_pass
        ],
        "f2pFailedCount": fail_to_pass_count,
        "p2pRegressedCount": pass_to_pass_count,
    }
    repo = task.get("repo")
    if repo:
        meta["repo"] = repo
    base_commit = task.get("base_commit")
    if base_commit:
        meta["baseCommit"] = base_commit
    if fail_to_pass_total:
        meta["f2pTotal"] = fail_to_pass_total
    if test_files:
        meta["testFiles"] = test_files
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


def enqueue_settled_case_reports(
    session: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Freeze early reports for cases whose final outcome is already immutable.

    The worker runs this after trace ingestion and before the reporting POST
    pass, so a long evaluation no longer waits for its last case before its
    earlier bad cases reach harness-evolution. A case is a candidate only when:

    - its run is still active and has self-evolution reporting enabled (the
      terminal batch enqueue keeps owning finished/cancelled runs);
    - execution settled to ``succeeded``, ``failed`` or ``timeout``, the result
      is not ``resolved`` and at least one attempt ran;
    - a failed/timed-out case has exhausted ``run.max_attempts``; a succeeded
      case cannot be retried by the workbench at all;
    - trace ingestion published a terminal badge and the final attempt still
      registers ingestible trace objects (the #272 rule);
    - no report row exists for the case yet.

    Payloads are frozen with the same builder as the terminal enqueue. A
    savepoint catches the unique ``(tenant_id, bad_case_id)`` race with the
    terminal batch or another worker, so repeated or concurrent passes stay
    idempotent. Returns the number of inserted rows and commits.
    """

    from .trace_ingestion import has_ingestible_trace_objects, set_case_trace_status

    settings = settings or get_settings()
    now = _now(now)
    batch = max(1, limit or settings.self_evolution_batch_size)
    report_exists = exists().where(
        SelfEvolutionReport.eval_case_run_id == EvalCaseRun.id
    )
    candidates = list(
        session.execute(
            select(EvalCaseRun, EvalRun)
            .join(
                EvalRun,
                (EvalCaseRun.eval_run_id == EvalRun.id)
                & (EvalCaseRun.tenant_id == EvalRun.tenant_id),
            )
            .where(
                EvalRun.self_evolution_enabled.is_(True),
                EvalRun.status.not_in(SETTLED_SKIPPED_RUN_STATUSES),
                EvalCaseRun.execution_status.in_(SETTLED_CASE_STATUSES),
                EvalCaseRun.result_status != "resolved",
                EvalCaseRun.latest_attempt_no > 0,
                EvalCaseRun.trace_status.in_(SETTLED_TRACE_STATUSES),
                ~report_exists,
                or_(
                    EvalCaseRun.execution_status == "succeeded",
                    EvalCaseRun.latest_attempt_no >= EvalRun.max_attempts,
                ),
            )
            .order_by(EvalRun.created_at, EvalCaseRun.ordinal, EvalCaseRun.id)
            .limit(batch)
        )
    )
    created: dict[str, int] = {}
    runs: dict[str, EvalRun] = {}
    for case, run in candidates:
        final_attempt = session.scalar(
            select(EvalCaseAttempt)
            .where(
                EvalCaseAttempt.eval_case_run_id == case.id,
                EvalCaseAttempt.attempt_no == case.latest_attempt_no,
            )
            .order_by(EvalCaseAttempt.attempt_no)
        )
        if final_attempt is None:
            continue
        if not has_ingestible_trace_objects(final_attempt):
            # A terminal badge can be left behind by an older attempt: the
            # retry reset makes this rare, but the final attempt is the one
            # that must own a trace. Mark it unlinked so the same stale row is
            # not selected on every pass (#272).
            set_case_trace_status(
                session, case_run=case, attempt=final_attempt, status="unlinked"
            )
            continue
        payload = build_bad_case_payload(
            session,
            run=run,
            case=case,
            final_attempt=final_attempt,
            now=now,
            settings=settings,
        )
        try:
            with session.begin_nested():
                session.add(
                    SelfEvolutionReport(
                        tenant_id=run.tenant_id,
                        eval_run_id=run.id,
                        eval_case_run_id=case.id,
                        bad_case_id=payload["badCaseId"],
                        status="pending",
                        payload_json=payload,
                    )
                )
                session.flush()
        except IntegrityError:
            # The terminal batch (or another worker) won the same badCaseId;
            # the unique row is the idempotency contract, not an error.
            continue
        created[run.id] = created.get(run.id, 0) + 1
        runs[run.id] = run

    for run_id, count in created.items():
        append_run_event(
            session,
            runs[run_id],
            "eval.self_evolution.enqueued",
            {"count": count, "workspace_id": runs[run_id].self_evolution_workspace_id},
        )
    session.commit()
    return sum(created.values())


def backfill_missing_session_sources(
    session: Session,
    run: EvalRun,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Rebuild succeeded reports that were frozen before sessionSource existed.

    Configuration changes cannot repair a frozen payload: the terminal enqueue
    skips an existing ``(tenant, badCaseId)`` and the worker never recomputes
    it. This one-off pass rebuilds payloads for reports that are already
    succeeded, still have a native main session, and have no sessionSource.
    Reset rows are pending again so the normal worker retry path re-POSTs the
    same badCaseId (HE answers 200 and overwrites the upstream fields).

    Reports without a native session stay untouched: they cannot produce a
    valid envelope, and #272 handles their reporting rule. The pass is
    idempotent: rows that already carry sessionSource or are not succeeded are
    counted and skipped.
    """

    now = _now(now)
    settings = settings or get_settings()
    counts = {
        "scanned": 0,
        "updated": 0,
        "skipped_existing": 0,
        "skipped_no_trace": 0,
        "skipped_not_configured": 0,
    }
    if not run.self_evolution_enabled:
        return counts

    reports = list(
        session.scalars(
            select(SelfEvolutionReport)
            .where(
                SelfEvolutionReport.eval_run_id == run.id,
                SelfEvolutionReport.status == "succeeded",
            )
            .order_by(SelfEvolutionReport.created_at, SelfEvolutionReport.id)
        )
    )
    counts["scanned"] = len(reports)
    for report in reports:
        payload = report.payload_json or {}
        if payload.get("sessionSource"):
            counts["skipped_existing"] += 1
            continue

        case = session.get(EvalCaseRun, report.eval_case_run_id)
        if case is None:
            counts["skipped_no_trace"] += 1
            continue
        attempts = list(
            session.scalars(
                select(EvalCaseAttempt)
                .where(EvalCaseAttempt.eval_case_run_id == case.id)
                .order_by(EvalCaseAttempt.attempt_no)
            )
        )
        if not attempts:
            counts["skipped_no_trace"] += 1
            continue
        final_attempt = attempts[-1]
        if _report_session_id(session, final_attempt.id) is None:
            counts["skipped_no_trace"] += 1
            continue
        if (
            run.runtime_key != "claude-code"
            or not settings.self_evolution_session_signing_key
            or not settings.self_evolution_public_base_url
        ):
            counts["skipped_not_configured"] += 1
            continue

        if dry_run:
            counts["updated"] += 1
            continue
        report.payload_json = build_bad_case_payload(
            session,
            run=run,
            case=case,
            final_attempt=final_attempt,
            now=now,
            settings=settings,
        )
        report.status = "pending"
        report.attempts = 0
        report.next_attempt_at = now
        report.last_status_code = None
        report.last_error = None
        report.reported_at = None
        counts["updated"] += 1

    if not dry_run and counts["updated"]:
        append_run_event(
            session,
            run,
            "eval.self_evolution.backfilled",
            {"count": counts["updated"]},
        )
        session.commit()
    return counts


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
    "backfill_missing_session_sources",
    "bad_case_identifier",
    "build_bad_case_payload",
    "build_session_url_template",
    "enqueue_self_evolution_reports",
    "enqueue_settled_case_reports",
    "process_pending_self_evolution_reports",
    "session_signature",
    "session_signature_matches",
]
