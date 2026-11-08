from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, TimestampMixin, json_type, new_id, tenant_fk

DATASET_VERSION_STATUSES = ("importing", "ready", "failed", "archived")
DATASET_IMPORT_STATUSES = ("pending", "running", "completed", "failed")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Dataset(Base, TimestampMixin):
    __tablename__ = "dataset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    task_family: Mapped[str] = mapped_column(String(64), nullable=False, default="code_patch")
    environment_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="linux_docker")
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "slug", name="uq_dataset_project_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_dataset_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_dataset_project_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_dataset_tenant_created", "tenant_id", "created_at"),
    )


class DatasetVersion(Base, TimestampMixin):
    __tablename__ = "dataset_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(200), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="importing", server_default="importing"
    )
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_manifest_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    task_manifest_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "source_revision",
            "adapter_version",
            name="uq_dataset_version_source",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_dataset_version_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_version_dataset_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_dataset_version_status", "dataset_id", "status"),
        CheckConstraint(_in_clause("status", DATASET_VERSION_STATUSES), name="status"),
    )


class DatasetCase(Base, CreatedAtMixin):
    __tablename__ = "dataset_case"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_version_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    case_key: Mapped[str] = mapped_column(String(200), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    problem_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_patch_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    test_patch_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expected_metrics_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    extra_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "case_key", name="uq_dataset_case_version_case_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_case_version_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_dataset_case_version_ordinal", "dataset_version_id", "ordinal"),
    )


class DatasetImport(Base, TimestampMixin):
    __tablename__ = "dataset_import"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    source_config_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_report_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_import_dataset_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_dataset_import_status", "status", "created_at"),
        CheckConstraint(_in_clause("status", DATASET_IMPORT_STATUSES), name="status"),
    )


class MetricDefinition(Base, CreatedAtMixin):
    __tablename__ = "metric_definition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    aggregator_key: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    display_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="number")
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="count")
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="higher")
    denominator_policy: Mapped[str] = mapped_column(
        String(64), nullable=False, default="selected_total"
    )
    config_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "metric_key", "version", name="uq_metric_definition_key_version"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_metric_definition_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_metric_definition_dataset_tenant",
            ondelete="CASCADE",
        ),
    )


class DatasetVersionMetric(Base, CreatedAtMixin):
    __tablename__ = "dataset_version_metric"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_version_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    metric_definition_id: Mapped[str] = mapped_column(String(36), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "metric_definition_id",
            name="uq_dataset_version_metric_pair",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_version_metric_version_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_definition_id"],
            ["metric_definition.tenant_id", "metric_definition.id"],
            name="fk_dataset_version_metric_definition_tenant",
            ondelete="CASCADE",
        ),
    )
