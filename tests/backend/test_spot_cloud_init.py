"""M4-04 cloud-init for ephemeral spot runners.

The JIT blob must reach the instance exactly once and the cleanup service must
remove the one-time material; these tests parse the cloud-config rather than
asserting on a string blob.
"""

from __future__ import annotations

import base64
import io
import tarfile

import yaml
from app.services.provisioning.spot_cloud_init import render_cloud_init


def _files(document: dict) -> dict[str, str]:
    files: dict[str, str] = {}
    archive: bytes | None = None
    for item in document.get("write_files", []):
        raw = base64.b64decode(item["content"])
        if item["path"] == "/etc/eval/scripts.tar.gz":
            archive = raw
        else:
            files[item["path"]] = raw.decode()
    # The scripts travel as one gzipped tar; tests work with the extracted
    # bootstrap/heartbeat/cleanup as before.
    if archive is not None:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    files[f"/etc/eval/{member.name}"] = tar.extractfile(member).read().decode()
    return files


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
    assert document["runcmd"] == [
        [
            "bash",
            "-c",
            (
                "tar -xzf /etc/eval/scripts.tar.gz -C /etc/eval && "
                "rm -f /etc/eval/scripts.tar.gz && "
                "chmod 0700 /etc/eval/heartbeat.sh /etc/eval/bootstrap.sh /etc/eval/cleanup.sh && "
                "bash /etc/eval/bootstrap.sh"
            ),
        ]
    ]
    files = _files(document)
    assert set(files) == {
        "/etc/eval/heartbeat.sh",
        "/etc/eval/bootstrap.sh",
        "/etc/eval/cleanup.sh",
        "/etc/eval/runner-jit.env",
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
    files = _files(document)
    bootstrap = files["/etc/eval/bootstrap.sh"]
    assert "single-use-jit-value" in files["/etc/eval/runner-jit.env"]
    assert "single-use-jit-value" not in bootstrap
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


def test_bootstrap_powers_off_after_the_runner_exits() -> None:
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
    runner_unit = bootstrap.split("<<'RUNNER_UNIT'\n", 1)[1].split("\nRUNNER_UNIT", 1)[0]
    # Zero-resident lifecycle: a one-job runner exiting powers the spot instance
    # off. ExecStopPost is a stop hook, not a boot/start command, so a freshly
    # started runner never triggers it.
    assert "ExecStopPost=/bin/sh -c" in runner_unit
    assert "systemctl poweroff" in runner_unit
    assert "ExecStartPre" not in runner_unit
    assert runner_unit.count("ExecStopPost=") == 1


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


def test_bootstrap_preloads_the_scripts_bundle_from_private_oss() -> None:
    name = "eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz"
    text = render_cloud_init(
        platform_base_url="http://10.0.0.5:8000",
        runner_name="spot-abc",
        resource_pool_id="pool-1",
        labels=["self-hosted"],
        encoded_jit="jit",
        heartbeat_token="heartbeat-token",
        runner_tarball_url="http://10.0.0.5:8000/runner/v1/runner-tarball/2.338.2.tar.gz",
        scripts_bundle={
            "name": name,
            "md5": "0" * 32,
            "sha256": "a" * 64,
            "size": 40960,
        },
        scripts_bundle_url=(
            "https://bucket.oss-cn-hangzhou-internal.aliyuncs.com/runner/scripts/"
            f"{name}?X-Amz-Signature=short-lived"
        ),
    )
    bootstrap = _files(yaml.safe_load(text))["/etc/eval/bootstrap.sh"]

    assert "mkdir -p /opt/eval/runner-scripts" in bootstrap
    assert f"RUNNER_SCRIPTS_BUNDLE_NAME={name}" in bootstrap
    assert f"RUNNER_SCRIPTS_BUNDLE_SHA256={'a' * 64}" in bootstrap
    assert "runner/scripts/" in bootstrap
    assert "oss-cn-hangzhou-internal.aliyuncs.com" in bootstrap
    assert "sha256sum -c -" in bootstrap
    # A valid baked file skips the download; the temporary file is only moved
    # into place after the sha256 check.
    assert bootstrap.index("already baked into the image") < bootstrap.index(
        "runner scripts preloaded"
    )
    assert bootstrap.index("sha256sum -c -") < bootstrap.index('mv "$tmp" "$target"')
    # The presigned URL is a short-lived capability, not an OSS credential.
    assert "S3_SECRET_KEY" not in bootstrap
    assert "S3_ACCESS_KEY" not in bootstrap
    # The image cache is root-readable so the runner service can read it.
    assert "chmod 0644" in bootstrap
    assert len(text.encode()) < 16 * 1024


def test_bootstrap_enables_reboot_audit_and_persistent_journal() -> None:
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
    # auditd records the execve of systemctl/reboot/shutdown so the next
    # SIGTERM can name the reboot initiator even when it is automated.
    assert "apt-get install -y --no-install-recommends --no-upgrade auditd" in bootstrap
    assert "/etc/audit/rules.d/eval-reboot.rules" in bootstrap
    assert "-S execve" in bootstrap
    assert "eval_systemctl_exec" in bootstrap
    assert "augenrules --load" in bootstrap
    assert "systemctl enable --now auditd" in bootstrap
    # Persistent journal keeps the lines before the stop across a restart.
    assert "Storage=persistent" in bootstrap
    assert "/var/log/journal" in bootstrap


def test_bootstrap_disables_runtime_auto_upgrades_and_needrestart() -> None:
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
    # The 2026-11-20 SIGTERM came from apt-daily-upgrade/unattended-upgrades
    # randomly firing inside the 60-minute job window. Both the periodic policy
    # and the units that run it must be disabled before any package operation.
    assert "APT::Periodic::Update-Package-Lists \"0\";" in bootstrap
    assert "APT::Periodic::Unattended-Upgrade \"0\";" in bootstrap
    assert "/etc/apt/apt.conf.d/99-eval-no-auto-upgrade" in bootstrap
    assert "20auto-upgrades" in bootstrap
    for unit in (
        "apt-daily.timer",
        "apt-daily-upgrade.timer",
        "apt-daily.service",
        "apt-daily-upgrade.service",
        "unattended-upgrades.service",
        "ua-timer.timer",
    ):
        assert unit in bootstrap
    assert "systemctl mask apt-daily.timer" in bootstrap
    assert "systemctl stop apt-daily.timer" in bootstrap
    # needrestart may restart services after package changes; list-only mode
    # keeps it from touching the runner.
    assert "/etc/needrestart/conf.d/99-eval-no-restart.conf" in bootstrap
    assert "$nrconf{restart} = 'l';" in bootstrap
    # Legacy installs must not upgrade packages already present on the image.
    assert "--no-upgrade" in bootstrap
    assert bootstrap.index("systemctl mask apt-daily.timer") < bootstrap.index("apt-get update -y")


def test_bootstrap_prefers_the_baked_runner_image_and_refuses_pending_reboot() -> None:
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
    # A baked image carries docker/node/auditd; cloud-init must not apt-install
    # or re-download Node on those hosts.
    assert "RUNNER_IMAGE_MARKER=/etc/eval/runner-image-baked" in bootstrap
    assert 'if [ -f "$RUNNER_IMAGE_MARKER" ]; then' in bootstrap
    assert "baked runner image detected; skipping package installation" in bootstrap
    assert "baked runner image is missing $tool; rebuild the image" in bootstrap
    # The guard must run both before dependency setup and before the runner
    # starts, so a pending reboot can never land in the middle of a job.
    assert bootstrap.count("/var/run/reboot-required") >= 2
    assert "reboot-required at boot; refusing to start" in bootstrap
    assert "reboot-required before runner start; refusing to start" in bootstrap
    assert bootstrap.index("reboot-required before runner start") < bootstrap.index(
        "systemctl enable --now github-runner.service"
    )
    # A job-scoped inhibitor blocks shutdown/sleep while the runner is alive;
    # it is released before ExecStopPost powers the instance off.
    runner_unit = bootstrap.split("<<'RUNNER_UNIT'\n", 1)[1].split("\nRUNNER_UNIT", 1)[0]
    assert "ExecStart=/usr/bin/systemd-inhibit --what=shutdown:sleep" in runner_unit
    assert "--mode=block" in runner_unit
    assert "run.sh --jitconfig \"$JIT_CONFIG\"" in runner_unit
    assert runner_unit.count("ExecStart=") == 1


def test_cloud_init_stays_within_user_data_limit_for_a_real_jit() -> None:
    # GitHub JIT configs are base64 JWTs; the tar layout keeps a 4 KiB blob
    # inside Aliyun's 16 KiB UserData ceiling even with the largest scripts
    # bundle URL.
    jit = "J" * 4096
    text = render_cloud_init(
        platform_base_url="http://10.0.0.5:8000",
        runner_name="spot-abc",
        resource_pool_id="pool-1",
        labels=["self-hosted", "linux", "eval"],
        encoded_jit=jit,
        heartbeat_token="t" * 64,
        runner_tarball_url="http://10.0.0.5:8000/runner/v1/runner-tarball/2.338.2.tar.gz",
        runner_tarball_token="t" * 64,
        scripts_bundle={
            "name": "eval-runner-scripts-" + "a" * 32 + ".tar.gz",
            "sha256": "a" * 64,
        },
        scripts_bundle_url=(
            "https://bucket.oss-cn-hangzhou-internal.aliyuncs.com/runner/scripts/"
            + "a" * 40
            + ".tar.gz?X-Amz-Signature="
            + "b" * 64
        ),
    )
    assert len(text.encode()) < 16 * 1024
    files = _files(yaml.safe_load(text))
    assert jit in files["/etc/eval/runner-jit.env"]
