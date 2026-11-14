"""M8-26: administrator-editable public image source settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..services import identity
from ..services.dataset_images import (
    clear_override,
    parse_source_records,
    save_override,
    sources_view,
)
from .dependencies import require_admin, require_session
from .errors import error_detail

router = APIRouter(tags=["settings"])


class ImageSourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    registry: str = Field(min_length=1, max_length=300)
    prefix: str = Field(default="", max_length=200)
    enabled: bool = True


class ReplaceImageSourcesRequest(BaseModel):
    sources: list[ImageSourceInput] = Field(min_length=1, max_length=20)


def _view(session: Session, settings: Settings) -> dict:
    return sources_view(session, settings)


@router.get("/api/v1/settings/dataset-image-sources")
def get_dataset_image_sources(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _context: identity.AuthContext = Depends(require_session),
) -> dict:
    return _view(session, settings)


@router.put("/api/v1/settings/dataset-image-sources")
def replace_dataset_image_sources(
    payload: ReplaceImageSourcesRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    try:
        sources = parse_source_records([item.model_dump() for item in payload.sources])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail("invalid_dataset_image_sources", str(exc)),
        ) from exc
    save_override(session, sources, updated_by=context.user.email)
    session.commit()
    return _view(session, settings)


@router.delete("/api/v1/settings/dataset-image-sources")
def reset_dataset_image_sources(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    context: identity.AuthContext = Depends(require_admin),
) -> dict:
    clear_override(session)
    session.commit()
    return _view(session, settings)
