"""M5-06 result baseline recommendation and configuration comparison (issue #88).

Covers the recommendation tiers, manual baseline validation, per-metric delta
rules (version/denominator/case-set guards, histogram and cost direction) and
the API error envelope. The fixtures build runs directly so a scenario can vary
one frozen input at a time without running the dispatcher.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.models import (
    Dataset,
    DatasetVersion,
    EvalCaseRun,
    EvalRun,
    MetricDefinition,
    MetricResult,
)
from app.seed import ensure_default_scope
from app.services import comparison
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

FINAL_STATUSES = {"completed", "partial"}


def _definitions(session: Session, dataset: Dataset) -> dict[str, MetricDefinition]:
    specs = {
        "resolved_rate": {
            "version": 1,
            "display_name": "解决率",
            "aggregator_key": "ratio",
            "display_kind": "percentage",
            "direction": "higher_is_better",
            "denominator_policy": "selected_total",
        },
        "custom_rate": {
            "version": 1,
            "display_name": "自定义通过率",
            "aggregator_key": "ratio",
            "display_kind": "percentage",
            "direction": "higher_is_better",
            "denominator_policy": "selected_total",
        },
        "resolved_count": {
            "version": 1,
            "display_name": "Resolved 数",
            "aggregator_key": "sum",
            "display_kind": "number",
            "direction": "higher_is_better",
            "denominator_policy": None,
        },
        "cost_per_case": {
            "version": 1,
            "display_name": "平均成本",
            "aggregator_key": "mean",
            "display_kind": "cny",
            "direction": "lower_is_better",
            "denominator_policy": None,
        },
        "duration_percentiles": {
            "version": 1,
            "display_name": "耗时分布",
            "aggregator_key": "histogram",
            "display_kind": "histogram",
            "direction": "lower_is_better",
            "denominator_policy": None,
        },
    }
    definitions: dict[str, MetricDefinition] = {}
    for key, spec in specs.items():
        definition = MetricDefinition(
            tenant_id=dataset.tenant_id,
            dataset_id=dataset.id,
            metric_key=key,
            version=spec["version"],
            display_name=spec["display_name"],
            aggregator_key=spec["aggregator_key"],
            display_kind=spec["display_kind"],
            direction=spec["direction"],
            denominator_policy=spec["denominator_policy"],
            formula_text=f"{key} 口径",
            config_json={},
        )
        session.add(definition)
        definitions[key] = definition
    session.flush()
    return definitions


def _dataset(session: Session, *, version: str = "v1") -> tuple[Dataset, DatasetVersion, dict[str, MetricDefinition]]:
    tenant, _workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug=f"swebench-{suffix}",
        name="SWE-bench Verified",
        adapter_key="swebench",
        task_family="code_patch",
        environment_kind="linux_docker",
        metadata_json={},
    )
    session.add(dataset)
    session.flush()
    dataset_version = DatasetVersion(
        tenant_id=tenant.id,
        dataset_id=dataset.id,
        version=version,
        source_uri="fixture://swebench",
        source_revision="40-char-commit",
        adapter_version="1.0",
        case_count=10,
        status="active",
        execution_image_digest="e" * 64,
        scoring_image_digest="s" * 64,
    )
    session.add(dataset_version)
    session.flush()
    return dataset, dataset_version, _definitions(session, dataset)


def _make_run(
    session: Session,
    *,
    version: DatasetVersion,
    definitions: dict[str, MetricDefinition],
    name: str,
    case_keys: tuple[str, ...],
    metric_values: dict[str, tuple[object, int | None, int | None]],
    status: str = "completed",
    is_final: bool = True,
    runtime: tuple[str, str] = ("claude-code", "1.0"),
    model_name: str = "deepseek-v4.1",
    asset_commit: str = "a" * 40,
    finished_minutes_ago: int = 10,
) -> EvalRun:
    tenant, _workspace, project = ensure_default_scope(session)
    now = datetime.now(UTC)
    finished = now - timedelta(minutes=finished_minutes_ago) if status in FINAL_STATUSES else None
    run = EvalRun(
        tenant_id=tenant.id,
        project_id=project.id,
        name=name,
        status=status,
        idempotency_key=f"{name}-{uuid4().hex}",
        created_by="tester",
        total_cases=len(case_keys),
        dataset_version_id=version.id,
        runtime_key=runtime[0],
        runtime_version=runtime[1],
        model_name=model_name,
        asset_snapshot_json={
            "resolved_commit": asset_commit,
            "tree_digest": "t" * 40,
        },
        requested_concurrency=4,
        case_timeout_seconds=3600,
        max_attempts=1,
        shard_size=64,
        execution_image_digest="e" * 64,
        scoring_image_digest="s" * 64,
        finished_at=finished,
        created_at=now - timedelta(minutes=finished_minutes_ago + 5),
    )
    session.add(run)
    session.flush()
    for ordinal, case_key in enumerate(case_keys):
        session.add(
            EvalCaseRun(
                tenant_id=tenant.id,
                eval_run_id=run.id,
                dataset_case_id=f"dc-{run.id}-{case_key}",
                case_key=case_key,
                ordinal=ordinal,
                execution_status="succeeded",
                result_status="resolved" if ordinal == 0 else "unresolved",
                latest_attempt_no=1,
            )
        )
    for metric_key, (value, numerator, denominator) in metric_values.items():
        definition = definitions[metric_key]
        session.add(
            MetricResult(
                tenant_id=tenant.id,
                eval_run_id=run.id,
                metric_definition_id=definition.id,
                metric_key=metric_key,
                metric_version=definition.version,
                value_json={"value": value},
                numerator=Decimal(numerator) if numerator is not None else None,
                denominator=Decimal(denominator) if denominator is not None else None,
                is_final=is_final,
            )
        )
    session.flush()
    session.commit()
    return run


def _values(
    resolved_rate: float = 0.4,
    *,
    resolved: int = 4,
    denominator: int = 10,
    cost: float = 0.3,
    p50: int = 1000,
    p95: int = 3000,
) -> dict[str, tuple[object, int | None, int | None]]:
    return {
        "resolved_rate": (resolved_rate, resolved, denominator),
        "custom_rate": (resolved_rate, resolved, denominator),
        "resolved_count": (resolved, resolved, None),
        "cost_per_case": (cost, None, None),
        "duration_percentiles": ({"p50": p50, "p95": p95}, None, 10),
    }


def _ten_cases() -> tuple[str, ...]:
    return tuple(f"case-{index}" for index in range(10))


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def test_auto_recommends_newest_identical_run_and_computes_deltas(seeded: Session) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    newest = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="基线-较新",
        case_keys=_ten_cases(),
        metric_values=_values(0.3, resolved=3, cost=0.35, p50=1100, p95=3200),
        finished_minutes_ago=5,
    )
    _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="基线-较旧",
        case_keys=_ten_cases(),
        metric_values=_values(0.2, resolved=2),
        finished_minutes_ago=10,
    )
    _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="不同 Runtime",
        case_keys=_ten_cases(),
        metric_values=_values(0.9),
        runtime=("other-runtime", "2.0"),
        finished_minutes_ago=1,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.4, resolved=4, cost=0.3, p50=1000, p95=3000),
        finished_minutes_ago=0,
    )

    response = comparison.compare_run(seeded, current, tenant_id=current.tenant_id)

    assert response["baseline_run_id"] == newest.id
    assert response["recommendation"]["tier"] == comparison.TIER_ASSETS
    assert "同资产快照" in response["recommendation"]["reason"]
    assert response["comparable"] is True
    assert response["case_set"]["comparable"] is True
    assert response["empty_state"] is None

    deltas = {item["metric_key"]: item for item in response["metric_deltas"]}
    rate = deltas["resolved_rate"]
    assert rate["comparable"] is True
    assert rate["delta"] == pytest.approx(0.1)
    assert rate["improvement"] is True
    assert rate["current_numerator"] == "4"
    assert rate["current_denominator"] == "10"

    cost = deltas["cost_per_case"]
    assert cost["delta"] == pytest.approx(-0.05)
    assert cost["improvement"] is True  # lower cost is better

    duration = deltas["duration_percentiles"]
    assert duration["delta"] == {"p50": pytest.approx(-100.0), "p95": pytest.approx(-200.0)}
    assert duration["improvement"] == {"p50": True, "p95": True}

    assert all(row["changed"] is False for row in response["configuration_differences"])
    assert response["candidates"], "candidate list feeds manual selection"


def test_candidate_window_ignores_runs_without_final_metrics(seeded: Session) -> None:
    """Metric-less terminal runs must not crowd an older eligible baseline out.

    The candidate query used to apply LIMIT before discarding runs without final
    MetricResult rows, so more than CANDIDATE_LIMIT * 3 such runs hid an
    otherwise valid baseline and produced the empty state.
    """

    _dataset_record, version, definitions = _dataset(seeded)
    baseline = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="可作基线的较旧评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.3),
        finished_minutes_ago=comparison.CANDIDATE_LIMIT * 3 + 120,
    )
    # Newer terminal runs with no MetricResult rows at all. They only need the
    # EvalRun row (same dataset version, terminal status, newer finished_at).
    for minutes in range(1, comparison.CANDIDATE_LIMIT * 3 + 6):
        _make_run(
            seeded,
            version=version,
            definitions=definitions,
            name=f"无指标评测-{minutes}",
            case_keys=(),
            metric_values={},
            finished_minutes_ago=minutes,
        )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.4),
        finished_minutes_ago=0,
    )

    response = comparison.compare_run(seeded, current, tenant_id=current.tenant_id)

    assert response["baseline_run_id"] == baseline.id
    assert response["empty_state"] is None
    assert response["recommendation"]["tier"] == comparison.TIER_ASSETS
    assert response["case_set"]["comparable"] is True
    assert [candidate["id"] for candidate in response["candidates"]] == [baseline.id]


def test_relaxed_tier_when_asset_snapshot_differs(seeded: Session) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    baseline = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="资产不同",
        case_keys=_ten_cases(),
        metric_values=_values(0.3),
        asset_commit="b" * 40,
        finished_minutes_ago=5,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.4),
        asset_commit="a" * 40,
        finished_minutes_ago=0,
    )

    response = comparison.compare_run(seeded, current, tenant_id=current.tenant_id)

    assert response["baseline_run_id"] == baseline.id
    assert response["recommendation"]["tier"] == comparison.TIER_RELAXED
    assert "资产快照不同" in response["recommendation"]["reason"]
    rows = {row["key"]: row for row in response["configuration_differences"]}
    assert rows["asset_commit"]["changed"] is True
    assert rows["asset_commit"]["incomparable"] is False


def test_empty_state_when_no_matching_runtime_or_model(seeded: Session) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    other = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="其它 Runtime",
        case_keys=_ten_cases(),
        metric_values=_values(0.3),
        runtime=("other-runtime", "2.0"),
        finished_minutes_ago=5,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.4),
        finished_minutes_ago=0,
    )

    response = comparison.compare_run(seeded, current, tenant_id=current.tenant_id)

    assert response["baseline_run_id"] is None
    assert response["empty_state"]["code"] == "no_comparable_baseline"
    assert response["metric_deltas"] == []
    assert [candidate["id"] for candidate in response["candidates"]] == [other.id]
    assert "Runtime 不同" in response["candidates"][0]["reasons"]


def test_running_run_uses_snapshot_results(seeded: Session) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    baseline = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="基线",
        case_keys=_ten_cases(),
        metric_values=_values(0.3),
        finished_minutes_ago=5,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.4),
        status="running",
        is_final=False,
        finished_minutes_ago=0,
    )

    response = comparison.compare_run(seeded, current, tenant_id=current.tenant_id)

    assert response["baseline_run_id"] == baseline.id
    assert response["is_final"] is False
    rate = {item["metric_key"]: item for item in response["metric_deltas"]}["resolved_rate"]
    assert rate["current_is_final"] is False
    assert rate["baseline_is_final"] is True
    assert rate["delta"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Manual baseline and comparability guards
# ---------------------------------------------------------------------------


def test_manual_baseline_marks_different_case_set_incomparable(seeded: Session) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    baseline = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="基线-较小用例集",
        case_keys=("case-a", "case-b", "case-c"),
        metric_values=_values(0.3),
        runtime=("other-runtime", "2.0"),
        finished_minutes_ago=5,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=tuple(f"case-{index}" for index in range(10)),
        metric_values=_values(0.4),
        finished_minutes_ago=0,
    )

    response = comparison.compare_run(
        seeded,
        current,
        tenant_id=current.tenant_id,
        baseline_run_id=baseline.id,
    )

    assert response["baseline_run_id"] == baseline.id
    assert response["manual"] is True
    assert response["comparable"] is False
    assert response["case_set"]["current_count"] == 10
    assert response["case_set"]["baseline_count"] == 3
    assert any("用例集合不同" in reason for reason in response["incomparable_reasons"])
    rows = {row["key"]: row for row in response["configuration_differences"]}
    assert rows["case_set"]["changed"] is True
    assert rows["case_set"]["incomparable"] is True
    assert rows["runtime"]["changed"] is True
    assert rows["runtime"]["incomparable"] is False
    for item in response["metric_deltas"]:
        assert item["comparable"] is False
        assert item["delta"] is None
        assert "用例集合不同" in item["incomparable_reason"]


def test_metric_version_and_denominator_mismatch_block_delta(seeded: Session) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    # A second version of resolved_rate: same key, different口径 version.
    resolved_rate_v2 = MetricDefinition(
        tenant_id=version.tenant_id,
        dataset_id=version.dataset_id,
        metric_key="resolved_rate",
        version=2,
        display_name="解决率",
        aggregator_key="ratio",
        display_kind="percentage",
        direction="higher_is_better",
        denominator_policy="selected_total",
        config_json={},
    )
    seeded.add(resolved_rate_v2)
    seeded.flush()

    baseline = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="基线-v1",
        case_keys=_ten_cases(),
        metric_values=_values(0.3),
        finished_minutes_ago=5,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions={**definitions, "resolved_rate": resolved_rate_v2},
        name="当前-v2",
        case_keys=_ten_cases(),
        metric_values=_values(0.4),
        finished_minutes_ago=0,
    )
    # Force a denominator mismatch on a metric whose version matches.
    current_rows = list(
        seeded.query(MetricResult).filter(MetricResult.eval_run_id == current.id)
    )
    for row in current_rows:
        if row.metric_key == "custom_rate":
            row.denominator = Decimal(8)
    seeded.commit()

    response = comparison.compare_run(
        seeded, current, tenant_id=current.tenant_id, baseline_run_id=baseline.id
    )

    deltas = {item["metric_key"]: item for item in response["metric_deltas"]}
    version_mismatch = deltas["resolved_rate"]
    assert version_mismatch["comparable"] is False
    assert "指标版本不同" in version_mismatch["incomparable_reason"]
    denominator_mismatch = deltas["custom_rate"]
    assert denominator_mismatch["comparable"] is False
    assert "分母不同" in denominator_mismatch["incomparable_reason"]
    # A metric with matching version and denominator still compares.
    assert deltas["resolved_count"]["comparable"] is True


def test_unknown_display_kind_degrades_without_delta() -> None:
    def row(value: object) -> SimpleNamespace:
        return SimpleNamespace(
            metric_key="radar_score",
            metric_version=1,
            denominator=None,
            numerator=None,
            value_json={"value": value},
        )

    def definition(kind: str) -> SimpleNamespace:
        return SimpleNamespace(
            metric_key="radar_score",
            display_name="未来指标",
            description=None,
            formula_text=None,
            display_kind=kind,
            unit=None,
            direction="higher_is_better",
            denominator_policy=None,
        )

    items = comparison._metric_deltas(
        [(row(0.6), definition("radar"))],
        [(row(0.5), definition("radar"))],
        case_set_same=True,
        current_is_final=True,
        baseline_is_final=True,
    )

    assert items[0]["comparable"] is False
    assert items[0]["delta"] is None
    assert "未知指标类型" in items[0]["incomparable_reason"]
    assert items[0]["current_value"] == 0.6


# ---------------------------------------------------------------------------
# API envelope
# ---------------------------------------------------------------------------


def test_compare_api_returns_recommendation_and_error_codes(
    seeded: Session, client: TestClient
) -> None:
    _dataset_record, version, definitions = _dataset(seeded)
    baseline = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="基线",
        case_keys=_ten_cases(),
        metric_values=_values(0.3),
        finished_minutes_ago=5,
    )
    current = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="当前评测",
        case_keys=_ten_cases(),
        metric_values=_values(0.4),
        finished_minutes_ago=0,
    )
    empty = _make_run(
        seeded,
        version=version,
        definitions=definitions,
        name="无结果基线",
        case_keys=_ten_cases(),
        metric_values={},
        finished_minutes_ago=1,
    )
    seeded.commit()

    response = client.get(f"/api/v1/eval-runs/{current.id}/compare")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["baseline_run_id"] == baseline.id
    assert body["recommendation"]["tier"] == comparison.TIER_ASSETS
    assert {item["metric_key"] for item in body["metric_deltas"]} >= {"resolved_rate"}

    manual = client.get(
        f"/api/v1/eval-runs/{current.id}/compare",
        params={"baseline_run_id": baseline.id},
    )
    assert manual.status_code == 200
    assert manual.json()["manual"] is True

    self_baseline = client.get(
        f"/api/v1/eval-runs/{current.id}/compare",
        params={"baseline_run_id": current.id},
    )
    assert self_baseline.status_code == 400
    assert self_baseline.json()["detail"]["error"]["code"] == "baseline_run_self"

    missing = client.get(
        f"/api/v1/eval-runs/{current.id}/compare",
        params={"baseline_run_id": "does-not-exist"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "baseline_run_not_found"

    no_results = client.get(
        f"/api/v1/eval-runs/{current.id}/compare",
        params={"baseline_run_id": empty.id},
    )
    assert no_results.status_code == 409
    assert no_results.json()["detail"]["error"]["code"] == "baseline_results_unavailable"

    unknown_run = client.get("/api/v1/eval-runs/does-not-exist/compare")
    assert unknown_run.status_code == 404
    assert unknown_run.json()["detail"]["error"]["code"] == "eval_run_not_found"
