from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    MetricDefinition,
    MetricResult,
    TestCaseResult,
    utcnow,
)
from ..scoring.service import case_test_summary
from ..seed import ensure_default_scope
from ..services import comparison, identity, token_usage
from ..services import evaluations as evaluation_service
from ..services.eval_templates import run_asset_identity
from ..services.evaluations import TERMINAL_RUN_STATUSES, iso_utc
from .dependencies import Actor, require_session, require_session_or_service

router = APIRouter(tags=["results"])


def _scope(session: Session) -> str:
    tenant, _, _ = ensure_default_scope(session)
    return tenant.id


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "request_id": None}},
    )


def _run_or_404(session: Session, tenant_id: str, run_id: str) -> EvalRun:
    run = session.scalar(
        select(EvalRun).where(EvalRun.id == run_id, EvalRun.tenant_id == tenant_id)
    )
    if run is None:
        raise _error(404, "eval_run_not_found", "评测不存在")
    return run


# M8-53 (#332): token/cost cards are derived from the trace tree on every
# request, so an existing completed run shows them without a backfill command
# and without depending on a frozen MetricResult row.
USAGE_METRIC_KEYS = (
    "total_tokens",
    "tokens_per_case",
    "total_tokens_with_cache",
    "tokens_with_cache_per_case",
    "total_cost_usd",
    "cost_per_case_usd",
)


def _token_metric_items(
    summary: dict[str, object], *, is_final: bool, generated_at: str
) -> list[dict[str, object]]:
    cases_with_tokens = int(summary.get("cases_with_tokens") or 0)
    total_tokens = int(summary.get("total_tokens") or 0)
    total_tokens_with_cache = int(
        summary.get("total_tokens_with_cache") or total_tokens
    )
    cost_usd = float(summary.get("cost_usd") or 0)
    total_value = total_tokens if cases_with_tokens else None
    total_with_cache_value = (
        total_tokens_with_cache if cases_with_tokens else None
    )
    average_tokens = summary.get("average_tokens")
    average_tokens_with_cache = summary.get("average_tokens_with_cache")
    average_cost = summary.get("average_cost_usd")
    common = {
        "metric_version": 1,
        "display_kind": "number",
        "direction": "lower_is_better",
        "is_final": is_final,
        "generated_at": generated_at,
    }
    return [
        {
            **common,
            "metric_key": "total_tokens",
            "display_name": "总 Token",
            "description": "每个用例所有 attempt 的 input_tokens + output_tokens 合计；缓存读写不计入。",
            "formula_text": (
                "total_tokens = Σ 每个用例所有 attempt 的 input_tokens + output_tokens"
            ),
            "unit": "tokens",
            "denominator_policy": None,
            "value": total_value,
            "numerator": total_tokens,
            "denominator": None,
        },
        {
            **common,
            "metric_key": "tokens_per_case",
            "display_name": "平均 Token / 用例",
            "description": "按有轨迹数据的用例数取平均。",
            "formula_text": (
                "tokens_per_case = total_tokens / cases_with_tokens；"
                "cases_with_tokens 为至少有一个已解析 attempt 轨迹的用例数"
            ),
            "unit": "tokens",
            "denominator_policy": "cases_with_tokens",
            "value": average_tokens,
            "numerator": total_tokens,
            "denominator": cases_with_tokens or None,
        },
        {
            **common,
            "metric_key": "total_tokens_with_cache",
            "display_name": "总 Token（含缓存）",
            "description": (
                "每个用例所有 attempt 的非缓存输入 + cache 输入（read + "
                "creation）+ 输出；与远端模型用量口径一致。"
            ),
            "formula_text": (
                "total_tokens_with_cache = total_tokens + cache_read_tokens + "
                "cache_creation_tokens"
            ),
            "unit": "tokens",
            "denominator_policy": None,
            "value": total_with_cache_value,
            "numerator": total_tokens_with_cache,
            "denominator": None,
        },
        {
            **common,
            "metric_key": "tokens_with_cache_per_case",
            "display_name": "平均 Token（含缓存）/ 用例",
            "description": "按有轨迹数据的用例数取平均，cache 输入计入。",
            "formula_text": (
                "tokens_with_cache_per_case = total_tokens_with_cache / "
                "cases_with_tokens"
            ),
            "unit": "tokens",
            "denominator_policy": "cases_with_tokens",
            "value": average_tokens_with_cache,
            "numerator": total_tokens_with_cache,
            "denominator": cases_with_tokens or None,
        },
        {
            **common,
            "metric_key": "total_cost_usd",
            "display_name": "累计成本（USD）",
            "description": "trace cost-state 上报的成本，单位 USD。",
            "formula_text": "total_cost_usd = Σ 所有 attempt 的 TraceSpan.cost（USD）",
            "unit": "USD",
            "denominator_policy": None,
            "value": cost_usd if cases_with_tokens else None,
            "numerator": cost_usd,
            "denominator": None,
        },
        {
            **common,
            "metric_key": "cost_per_case_usd",
            "display_name": "平均成本（USD / 用例）",
            "description": "按有轨迹数据的用例数取平均，单位 USD。",
            "formula_text": (
                "cost_per_case_usd = total_cost_usd / cases_with_tokens"
            ),
            "unit": "USD",
            "denominator_policy": "cases_with_tokens",
            "value": average_cost,
            "numerator": cost_usd,
            "denominator": cases_with_tokens or None,
        },
    ]


