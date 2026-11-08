"""Runner heartbeat persistence, state machine and queries (M2-04).

State machine (design: plans/m1-02-fullstack-design/06 §7):

    offline --heartbeat/register--> online/busy
    online  --busy heartbeat-->     busy (single case attempt binding)
    busy    --same attempt-->       busy (progress heartbeats)
    busy    --other attempt-->      409 RunnerBusyConflict (one job per runner)
    online/busy --heartbeat timeout--> offline (binding kept for diagnosis)
    offline --heartbeat/register--> online (recovery)

The payload contract is pinned to the deployed heartbeat agent: it sends
`{runner_id, name, labels, status, current_case_attempt_id}` and treats any
2xx as success.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import Project, ResourcePool, Runner, Tenant


class RunnerBusyConflict(Exception):
    """A runner already bound to another case attempt received a new job."""

    def __init__(self, runner: Runner, incoming_attempt: str) -> None:
        self.runner = runner
        self.incoming_attempt = incoming_attempt
        super().__init__(
            f"runner {runner.provider_runner_id} busy with "
            f"{runner.busy_case_attempt_id}, refusing {incoming_attempt}"
        )


class RunnerNotFound(Exception):
    pass


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC for arithmetic."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def ensure_fixed_pool(session: Session, settings: Settings | None = None) -> ResourcePool:
    """Return the system pool that fixed self-hosted runners join."""

    settings = settings or get_settings()
    tenant_id = str(DEFAULT_TENANT_ID)
    project_id = str(DEFAULT_PROJECT_ID)

    pool = session.scalar(
        select(ResourcePool).where(
            ResourcePool.tenant_id == tenant_id,
            ResourcePool.project_id == project_id,
            ResourcePool.provider_key == settings.runner_fixed_pool_provider_key,
            ResourcePool.name == settings.runner_fixed_pool_name,
        )
    )
    if pool is not None:
        return pool

    pool = ResourcePool(
        tenant_id=tenant_id,
        project_id=project_id,
        name=settings.runner_fixed_pool_name,
        provider_key=settings.runner_fixed_pool_provider_key,
        runner_group=settings.runner_fixed_pool_name,
        labels_json={"kind": "fixed"},
        status="active",
    )
    session.add(pool)
    try:
        session.flush()
    except IntegrityError:
        # Concurrent first heartbeats race on uq_resource_pool_project_name.
        session.rollback()
        pool = session.scalar(
            select(ResourcePool).where(
                ResourcePool.tenant_id == tenant_id,
                ResourcePool.project_id == project_id,
                ResourcePool.provider_key == settings.runner_fixed_pool_provider_key,
                ResourcePool.name == settings.runner_fixed_pool_name,
            )
        )
        if pool is None:
            raise
    return pool


def _find_runner(session: Session, pool: ResourcePool, provider_runner_id: str) -> Runner | None:
    return session.scalar(
        select(Runner).where(
            Runner.resource_pool_id == pool.id,
            Runner.provider_runner_id == provider_runner_id,
        )
    )


def record_runner_heartbeat(
    session: Session,
    *,
    runner_id: str,
    name: str,
    labels: list[str],
    status: str,
    current_case_attempt_id: str | None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> Runner:
    """Upsert one runner from a heartbeat payload and apply the state machine."""

    now = now or datetime.now(UTC)
    settings = settings or get_settings()
    pool = ensure_fixed_pool(session, settings)

    runner = _find_runner(session, pool, runner_id)
    if runner is None:
        runner = Runner(
            tenant_id=str(DEFAULT_TENANT_ID),
            resource_pool_id=pool.id,
            provider_runner_id=runner_id,
            name=name,
            labels_json=labels,
            status="online",
            registered_at=now,
        )
        session.add(runner)
    else:
        runner.name = name
        runner.labels_json = labels
        if runner.deregistered_at is not None:
            runner.deregistered_at = None

    if status == "busy":
        bound = runner.busy_case_attempt_id
        if bound is not None and bound != current_case_attempt_id:
            raise RunnerBusyConflict(runner, current_case_attempt_id or "")
        runner.busy_case_attempt_id = current_case_attempt_id
    else:
        # online (job finished) or explicit offline (graceful shutdown): release.
        runner.busy_case_attempt_id = None

    runner.status = status
    runner.last_heartbeat_at = now
    session.commit()
    return runner


def sweep_stale_runners(
    session: Session,
    *,
    timeout_seconds: int,
    now: datetime | None = None,
) -> int:
    """Heartbeat timeout → offline. The busy binding is kept for diagnosis."""

    now = now or datetime.now(UTC)
    stale_before = now - timedelta(seconds=timeout_seconds)
    swept = 0
    runners = session.scalars(
        select(Runner).where(Runner.status.in_(("online", "busy")))
    ).all()
    for runner in runners:
        last_seen = _aware(runner.last_heartbeat_at)
        if last_seen is not None and last_seen < stale_before:
            runner.status = "offline"
            swept += 1
    if swept:
        session.commit()
    return swept


def bind_case(session: Session, *, runner: Runner, case_run_id: str) -> Runner:
    """Case-contract callback proves the runner is executing `case_run_id`.

    Same single-job rule as runner-level heartbeats: a runner already bound to
    another case attempt conflicts (409 at the API edge).
    """

    bound = runner.busy_case_attempt_id
    if bound is not None and bound != case_run_id:
        raise RunnerBusyConflict(runner, case_run_id)
    runner.busy_case_attempt_id = case_run_id
    runner.status = "busy"
    runner.last_heartbeat_at = datetime.now(UTC)
    session.commit()
    return runner


def release_case(session: Session, *, runner: Runner) -> Runner:
    """Terminal case callback (completed/failed): the runner is free again."""

    runner.busy_case_attempt_id = None
    runner.status = "online"
    runner.last_heartbeat_at = datetime.now(UTC)
    session.commit()
    return runner


def runner_payload(runner: Runner, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    last_seen = _aware(runner.last_heartbeat_at)
    age = (now - last_seen).total_seconds() if last_seen is not None else None
    labels = runner.labels_json if isinstance(runner.labels_json, list) else []
    return {
        "id": runner.id,
        "provider_runner_id": runner.provider_runner_id,
        "name": runner.name,
        "labels": list(labels),
        "status": runner.status,
        "current_case_attempt_id": runner.busy_case_attempt_id,
        "last_heartbeat_at": _iso(last_seen),
        "heartbeat_age_seconds": int(age) if age is not None else None,
        "registered_at": _iso(_aware(runner.registered_at)),
        "resource_pool_id": runner.resource_pool_id,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds")


def get_runner_by_provider_id(session: Session, provider_runner_id: str) -> Runner:
    tenant_id = str(DEFAULT_TENANT_ID)
    runner = session.scalar(
        select(Runner).where(
            Runner.tenant_id == tenant_id,
            Runner.provider_runner_id == provider_runner_id,
        )
    )
    if runner is None:
        raise RunnerNotFound(provider_runner_id)
    return runner


def list_runners(session: Session) -> list[Runner]:
    tenant_id = str(DEFAULT_TENANT_ID)
    return list(
        session.scalars(
            select(Runner)
            .where(Runner.tenant_id == tenant_id)
            .order_by(Runner.provider_runner_id)
        )
    )


def default_scope_ready(session: Session) -> bool:
    """Defensive check that the default tenant/project seed exists."""

    tenant = session.scalar(select(Tenant).where(Tenant.id == str(DEFAULT_TENANT_ID)))
    project = session.scalar(select(Project).where(Project.id == str(DEFAULT_PROJECT_ID)))
    return tenant is not None and project is not None
