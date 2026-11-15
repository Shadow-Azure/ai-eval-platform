"""M8-34 GitHub ``workflow_job`` webhook.

The endpoint must authenticate the raw body, wake only active spot pools and
stay disabled until an operator configures ``GITHUB_WEBHOOK_SECRET``. It must
never perform provider work inside the request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.models import ResourcePool
from app.seed import ensure_default_scope
from sqlalchemy.orm import Session

WEBHOOK_SECRET = "github-webhook-test-secret"
WEBHOOK_PATH = "/api/v1/webhooks/github"
FAR_FUTURE = datetime(2030, 1, 1, 0, 0, 0)


def _payload(action: str = "queued") -> bytes:
    return json.dumps(
        {
            "action": action,
            "workflow_job": {
                "id": 12345,
                "name": "execute",
                "labels": ["self-hosted", "linux", "eval"],
                "status": action,
            },
            "repository": {"full_name": "Shadow-Azure/ai-eval-platform"},
        },
        separators=(",", ":"),
    ).encode()


def _signature(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(client, body: bytes, *, event: str = "workflow_job", signature: str | None = None):
    return client.post(
        WEBHOOK_PATH,
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature if signature is not None else _signature(body),
        },
    )


@pytest.fixture
def pools(session: Session) -> dict[str, ResourcePool]:
    tenant, _workspace, project = ensure_default_scope(session)
    spot = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name="spot-webhook",
        provider_key="fake",
        provisioning_mode="spot",
        status="active",
        next_check_at=FAR_FUTURE,
    )
    fixed = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name="fixed-webhook",
        provider_key="fake",
        provisioning_mode="fixed",
        status="active",
        next_check_at=FAR_FUTURE,
    )
    disabled = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name="disabled-spot-webhook",
        provider_key="fake",
        provisioning_mode="spot",
        status="disabled",
        next_check_at=FAR_FUTURE,
    )
    session.add_all([spot, fixed, disabled])
    session.commit()
    return {"spot": spot, "fixed": fixed, "disabled": disabled}


def test_valid_workflow_job_wakes_active_spot_pools(
    client, session: Session, configure_settings, pools: dict[str, ResourcePool]
) -> None:
    configure_settings(GITHUB_WEBHOOK_SECRET=WEBHOOK_SECRET)
    body = _payload("queued")
    now_naive = datetime.now(UTC).replace(tzinfo=None)

    response = _post(client, body)

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["pools_marked"] == 1
    session.expire_all()
    spot = session.get(ResourcePool, pools["spot"].id)
    fixed = session.get(ResourcePool, pools["fixed"].id)
    disabled = session.get(ResourcePool, pools["disabled"].id)
    assert spot is not None and spot.next_check_at is not None
    assert spot.next_check_at <= now_naive + timedelta(seconds=5)
    # Non-spot and disabled pools are never woken by a workflow job.
    assert fixed is not None and fixed.next_check_at == FAR_FUTURE
    assert disabled is not None and disabled.next_check_at == FAR_FUTURE


def test_invalid_signature_is_rejected(
    client, session: Session, configure_settings, pools: dict[str, ResourcePool]
) -> None:
    configure_settings(GITHUB_WEBHOOK_SECRET=WEBHOOK_SECRET)
    body = _payload("in_progress")

    response = _post(client, body, signature="sha256=" + "0" * 64)

    assert response.status_code == 401
    session.expire_all()
    spot = session.get(ResourcePool, pools["spot"].id)
    assert spot is not None and spot.next_check_at == FAR_FUTURE


def test_unset_secret_disables_the_endpoint(
    client, configure_settings, pools: dict[str, ResourcePool]
) -> None:
    configure_settings(GITHUB_WEBHOOK_SECRET="")

    response = _post(client, _payload("completed"))

    assert response.status_code == 503


def test_other_events_are_accepted_and_ignored(
    client, session: Session, configure_settings, pools: dict[str, ResourcePool]
) -> None:
    configure_settings(GITHUB_WEBHOOK_SECRET=WEBHOOK_SECRET)
    body = _payload("queued")

    response = _post(client, body, event="push")

    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    session.expire_all()
    spot = session.get(ResourcePool, pools["spot"].id)
    assert spot is not None and spot.next_check_at == FAR_FUTURE