@router.get("/api/v1/eval-runs/{run_id}/results")
def get_eval_results(
    run_id: str,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_session_or_service("eval:read")),
) -> dict[str, object]:
    tenant_id = _scope(session)
    run = _run_or_404(session, tenant_id, run_id)
    rows = list(
        session.execute(
            select(MetricResult, MetricDefinition)
            .join(
                MetricDefinition,
                MetricDefinition.id == MetricResult.metric_definition_id,
            )
            .where(MetricResult.eval_run_id == run.id)
            .order_by(MetricDefinition.metric_key)
        )
    )
    final_rows = [(result, definition) for result, definition in rows if result.is_final]
    selected = final_rows or rows
    # M10 (#378): the stable results contract marks a terminal run as final
    # even when it produced no metric rows (for example a fully failed run),
    # so a poller can stop based on ``is_final`` alone.
    is_final = bool(final_rows) or run.status in TERMINAL_RUN_STATUSES
    items = []
    for result, definition in selected:
        value = result.value_json.get("value") if isinstance(result.value_json, dict) else None
        items.append(
            {
                "metric_key": result.metric_key,
                "metric_version": result.metric_version,
                "display_name": definition.display_name,
                "description": definition.description,
                "formula_text": definition.formula_text,
                "display_kind": definition.display_kind,
                "unit": definition.unit,
                "direction": definition.direction,
                "denominator_policy": definition.denominator_policy,
                "value": value,
                "numerator": result.numerator,
                "denominator": result.denominator,
                "is_final": result.is_final,
                "generated_at": iso_utc(result.generated_at),
            }
        )
    token_summary = token_usage.run_token_summary(session, run.id)
    # A frozen MetricResult for a usage key would be stale (traces arrive after
    # completion), so the live trace-derived item always wins.
    items = [
        item for item in items if item["metric_key"] not in USAGE_METRIC_KEYS
    ]
    items.extend(
        _token_metric_items(
            token_summary,
            is_final=is_final,
            generated_at=iso_utc(run.finished_at or utcnow()),
        )
    )
    response: dict[str, object] = {
        "run_id": run.id,
        "template_id": run.template_id,
        "status": run.status,
        "is_final": is_final,
        "status_url": f"/api/v1/eval-runs/{run.id}/status",
        "result_url": f"/api/v1/eval-runs/{run.id}/results",
        "items": items,
        "token_summary": token_summary,
    }
    # M10 (#429): the frozen asset identity and the caller lineage are additive
    # fields, present only for runs that actually carry them.
    asset = run_asset_identity(session, run)
    if asset is not None:
        response["asset"] = asset
    lineage = run.evolution_lineage_json or {}
    if lineage:
        response["lineage"] = lineage
    return response


