from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, json_type


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
