"""Issue #273: OSS dataset image tarball keys, presigning and failure codes."""

from __future__ import annotations

from app.config import Settings
from app.runner_contracts import validate_payload
from app.services.dataset_image_tarballs import (
    DOWNLOAD_BUDGET_SECONDS,
    DOWNLOAD_MARGIN_SECONDS,
    MIN_PRESIGN_TTL_SECONDS,
    PRESIGN_TTL_SECONDS,
    presign_ttl_seconds,
    tarball_key,
    task_image_tarball,
)
from app.services.object_store import internal_aliyun_endpoint, object_store_from_settings


def _settings(**overrides) -> Settings:
    values = {
        "s3_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
        "s3_bucket": "ai-eval-platform-artifacts",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "s3_region": "oss-cn-hangzhou",
    }
    values.update(overrides)
    return Settings(**values)


def test_tarball_key_is_deterministic_and_prefixed() -> None:
    image = "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    key = tarball_key(image)
    assert key.startswith("dataset-images/")
    assert key.endswith(".tar.gz")
    assert key == tarball_key(f"  {image}  ")
    assert key != tarball_key("swebench/sweb.eval.x86_64.astropy_1776_astropy-13033:latest")


def test_tarball_key_rejects_empty_image() -> None:
    try:
        tarball_key("")
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("empty image must raise")


def test_internal_endpoint_derivation() -> None:
    assert (
        internal_aliyun_endpoint("https://oss-cn-hangzhou.aliyuncs.com")
        == "https://oss-cn-hangzhou-internal.aliyuncs.com"
    )
    assert internal_aliyun_endpoint("oss-cn-hangzhou.aliyuncs.com") == (
        "https://oss-cn-hangzhou-internal.aliyuncs.com"
    )
    assert internal_aliyun_endpoint("https://minio.local:9000") is None
    assert internal_aliyun_endpoint("https://oss-cn-hangzhou-internal.aliyuncs.com") is None


def test_task_image_tarball_presigns_internal_url() -> None:
    tarball = task_image_tarball(
        _settings(), "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    )
    assert tarball is not None
    assert tarball["key"].startswith("dataset-images/")
    assert tarball["format"] == "tar.gz"
    assert "oss-cn-hangzhou-internal.aliyuncs.com" in tarball["url"]
    assert "X-Amz-Signature" in tarball["url"]
    assert f"X-Amz-Expires={PRESIGN_TTL_SECONDS}" in tarball["url"]


def test_presign_ttl_covers_the_download_budget_only() -> None:
    # M8-54 (#371): no queue time, no case timeout — just one curl budget plus
    # a scheduling/load margin. Raising this default past 30 minutes would
    # reintroduce the exposure the ADR rejects.
    assert PRESIGN_TTL_SECONDS == DOWNLOAD_BUDGET_SECONDS + DOWNLOAD_MARGIN_SECONDS
    assert DOWNLOAD_BUDGET_SECONDS == 900
    assert 900 <= PRESIGN_TTL_SECONDS <= 1800


def test_presign_ttl_override_has_a_floor() -> None:
    assert presign_ttl_seconds(None) == PRESIGN_TTL_SECONDS
    assert presign_ttl_seconds(PRESIGN_TTL_SECONDS) == PRESIGN_TTL_SECONDS
    assert presign_ttl_seconds(0) == MIN_PRESIGN_TTL_SECONDS
    assert presign_ttl_seconds(42) == MIN_PRESIGN_TTL_SECONDS


def test_settings_default_ttl_matches_download_budget() -> None:
    default = Settings.model_fields["dataset_image_tarball_ttl_seconds"].default
    assert default == PRESIGN_TTL_SECONDS


def test_task_image_tarball_is_none_without_store_or_image() -> None:
    assert task_image_tarball(_settings(s3_endpoint=None), "swebench/x:latest") is None
    assert task_image_tarball(_settings(), None) is None


def test_object_store_internal_uses_derived_endpoint() -> None:
    store = object_store_from_settings(_settings(), internal=True)
    assert store is not None
    assert "oss-cn-hangzhou-internal.aliyuncs.com" in store.endpoint


def test_failure_contract_accepts_image_unavailable() -> None:
    payload = {
        "schema_version": "1.0",
        "event_id": "evt-image-unavailable",
        "case_run_id": "case-1",
        "attempt": 1,
        "sequence": 7,
        "error_code": "image_unavailable",
        "retryable": True,
        "error_summary": "所有镜像源均不可用",
        "phase": "executing",
    }
    validate_payload("failure", payload)