@router.get("/api/v1/eval-runs/{run_id}/status")
def get_eval_status(
    run_id: str,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_session_or_service("eval:read")),
) -> dict[str, object]:
    """M10 (#378): the stable poll read model for template-launched runs.

    Returns terminal/progress/counts/resolve-rate without the full detail
    payload, so an external script can poll cheaply and stop at ``terminal``.
    """

    tenant_id = _scope(session)
    run = _run_or_404(session, tenant_id, run_id)
    totals = evaluation_service.evaluation_case_totals(session, [run.id])
    resolved, scored = totals.get(run.id, (0, 0))
    total = int(run.total_cases or 0)
    unfinished = int(run.queued_count or 0) + int(run.running_count or 0)
    finished = max(0, total - unfinished)
    percent = (
        100
        if total and finished >= total
        else (min(99, round(finished / total * 100)) if total else 0)
    )
    duration = evaluation_service.run_duration_seconds(run)
    response: dict[str, object] = {
        "run_id": run.id,
        "template_id": run.template_id,
        "template_version": run.template_version,
        "status": run.status,
        "terminal": run.status in TERMINAL_RUN_STATUSES,
        "total_cases": total,
        "queued_count": run.queued_count,
        "running_count": run.running_count,
        "succeeded_count": run.succeeded_count,
        "failed_count": run.failed_count,
        "finished_count": finished,
        "progress": {
            "total": total,
            "finished": finished,
            "percent": percent,
        },
        "resolved_count": resolved,
        "scored_count": scored,
        "resolve_rate": (round(resolved / scored * 100, 1) if scored else None),
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
        "duration_seconds": int(duration) if duration is not None else None,
        "status_url": f"/api/v1/eval-runs/{run.id}/status",
        "result_url": f"/api/v1/eval-runs/{run.id}/results",
    }
    # M10 (#429): additive asset echo, present only when the run froze assets.
    asset = run_asset_identity(session, run)
    if asset is not None:
        response["asset"] = asset
    return response


@router.get("/api/v1/eval-runs/{run_id}/compare")
def compare_eval_run(
    run_id: str,
    baseline_run_id: str | None = Query(default=None, max_length=36),
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_session),
) -> dict[str, object]:
    tenant_id = _scope(session)
    run = _run_or_404(session, tenant_id, run_id)
    try:
        return comparison.compare_run(
            session,
            run,
            tenant_id=tenant_id,
            baseline_run_id=baseline_run_id or None,
        )
    except comparison.ComparisonInvalid as exc:
        raise _error(exc.status_code, exc.code, str(exc)) from exc


@router.get("/api/v1/eval-case-runs/{case_run_id}/tests")
def get_case_tests(
    case_run_id: str,
    attempt: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
    _context: identity.AuthContext = Depends(require_session),
) -> dict[str, object]:
    tenant_id = _scope(session)
    case_run = session.scalar(
        select(EvalCaseRun).where(
            EvalCaseRun.id == case_run_id, EvalCaseRun.tenant_id == tenant_id
        )
    )
    if case_run is None:
        raise _error(404, "eval_case_run_not_found", "评测用例不存在")
    attempt_statement = select(EvalCaseAttempt).where(
        EvalCaseAttempt.eval_case_run_id == case_run.id
    )
    if attempt is None:
        attempt_statement = attempt_statement.order_by(EvalCaseAttempt.attempt_no.desc())
        case_attempt = session.scalars(attempt_statement.limit(1)).first()
    else:
        case_attempt = session.scalars(
            attempt_statement.where(EvalCaseAttempt.attempt_no == attempt)
        ).first()
    if case_attempt is None:
        return {
            "case_run_id": case_run.id,
            "attempt": None,
            "items": [],
            "summary": {},
        }
    tests = list(
        session.scalars(
            select(TestCaseResult)
            .where(TestCaseResult.eval_case_attempt_id == case_attempt.id)
            .order_by(TestCaseResult.test_group, TestCaseResult.test_name)
        )
    )
    return {
        "case_run_id": case_run.id,
        "attempt": case_attempt.attempt_no,
        "result_status": case_run.result_status,
        "summary": case_test_summary(session, case_attempt.id),
        "items": [
            {
                "id": test.id,
                "suite_name": test.suite_name,
                "test_name": test.test_name,
                "outcome": test.outcome,
                "native_status": test.native_status,
                "test_group": test.test_group,
                "expected": test.expected,
                "duration_ms": test.duration_ms,
                "file_path": test.file_path,
                "line_no": test.line_no,
                "failure_message": test.failure_message,
                "log_object_key": test.log_object_key,
            }
            for test in tests
        ],
    }
