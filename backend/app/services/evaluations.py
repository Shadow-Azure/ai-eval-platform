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
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import (
    EVAL_RUN_STATUSES,
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalCaseAttempt,
    EvalCaseRun,
    EvalEvent,
    EvalRun,
    ModelConfig,
    ModelUsage,
    Outbox,
    ResourcePool,
)
from ..money import money_str
from ..runner_contracts import ContractInvalid, validate_payload
from .runner_labels import DEFAULT_RUNS_ON, pool_runner_labels

SHARD_DISPATCH_TOPIC = "eval.shard.dispatch"
MANIFEST_PATH_TEMPLATE = "/runner/v1/shards/{shard_id}/manifest"

# M8-31: the run-level Claude Code agentic turn cap defaults to 200 and is
# bounded to 1..1000 so a typo cannot hang a shard or starve the agent.
DEFAULT_MAX_TURNS = 200
MAX_TURNS_LIMIT = 1000

TERMINAL_RUN_STATUSES = ("completed", "partial", "failed", "cancelled")
# M4-05: a budget pause is deliberately non-terminal. While either status is
# set the scheduler/mutators must not create new work and the run must not be
# reclassified as completed/partial/failed.
PAUSED_RUN_STATUSES = ("pausing_budget", "paused_budget")
BUDGET_PAUSED = "budget_paused"
BUDGET_EXCEEDED = "budget_exceeded"
TERMINAL_CASE_STATUSES = ("succeeded", "failed", "timeout", "cancelled")
TERMINAL_ATTEMPT_STATUSES = (
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "artifact_missing",
    "dispatch_failed",
)
# Attempt phases that still own work. ``cancelling`` keeps the started ones
# (running/scoring/uploading) alive so their artifacts settle; only the ones
# that never started an agent are cancelled outright.
ACTIVE_ATTEMPT_STATUSES = (
    "pending",
    "dispatching",
    "queued",
    "leased",
    "running",
    "scoring",
    "uploading",
)
NOT_STARTED_ATTEMPT_STATUSES = ("pending", "dispatching", "queued")

