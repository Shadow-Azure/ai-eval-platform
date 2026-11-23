"""unique per-pool Runner labels and historical pool backfill

Revision ID: 0034_pool_unique_labels
Revises: 0033_template_reporting
Create Date: 2026-11-23

#414: a resource pool could dispatch with the global
``self-hosted,linux,eval`` labels because its ``labels_json`` only carried the
legacy ``{"kind": "fixed"}`` marker. GitHub matches a job to every self-hosted
runner owning its labels, so fixed jobs could run on spot runners (a spot
runner owns the fixed labels plus its own), which hid cross-pool capacity use.

This migration gives every existing pool an explicit ``labels_json.runs_on``
set that contains at least one pool-unique label (derived from the pool name,
with the row id as a collision breaker). Fixed pools therefore stop falling
back to the global label set. Runner registration is deliberately not touched:
the fixed runner must be re-registered with the new label before the
``runs_on`` preflight accepts it, which keeps an unlabeled runner from
silently receiving jobs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_pool_unique_labels"
down_revision: str | None = "0033_template_reporting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

DEFAULT_RUNS_ON = ("self-hosted", "linux", "eval")
LABEL_KEYS = ("runs_on", "runner_labels", "labels")
NON_LABEL_KEYS = frozenset({"kind"})
POOL_LABEL_PREFIX = "pool-"
MAX_LABEL_LENGTH = 64


def _normalize(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, Mapping):
        return [
            str(key).strip()
            for key, flag in value.items()
            if flag and str(key).strip()
        ]
    return []


def _resolve(labels: Any, config: Any) -> list[str] | None:
    for source in (config, labels):
        if not isinstance(source, Mapping):
            continue
        for key in LABEL_KEYS:
            values = _normalize(source.get(key))
            if values:
                return values
    if isinstance(labels, Mapping) and not any(key in labels for key in LABEL_KEYS):
        values = [
            str(key).strip()
            for key, flag in labels.items()
            if flag and str(key).strip() and str(key).strip() not in NON_LABEL_KEYS
        ]
        if values:
            return values
    return None


def _derive_pool_label(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    body = slug or "default"
    available = MAX_LABEL_LENGTH - len(POOL_LABEL_PREFIX)
    return f"{POOL_LABEL_PREFIX}{body[:available].rstrip('-') or 'default'}"


def _short_label(pool_id: str) -> str:
    return f"{POOL_LABEL_PREFIX}{str(pool_id).replace('-', '')[:12]}"[:MAX_LABEL_LENGTH]


def _dedupe(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for label in labels:
        key = label.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(label.strip())
    return result[:MAX_LABEL_LENGTH]


def _ensure_unique(row: Mapping[str, Any], labels: list[str]) -> list[str]:
    if not labels:
        labels = list(DEFAULT_RUNS_ON)
    required = {label.lower() for label in DEFAULT_RUNS_ON}
    pool_specific = [label for label in labels if label.lower() not in required]
    if not pool_specific:
        labels = [*labels, _derive_pool_label(str(row["name"] or ""))]
    return _dedupe(labels)


def upgrade() -> None:
    conn = op.get_bind()
    pool = sa.table(
        "resource_pool",
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("labels_json", _JSON),
        sa.column("instance_config_json", _JSON),
    )
    rows = [
        dict(row)
        for row in conn.execute(
            sa.select(
                pool.c.id,
                pool.c.name,
                pool.c.labels_json,
                pool.c.instance_config_json,
            )
        ).mappings()
    ]
    if not rows:
        return

    labels_by_id: dict[str, list[str]] = {}
    for row in rows:
        labels = _resolve(row["labels_json"], row["instance_config_json"]) or []
        labels_by_id[str(row["id"])] = _ensure_unique(row, labels)

    # A subset/superset pair lets one pool's jobs reach another pool's runners.
    # Break every such pair with an id-derived label before writing the rows.
    for _ in range(len(rows) + 1):
        changed = False
        for left in rows:
            left_labels = labels_by_id[str(left["id"])]
            left_set = {label.lower() for label in left_labels}
            for right in rows:
                if left["id"] == right["id"]:
                    continue
                right_labels = labels_by_id[str(right["id"])]
                right_set = {label.lower() for label in right_labels}
                if not (left_set <= right_set or right_set <= left_set):
                    continue
                if left_set == right_set:
                    labels_by_id[str(left["id"])] = _dedupe(
                        [*left_labels, _short_label(str(left["id"]))]
                    )
                    labels_by_id[str(right["id"])] = _dedupe(
                        [*right_labels, _short_label(str(right["id"]))]
                    )
                elif left_set <= right_set:
                    labels_by_id[str(left["id"])] = _dedupe(
                        [*left_labels, _short_label(str(left["id"]))]
                    )
                else:
                    labels_by_id[str(right["id"])] = _dedupe(
                        [*right_labels, _short_label(str(right["id"]))]
                    )
                changed = True
                break
            if changed:
                break
        if not changed:
            break

    for row in rows:
        labels = labels_by_id[str(row["id"])]
        labels_json = dict(row["labels_json"] or {}) if isinstance(row["labels_json"], Mapping) else {}
        config = dict(row["instance_config_json"] or {}) if isinstance(row["instance_config_json"], Mapping) else {}
        labels_json["runs_on"] = labels
        for key in LABEL_KEYS:
            if key in config:
                config[key] = labels
        conn.execute(
            sa.update(pool)
            .where(pool.c.id == row["id"])
            .values(labels_json=labels_json, instance_config_json=config)
        )


def downgrade() -> None:
    # The unique labels are now part of how runners are registered; removing
    # them would recreate the cross-pool dispatch bug. Backfill is inert on
    # re-upgrade because existing ``runs_on`` values are preserved.
    pass
