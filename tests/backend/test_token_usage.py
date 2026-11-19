"""M8-53 (#332): token/cost aggregation from trace spans.

Covers the case-list ``metrics`` object, the run/detail ``token_summary``, the
synthetic token metric items in ``/results`` and the dataset-level
``token_stats`` / per-case ``token_avg`` fields. All fixtures run on SQLite.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest
from app.config import get_settings
from app.defaults import DEFAULT_TENANT_ID
from app.models import (
    EvalCaseAttempt,
    EvalCaseRun,
    TraceFile,
    TraceSession,
    TraceSpan,
)
from app.seed import ensure_default_scope
from app.services import evaluations
from dataset_helpers import fixture_records
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

TOKEN_METRIC_KEYS = {
    "total_tokens",
    "tokens_per_case",
    "total_cost_usd",
    "cost_per_case_usd",
}


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def post_import(
    client: TestClient,
    *,
    records,
    version: str = "verified-smoke",
    slug: str = "swebench-verified",
    name: str = "SWE-bench Verified",
) -> dict:
    response = client.post(
        "/api/v1/dataset-imports",
        json={
            "dataset_slug": slug,
            "dataset_name": name,
            "adapter_key": "swebench",
            "version": version,
            "source_uri": "tests/backend/fixtures/swebench/verified-slice.jsonl",
            "source_revision": "rev-0001",
            "records": records,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _run(session: Session, *, version_id: str | None = None, case_keys: list[str] | None = None, key: str):
    run, _created = evaluations.create_synthetic_evaluation(
        session,
        name="token-usage",
        case_keys=case_keys or [],
        selected_case_keys=None,
        dataset_version_id=version_id,
        idempotency_key=key,
        created_by="agent",
        settings=get_settings(),
    )
    session.commit()
    return run


def _cases(session: Session, run_id: str) -> list[EvalCaseRun]:
    return list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run_id)
            .order_by(EvalCaseRun.ordinal)
        )
    )


def _attempt(session: Session, case_id: str, attempt_no: int) -> EvalCaseAttempt:
    attempt = session.scalar(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id == case_id,
            EvalCaseAttempt.attempt_no == attempt_no,
        )
    )
    assert attempt is not None
    return attempt


def _add_trace(
    session: Session,
    case: EvalCaseRun,
    *,
    attempt_no: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: str,
) -> None:
    """One LLM span (tokens) + one AGENT summary span (tokens + cost).

    The AGENT span mirrors Claude Code: its tokens must be ignored (the LLM
    span already carries them) but its cost must be counted.
    """

    attempt = _attempt(session, case.id, attempt_no)
    suffix = f"{case.id}:{attempt_no}"
    trace_file = TraceFile(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_attempt_id=attempt.id,
        eval_case_run_id=case.id,
        runtime_key="claude-code",
        parser_version="claude-code-2.1.278-v1",
        artifact_kind="trace",
        object_key=f"traces/{suffix}.jsonl",
        sha256=hashlib.sha256(suffix.encode()).hexdigest(),
        size=12,
        status="ready",
    )
    session.add(trace_file)
    session.flush()
    trace_session = TraceSession(
        tenant_id=str(DEFAULT_TENANT_ID),
        trace_file_id=trace_file.id,
        eval_case_attempt_id=attempt.id,
        runtime_key="claude-code",
        parser_version="claude-code-2.1.278-v1",
        native_session_id=f"session-{suffix}",
        native_session_key=f"session-{suffix}",
        status="ready",
        ordinal=0,
    )
    session.add(trace_session)
    session.flush()
    session.add_all(
        [
            TraceSpan(
                tenant_id=str(DEFAULT_TENANT_ID),
                session_id=trace_session.id,
                native_event_id=f"llm-{suffix}",
                ordinal=0,
                kind="LLM",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            TraceSpan(
                tenant_id=str(DEFAULT_TENANT_ID),
                session_id=trace_session.id,
                native_event_id=f"agent-{suffix}",
                ordinal=1,
                kind="AGENT",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=Decimal(cost_usd),
            ),
        ]
    )
    session.commit()


def _two_case_run(session: Session, *, key: str) -> tuple[EvalCaseRun, EvalCaseRun, str]:
    run = _run(session, case_keys=["case-a", "case-b"], key=key)
    case_a, case_b = _cases(session, run.id)
    return case_a, case_b, run.id


def _seed_all_attempt_usage(session: Session, case_a: EvalCaseRun) -> None:
    # Attempt 1 and attempt 2 both count: design 07 §3.2 "所有 attempt".
    _add_trace(
        session,
        case_a,
        attempt_no=1,
        input_tokens=100,
        output_tokens=20,
        cost_usd="0.50",
    )
    attempt_two = EvalCaseAttempt(
        tenant_id=str(DEFAULT_TENANT_ID),
        eval_case_run_id=case_a.id,
        attempt_no=2,
        status="succeeded",
    )
    session.add(attempt_two)
    session.flush()
    case_a.latest_attempt_no = 2
    session.commit()
    _add_trace(
        session,
        case_a,
        attempt_no=2,
        input_tokens=50,
        output_tokens=10,
        cost_usd="0.25",
    )


# ---------------------------------------------------------------------------
# Case list / run detail
# ---------------------------------------------------------------------------


def test_case_list_metrics_use_all_attempts_and_run_detail_summary(
    seeded: Session, client: TestClient
) -> None:
    case_a, case_b, run_id = _two_case_run(seeded, key="token-case-list")
    _seed_all_attempt_usage(seeded, case_a)

    response = client.get(f"/api/v1/eval-runs/{run_id}/cases", params={"limit": 20})
    assert response.status_code == 200, response.text
    by_key = {item["case_key"]: item for item in response.json()["items"]}
    metrics = by_key["case-a"]["metrics"]
    assert metrics["input_tokens"] == 150
    assert metrics["output_tokens"] == 30
    assert metrics["total_tokens"] == 180
    assert metrics["cost_usd"] == pytest.approx(0.75)
    # A case without a parsed trace is explicit, not silently zero.
    assert by_key["case-b"]["metrics"]["total_tokens"] is None
    assert by_key["case-b"]["metrics"]["cost_usd"] is None

    detail = client.get(f"/api/v1/evaluations/{run_id}")
    assert detail.status_code == 200, detail.text
    summary = detail.json()["token_summary"]
    assert summary["input_tokens"] == 150
    assert summary["output_tokens"] == 30
    assert summary["total_tokens"] == 180
    assert summary["cost_usd"] == pytest.approx(0.75)
    assert summary["case_count"] == 2
    assert summary["cases_with_tokens"] == 1
    assert summary["average_tokens"] == 180
    assert summary["average_cost_usd"] == pytest.approx(0.75)


def test_results_include_live_token_metrics_without_metric_result_rows(
    seeded: Session, client: TestClient
) -> None:
    case_a, _case_b, run_id = _two_case_run(seeded, key="token-results")
    _seed_all_attempt_usage(seeded, case_a)

    response = client.get(f"/api/v1/eval-runs/{run_id}/results")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_summary"]["total_tokens"] == 180
    assert body["token_summary"]["cost_usd"] == pytest.approx(0.75)

    by_key = {item["metric_key"]: item for item in body["items"]}
    assert TOKEN_METRIC_KEYS <= set(by_key)
    assert by_key["total_tokens"]["value"] == 180
    assert by_key["total_tokens"]["display_kind"] == "number"
    assert by_key["total_tokens"]["unit"] == "tokens"
    assert by_key["tokens_per_case"]["value"] == 180
    assert by_key["tokens_per_case"]["denominator"] == 1
    assert by_key["total_cost_usd"]["unit"] == "USD"
    assert by_key["total_cost_usd"]["value"] == pytest.approx(0.75)
    assert by_key["cost_per_case_usd"]["value"] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Dataset level
# ---------------------------------------------------------------------------


def test_dataset_token_stats_and_version_case_average(
    seeded: Session, client: TestClient
) -> None:
    imported = post_import(client, records=fixture_records())
    version_id = imported["version"]["id"]
    dataset_id = imported["dataset"]["id"]
    run = _run(seeded, version_id=version_id, key="token-dataset")
    # A run without a parsed trace must not dilute the per-evaluation average.
    _run(seeded, version_id=version_id, key="token-dataset-empty")
    cases = _cases(seeded, run.id)
    assert len(cases) == len(fixture_records())
    first_case = cases[0]
    _add_trace(
        seeded,
        first_case,
        attempt_no=1,
        input_tokens=100,
        output_tokens=20,
        cost_usd="0.50",
    )

    catalog = client.get("/api/v1/datasets", params={"limit": 200})
    assert catalog.status_code == 200, catalog.text
    item = next(
        row for row in catalog.json()["items"] if row["id"] == dataset_id
    )
    stats = item["token_stats"]
    assert stats["total_tokens"] == 120
    assert stats["input_tokens"] == 100
    assert stats["output_tokens"] == 20
    assert stats["cost_usd"] == pytest.approx(0.50)
    assert stats["case_count"] == len(cases) * 2
    assert stats["eval_count"] == 2
    assert stats["eval_count_with_tokens"] == 1
    assert stats["average_tokens"] == 120
    assert stats["average_eval_tokens"] == 120
    assert stats["average_cost_usd"] == pytest.approx(0.50)

    detail = client.get(f"/api/v1/datasets/{dataset_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["token_stats"]["total_tokens"] == 120

    preview = client.get(
        f"/api/v1/dataset-versions/{version_id}/cases", params={"limit": 200}
    )
    assert preview.status_code == 200, preview.text
    by_key = {item["case_key"]: item for item in preview.json()["items"]}
    measured = by_key[first_case.case_key]
    assert measured["token_avg"] == 120
    assert measured["token_run_count"] == 1
    other = next(
        item for key, item in by_key.items() if key != first_case.case_key
    )
    assert other["token_avg"] is None
    assert other["token_run_count"] == 0


def test_dataset_catalog_without_runs_has_zero_token_stats(
    seeded: Session, client: TestClient
) -> None:
    imported = post_import(client, records=fixture_records())
    dataset_id = imported["dataset"]["id"]
    item = client.get("/api/v1/datasets", params={"limit": 200}).json()["items"][0]
    stats = item["token_stats"]
    assert stats["total_tokens"] == 0
    assert stats["cost_usd"] == 0
    assert stats["case_count"] == 0
    assert stats["eval_count"] == 0
    assert stats["eval_count_with_tokens"] == 0
    assert stats["average_tokens"] is None
    assert stats["average_eval_tokens"] is None
    # A dataset with no eval runs still resolves its id for detail reads.
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 200


def test_case_list_sorts_by_token_and_cost(
    seeded: Session, client: TestClient
) -> None:
    case_a, case_b, run_id = _two_case_run(seeded, key="token-sort")
    _seed_all_attempt_usage(seeded, case_a)  # 180 tokens, 0.75 USD
    _add_trace(
        seeded,
        case_b,
        attempt_no=1,
        input_tokens=50,
        output_tokens=10,
        cost_usd="0.10",
    )

    desc = client.get(
        f"/api/v1/eval-runs/{run_id}/cases",
        params={"sort": "total_tokens", "order": "desc"},
    )
    assert desc.status_code == 200, desc.text
    assert [item["case_key"] for item in desc.json()["items"]] == [
        case_a.case_key,
        case_b.case_key,
    ]

    asc = client.get(
        f"/api/v1/eval-runs/{run_id}/cases",
        params={"sort": "cost_usd", "order": "asc"},
    )
    assert asc.status_code == 200, asc.text
    assert [item["case_key"] for item in asc.json()["items"]] == [
        case_b.case_key,
        case_a.case_key,
    ]
