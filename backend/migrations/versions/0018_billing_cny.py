"""billing amounts move from USD to CNY

Revision ID: 0018_billing_cny
Revises: 0014_budget_pause_resume
Create Date: 2026-11-10

M5-10: the platform's billing currency is CNY everywhere, so the fee columns
lose their USD suffix and keep the same values expressed in CNY. Columns are
renamed (never reinterpreted silently) and existing rows are converted once at
the 7.2 rate the Aliyun spot provisioner used before this change; after the
migration no runtime conversion remains.

Covered: resource-pool spot price/threshold, model usage cost, evaluation
budget/spend and the configured model token rates, plus the metric display
kind that marks a currency value. The widened old value ``usd`` stays allowed
by the check constraint so the table recreate can copy pre-existing rows on
SQLite; rows are rewritten to ``cny`` immediately afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_billing_cny"
down_revision: str | None = "0017_eval_preparations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
LEGACY_RATE = Decimal("7.2")
DISPLAY_CONSTRAINT = "ck_metric_definition_display_kind"
DISPLAY_VALUES_CNY = "'number','percentage','histogram','ms','cny','usd'"
DISPLAY_VALUES_USD = "'number','percentage','histogram','ms','usd'"

# (table, old column, new column, decimal places of the column)
RENAMES = (
    ("resource_pool", "price_threshold_usd", "price_threshold_cny", "0.0001"),
    ("resource_pool", "price_usd", "price_cny", "0.0001"),
    ("model_usage", "cost_usd", "cost_cny", "0.000001"),
    ("eval_run", "model_budget_usd", "model_budget_cny", "0.000001"),
    ("eval_run", "budget_spent_usd", "budget_spent_cny", "0.000001"),
)


def _convert_column(table: str, column: str, places: str, factor: Decimal) -> None:
    """Multiply every non-null value once, in Python for dialect portability."""

    bind = op.get_bind()
    meta = sa.MetaData()
    rows = sa.Table(
        table,
        meta,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(column, sa.Numeric(precision=18, scale=6)),
    )
    for row_id, value in bind.execute(sa.select(rows.c.id, rows.c[column])).all():
        if value is None:
            continue
        converted = (Decimal(str(value)) * factor).quantize(Decimal(places))
        bind.execute(rows.update().where(rows.c.id == row_id).values({column: converted}))


def _convert_pricing(factor: Decimal) -> None:
    """Convert the configured CNY-per-1M-token rates; they are plain JSON."""

    bind = op.get_bind()
    meta = sa.MetaData()
    rows = sa.Table(
        "model_config",
        meta,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("pricing_json", JSON_TYPE),
    )
    for row_id, pricing in bind.execute(
        sa.select(rows.c.id, rows.c.pricing_json)
    ).all():
        if not isinstance(pricing, dict):
            continue
        converted = dict(pricing)
        changed = False
        for key in ("input_per_1m", "output_per_1m"):
            value = converted.get(key)
            if isinstance(value, (int, float)):
                converted[key] = float(
                    (Decimal(str(value)) * factor).quantize(Decimal("0.000001"))
                )
                changed = True
        if changed:
            bind.execute(
                rows.update().where(rows.c.id == row_id).values(pricing_json=converted)
            )


def _replace_display_constraint(values: str) -> None:
    with op.batch_alter_table("metric_definition") as batch:
        batch.drop_constraint(op.f(DISPLAY_CONSTRAINT), type_="check")
        batch.create_check_constraint(
            op.f(DISPLAY_CONSTRAINT),
            f"display_kind IN ({values})",
        )


def upgrade() -> None:
    for table, old, new, places in RENAMES:
        op.alter_column(table, old, new_column_name=new)
        _convert_column(table, new, places, LEGACY_RATE)

    _convert_pricing(LEGACY_RATE)

    _replace_display_constraint(DISPLAY_VALUES_CNY)
    op.execute("UPDATE metric_definition SET display_kind = 'cny' WHERE display_kind = 'usd'")


def downgrade() -> None:
    op.execute("UPDATE metric_definition SET display_kind = 'usd' WHERE display_kind = 'cny'")
    _replace_display_constraint(DISPLAY_VALUES_USD)

    _convert_pricing(Decimal(1) / LEGACY_RATE)

    for table, old, new, places in RENAMES:
        _convert_column(table, new, places, Decimal(1) / LEGACY_RATE)
        op.alter_column(table, new, new_column_name=old)
