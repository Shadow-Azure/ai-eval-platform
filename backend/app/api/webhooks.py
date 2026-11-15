"""GitHub webhook receiver for the spot capacity controller (M8-34).

The controller normally polls every ``eval_capacity_check_seconds``; a
``workflow_job`` webhook makes the worker reconcile immediately instead of
waiting for the next poll. The request only marks active spot pools as due
(``next_check_at = now``): provider calls stay in the worker, so a webhook
delivery can never block on Aliyun/GitHub latency.

Security: GitHub signs the raw body with HMAC-SHA256 into
``X-Hub-Signature-256``. The secret comes from ``GITHUB_WEBHOOK_SECRET``; when
it is unset the endpoint is disabled (503) rather than open to unauthenticated
wake-ups. Signatures are compared in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResourcePool
from ..services.capacity_control import PROVISIONING_MODE_SPOT
from .dependencies import SessionDep, SettingsDep
from .errors import error_detail

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

GITHUB_EVENT_WORKFLOW_JOB = "workflow_job"
#: Job actions that change queue/running state and therefore capacity demand.
WORKFLOW_JOB_ACTIONS = frozenset({"queued", "in_progress", "completed"})


def _signature_matches(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of GitHub's ``sha256=<hex>`` header."""

    if not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _mark_active_spot_pools_due(session: Session, now: datetime) -> int:
    """Set ``next_check_at=now`` for every active spot pool; no provider call."""

    pools = list(
        session.scalars(
            select(ResourcePool).where(
                ResourcePool.status == "active",
                ResourcePool.provisioning_mode == PROVISIONING_MODE_SPOT,
            )
        )
    )
    for pool in pools:
        pool.next_check_at = now
    session.commit()
    return len(pools)


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, Any]:
    """Verify and accept a GitHub webhook delivery.

    Only ``workflow_job`` events with a capacity-relevant action wake the
    controller. Every other event is accepted and ignored so GitHub does not
    retry a delivery this deployment does not care about.
    """

    secret = settings.github_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=503,
            detail=error_detail(
                "webhook_disabled",
                "GitHub webhook is disabled: GITHUB_WEBHOOK_SECRET is not configured.",
            ),
        )
    body = await request.body()
    if not _signature_matches(secret, body, x_hub_signature_256):
        raise HTTPException(
            status_code=401,
            detail=error_detail("invalid_signature", "Invalid X-Hub-Signature-256."),
        )
    if x_github_event != GITHUB_EVENT_WORKFLOW_JOB:
        return {"status": "ignored", "reason": "unsupported_event"}
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=error_detail("invalid_payload", "Webhook payload is not valid JSON."),
        ) from exc
    action = payload.get("action") if isinstance(payload, dict) else None
    if action not in WORKFLOW_JOB_ACTIONS:
        return {"status": "ignored", "reason": "unsupported_action"}
    pools = _mark_active_spot_pools_due(session, datetime.now(UTC))
    return {
        "status": "accepted",
        "event": GITHUB_EVENT_WORKFLOW_JOB,
        "action": action,
        "pools_marked": pools,
    }
