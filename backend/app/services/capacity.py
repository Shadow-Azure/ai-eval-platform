"""Effective concurrency for one evaluation (M4-01, design 06 §1).

The platform derives the real parallelism from what can actually run:

    effective = min(requested_concurrency, online pool runners, pool max)

`tenant_remaining_quota` and "GitHub remaining scheduling capacity" also
appear in the design formula but have no data source in this milestone; they
are intentionally left unbounded, while GitHub's own limits (matrix 256,
24h queue, 5d job) remain the outer envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import EvalRun, ResourcePool, Runner

# Values persisted on ``eval_run.queue_reason``; NULL means "not waiting".
QUEUE_REASON_CAPACITY_WAITING = "capacity_waiting"
QUEUE_REASON_CONCURRENCY_CAP = "concurrency_cap"
QUEUE_REASONS = (QUEUE_REASON_CAPACITY_WAITING, QUEUE_REASON_CONCURRENCY_CAP)

# Attempt states that prove a shard has already been released to GitHub.
# ``pending`` means "not released yet" and must not consume capacity.
RELEASED_ATTEMPT_STATUSES = (
    "dispatching",
    "queued",
    "leased",
    "running",
    "scoring",
    "uploading",
)


def combine_effective_concurrency(
    requested: int, runner_capacity: int, pool_max: int | None
) -> int:
    """Pure formula shared by the dispatcher, preflight and callbacks."""

    cap = max(0, int(requested or 0))
    cap = min(cap, max(0, int(runner_capacity or 0)))
    if pool_max is not None:
        cap = min(cap, max(0, int(pool_max)))
    return max(0, cap)


def pool_max_capacity(pool: ResourcePool | None) -> int | None:
    """Read the pool's max capacity.

    M4-03 adds the real column; until then the value lives in
    ``labels_json["max_capacity"]`` and a missing/invalid value means "no
    platform-side pool bound", so the runner count is the binding limit.
    """

    if pool is None or not isinstance(pool.labels_json, dict):
        return None
    raw = pool.labels_json.get("max_capacity")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def pool_runner_capacity(
    session: Session,
    pool: ResourcePool,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Online/busy runners with a fresh heartbeat inside the pool."""

    now = now or datetime.now(UTC)
    stale_before = now - timedelta(seconds=settings.runner_heartbeat_timeout_seconds)
    return int(
        session.scalar(
            select(func.count())
            .select_from(Runner)
            .where(
                Runner.resource_pool_id == pool.id,
                Runner.status.in_(("online", "busy")),
                (Runner.last_heartbeat_at.is_(None))
                | (Runner.last_heartbeat_at >= stale_before),
            )
        )
        or 0
    )


def effective_concurrency(
    session: Session,
    run: EvalRun,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Effective cap for ``run``; runs without a pool stay ungated.

    Synthetic/legacy runs have no ``resource_pool_id``; the pre-M4 path lets
    the GitHub queue schedule them and the runner count remains the real
    bound. Pool-backed runs (the M4 product path) are gated.
    """

    requested = int(run.requested_concurrency or 0)
    if not run.resource_pool_id:
        return max(0, requested)
    pool = session.get(ResourcePool, run.resource_pool_id)
    if pool is None or pool.status != "active":
        return 0
    return combine_effective_concurrency(
        requested,
        pool_runner_capacity(session, pool, settings=settings, now=now),
        pool_max_capacity(pool),
    )
