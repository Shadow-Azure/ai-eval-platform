"""M8-34 spot lifecycle configuration defaults.

The zero-resident lifecycle relies on a fast fallback poll and a short reclaim
grace; these tests pin the defaults while proving environment overrides still
work.
"""

from __future__ import annotations

import pytest
from app.config import Settings


def _unset(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_spot_lifecycle_defaults_are_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset(
        monkeypatch,
        "EVAL_CAPACITY_CHECK_SECONDS",
        "EVAL_SPOT_RUNNER_LOST_GRACE_SECONDS",
        "GITHUB_WEBHOOK_SECRET",
    )

    settings = Settings(_env_file=None)

    assert settings.eval_capacity_check_seconds == 30
    assert settings.eval_spot_runner_lost_grace_seconds == 60
    # Empty by default: the webhook is disabled until an operator configures it.
    assert settings.github_webhook_secret is None


def test_spot_lifecycle_settings_accept_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVAL_CAPACITY_CHECK_SECONDS", "45")
    monkeypatch.setenv("EVAL_SPOT_RUNNER_LOST_GRACE_SECONDS", "90")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook-secret")

    settings = Settings(_env_file=None)

    assert settings.eval_capacity_check_seconds == 45
    assert settings.eval_spot_runner_lost_grace_seconds == 90
    assert settings.github_webhook_secret == "webhook-secret"
