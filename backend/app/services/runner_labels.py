"""Runner label selection shared by dispatch and spot provisioning.

A spot pool may register its ephemeral runners with custom labels; shard jobs
must target the same labels, otherwise a custom-label pool can register
runners that never receive a job. Both the cloud-init registration path and
the shard dispatch/manifest path therefore resolve labels through this module
instead of keeping two private copies of the rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..models import ResourcePool

#: Labels the fixed Linux runner registers with and the shard workflow defaults
#: to. Synthetic evaluations that have no pool also use them.
DEFAULT_RUNS_ON: tuple[str, ...] = ("self-hosted", "linux", "eval")

#: Pool-level keys accepted for labels, in priority order. ``labels`` is the
#: legacy alias kept for pools created before ``runs_on`` was documented.
LABEL_KEYS: tuple[str, ...] = ("runs_on", "runner_labels", "labels")

#: GitHub Actions caps a job's label set in practice; keep dispatch payloads
#: small and reject accidental pasted blobs by truncating here.
MAX_LABELS = 16


def _labels(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def pool_runner_labels(
    pool: ResourcePool | None,
    *,
    fallback: Iterable[str] = DEFAULT_RUNS_ON,
) -> list[str]:
    """Return the labels a shard of ``pool`` must run on.

    ``instance_config_json`` wins over ``labels_json`` so an operator can pin
    job labels per pool while experimenting; both use the same key set used by
    the cloud-init registration path.
    """

    if pool is not None:
        for source in (pool.instance_config_json, pool.labels_json):
            if not isinstance(source, Mapping):
                continue
            for key in LABEL_KEYS:
                labels = _labels(source.get(key))
                if labels:
                    return labels[:MAX_LABELS]
    labels = [str(item).strip() for item in fallback if str(item).strip()]
    return labels[:MAX_LABELS] or list(DEFAULT_RUNS_ON)