# Stable cancellation error codes (diagnosable on attempt/case rows).
RUN_CANCELLED = "run_cancelled"
CANCEL_GRACE_EXCEEDED = "cancel_grace_exceeded"


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
    selected_case_keys: list[str] | None = None,
    shard_size: int | None = None,
    requested_concurrency: int | None = None,
    case_timeout_seconds: int | None = None,
    max_attempts: int | None = None,
    idempotency_key: str | None = None,
    created_by: str,
    model_config_id: str | None = None,
    model_name: str | None = None,
    model_budget_cny: Decimal | float | str | None = None,
    dataset_version_id: str | None = None,
    runtime_key: str | None = None,
    runtime_version: str | None = None,
    resource_pool_id: str | None = None,
    scoring_image_digest: str | None = None,
    execution_image_digest: str | None = None,
    asset_snapshot_id: str | None = None,
    asset_snapshot_json: dict | None = None,
    consistency_json: dict | None = None,
    prompt_suffix: str | None = None,
    max_turns: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, bool]:
    """Create the evaluation + shard dispatch outbox in one transaction.

    Replays with the same (tenant, created_by, idempotency_key) return the
    original run untouched.
    """

    settings = settings or Settings()
    now = _now(now)
    shard_size = shard_size if shard_size is not None else settings.eval_shard_size_default
    # GitHub runs at most 256 jobs per workflow run; the prepare job plus one
    # matrix job per case must stay inside that envelope (255 + 1 = 256).
    if not isinstance(shard_size, int) or not 1 <= shard_size <= 255:
        raise EvaluationInvalid("shard_size 必须在 1..255 之间", field="shard_size")
    requested = (
        requested_concurrency
        if requested_concurrency is not None
        else settings.eval_default_requested_concurrency
    )
    if not isinstance(requested, int) or not 1 <= requested <= 256:
        raise EvaluationInvalid("requested_concurrency 必须在 1..256 之间", field="requested_concurrency")
    timeout = case_timeout_seconds if case_timeout_seconds is not None else 3600
    if not isinstance(timeout, int) or not (
        settings.eval_min_case_timeout_seconds
        <= timeout
        <= settings.eval_max_case_timeout_seconds
    ):
        raise EvaluationInvalid(
            "case_timeout_seconds 超出允许范围", field="case_timeout_seconds"
        )
    attempts = max_attempts if max_attempts is not None else 2
    if not isinstance(attempts, int) or not 1 <= attempts <= settings.eval_max_case_attempts:
        raise EvaluationInvalid("max_attempts 超出允许范围", field="max_attempts")
    turns = max_turns if max_turns is not None else DEFAULT_MAX_TURNS
    if not isinstance(turns, int) or not 1 <= turns <= MAX_TURNS_LIMIT:
        raise EvaluationInvalid(
            f"max_turns 必须在 1..{MAX_TURNS_LIMIT} 之间", field="max_turns"
        )
    idempotency_key = idempotency_key or str(uuid.uuid4())

    tenant_id = str(DEFAULT_TENANT_ID)
    config = None
    if model_config_id:
        config = session.get(ModelConfig, model_config_id)
        if config is None or config.tenant_id != tenant_id:
            raise EvaluationInvalid("模型配置不存在或不可用", field="model_config_id")
        if not model_name:
            model_name = config.model_name
        if model_name != config.model_name:
            raise EvaluationInvalid("model_name 必须与模型配置一致", field="model_name")
    elif model_name:
        raise EvaluationInvalid("指定 model_name 时必须同时指定 model_config_id", field="model_name")
    budget = None
    if model_budget_cny is not None:
        budget = Decimal(str(model_budget_cny))
        if budget < 0:
            raise EvaluationInvalid("model_budget_cny 不能为负", field="model_budget_cny")
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

    case_specs: list[dict] = []
    if dataset_version_id:
        version = session.get(DatasetVersion, dataset_version_id)
        if version is None or version.tenant_id != tenant_id:
            raise EvaluationInvalid("数据集版本不存在", field="dataset_version_id")
        dataset = session.get(Dataset, version.dataset_id)
        if dataset is None or dataset.tenant_id != tenant_id:
            raise EvaluationInvalid("数据集不存在", field="dataset_version_id")
        if version.status != "active" or version.case_count <= 0:
            raise EvaluationInvalid("数据集版本不可用", field="dataset_version_id")
        cases = list(
            session.scalars(
                select(DatasetCase)
                .where(DatasetCase.dataset_version_id == version.id)
                .order_by(DatasetCase.ordinal)
            )
        )
        if selected_case_keys is not None:
            wanted = set(selected_case_keys)
            selected = [case for case in cases if case.case_key in wanted]
            if len(selected) != len(wanted):
                raise EvaluationInvalid(
                    "case_keys 包含不属于该数据集版本的用例", field="case_keys"
                )
            cases = selected
        if not cases:
            raise EvaluationInvalid("数据集版本没有用例", field="dataset_version_id")
        if len(cases) > settings.eval_max_cases:
            raise EvaluationInvalid(
                f"单个评测最多 {settings.eval_max_cases} 个用例", field="case_keys"
            )
        case_specs = [
            {
                "case_key": case.case_key,
                "dataset_case_id": case.id,
                "ordinal": case.ordinal or index + 1,
                "task_json": {
                    "case_key": case.case_key,
                    "problem_statement": case.problem_statement,
                    "repo": case.repo,
                    "base_commit": case.base_commit,
                    "test_patch": case.test_patch,
                    "expected_metrics": case.expected_metrics_json,
                    # M8-25: freeze the per-instance runner image with the task
                    # view so a later dataset edit cannot change it.
                    "environment_image": case.environment_image,
                },
            }
            for index, case in enumerate(cases)
        ]
        name = name or dataset.name
    else:
        cleaned = _clean_case_keys(case_keys, settings)
        case_specs = [
            {"case_key": key, "dataset_case_id": key, "ordinal": index + 1, "task_json": {}}
            for index, key in enumerate(cleaned)
        ]
    total_cases = len(case_specs)

    run = EvalRun(
        tenant_id=tenant_id,
        project_id=project_id,
        name=(name or "合成评测")[:200],
        status="queued",
        requested_concurrency=requested,
        effective_concurrency=None,
        queue_reason=None,
        shard_size=shard_size,
        total_cases=total_cases,
        queued_count=total_cases,
        model_config_id=model_config_id,
        model_name=model_name,
        model_budget_cny=budget,
        dataset_version_id=dataset_version_id,
        runtime_key=runtime_key,
        runtime_version=runtime_version,
        resource_pool_id=resource_pool_id,
        scoring_image_digest=scoring_image_digest,
        execution_image_digest=execution_image_digest,
        asset_snapshot_id=asset_snapshot_id,
        asset_snapshot_json=asset_snapshot_json or {},
        consistency_json=consistency_json or {},
        prompt_suffix=(prompt_suffix or "").strip(),
        idempotency_key=idempotency_key[:128],
        created_by=created_by[:128],
        case_timeout_seconds=timeout,
        max_attempts=attempts,
        max_turns=turns,
    )
    session.add(run)
    session.flush()

    shard_payloads = build_shard_dispatches(
        session,
        run=run,
        case_specs=case_specs,
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
                "dataset_version_id": dataset_version_id,
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
    case_specs: list[dict],
    shard_size: int,
    settings: Settings,
    now: datetime,
) -> ShardDispatchBundle:
    """Build one outbox row per shard + one pending attempt per case.

    All rows stay uncommitted; the caller owns the transaction.
    """

    for start in range(0, len(case_specs), shard_size):
        for offset, spec in enumerate(case_specs[start : start + shard_size]):
            session.add(
                EvalCaseRun(
                    tenant_id=str(DEFAULT_TENANT_ID),
                    eval_run_id=run.id,
                    dataset_case_id=str(spec.get("dataset_case_id") or spec["case_key"])[:128],
                    case_key=str(spec["case_key"])[:200],
                    ordinal=int(spec.get("ordinal") or start + offset + 1),
                    task_json=spec.get("task_json") or {},
                )
            )
        session.flush()

    case_runs = list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    return build_case_dispatches(
        session,
        run=run,
        case_runs=case_runs,
        attempt_no=1,
        shard_size=shard_size,
        shard_prefix="shard",
        settings=settings,
        now=now,
    )


