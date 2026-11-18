"""Agent wall-clock timeout bounds and execution timeout hierarchy (M8-32/M8-43).

A dataset version freezes its default Agent budget; an evaluation launch may
override it. The effective value is stored on the run and handed to the runner.
Rows that predate the columns read as the platform fallback so old runs keep
working.

Issue #304: the GitHub ``execute`` job used a static 60-minute timeout while a
run could freeze a 60-minute Agent budget. The job therefore died first and the
runner never uploaded a trace or sent a terminal callback. The three budgets
must stay strictly ordered:

    dispatch TTL > job timeout > case timeout > agent timeout

``case`` leaves time for scoring and artifact upload after the Agent stops;
``job`` leaves time for the runner's startup and terminal callback. The platform
derives ``case`` and ``job`` from the frozen Agent budget instead of letting a
launch request produce equal or inverted values.
"""

from __future__ import annotations

import math

DEFAULT_AGENT_TIMEOUT_SECONDS = 1800  # 30 minutes
MIN_AGENT_TIMEOUT_SECONDS = 60  # 1 minute
MAX_AGENT_TIMEOUT_SECONDS = 86400  # 1440 minutes

# Scoring, upload and terminal callback headroom after the Agent stops. The
# issue's reference set is agent=60m, case=80m, job=90m, dispatch TTL=100m.
CASE_TIMEOUT_HEADROOM_SECONDS = 1200  # 20 minutes
JOB_TIMEOUT_HEADROOM_SECONDS = 600  # 10 minutes
DISPATCH_TIMEOUT_HEADROOM_SECONDS = 600  # 10 minutes
# GitHub self-hosted jobs may run for up to five days; keep the workflow value
# inside that envelope so a dispatch can never produce an invalid job timeout.
MAX_JOB_TIMEOUT_MINUTES = 5 * 24 * 60


class TimeoutHierarchyError(ValueError):
    """An Agent budget cannot be given a valid case/job timeout hierarchy."""


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


def minimum_case_timeout(agent_timeout_seconds: int) -> int:
    """Shortest case budget that still leaves post-Agent headroom."""

    return int(agent_timeout_seconds) + CASE_TIMEOUT_HEADROOM_SECONDS


def effective_case_timeout(
    *,
    agent_timeout_seconds: int,
    requested_case_timeout_seconds: int,
    max_case_timeout_seconds: int,
) -> int:
    """Return a case budget strictly above the Agent budget, or raise.

    The requested value wins when it already carries enough headroom. A shorter
    request is raised to the minimum instead of silently letting the GitHub job
    or the platform reaper kill an Agent that is still running. If the minimum
    itself exceeds the platform cap the combination is rejected: it has no
    valid hierarchy.
    """

    required = minimum_case_timeout(agent_timeout_seconds)
    if required > max_case_timeout_seconds:
        raise TimeoutHierarchyError(
            "agent_timeout_seconds 过大：单用例超时至少需要 "
            f"{required} 秒，超过平台上限 {max_case_timeout_seconds} 秒"
        )
    return max(int(requested_case_timeout_seconds), required)


def job_timeout_seconds(case_timeout_seconds: int) -> int:
    """GitHub execute job budget for one case (strictly above the case)."""

    return int(case_timeout_seconds) + JOB_TIMEOUT_HEADROOM_SECONDS


def job_timeout_minutes(case_timeout_seconds: int) -> int:
    """Ceil the job budget to whole minutes for ``timeout-minutes``."""

    minutes = math.ceil(job_timeout_seconds(case_timeout_seconds) / 60)
    return max(1, min(minutes, MAX_JOB_TIMEOUT_MINUTES))


def dispatch_timeout_seconds(case_timeout_seconds: int) -> int:
    """Manifest deadline / dispatch TTL that outlives the execute job."""

    return job_timeout_seconds(case_timeout_seconds) + DISPATCH_TIMEOUT_HEADROOM_SECONDS
