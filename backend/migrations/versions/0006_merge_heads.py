"""merge M2 outbox and M3 dataset/model/scoring heads

Revision ID: 0006_merge_heads
Revises: 0003_outbox_cancelled, 0005_scoring
Create Date: 2026-11-09
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0006_merge_heads"
down_revision: tuple[str, ...] = ("0003_outbox_cancelled", "0005_scoring")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
