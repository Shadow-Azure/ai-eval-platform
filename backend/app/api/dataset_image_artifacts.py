"""M8 (#290): dataset runtime image tarball readiness API.

The launch wizard polls one preparation's per-image tarball state and asks the
backend to enqueue missing images; administrators can retry failed ones.
Responses are JSON-safe (no OSS credentials or signed URLs); the worker plane
owns the Docker/OSS write path. Each image row carries ``next_attempt_at`` so the
wizard knows whether a visible failure is still inside the worker's automatic
retry window or terminal, plus the #436 queued/processing split and the worker
heartbeat. GET is read-only; only the explicit ensure POST reaps a stale queue,
so polling cannot write.
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
from ..services import identity
from .dependencies import require_admin, require_session
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


def _load_preparation(
    session: Session, preparation_id: str, context: identity.AuthContext
) -> EvalPreparation:
    """Ownership + TTL gate shared by all three endpoints (404 then 409)."""

    preparation = session.get(EvalPreparation, preparation_id)
    if (
        preparation is None
        or preparation.tenant_id != str(DEFAULT_TENANT_ID)
        or preparation.created_by != context.user.email[:128]
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
    return artifact_service.readiness(
        session, refs or [], settings, recheck=recheck, enqueue=enqueue
    )


@router.get("/api/v1/dataset-images/artifacts")
def get_dataset_image_artifacts(
    preparation_id: Annotated[str, Query(min_length=1, max_length=36)],
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_session),
) -> dict:
    preparation = _load_preparation(session, preparation_id, context)
    # Read-only by design (#436): an unstarted worker is reported via the
    # ``worker`` block, not by writing a failed status on every 3s poll.
    return _readiness(session, preparation, settings)


@router.post("/api/v1/dataset-images/artifacts/ensure")
def ensure_dataset_image_artifacts(
    payload: EnsureArtifactsRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_session),
) -> dict:
    preparation = _load_preparation(session, payload.preparation_id, context)
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
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    preparation = _load_preparation(session, payload.preparation_id, context)
    result = artifact_service.retry_preparation_images(
        session, preparation, settings, images=payload.images
    )
    session.commit()
    return result
