"""Score ingestion and evaluation metric aggregation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    EvalCaseAttempt,
    EvalCaseRun,
    EvalRun,
    MetricDefinition,
    MetricResult,
    ScoreArtifact,
    TestCaseResult,
    utcnow,
)
from ..object_store import ObjectStore
from .swebench import ParsedCase, ScoreParseError, parse_report

PARSER_VERSION = "swebench-1.0"


def ingest_score_report(
    session: Session,
    *,
    case_run: EvalCaseRun,
    attempt: EvalCaseAttempt,
    report: object,
    store: ObjectStore | None = None,
    parser_version: str = PARSER_VERSION,
) -> tuple[ScoreArtifact, ParsedCase | None]:
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    object_key = f"evals/{case_run.eval_run_id}/attempts/{attempt.id}/score/report.json"
    if store is not None:
        store.put_bytes(object_key, canonical)
    artifact = session.scalar(
        select(ScoreArtifact).where(
            ScoreArtifact.eval_case_attempt_id == attempt.id,
            ScoreArtifact.sha256 == digest,
            ScoreArtifact.parser_version == parser_version,
        )
    )
    if artifact is None:
        artifact = ScoreArtifact(
            tenant_id=case_run.tenant_id,
            eval_case_attempt_id=attempt.id,
            object_key=object_key,
            sha256=digest,
            size=len(canonical),
            parser_version=parser_version,
        )
        session.add(artifact)
        session.flush()
    try:
        parsed = parse_report(report, case_run.case_key)
    except ScoreParseError as error:
        artifact.parse_error = error.message
        case_run.result_status = "not_scored"
        session.flush()
        return artifact, None

    session.execute(
        delete(TestCaseResult).where(TestCaseResult.eval_case_attempt_id == attempt.id)
    )
    for test in parsed.tests:
        session.add(
            TestCaseResult(
                tenant_id=case_run.tenant_id,
                eval_case_attempt_id=attempt.id,
                suite_name=test.suite_name,
                test_name=test.test_name,
                outcome=test.outcome,
                native_status=test.native_status,
                test_group=test.test_group,
                expected=test.expected,
                file_path=test.file_path,
                failure_message=test.failure_message,
            )
        )
    case_run.result_status = parsed.result_status
    if parsed.failure_message:
        case_run.error_summary = parsed.failure_message[:1000]
    artifact.parse_error = None
    artifact.parsed_at = utcnow()
    session.flush()
    return artifact, parsed


@dataclass(frozen=True)
class EvaluationStats:
    selected_total: int
    resolved: int
    unresolved: int
    execution_failed: int
    executed: int
    tokens: int
    cost_usd: Decimal
    durations: tuple[int, ...]

    @property
    def graded(self) -> int:
        return self.resolved + self.unresolved


def collect_stats(session: Session, run: EvalRun) -> EvaluationStats:
    cases = list(
        session.scalars(select(EvalCaseRun).where(EvalCaseRun.eval_run_id == run.id))
    )
    resolved = sum(1 for case in cases if case.result_status == "resolved")
    unresolved = sum(1 for case in cases if case.result_status == "unresolved")
    execution_failed = sum(
        1 for case in cases if case.execution_status in ("failed", "timeout", "cancelled")
    )
    executed = sum(
        1
        for case in cases
        if case.latest_attempt_no > 0
        or case.execution_status in ("succeeded", "failed", "timeout", "cancelled")
    )
    tokens = 0
    cost = Decimal("0")
    durations: list[int] = []
    for case in cases:
        metrics = case.metrics_json if isinstance(case.metrics_json, dict) else {}
        tokens += int(metrics.get("input_tokens", 0) or 0)
        tokens += int(metrics.get("output_tokens", 0) or 0)
        cost += Decimal(str(metrics.get("cost_usd", 0) or 0))
        duration = case.duration_ms or metrics.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            durations.append(int(duration))
    return EvaluationStats(
        selected_total=len(cases),
        resolved=resolved,
        unresolved=unresolved,
        execution_failed=execution_failed,
        executed=executed,
        tokens=tokens,
        cost_usd=cost,
        durations=tuple(durations),
    )


def _percentile(values: tuple[int, ...], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return int(round(ordered[lower] * (1 - weight) + ordered[upper] * weight))


def metric_value(
    metric_key: str, stats: EvaluationStats
) -> tuple[object, Decimal | None, Decimal | None] | None:
    total = stats.selected_total
    if metric_key == "resolved_count":
        return stats.resolved, Decimal(stats.resolved), None
    if metric_key == "resolved_rate":
        value = stats.resolved / total if total else None
        return value, Decimal(stats.resolved), Decimal(total)
    if metric_key == "resolved_rate_scored":
        graded = stats.graded
        value = stats.resolved / graded if graded else None
        return value, Decimal(stats.resolved), Decimal(graded)
    if metric_key == "execution_failure_rate":
        value = stats.execution_failed / total if total else None
        return value, Decimal(stats.execution_failed), Decimal(total)
    if metric_key == "tokens_per_case":
        value = stats.tokens / stats.executed if stats.executed else None
        return value, Decimal(stats.tokens), Decimal(stats.executed)
    if metric_key == "cost_per_case":
        value = (
            float(stats.cost_usd / stats.executed) if stats.executed else None
        )
        return value, stats.cost_usd, Decimal(stats.executed)
    if metric_key == "duration_percentiles":
        return (
            {
                "p50": _percentile(stats.durations, 0.50),
                "p95": _percentile(stats.durations, 0.95),
            },
            None,
            Decimal(len(stats.durations)),
        )
    return None


def recompute_metrics(
    session: Session,
    *,
    run: EvalRun,
    definitions: list[MetricDefinition],
    is_final: bool = False,
) -> list[MetricResult]:
    stats = collect_stats(session, run)
    results: list[MetricResult] = []
    for definition in definitions:
        computed = metric_value(definition.metric_key, stats)
        if computed is None:
            continue
        value, numerator, denominator = computed
        result = session.scalar(
            select(MetricResult).where(
                MetricResult.eval_run_id == run.id,
                MetricResult.metric_definition_id == definition.id,
                MetricResult.is_final == is_final,
            )
        )
        if result is None:
            result = MetricResult(
                tenant_id=run.tenant_id,
                eval_run_id=run.id,
                metric_definition_id=definition.id,
                metric_key=definition.metric_key,
                metric_version=definition.version,
                is_final=is_final,
            )
            session.add(result)
        result.value_json = {"value": value}
        result.numerator = numerator
        result.denominator = denominator
        result.generated_at = utcnow()
        results.append(result)
    session.flush()
    return results


def case_test_summary(session: Session, attempt_id: str) -> dict[str, int]:
    tests = list(
        session.scalars(
            select(TestCaseResult).where(TestCaseResult.eval_case_attempt_id == attempt_id)
        )
    )
    summary: dict[str, int] = {}
    for test in tests:
        summary[test.outcome] = summary.get(test.outcome, 0) + 1
        group_key = f"{test.test_group.lower()}_{test.outcome}"
        summary[group_key] = summary.get(group_key, 0) + 1
    return summary
