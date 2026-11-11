"""dataset import file upload, isolated validation and commit phases

Revision ID: 0020_dataset_import_upload
Revises: 0019_trace_capture_status
Create Date: 2026-11-11

M5-15: the import console must upload a JSON/JSONL source file, validate it
in an isolated worker with a validation report, and only then materialise an
immutable dataset version. The dataset_import row therefore grows file
metadata, a durable validation report, progress counters and a heartbeat
column so a crashed worker can be detected instead of leaving the wizard
spinning forever. The status check gains the async phases.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_dataset_import_upload"
down_revision: str | None = "0019_trace_capture_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CONSTRAINT = "ck_dataset_import_status"
STATUS_VALUES = (
    "'queued','validating','validated','committing','succeeded','failed','duplicate','cancelled'"
)
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("dataset_import") as batch:
        batch.add_column(sa.Column("dataset_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("file_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("file_size", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("file_digest", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("storage_key", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("execution_image_digest", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("scoring_image_digest", sa.String(length=128), nullable=True))
        batch.add_column(
            sa.Column(
                "validation_report_json",
                JSON_TYPE,
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint(op.f(STATUS_CONSTRAINT), type_="check")
        batch.create_check_constraint(op.f(STATUS_CONSTRAINT), f"status IN ({STATUS_VALUES})")


def downgrade() -> None:
    # Older rows in async states cannot be represented by the legacy enum.
    op.execute(
        "UPDATE dataset_import SET status = 'failed' "
        "WHERE status IN ('queued','validating','validated','committing','cancelled')"
    )
    with op.batch_alter_table("dataset_import") as batch:
        batch.drop_constraint(op.f(STATUS_CONSTRAINT), type_="check")
        batch.create_check_constraint(
            op.f(STATUS_CONSTRAINT), "status IN ('succeeded','failed','duplicate')"
        )
        batch.drop_column("status_updated_at")
        batch.drop_column("processed_count")
        batch.drop_column("validation_report_json")
        batch.drop_column("storage_key")
        batch.drop_column("scoring_image_digest")
        batch.drop_column("execution_image_digest")
        batch.drop_column("file_digest")
        batch.drop_column("file_size")
        batch.drop_column("file_name")
        batch.drop_column("dataset_name")
