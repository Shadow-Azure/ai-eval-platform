"""Runner label selection shared by dispatch and spot provisioning.

A resource pool is only an isolation unit when every pool owns a unique
Runner label and both registration and dispatch use the exact same label set.
GitHub Actions matches a job to any self-hosted runner that owns all of the
job's labels, so a pool that falls back to the global ``self-hosted,linux,eval``
labels silently shares capacity with every other pool.

This module therefore treats ``runs_on`` as required pool data:

* a pool must carry an explicit ``runs_on`` label list (or the legacy
  ``runner_labels`` / ``labels`` aliases);
* a pool without one raises :class:`RunnerLabelsMissing` instead of falling
  back to :data:`DEFAULT_RUNS_ON`;
* only a synthetic run without a pool may use :data:`DEFAULT_RUNS_ON`.

``default_pool_runs_on`` derives the pool-unique label used by
``ensure_fixed_pool`` for the system fixed pool.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..models import ResourcePool

#: Labels the fixed Linux runner registers with. Only synthetic runs without a
#: resource pool may use them; a pool must add its own unique label.
DEFAULT_RUNS_ON: tuple[str, ...] = ("self-hosted", "linux", "eval")

#: Pool-level keys accepted for labels, in priority order. ``labels`` is the
#: legacy alias kept for pools created before ``runs_on`` was documented.
LABEL_KEYS: tuple[str, ...] = ("runs_on", "runner_labels", "labels")

#: ``ensure_fixed_pool`` used to write its provider marker into ``labels_json``
#: before pool labels were a user-facing setting. ``kind`` is metadata, not a
#: Runner label; treating it as one made fixed-pool shards wait for a runner
#: labeled ``kind``. Console-created spot pools used the same marker.
NON_LABEL_KEYS = frozenset({"kind"})

#: Bound the dispatch payload without changing registration semantics: shard
#: execution and cloud-init share this list, so a pathological pasted blob is
#: truncated identically on both sides instead of breaking the contract.
MAX_LABELS = 64

#: Prefix of the platform-derived label that makes a pool's runs_on unique.
POOL_LABEL_PREFIX = "pool-"
MAX_POOL_LABEL_LENGTH = 64


class RunnerLabelsMissing(Exception):
    """A pool exists but has no explicit ``runs_on`` label set.

    Dispatch must not fall back to the global default labels in this case: the
    job would be eligible for another pool's runners. The caller either fixes
    the pool (migration/update) or blocks the evaluation in preflight.
    """

    def __init__(self, pool: ResourcePool | None) -> None:
        self.pool_id = getattr(pool, "id", None)
        self.pool_name = getattr(pool, "name", None)
        super().__init__(
            f"资源池 {self.pool_name or self.pool_id!r} 未配置唯一的 runs_on Runner 标签"
        )


def normalize_labels(value: Any) -> list[str]:
    """Return labels from a string, list/tuple, or console label map."""

    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    # The console stores labels as ``{label: true}`` (or ``{label: "k=v"}``)
    # because its form parses a comma-separated list into a map. Treat truthy
    # keys as labels so UI-entered labels reach both registration and dispatch.
    if isinstance(value, Mapping):
        return [
            str(key).strip()
            for key, flag in value.items()
            if flag and str(key).strip()
        ]
    return []


def resolve_labels(
    labels_json: Mapping[str, Any] | None,
    instance_config_json: Mapping[str, Any] | None,
) -> list[str] | None:
    """Return explicit labels from a pool's two JSON sources, or ``None``.

    ``instance_config_json`` wins over ``labels_json`` so an operator can pin
    job labels per pool while experimenting; both use the same key set used by
    the cloud-init registration path. A console label map is accepted only
    when no documented label key is present, and its ``kind`` metadata key is
    ignored.
    """

    for source in (instance_config_json, labels_json):
        if not isinstance(source, Mapping):
            continue
        for key in LABEL_KEYS:
            labels = normalize_labels(source.get(key))
            if labels:
                return labels[:MAX_LABELS]
    if isinstance(labels_json, Mapping) and not any(
        key in labels_json for key in LABEL_KEYS
    ):
        labels = [
            str(key).strip()
            for key, flag in labels_json.items()
            if flag and str(key).strip() and str(key).strip() not in NON_LABEL_KEYS
        ]
        if labels:
            return labels[:MAX_LABELS]
    return None


def resolve_pool_labels(pool: ResourcePool) -> list[str] | None:
    """Return a pool's explicit labels, or ``None`` when it has none."""

    return resolve_labels(pool.labels_json, pool.instance_config_json)


def pool_runner_labels(pool: ResourcePool | None) -> list[str]:
    """Return the labels a shard of ``pool`` must run on.

    Only a synthetic run without a pool gets :data:`DEFAULT_RUNS_ON`. A pool
    with no explicit labels raises :class:`RunnerLabelsMissing` so the gap is
    visible at creation/preflight instead of silently dispatching to another
    pool's runners.
    """

    if pool is None:
        return list(DEFAULT_RUNS_ON)
    labels = resolve_pool_labels(pool)
    if not labels:
        raise RunnerLabelsMissing(pool)
    return labels


def derive_pool_label(pool_name: str) -> str:
    """Derive a stable, GitHub-safe unique label from a pool name."""

    slug = re.sub(r"[^a-z0-9]+", "-", (pool_name or "").strip().lower()).strip("-")
    body = slug or "default"
    available = MAX_POOL_LABEL_LENGTH - len(POOL_LABEL_PREFIX)
    return f"{POOL_LABEL_PREFIX}{body[:available].rstrip('-') or 'default'}"


def default_pool_runs_on(pool_name: str) -> list[str]:
    """Default labels for a platform-created pool: global labels + unique one."""

    labels = list(DEFAULT_RUNS_ON)
    unique = derive_pool_label(pool_name)
    if unique not in labels:
        labels.append(unique)
    return labels