def run_runs_on(session: Session, run: EvalRun, settings: Settings) -> list[str]:
    """Labels the shard jobs of ``run`` must target.

    A spot pool may register custom labels; evaluating without the pool's
    labels makes the job wait on the fixed runner forever. Fixed pools and
    synthetic runs without a pool fall back to the fixed-runner defaults.
    """

    pool = session.get(ResourcePool, run.resource_pool_id) if run.resource_pool_id else None
    if pool is not None and pool.provisioning_mode == "spot":
        fallback = [item.strip() for item in settings.runner_spot_labels.split(",") if item.strip()]
    else:
        fallback = list(DEFAULT_RUNS_ON)
    return pool_runner_labels(pool, fallback=fallback)


def build_case_dispatches(
    session: Session,
    *,
    run: EvalRun,
    case_runs: list[EvalCaseRun],
    attempt_no: int,
    shard_size: int,
    shard_prefix: str,
    settings: Settings,
    now: datetime,
) -> ShardDispatchBundle:
    """Build dispatch outbox rows + attempts for EXISTING case rows.

    Used by creation (attempt 1) and by ``resume-budget`` (a new attempt for
    every unfinished case). The caller owns the transaction; case rows are
    only mutated through ``latest_attempt_no``.
    """

    bundle = ShardDispatchBundle()
    tenant_id = str(DEFAULT_TENANT_ID)
    deadline = now + timedelta(minutes=settings.eval_shard_deadline_minutes)
    expires_at = now + timedelta(seconds=settings.eval_shard_ttl_seconds)
    runs_on = run_runs_on(session, run, settings)

    for shard_index, start in enumerate(range(0, len(case_runs), shard_size)):
        chunk = case_runs[start : start + shard_size]
        shard_id = f"{shard_prefix}_{shard_index:03d}"[:64]
        dispatch_key = f"{run.id}:{shard_id}:{attempt_no}"

        manifest = {
            "schema_version": "1.0",
            "eval_run_id": run.id,
            "shard_id": shard_id,
            "attempt": attempt_no,
            "dispatch_key": dispatch_key,
            "callback_base_url": settings.runner_callback_base_url,
            "deadline": deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "case_timeout_seconds": run.case_timeout_seconds,
            "max_attempts": run.max_attempts,
            # M8-31: freeze the turn cap into the byte-stable shard manifest.
            # Rows created before the column existed keep the 200 default.
            "max_turns": run.max_turns or DEFAULT_MAX_TURNS,
            "runs_on": runs_on,
            "cases": [
                {
                    "case_run_id": case_run.id,
                    "case_key": case_run.case_key,
                    "dataset_case_id": case_run.dataset_case_id,
                }
                for case_run in chunk
            ],
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        manifest_digest = "sha256:" + hashlib.sha256(manifest_text.encode()).hexdigest()
        query = urlencode(
            {
                "eval_run_id": run.id,
                "attempt": attempt_no,
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
            "attempt": attempt_no,
            "dispatch_key": dispatch_key,
            "manifest_url": manifest_url,
            "manifest_digest": manifest_digest,
            "runs_on": runs_on,
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
                    "attempt": attempt_no,
                    "shard_id": shard_id,
                },
                status="pending",
            )
        )
        for case_run in chunk:
            case_run.latest_attempt_no = attempt_no
            bundle.attempts.append(
                EvalCaseAttempt(
                    tenant_id=tenant_id,
                    eval_case_run_id=case_run.id,
                    attempt_no=attempt_no,
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


def spent_cny(session: Session, run_id: str) -> Decimal:
    """Authoritative model spend for one run (design 06 §8: ModelUsage is the
    source of truth; ``eval_run.budget_spent_cny`` is a reconciled snapshot)."""

    value = session.scalar(
        select(func.coalesce(func.sum(ModelUsage.cost_cny), 0)).where(
            ModelUsage.eval_run_id == run_id
        )
    )
    return Decimal(str(value or 0))


def budget_remaining_cny(run: EvalRun, spent: Decimal | None = None) -> Decimal | None:
    if run.model_budget_cny is None:
        return None
    return max(Decimal("0"), Decimal(str(run.model_budget_cny)) - (spent or Decimal("0")))


def get_evaluation(session: Session, run_id: str) -> EvalRun:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise EvaluationNotFound(run_id)
    return run


# M5-16: a run is "active" while it still owns queued/running work. Budget
# pauses and cancellation are non-terminal too, so the list must keep showing
# them as active instead of folding them into a finished bucket.
ACTIVE_RUN_STATUSES = tuple(
    status for status in EVAL_RUN_STATUSES if status not in TERMINAL_RUN_STATUSES
)
SCORED_RESULT_STATUSES = ("resolved", "unresolved")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def run_duration_seconds(run: EvalRun, *, now: datetime | None = None) -> float | None:
    """Elapsed wall time: final duration, or time so far for an active run."""

    started = _as_utc(run.started_at)
    if started is None:
        return None
    finished = _as_utc(run.finished_at)
    end = finished or _as_utc(now) or datetime.now(UTC)
    return max(0.0, (end - started).total_seconds())


def _run_search_pattern(search: str) -> str:
    return f"%{search.strip()}%"


def _run_filter_conditions(
    *, search: str | None = None, status: str | None = None, dataset_id: str | None = None
) -> list:
    conditions = []
    if status:
        conditions.append(EvalRun.status == status)
    if dataset_id:
        conditions.append(Dataset.id == dataset_id)
    if search and search.strip():
        pattern = _run_search_pattern(search)
        conditions.append(
            or_(
                EvalRun.name.ilike(pattern),
                EvalRun.runtime_key.ilike(pattern),
                EvalRun.model_name.ilike(pattern),
                Dataset.name.ilike(pattern),
                DatasetVersion.version.ilike(pattern),
            )
        )
    return conditions


def _run_select(*columns, joins: bool):
    statement = select(*columns).select_from(EvalRun)
    if joins:
        statement = statement.outerjoin(
            DatasetVersion, DatasetVersion.id == EvalRun.dataset_version_id
        ).outerjoin(Dataset, Dataset.id == DatasetVersion.dataset_id)
    return statement


def _needs_dataset_join(*, search: str | None, dataset_id: str | None) -> bool:
    return bool(dataset_id) or bool(search and search.strip())


def list_evaluations(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    status: str | None = None,
    dataset_id: str | None = None,
) -> tuple[list[tuple[EvalRun, str | None, str | None]], int]:
    """Server-side filtered run list plus the dataset labels the table shows.

    Search covers the visible identity fields (name, runtime, model, dataset
    name/version) so the filter bar never needs client-side post-filtering.
    """

    conditions = _run_filter_conditions(search=search, status=status, dataset_id=dataset_id)
    joins = True  # the page always renders dataset name/version
    rows = session.execute(
        _run_select(EvalRun, Dataset.name, DatasetVersion.version, joins=joins)
        .where(*conditions)
        .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
    ).all()
    items = [(row[0], row[1], row[2]) for row in rows]
    total = int(
        session.scalar(
            _run_select(
                func.count(),
                joins=_needs_dataset_join(search=search, dataset_id=dataset_id),
            ).where(*conditions)
        )
        or 0
    )
    return items, total


def evaluation_case_totals(
    session: Session, run_ids: list[str]
) -> dict[str, tuple[int, int]]:
    """Map run id → (resolved cases, scored cases) for the visible page."""

    if not run_ids:
        return {}
    rows = session.execute(
        select(
            EvalCaseRun.eval_run_id,
            func.coalesce(
                func.sum(case((EvalCaseRun.result_status == "resolved", 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case((EvalCaseRun.result_status.in_(SCORED_RESULT_STATUSES), 1), else_=0)
                ),
                0,
            ),
        )
        .where(EvalCaseRun.eval_run_id.in_(run_ids))
        .group_by(EvalCaseRun.eval_run_id)
    ).all()
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


def evaluation_list_stats(
    session: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    dataset_id: str | None = None,
) -> dict:
    """Aggregates for the four stat cards, computed over the filtered set.

    Resolve rate is case-weighted (resolved / scored) rather than an average of
    per-run percentages, so a 3-case run cannot outweigh a 500-case run.
    """

    conditions = _run_filter_conditions(search=search, status=status, dataset_id=dataset_id)
    joins = _needs_dataset_join(search=search, dataset_id=dataset_id)
    total = int(
        session.scalar(_run_select(func.count(), joins=joins).where(*conditions)) or 0
    )
    active = int(
        session.scalar(
            _run_select(func.count(), joins=joins).where(
                *conditions, EvalRun.status.in_(ACTIVE_RUN_STATUSES)
            )
        )
        or 0
    )
    running_cases = int(
        session.scalar(
            _run_select(func.coalesce(func.sum(EvalRun.running_count), 0), joins=joins).where(
                *conditions
            )
        )
        or 0
    )

    case_statement = (
        select(
            func.coalesce(
                func.sum(case((EvalCaseRun.result_status == "resolved", 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case((EvalCaseRun.result_status.in_(SCORED_RESULT_STATUSES), 1), else_=0)
                ),
                0,
            ),
        )
        .select_from(EvalCaseRun)
        .join(EvalRun, EvalRun.id == EvalCaseRun.eval_run_id)
    )
    if joins:
        case_statement = case_statement.outerjoin(
            DatasetVersion, DatasetVersion.id == EvalRun.dataset_version_id
        ).outerjoin(Dataset, Dataset.id == DatasetVersion.dataset_id)
    resolved_cases, scored_cases = session.execute(
        case_statement.where(*conditions)
    ).one()
    resolved_cases = int(resolved_cases or 0)
    scored_cases = int(scored_cases or 0)

    duration_rows = session.execute(
        _run_select(EvalRun.started_at, EvalRun.finished_at, joins=joins).where(
            *conditions,
            EvalRun.started_at.is_not(None),
            EvalRun.finished_at.is_not(None),
        )
    ).all()
    durations = [
        (_as_utc(finished) - _as_utc(started)).total_seconds()
        for started, finished in duration_rows
        if started is not None and finished is not None
    ]
    avg_duration = (
        int(round(sum(durations) / len(durations))) if durations else None
    )

    return {
        "total": total,
        "active": active,
        "running_cases": running_cases,
        "resolved_cases": resolved_cases,
        "scored_cases": scored_cases,
        "avg_resolve_rate": (
            round(resolved_cases / scored_cases * 100, 1) if scored_cases else None
        ),
        "avg_duration_seconds": avg_duration,
    }


def evaluation_summary(
    run: EvalRun,
    *,
    spent_cny: Decimal | None = None,
    dataset_name: str | None = None,
    dataset_version: str | None = None,
    resolved_count: int | None = None,
    scored_count: int | None = None,
    now: datetime | None = None,
) -> dict:
    spent = spent_cny if spent_cny is not None else run.budget_spent_cny
    unfinished = int(run.queued_count or 0) + int(run.running_count or 0)
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "total_cases": run.total_cases,
        "queued_count": run.queued_count,
        "running_count": run.running_count,
        "succeeded_count": run.succeeded_count,
        "failed_count": run.failed_count,
        "finished_count": max(0, int(run.total_cases or 0) - unfinished),
        "unfinished_count": unfinished,
        "shard_size": run.shard_size,
        "requested_concurrency": run.requested_concurrency,
        "effective_concurrency": run.effective_concurrency,
        "queue_reason": run.queue_reason,
        "resource_pool_id": run.resource_pool_id,
        "case_timeout_seconds": run.case_timeout_seconds,
        "max_attempts": run.max_attempts,
        "model_config_id": run.model_config_id,
        "model_name": run.model_name,
        "model_budget_cny": money_str(run.model_budget_cny),
        "budget_spent_cny": money_str(spent),
        "budget_remaining_cny": (
            money_str(budget_remaining_cny(run, spent if spent is not None else Decimal("0")))
            if run.model_budget_cny is not None
            else None
        ),
        "budget_version": run.budget_version,
        "pause_reason": run.pause_reason,
        "dataset_version_id": run.dataset_version_id,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "resolved_count": resolved_count,
        "scored_count": scored_count,
        "resolve_rate": (
            round(resolved_count / scored_count * 100, 1)
            if resolved_count is not None and scored_count
            else None
        ),
        "duration_seconds": (
            int(duration) if (duration := run_duration_seconds(run, now=now)) is not None else None
        ),
        "runtime_key": run.runtime_key,
        "runtime_version": run.runtime_version,
        "execution_image_digest": run.execution_image_digest,
        "scoring_image_digest": run.scoring_image_digest,
        # M8-31: rows predating the column (or a NULL snapshot) report 200.
        "max_turns": run.max_turns or DEFAULT_MAX_TURNS,
        "prompt_suffix": run.prompt_suffix or "",
        "asset_snapshot_id": run.asset_snapshot_id,
        "asset_resolved_commit": (run.asset_snapshot_json or {}).get("resolved_commit"),
        "asset_count": (run.asset_snapshot_json or {}).get("asset_count"),
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "idempotency_key": run.idempotency_key,
        "created_by": run.created_by,
    }


def evaluation_detail(
    session: Session, run: EvalRun, *, now: datetime | None = None
) -> dict:
    """Run detail plus one server-side progress snapshot (M5-17).

    Percent, resolve rate and the remaining-time estimate are aggregated here
    so every client shows the same numbers instead of stitching them together
    from different polling windows.
    """

    now = _now(now)
    cases = list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    # The detail view reconciles the spend snapshot against the gateway usage
    # rows so the pause dialog always shows the authoritative figure.
    live_spent = spent_cny(session, run.id) if run.model_budget_cny is not None else None
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
    case_counts = {
        "all": len(cases),
        "resolved": 0,
        "unresolved": 0,
        "running": 0,
        "failed": 0,
        "queued": 0,
    }
    trace_counts: dict[str, int] = {}
    retryable_count = 0
    for case in cases:
        if case.result_status == "resolved":
            case_counts["resolved"] += 1
        if case.result_status == "unresolved":
            case_counts["unresolved"] += 1
        if case.execution_status == "running":
            case_counts["running"] += 1
        if case.execution_status in ("failed", "timeout"):
            case_counts["failed"] += 1
        if case.execution_status == "queued":
            case_counts["queued"] += 1
        trace_counts[case.trace_status] = trace_counts.get(case.trace_status, 0) + 1
        if (
            run.status == "running"
            and case.execution_status in ("failed", "timeout")
            and case.latest_attempt_no < run.max_attempts
        ):
            retryable_count += 1

    total = int(run.total_cases or len(cases))
    finished = max(0, total - case_counts["queued"] - case_counts["running"])
    # Never round up to "100%" while a case is still queued/running.
    percent = (
        100
        if total and finished >= total
        else (min(99, round(finished / total * 100)) if total else 0)
    )
    resolve_denominator = (
        case_counts["resolved"] + case_counts["unresolved"] + case_counts["failed"]
    )
    resolve_rate = (
        round(case_counts["resolved"] / resolve_denominator * 100, 1)
        if resolve_denominator
        else None
    )
    estimated_remaining_seconds = None
    estimated_finish_at = None
    started_at = run.started_at
    if started_at is not None and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    # Only an actively running evaluation gets a moving ETA. Paused budget,
    # cancelling and terminal runs keep the last progress snapshot but must not
    # promise a completion time that the scheduler is not advancing toward.
    if run.status == "running" and started_at is not None and 0 < finished < total:
        elapsed = max(0.0, (now - started_at).total_seconds())
        estimated_remaining_seconds = max(
            0, round(elapsed / finished * (total - finished))
        )
        estimated_finish_at = now + timedelta(seconds=estimated_remaining_seconds)

    return {
        **evaluation_summary(run, spent_cny=live_spent),
        "asset_snapshot": run.asset_snapshot_json or {},
        # M5-04: the frozen preparation snapshot (execution policy, case
        # subset summary, digest) is part of the run record for traceability.
        "consistency": run.consistency_json or {},
        "case_counts": case_counts,
        "trace_counts": trace_counts,
        "retryable_count": retryable_count,
        "progress": {
            "total": total,
            "finished": finished,
            "percent": percent,
            "resolve_rate": resolve_rate,
            "estimated_remaining_seconds": estimated_remaining_seconds,
            "estimated_finish_at": iso_utc(estimated_finish_at),
        },
        "cases": [
            {
                "id": case.id,
                "case_key": case.case_key,
                "ordinal": case.ordinal,
                "repo": (case.task_json or {}).get("repo"),
                "execution_status": case.execution_status,
                "result_status": case.result_status,
                "trace_status": case.trace_status,
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
    before_id: int | None = None,
    limit: int = 50,
) -> tuple[list[dict], int | None, bool, str]:
    """One event window.

    - ``after_id``: forward replay for SSE (ascending, cursor = last id);
    - ``before_id``: older page for the timeline (ascending, cursor = first id);
    - neither: the newest window (ascending), so the timeline opens at the end
      even when the run emitted more than ``limit`` events.
    """

    get_evaluation(session, run_id)
    base = select(EvalEvent).where(EvalEvent.eval_run_id == run_id)
    if after_id is not None:
        rows = list(
            session.scalars(base.where(EvalEvent.id > after_id).order_by(EvalEvent.id).limit(limit + 1))
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        direction = "newer"
    else:
        if before_id is not None:
            base = base.where(EvalEvent.id < before_id)
        rows = list(session.scalars(base.order_by(EvalEvent.id.desc()).limit(limit + 1)))
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        next_cursor = rows[0].id if has_more and rows else None
        direction = "older"
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
    return items, next_cursor, has_more, direction


# ---------------------------------------------------------------------------
# Mutations: cancel + run progress rollup
# ---------------------------------------------------------------------------


def cancel_evaluation(session: Session, *, run_id: str, now: datetime | None = None) -> tuple[EvalRun, bool]:
    """Request cancellation of a non-terminal evaluation.

    State machine (M4-02 A2):

    - replay on ``cancelled`` is a no-op; any other terminal state means the
      client was too late (``EvaluationAlreadyFinal``);
    - ``queued``/``dispatching``/``pending`` attempts never started an agent and
      are cancelled immediately together with their cases and pending shard
      dispatches; ``running``/``scoring``/``uploading`` attempts stay alive and
      settle through their callbacks (their artifacts are already paid for);
    - no active attempt left → the run is finalized here, otherwise it stays
      ``cancelling`` and ``reconciler.reconcile`` finalizes it (also after
      ``eval_cancel_grace_seconds``).
    """

    now = _now(now)
    run = get_evaluation(session, run_id)
    if run.status == "cancelled":
        return run, False
    if run.status in TERMINAL_RUN_STATUSES:
        raise EvaluationAlreadyFinal(run.status)

    # Local import: ``lifecycle`` imports this module at import time (its retry
    # guard needs TERMINAL_RUN_STATUSES), so the back-edge must stay lazy.
    from . import lifecycle

    was_paused = run.status in PAUSED_RUN_STATUSES
    run.status = "cancelling"
    if run.cancel_requested_at is None:
        run.cancel_requested_at = now

    attempts = session.scalars(
        select(EvalCaseAttempt)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .where(
            EvalCaseRun.eval_run_id == run.id,
            EvalCaseAttempt.status.in_(NOT_STARTED_ATTEMPT_STATUSES),
        )
    ).all()
    for attempt in attempts:
        lifecycle.mark_status(attempt, "cancelled", now)
        attempt.ended_at = now
        attempt.error_code = RUN_CANCELLED
        case = session.get(EvalCaseRun, attempt.eval_case_run_id)
        if case is not None and case.execution_status not in TERMINAL_CASE_STATUSES:
            case.execution_status = "cancelled"
            case.error_code = RUN_CANCELLED

    outbox_rows = session.scalars(
        select(Outbox).where(
            Outbox.topic == SHARD_DISPATCH_TOPIC,
            Outbox.aggregate_id == run.id,
            Outbox.status.in_(("pending", "processing")),
        )
    ).all()
    for row in outbox_rows:
        # Keep client_payload/manifest for reconciliation; only annotate.
        row.payload_json = {**row.payload_json, "last_error": RUN_CANCELLED}
        row.status = "cancelled"

    if was_paused:
        # A paused run has no active attempt left to settle, so the queued
        # unfinished cases would otherwise keep the run in ``cancelling``
        # forever. Cases with a still-active attempt are left for that attempt.
        active_cases = {attempt.eval_case_run_id for attempt in active_attempts(session, run)}
        for case in session.scalars(
            select(EvalCaseRun).where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseRun.execution_status.in_(("queued", "running")),
            )
        ):
            if case.id in active_cases:
                continue
            case.execution_status = "cancelled"
            case.error_code = RUN_CANCELLED
        run.pause_reason = None

    append_run_event(session, run, "eval.cancelling", {"reason": "client_requested"})
    session.flush()
    if active_attempts(session, run):
        # In-flight attempts keep the run in ``cancelling``; still refresh the
        # counters so the console never shows the pre-cancel queue size.
        refresh_run_progress(session, run, now=now)
        session.commit()
        return run, True

    finalize_cancelled(session, run, now=now)
    return run, True


def active_attempts(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    return list(
        session.scalars(
            select(EvalCaseAttempt)
            .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
            .where(
                EvalCaseRun.eval_run_id == run.id,
                EvalCaseAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
            )
        )
    )


def _active_attempts(session: Session, run: EvalRun) -> list[EvalCaseAttempt]:
    return active_attempts(session, run)


def finalize_cancelled(session: Session, run: EvalRun, *, now: datetime) -> bool:
    """Move a ``cancelling`` run to ``cancelled`` exactly once.

    Idempotent entry point shared by ``cancel_evaluation`` and the reconciler:
    counters are refreshed from the case rows (never accumulated), the terminal
    status is only written for a run that is still ``cancelling`` and the
    ``eval.cancelled`` event is deduped per run/event type.
    """

    if run.status != "cancelling":
        return False
    run.status = "cancelled"
    if run.finished_at is None:
        run.finished_at = now
    session.flush()
    refresh_run_progress(session, run, now=now)
    append_run_event(session, run, "eval.cancelled", {"reason": "client_requested"})
    session.commit()
    return True


def run_event_exists(session: Session, run: EvalRun, event_type: str) -> bool:
    """Whether a run-level event of ``event_type`` was already recorded."""

    return (
        session.scalar(
            select(EvalEvent.id)
            .where(
                EvalEvent.eval_run_id == run.id,
                EvalEvent.event_type == event_type,
                EvalEvent.eval_case_run_id.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def append_run_event(session: Session, run: EvalRun, event_type: str, payload: dict) -> None:
    """Run-level event; deduped per (run, event_type) since the natural unique
    key only covers case-level (case_run, attempt, sequence) rows."""

    if run_event_exists(session, run, event_type):
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

    if run.status in PAUSED_RUN_STATUSES:
        # A budget pause is non-terminal: counters stay authoritative but the
        # run must never be reclassified while waiting for the operator.
        return run

    if run.status == "cancelling":
        # A cancelling run must never be reclassified as completed/partial/
        # failed; it becomes cancelled once no case is still queued/running
        # (running includes scoring/uploading attempts, which keep the case open).
        if run.queued_count == 0 and run.running_count == 0:
            run.status = "cancelled"
            if run.finished_at is None:
                run.finished_at = now
            append_run_event(session, run, "eval.cancelled", {"reason": "client_requested"})
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


def iso_utc(value: datetime | None) -> str | None:
    """Serialize a DB timestamp as UTC ISO-8601 with an explicit offset.

    SQLite (local dev/tests) drops ``tzinfo``; a naive value must not be
    rendered as local time by the browser. Postgres already returns aware
    datetimes and is serialized unchanged.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="microseconds")


# Internal alias kept for the service layer (the public helper is shared with
# the results API).
_iso = iso_utc
