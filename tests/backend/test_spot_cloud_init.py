"""M4-04 cloud-init for ephemeral spot runners.

The JIT blob must reach the instance exactly once and the cleanup service must
remove the one-time material; these tests parse the cloud-config rather than
asserting on a string blob.
"""

from __future__ import annotations

import base64

import yaml
from app.services.provisioning.spot_cloud_init import render_cloud_init


def _files(document: dict) -> dict[str, str]:
    return {
        item["path"]: base64.b64decode(item["content"]).decode()
        for item in document.get("write_files", [])
    }


def test_cloud_init_is_valid_yaml_and_bounded() -> None:
    text = render_cloud_init(
        platform_base_url="http://platform.example",
        runner_name="spot-abc",
        resource_pool_id="pool-1",
        labels=["self-hosted", "linux", "eval"],
        encoded_jit="jit-head+jit-body/jit-tail==",
        heartbeat_token="heartbeat-token",
        runner_tarball_url="https://oss.example/actions-runner.tar.gz",
    )
    assert len(text.encode()) < 16 * 1024  # Aliyun UserData limit
    document = yaml.safe_load(text)
    assert document["runcmd"] == [["bash", "/etc/eval/bootstrap.sh"]]
    files = _files(document)
    assert set(files) == {
        "/etc/eval/heartbeat.sh",
        "/etc/eval/bootstrap.sh",
        "/etc/eval/cleanup.sh",
    }


def test_bootstrap_contains_jit_and_installs_runner_once() -> None:
    document = yaml.safe_load(
        render_cloud_init(
            platform_base_url="http://platform.example",
            runner_name="spot-abc",
            resource_pool_id="pool-1",
            labels=["self-hosted"],
            encoded_jit="single-use-jit-value",
            heartbeat_token="heartbeat-token",
            runner_tarball_url="https://oss.example/actions-runner.tar.gz",
        )
    )
    bootstrap = _files(document)["/etc/eval/bootstrap.sh"]
    assert "single-use-jit-value" in bootstrap
    assert "https://oss.example/actions-runner.tar.gz" in bootstrap
    assert "run.sh --jitconfig \"$JIT_CONFIG\"" in bootstrap
    assert "systemctl enable --now github-runner.service" in bootstrap
    # The eval-shard workflow runs Node on the runner host; without it the
    # prepare job dies with `node: command not found` and cases never start.
    assert "npmmirror.com/mirrors/node" in bootstrap
    assert "node-v${NODE_VERSION}-linux-x64.tar.xz" in bootstrap
    assert "node --version" in bootstrap
    # GitHub downloads from Aliyun are slow; allow retries before giving up.
    assert "--max-time 600 --retry 3" in bootstrap
    # An ephemeral runner exits after one job; the heartbeat must stop with it
    # or the platform keeps seeing a phantom online runner.
    assert "BindsTo=github-runner.service" in bootstrap
    assert bootstrap.index("systemctl enable --now github-runner.service") < bootstrap.index(
        "systemctl enable --now eval-heartbeat.service"
    )


def test_cleanup_removes_jit_env_and_cloud_init_user_data() -> None:
    document = yaml.safe_load(
        render_cloud_init(
            platform_base_url="http://platform.example",
            runner_name="spot-abc",
            resource_pool_id="pool-1",
            labels=["self-hosted"],
            encoded_jit="single-use-jit-value",
            heartbeat_token="heartbeat-token",
            runner_tarball_url="https://oss.example/actions-runner.tar.gz",
        )
    )
    cleanup = _files(document)["/etc/eval/cleanup.sh"]
    assert "shred -u /etc/eval/runner-jit.env" in cleanup
    assert "/var/lib/cloud/instance/user-data.txt" in cleanup


def test_heartbeat_payload_includes_pool_binding() -> None:
    document = yaml.safe_load(
        render_cloud_init(
            platform_base_url="http://platform.example",
            runner_name="spot-abc",
            resource_pool_id="pool-1",
            labels=["self-hosted", "linux"],
            encoded_jit="jit",
            heartbeat_token=None,
            runner_tarball_url="https://oss.example/actions-runner.tar.gz",
        )
    )
    heartbeat = _files(document)["/etc/eval/heartbeat.sh"]
    assert '"resource_pool_id":"%s"' in heartbeat
    assert "pool-1" in heartbeat
    assert "heartbeat-token" not in heartbeat


def test_bootstrap_carries_the_runner_token_for_the_platform_proxy() -> None:
    document = yaml.safe_load(
        render_cloud_init(
            platform_base_url="http://10.0.0.5:8000",
            runner_name="spot-abc",
            resource_pool_id="pool-1",
            labels=["self-hosted"],
            encoded_jit="jit",
            heartbeat_token="heartbeat-token",
            runner_tarball_url="http://10.0.0.5:8000/runner/v1/runner-tarball/2.338.2.tar.gz",
            runner_tarball_token="heartbeat-token",
        )
    )
    bootstrap = _files(document)["/etc/eval/bootstrap.sh"]
    assert "http://10.0.0.5:8000/runner/v1/runner-tarball/2.338.2.tar.gz" in bootstrap
    assert "RUNNER_TARBALL_TOKEN=heartbeat-token" in bootstrap
    assert 'authorization: Bearer $RUNNER_TARBALL_TOKEN' in bootstrap


def test_bootstrap_sends_no_token_to_an_external_mirror() -> None:
    document = yaml.safe_load(
        render_cloud_init(
            platform_base_url="http://10.0.0.5:8000",
            runner_name="spot-abc",
            resource_pool_id="pool-1",
            labels=["self-hosted"],
            encoded_jit="jit",
            heartbeat_token="heartbeat-token",
            runner_tarball_url="https://mirror.example/runner.tar.gz",
        )
    )
    bootstrap = _files(document)["/etc/eval/bootstrap.sh"]
    assert "https://mirror.example/runner.tar.gz" in bootstrap
    # Empty token selects the unauthenticated curl branch; the runner token
    # never reaches this script for an external mirror.
    assert "RUNNER_TARBALL_TOKEN=''" in bootstrap
    assert "heartbeat-token" not in bootstrap
