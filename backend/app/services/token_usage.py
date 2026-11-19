"""Token and cost aggregation for evaluation cases (M8-53, issue #332).

The source of truth is the trace tree, not the runner completion summary: the
runner sends ``input_tokens: 0`` and the parser stores the real usage on
``trace_span``.

Two rules keep the numbers honest:

* Every attempt of a case is counted, following design 07 §3.2 ("所有 attempt
  token 总投入"): a retry really spends tokens, so a case row is the sum of its
  attempts and the run total is the sum of the case rows.
* Token sums exclude ``kind='AGENT'`` spans. Claude Code stores the session
  total on the root AGENT span while every LLM span stores its own usage, so
  summing both double counts. Cost stays on the AGENT span, therefore cost sums
  every span.

Cost is the provider-reported ``TraceSpan.cost`` in USD (Claude Code's
``cost-state.totalCostUSD``). It is deliberately named ``cost_usd`` and never
formatted as CNY by the backend.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import (
    DatasetVersion,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    TraceSession,
    TraceSpan,
)

# AGENT spans duplicate the per-LLM usage for the Claude Code parser. Other
# parsers put usage on MESSAGE/LLM spans and do not create AGENT summary rows.
_TOKEN_SPAN = TraceSpan.kind != "AGENT"
_COST_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class TokenUsage:
    """One case's cumulative token/cost usage across all attempts."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    span_count: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def has_trace(self) -> bool:
        return self.span_count > 0

    def as_metrics(self) -> dict[str, Any]:
        """Fields merged into ``eval_case_run.metrics_json`` / API ``metrics``."""

        if not self.has_trace:
            return {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cost_usd": None,
            }
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": _money_float(self.cost_usd),
        }


def _money_float(value: Decimal) -> float:
    return float(value.quantize(_COST_QUANT))


def _usage_from_row(row: Any) -> TokenUsage:
    return TokenUsage(
        input_tokens=int(row.input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
        cost_usd=Decimal(str(row.cost_usd or 0)).quantize(_COST_QUANT),
        span_count=int(row.span_count or 0),
    )


def _usage_columns() -> tuple[Any, ...]:
    return (
        func.coalesce(
            func.sum(case((_TOKEN_SPAN, TraceSpan.input_tokens), else_=0)), 0
        ).label("input_tokens"),
        func.coalesce(
            func.sum(case((_TOKEN_SPAN, TraceSpan.output_tokens), else_=0)), 0
        ).label("output_tokens"),
        func.coalesce(func.sum(TraceSpan.cost), 0).label("cost_usd"),
        func.count(TraceSpan.id).label("span_count"),
    )


def case_usage(
    session: Session, case_ids: Iterable[str]
) -> dict[str, TokenUsage]:
    """Cumulative usage for the given case-run ids in one query.

    Every attempt's trace sessions are summed; cases without a parsed trace
    still appear with ``span_count == 0`` so coverage stays explicit.
    """

    ids = sorted({case_id for case_id in case_ids if case_id})
    if not ids:
        return {}
    statement = (
        select(
            EvalCaseRun.id.label("case_id"),
            *_usage_columns(),
        )
        .outerjoin(
            EvalCaseAttempt,
            (EvalCaseAttempt.eval_case_run_id == EvalCaseRun.id)
            & (EvalCaseAttempt.tenant_id == EvalCaseRun.tenant_id),
        )
        .outerjoin(
            TraceSession,
            (TraceSession.eval_case_attempt_id == EvalCaseAttempt.id)
            & (TraceSession.tenant_id == EvalCaseAttempt.tenant_id),
        )
        .outerjoin(
            TraceSpan,
            (TraceSpan.session_id == TraceSession.id)
            & (TraceSpan.tenant_id == TraceSession.tenant_id),
        )
        .where(EvalCaseRun.id.in_(ids))
        .group_by(EvalCaseRun.id)
    )
    return {
        row.case_id: _usage_from_row(row)
        for row in session.execute(statement)
    }


def summarize_case_usage(usages: Iterable[TokenUsage]) -> dict[str, Any]:
    """Aggregate case usage into the ``token_summary`` response shape."""

    rows = list(usages)
    case_count = len(rows)
    cases_with_tokens = sum(1 for usage in rows if usage.has_trace)
    input_tokens = sum(usage.input_tokens for usage in rows)
    output_tokens = sum(usage.output_tokens for usage in rows)
    total_tokens = input_tokens + output_tokens
    cost_usd = sum((usage.cost_usd for usage in rows), Decimal("0"))
    if cases_with_tokens:
        average_tokens: int | None = int(round(total_tokens / cases_with_tokens))
        average_cost_usd: float | None = _money_float(cost_usd / cases_with_tokens)
    else:
        average_tokens = None
        average_cost_usd = None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": _money_float(cost_usd),
        "case_count": case_count,
        "cases_with_tokens": cases_with_tokens,
        "average_tokens": average_tokens,
        "average_cost_usd": average_cost_usd,
    }


def run_token_summary(session: Session, run_id: str) -> dict[str, Any]:
    """Token summary for one evaluation run (all attempts per case)."""

    case_ids = list(
        session.scalars(
            select(EvalCaseRun.id).where(EvalCaseRun.eval_run_id == run_id)
        )
    )
    return summarize_case_usage(case_usage(session, case_ids).values())


