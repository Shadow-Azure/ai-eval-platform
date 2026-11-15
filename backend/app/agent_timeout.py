"""Agent wall-clock timeout bounds (M8-32 / #248).

A dataset version freezes its default; an evaluation launch may override it.
The effective value is stored on the run and handed to the runner. Rows that
predate the columns read as the platform fallback so old runs keep working.
"""

from __future__ import annotations

DEFAULT_AGENT_TIMEOUT_SECONDS = 1800  # 30 minutes
MIN_AGENT_TIMEOUT_SECONDS = 60  # 1 minute
MAX_AGENT_TIMEOUT_SECONDS = 86400  # 1440 minutes


def is_valid_agent_timeout(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_AGENT_TIMEOUT_SECONDS <= value <= MAX_AGENT_TIMEOUT_SECONDS
    )


def effective_agent_timeout(
    override: int | None, dataset_default: int | None = None
) -> int:
    """Resolve override > dataset default > platform fallback (never NULL)."""

    value = override if override is not None else dataset_default
    return int(value) if value is not None else DEFAULT_AGENT_TIMEOUT_SECONDS
