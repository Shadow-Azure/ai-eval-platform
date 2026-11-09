from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, json_type, new_id, tenant_fk, utcnow

TEST_OUTCOMES = ("passed", "failed", "skipped", "error")
TEST_GROUPS = ("F2P", "P2P", "OTHER")


class ScoreArtifact(Base, CreatedAtMixin):
    __tablename__ = "score_artifact"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_case_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "eval_case_attempt_id",
            "sha256",
            "parser_version",
            name="uq_score_artifact_attempt_digest_parser",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_score_artifact_attempt_tenant",
            ondelete="CASCADE",
        ),
    )


class TestCaseResult(Base, CreatedAtMixin):
    __tablename__ = "test_case_result"
    __test__ = False  # SQLAlchemy model, not a pytest test class

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_case_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    suite_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    test_name: Mapped[str] = mapped_column(String(500), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    native_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    test_group: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    expected: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    log_range_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    extra_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "eval_case_attempt_id",
            "suite_name",
            "test_name",
            "test_group",
            name="uq_test_case_result_attempt_test",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_test_case_result_attempt_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_test_case_result_attempt_outcome", "eval_case_attempt_id", "outcome"),
        CheckConstraint("outcome IN ('passed','failed','skipped','error')", name="outcome"),
        CheckConstraint("test_group IN ('F2P','P2P','OTHER')", name="test_group"),
    )


class MetricResult(Base, CreatedAtMixin):
    __tablename__ = "metric_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    metric_definition_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    value_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    numerator: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    denominator: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "eval_run_id",
            "metric_definition_id",
            "is_final",
            name="uq_metric_result_run_definition_final",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_metric_result_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_definition_id"],
            ["metric_definition.tenant_id", "metric_definition.id"],
            name="fk_metric_result_definition_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_metric_result_run_final", "eval_run_id", "is_final"),
    )