def empty_dataset_token_stats() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "case_count": 0,
        "eval_count": 0,
        "average_tokens": None,
        "average_eval_tokens": None,
        "average_cost_usd": None,
    }


def _dataset_case_subquery(dataset_ids: Iterable[str]):
    ids = sorted({dataset_id for dataset_id in dataset_ids if dataset_id})
    if not ids:
        return None, []
    per_case = (
        select(
            EvalCaseRun.id.label("case_id"),
            EvalCaseRun.eval_run_id.label("run_id"),
            EvalCaseRun.case_key.label("case_key"),
            DatasetVersion.dataset_id.label("dataset_id"),
            *_usage_columns(),
        )
        .join(
            EvalRun,
            (EvalRun.id == EvalCaseRun.eval_run_id)
            & (EvalRun.tenant_id == EvalCaseRun.tenant_id),
        )
        .join(
            DatasetVersion,
            (DatasetVersion.id == EvalRun.dataset_version_id)
            & (DatasetVersion.tenant_id == EvalRun.tenant_id),
        )
        .outerjoin(
            EvalCaseAttempt,
            (EvalCaseAttempt.eval_case_run_id == EvalCaseRun.id)
            & (EvalCaseAttempt.tenant_id == EvalCaseRun.tenant_id),
        )
        .outerjoin(
            TraceSession,
            (TraceSession.eval_case_attempt_id == EvalCaseAttempt.id)
            & (TraceSession.tenant_id == EvalCaseAttempt.tenant_id),
        )
        .outerjoin(
            TraceSpan,
            (TraceSpan.session_id == TraceSession.id)
            & (TraceSpan.tenant_id == TraceSession.tenant_id),
        )
        .where(DatasetVersion.dataset_id.in_(ids))
        .group_by(
            EvalCaseRun.id,
            EvalCaseRun.eval_run_id,
            EvalCaseRun.case_key,
            DatasetVersion.dataset_id,
        )
        .subquery()
    )
    return per_case, ids


def dataset_token_stats(
    session: Session, dataset_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Cumulative token stats per dataset, across every version and run.

    Averages use the number of case runs that actually have trace data, so
    legacy runs without parsed traces do not silently dilute the number.
    """

    per_case, ids = _dataset_case_subquery(dataset_ids)
    if per_case is None:
        return {}
    rows = session.execute(
        select(
            per_case.c.dataset_id,
            func.coalesce(func.sum(per_case.c.input_tokens), 0),
            func.coalesce(func.sum(per_case.c.output_tokens), 0),
            func.coalesce(func.sum(per_case.c.cost_usd), 0),
            func.count(per_case.c.case_id),
            func.count(func.distinct(per_case.c.run_id)),
            func.coalesce(
                func.sum(case((per_case.c.span_count > 0, 1), else_=0)), 0
            ),
        ).group_by(per_case.c.dataset_id)
    ).all()
    stats = {dataset_id: empty_dataset_token_stats() for dataset_id in ids}
    for row in rows:
        dataset_id = row[0]
        input_tokens = int(row[1] or 0)
        output_tokens = int(row[2] or 0)
        total_tokens = input_tokens + output_tokens
        cost_usd = Decimal(str(row[3] or 0)).quantize(_COST_QUANT)
        case_count = int(row[4] or 0)
        eval_count = int(row[5] or 0)
        cases_with_tokens = int(row[6] or 0)
        stats[dataset_id] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": _money_float(cost_usd),
            "case_count": case_count,
            "eval_count": eval_count,
            "average_tokens": (
                int(round(total_tokens / cases_with_tokens))
                if cases_with_tokens
                else None
            ),
            "average_eval_tokens": (
                int(round(total_tokens / eval_count)) if eval_count else None
            ),
            "average_cost_usd": (
                _money_float(cost_usd / cases_with_tokens)
                if cases_with_tokens
                else None
            ),
        }
    return stats


def version_case_token_averages(
    session: Session, *, dataset_id: str
) -> dict[str, Mapping[str, Any]]:
    """Per-case average tokens across the dataset's runs (all versions).

    Returns ``{case_key: {"token_avg": int | None, "token_run_count": int}}``.
    ``token_avg`` is the mean of the per-run all-attempt totals for runs that
    actually parsed a trace; ``token_run_count`` is how many such runs
    contributed.
    """

    per_case, _ids = _dataset_case_subquery([dataset_id])
    if per_case is None:
        return {}
    rows = session.execute(
        select(
            per_case.c.case_key,
            func.coalesce(
                func.sum(per_case.c.input_tokens + per_case.c.output_tokens), 0
            ),
            func.coalesce(
                func.sum(case((per_case.c.span_count > 0, 1), else_=0)), 0
            ),
        ).group_by(per_case.c.case_key)
    ).all()
    averages: dict[str, Mapping[str, Any]] = {}
    for case_key, total_tokens, run_count in rows:
        count = int(run_count or 0)
        averages[str(case_key)] = {
            "token_avg": (
                int(round(int(total_tokens or 0) / count)) if count else None
            ),
            "token_run_count": count,
        }
    return averages


__all__ = [
    "TokenUsage",
    "dataset_token_stats",
    "empty_dataset_token_stats",
    "case_usage",
    "run_token_summary",
    "summarize_case_usage",
    "version_case_token_averages",
]
