"""M8-34/M8-48 spot lifecycle configuration defaults.

The zero-resident lifecycle relies on a fast fallback poll and a bounded
reclaim grace; M8-48 adds a warm-pool floor, a longer lost grace, a young
runner guard and offline-row retention. These tests pin the defaults while
proving environment overrides still work.
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
        "EVAL_SPOT_RUNNER_MIN_IDLE_SECONDS",
        "EVAL_RUNNER_OFFLINE_RETENTION_SECONDS",
        "EVAL_RUNNER_CLEANUP_BATCH_SIZE",
        "EVAL_RUNNER_CLEANUP_INTERVAL_SECONDS",
        "GITHUB_WEBHOOK_SECRET",
    )

    settings = Settings(_env_file=None)

    assert settings.eval_capacity_check_seconds == 30
    # M8-48 (#313): 60s reclaimed healthy instances during a registration or
    # heartbeat jitter; 180s still frees a real exit within a few minutes.
    assert settings.eval_spot_runner_lost_grace_seconds == 180
    assert settings.eval_spot_runner_min_idle_seconds == 120
    assert settings.eval_runner_offline_retention_seconds == 604800
    assert settings.eval_runner_cleanup_batch_size == 200
    assert settings.eval_runner_cleanup_interval_seconds == 3600
    # Empty by default: the webhook is disabled until an operator configures it.
    assert settings.github_webhook_secret is None


def test_spot_lifecycle_settings_accept_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVAL_CAPACITY_CHECK_SECONDS", "45")
    monkeypatch.setenv("EVAL_SPOT_RUNNER_LOST_GRACE_SECONDS", "90")
    monkeypatch.setenv("EVAL_SPOT_RUNNER_MIN_IDLE_SECONDS", "60")
    monkeypatch.setenv("EVAL_RUNNER_OFFLINE_RETENTION_SECONDS", "259200")
    monkeypatch.setenv("EVAL_RUNNER_CLEANUP_BATCH_SIZE", "50")
    monkeypatch.setenv("EVAL_RUNNER_CLEANUP_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook-secret")

    settings = Settings(_env_file=None)

    assert settings.eval_capacity_check_seconds == 45
    assert settings.eval_spot_runner_lost_grace_seconds == 90
    assert settings.eval_spot_runner_min_idle_seconds == 60
    assert settings.eval_runner_offline_retention_seconds == 259200
    assert settings.eval_runner_cleanup_batch_size == 50
    assert settings.eval_runner_cleanup_interval_seconds == 600
    assert settings.github_webhook_secret == "webhook-secret"
