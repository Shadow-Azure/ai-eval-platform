from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, json_type, new_id

DEPLOYMENT_STATUSES = ("deploying", "succeeded", "failed", "rolled_back")


class Deployment(Base):
    """One delivery pipeline run recorded by the deploy workflow (M2-02).

    The table was introduced by migration ``0002_deployment`` and is written by
    ``backend/migrations/record_deployment.py``; M5-18 adds this read model and
    the console API on top of the same rows. ``detail_json`` carries the
    per-stage timeline, image references and the GitHub Actions run URL.
    """

    __tablename__ = "deployment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(200), nullable=False)
    migration_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rollback_of_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("deployment.id", ondelete="SET NULL"),
        nullable=True,
    )
    detail_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)


class ServiceHeartbeat(Base):
    """Last liveness report from one platform service (M3-06).

    The dispatch worker writes a row on every poll so the administrator
    operations page can tell a running worker from a stopped one without
    reading container-local files.
    """

    __tablename__ = "service_heartbeat"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
