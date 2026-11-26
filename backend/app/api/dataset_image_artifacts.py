"""M8 (#290/#436): dataset runtime image tarball readiness API.

The launch wizard polls one preparation's per-image tarball state and asks the
backend to enqueue missing images; administrators can retry failed ones.
Responses are JSON-safe (no OSS credentials or signed URLs); the worker plane
owns the Docker/OSS write path. Each image row carries ``next_attempt_at`` so the
wizard knows whether a visible failure is still inside the worker's automatic
retry window or terminal, plus the #436 queued/processing split and the worker
heartbeat. GET is read-only; only the explicit ensure/retry POSTs reap a stale
queue, so polling cannot write.

M10 (#429/#436): machine callers may use a service token. ``GET`` accepts
``eval:read``; ``ensure``/``retry`` accept ``eval:launch``. A service token can
only see preparations created by itself (``created_by == svc:<name>``), so the
readiness URL returned in a 409/503 is usable with the same credential without
exposing other users' launches.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..defaults import DEFAULT_TENANT_ID
from ..models import EvalPreparation
from ..services import dataset_image_artifacts as artifact_service
from ..services.preparation_ttl import renew_image_preparation_if_progressing
from .dependencies import Actor, require_session_or_service
from .errors import error_detail

router = APIRouter(tags=["dataset-images"])


class EnsureArtifactsRequest(BaseModel):
    preparation_id: str = Field(min_length=1, max_length=36)
    # True performs a fresh HEAD check before enqueueing; false only enqueues
    # the state already persisted by prepare.
    recheck: bool = False


class RetryArtifactsRequest(BaseModel):
    preparation_id: str = Field(min_length=1, max_length=36)
    # Omitted means every non-ready image frozen on the preparation.
    images: list[str] | None = Field(default=None, max_length=500)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_preparation(
    session: Session, preparation_id: str, actor: Actor
) -> EvalPreparation:
    """Ownership + TTL gate shared by all three endpoints (404 then 409).

    The actor label is the console email for a session and ``svc:<name>`` for a
    service token, matching ``preparation.created_by`` for both launch paths.
    """

    preparation = session.get(EvalPreparation, preparation_id)
    if (
        preparation is None
        or preparation.tenant_id != str(DEFAULT_TENANT_ID)
        or preparation.created_by != actor.label[:128]
    ):
        raise HTTPException(
            status_code=404,
            detail=error_detail("preparation_not_found", "准备快照不存在或不属于当前用户"),
        )
    if preparation.status == "expired" or _as_utc(preparation.expires_at) <= datetime.now(UTC):
        if preparation.status != "expired":
            preparation.status = "expired"
            session.commit()
        raise HTTPException(
            status_code=409,
            detail=error_detail("preparation_expired", "准备快照已过期，请重新确认配置"),
        )
    return preparation


def _readiness(
    session: Session,
    preparation: EvalPreparation,
    settings: Settings,
    *,
    recheck: bool = False,
    enqueue: bool = False,
) -> dict:
    refs = artifact_service.snapshot_image_refs(preparation)
    result = artifact_service.readiness(
        session, refs or [], settings, recheck=recheck, enqueue=enqueue
    )
    # The explicit ensure POST may renew; the polling GET never writes.
    if enqueue:
        renew_image_preparation_if_progressing(preparation, settings, result)
    # Machine callers get the (possibly renewed) snapshot expiry in the same
    # response as the readiness they are polling.
    result["preparation_id"] = preparation.id
    result["expires_at"] = _iso(preparation.expires_at)
    return result


@router.get("/api/v1/dataset-images/artifacts")
def get_dataset_image_artifacts(
    preparation_id: Annotated[str, Query(min_length=1, max_length=36)],
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    actor: Actor = Depends(require_session_or_service("eval:read")),
) -> dict:
    preparation = _load_preparation(session, preparation_id, actor)
    # Read-only by design (#436): an unstarted worker is reported via the
    # ``worker`` block, not by writing a failed status on every 3s poll.
    return _readiness(session, preparation, settings)


@router.post("/api/v1/dataset-images/artifacts/ensure")
def ensure_dataset_image_artifacts(
    payload: EnsureArtifactsRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    actor: Actor = Depends(
        require_session_or_service("eval:launch", session_admin=False)
    ),
) -> dict:
    preparation = _load_preparation(session, payload.preparation_id, actor)
    artifact_service.reap_stale_preparing(session, settings)
    result = _readiness(
        session, preparation, settings, recheck=payload.recheck, enqueue=True
    )
    session.commit()
    return result


@router.post("/api/v1/dataset-images/artifacts/retry")
def retry_dataset_image_artifacts(
    payload: RetryArtifactsRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    actor: Actor = Depends(require_session_or_service("eval:launch")),
) -> dict:
    preparation = _load_preparation(session, payload.preparation_id, actor)
    result = artifact_service.retry_preparation_images(
        session, preparation, settings, images=payload.images
    )
    session.commit()
    return result
