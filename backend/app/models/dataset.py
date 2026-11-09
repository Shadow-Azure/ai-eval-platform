"""Dataset, metric and import models (M3-01, design 05 §4, decision D1/D10).

- 租户作用域：所有表带 ``tenant_id``，父表保留 ``UNIQUE(tenant_id, id)``，
  子表复合外键同时带租户（D1 默认租户作用域）。
- ``dataset_version`` / ``dataset_case`` 是不可变任务视图：只有 created_at，
  服务层没有任何 UPDATE 路径。
- ``metric_definition.aggregator_key`` 带 CHECK 白名单（D10：不在 DB 存并可
  执行任意代码；``formula_text`` 仅用于解释）。
"""

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

# 平台聚合器白名单（design 07 §3.1；新算法 = 新代码 + 平台版本，不是新 DB 值）。
AGGREGATOR_WHITELIST = ("ratio", "sum", "mean", "percentile", "histogram")
METRIC_DISPLAY_KINDS = ("number", "percentage", "histogram", "ms", "usd")
METRIC_DIRECTIONS = ("higher_is_better", "lower_is_better")
DATASET_VERSION_STATUSES = ("active", "archived")
DATASET_IMPORT_STATUSES = ("succeeded", "failed", "duplicate")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Dataset(Base, TimestampMixin):
    """稳定的数据集目录实体。"""

    __tablename__ = "dataset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    task_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_dataset_project_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_dataset_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_dataset_project_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_dataset_tenant_adapter", "tenant_id", "adapter_key"),
    )


class DatasetVersion(Base, CreatedAtMixin):
    """不可变版本：同 (dataset, version) 唯一，来源/修订/适配器版本防重。"""

    __tablename__ = "dataset_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    task_manifest_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    task_manifest_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    environment_image: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_dataset_version_dataset_version"),
        UniqueConstraint("tenant_id", "id", name="uq_dataset_version_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_version_dataset_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_in_clause("status", DATASET_VERSION_STATUSES), name="status"),
        Index("ix_dataset_version_tenant_dataset", "tenant_id", "dataset_id"),
    )


class DatasetCase(Base, CreatedAtMixin):
    """不可变任务视图行：导入后没有任何更新路径。"""

    __tablename__ = "dataset_case"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_version_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    case_key: Mapped[str] = mapped_column(String(200), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reference_patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_metrics_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    extra_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("dataset_version_id", "case_key", name="uq_dataset_case_version_case_key"),
        UniqueConstraint("tenant_id", "id", name="uq_dataset_case_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_case_dataset_version_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_dataset_case_version_ordinal", "dataset_version_id", "ordinal"),
    )


class DatasetImport(Base, CreatedAtMixin):
    """一次导入过程；失败时保留可诊断的错误分类报告。"""

    __tablename__ = "dataset_import"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    dataset_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="failed", server_default="failed")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_report_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="console")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_import_dataset_tenant",
            ondelete="SET NULL",
        ),
        CheckConstraint(_in_clause("status", DATASET_IMPORT_STATUSES), name="status"),
        Index("ix_dataset_import_tenant_created", "tenant_id", "created_at"),
    )


class MetricDefinition(Base, CreatedAtMixin):
    """版本化指标注册项；aggregator 只能来自平台白名单（D10）。"""

    __tablename__ = "metric_definition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    aggregator_key: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="number")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    direction: Mapped[str] = mapped_column(
        String(32), nullable=False, default="higher_is_better", server_default="higher_is_better"
    )
    denominator_policy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("dataset_id", "metric_key", "version", name="uq_metric_definition_key_version"),
        UniqueConstraint("tenant_id", "id", name="uq_metric_definition_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_metric_definition_dataset_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in_clause("aggregator_key", AGGREGATOR_WHITELIST), name="aggregator_key"),
        CheckConstraint(_in_clause("display_kind", METRIC_DISPLAY_KINDS), name="display_kind"),
        CheckConstraint(_in_clause("direction", METRIC_DIRECTIONS), name="direction"),
        Index("ix_metric_definition_tenant_key", "tenant_id", "metric_key"),
    )


class DatasetVersionMetric(Base, CreatedAtMixin):
    """固化该版本采用的指标集合。"""

    __tablename__ = "dataset_version_metric"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    dataset_version_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    metric_definition_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "metric_definition_id",
            name="uq_dataset_version_metric_pair",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_dataset_version_metric_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_version_metric_version_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_definition_id"],
            ["metric_definition.tenant_id", "metric_definition.id"],
            name="fk_dataset_version_metric_definition_tenant",
            ondelete="RESTRICT",
        ),
    )
