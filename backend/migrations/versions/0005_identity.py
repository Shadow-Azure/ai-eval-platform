"""identity: user / membership / auth_session / audit_log

Revision ID: 0005_identity
Revises: 0004_datasets_metrics
Create Date: 2026-11-09

M3-05 account-password login and session access control (design 05 §3).
Passwords are stored as salted scrypt hashes and sessions persist only the
SHA-256 token hash, matching the data-model contract.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_identity"
down_revision: str | None = "0004_datasets_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("email", name=op.f("uq_user_email")),
    )

    op.create_table(
        "membership",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column(
            "role",
            sa.String(length=32),
            server_default="viewer",
            nullable=False,
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin','viewer')", name=op.f("ck_membership_role")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspace.tenant_id", "workspace.id"],
            name="fk_membership_workspace_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_membership_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_membership_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership")),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )
    op.create_index(op.f("ix_membership_tenant_id"), "membership", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_membership_user_id"), "membership", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_membership_workspace_id"), "membership", ["workspace_id"], unique=False
    )

    op.create_table(
        "auth_session",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_auth_session_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_auth_session_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_session_token_hash")),
    )
    op.create_index(op.f("ix_auth_session_tenant_id"), "auth_session", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_auth_session_user_id"), "auth_session", ["user_id"], unique=False)
    op.create_index(
        "ix_auth_session_user_expiry", "auth_session", ["user_id", "expires_at"], unique=False
    )
    op.create_index(
        "ix_auth_session_expires_at", "auth_session", ["expires_at"], unique=False
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", _JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user.id"],
            name=op.f("fk_audit_log_actor_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_audit_log_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_tenant_id"), "audit_log", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_audit_log_actor_user_id"), "audit_log", ["actor_user_id"], unique=False
    )
    op.create_index(
        "ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_tenant_created", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_tenant_id"), table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_auth_session_expires_at", table_name="auth_session")
    op.drop_index("ix_auth_session_user_expiry", table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_user_id"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_tenant_id"), table_name="auth_session")
    op.drop_table("auth_session")

    op.drop_index(op.f("ix_membership_workspace_id"), table_name="membership")
    op.drop_index(op.f("ix_membership_user_id"), table_name="membership")
    op.drop_index(op.f("ix_membership_tenant_id"), table_name="membership")
    op.drop_table("membership")

    op.drop_table("user")
