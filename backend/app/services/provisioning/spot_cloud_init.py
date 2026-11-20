"""Cloud-init user data for an Aliyun spot ephemeral runner (M4-04).

The generated document installs Docker + the GitHub Actions runner, starts a
small platform heartbeat loop, runs the runner with its single-use JIT config,
and then shreds the JIT env file and cloud-init user data. Nothing here logs
the JIT blob or the platform heartbeat token.

Cloud-init is the only place the JIT blob exists outside process memory; it is
consumed once by ``run.sh --jitconfig`` and the cleanup service removes the
env file plus ``/var/lib/cloud/instance/user-data.txt`` after boot.
"""

from __future__ import annotations

import base64
import io
import json
import shlex
import tarfile
from collections.abc import Mapping, Sequence


def _heartbeat_script(
    *,
    platform_base_url: str,
    runner_name: str,
    resource_pool_id: str,
    labels: Sequence[str],
    heartbeat_token: str | None,
) -> str:
    labels_json = json.dumps(list(labels))
    token = heartbeat_token or ""
    return f"""#!/bin/bash
set -u
PLATFORM_BASE_URL={shlex.quote(platform_base_url)}
RUNNER_HEARTBEAT_TOKEN={shlex.quote(token)}
RESOURCE_POOL_ID={shlex.quote(resource_pool_id)}
RUNNER_ID={shlex.quote(runner_name)}
RUNNER_NAME={shlex.quote(runner_name)}
LABELS_JSON={shlex.quote(labels_json)}
if [ -z "$PLATFORM_BASE_URL" ]; then
  exit 0
fi
while :; do
  payload=$(printf '{{"runner_id":"%s","name":"%s","labels":%s,"status":"online","resource_pool_id":"%s"}}' \\
    "$RUNNER_ID" "$RUNNER_NAME" "$LABELS_JSON" "$RESOURCE_POOL_ID")
  if [ -n "$RUNNER_HEARTBEAT_TOKEN" ]; then
    curl -fsS -m 5 -X POST "$PLATFORM_BASE_URL/runner/v1/runners/heartbeat" \\
      -H 'content-type: application/json' \\
      -H "authorization: Bearer $RUNNER_HEARTBEAT_TOKEN" \\
      -d "$payload" >/dev/null 2>&1 || true
  else
    curl -fsS -m 5 -X POST "$PLATFORM_BASE_URL/runner/v1/runners/heartbeat" \\
      -H 'content-type: application/json' \\
      -d "$payload" >/dev/null 2>&1 || true
  fi
  sleep 30
done
"""


def _bootstrap_script(
    *,
    runner_tarball_url: str,
    runner_tarball_token: str | None,
    scripts_bundle: Mapping[str, object] | None = None,
    scripts_bundle_url: str | None = None,
) -> str:
    # The JIT blob is a single-use GitHub registration credential. It is
    # written 0600 and removed by the cleanup service after the runner reads
    # it; it is never echoed or written to a log.
    scripts_name = str((scripts_bundle or {}).get("name") or "")
    scripts_sha256 = str((scripts_bundle or {}).get("sha256") or "")
    return f"""#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# The platform proxy endpoint reuses the heartbeat token for downloads; an
# explicitly configured external mirror URL leaves it empty so no credential
# is sent to a third party.
RUNNER_TARBALL_TOKEN={shlex.quote(runner_tarball_token or "")}
RUNNER_SCRIPTS_BUNDLE_NAME={shlex.quote(scripts_name)}
RUNNER_SCRIPTS_BUNDLE_SHA256={shlex.quote(scripts_sha256)}
RUNNER_SCRIPTS_BUNDLE_URL={shlex.quote(scripts_bundle_url or "")}
# M8 #348: a cold spot instance must not patch itself while a job runs. Ubuntu
# cloud images enable apt-daily/unattended-upgrades with a random timer that can
# fire inside a 60-minute evaluation window and restart services through
# needrestart/systemd, sending SIGTERM to the one-job runner. Disable periodic
# package work before any package operation and never restart services from
# needrestart. A baked host image already carries docker/node/auditd; the apt
# path below is only the compatibility fallback for a stock cloud image.
RUNNER_IMAGE_MARKER=/etc/eval/runner-image-baked
cat > /etc/apt/apt.conf.d/99-eval-no-auto-upgrade <<'APT_NO_AUTO_UPGRADE'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
APT_NO_AUTO_UPGRADE
cp /etc/apt/apt.conf.d/99-eval-no-auto-upgrade /etc/apt/apt.conf.d/20auto-upgrades
mkdir -p /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/99-eval-no-restart.conf <<'NEEDRESTART_NO_RESTART'
# A one-job evaluation runner may not restart a service behind a running job.
$nrconf{{restart}} = 'l';
NEEDRESTART_NO_RESTART
systemctl stop apt-daily.timer apt-daily-upgrade.timer apt-daily.service \\
  apt-daily-upgrade.service unattended-upgrades.service ua-timer.timer >/dev/null 2>&1 || true
systemctl mask apt-daily.timer apt-daily-upgrade.timer apt-daily.service \\
  apt-daily-upgrade.service unattended-upgrades.service ua-timer.timer >/dev/null 2>&1 || true

# Refuse to accept a job when the image still carries a pending reboot; a
# package change could otherwise reboot the host in the middle of execution.
if [ -f /var/run/reboot-required ]; then
  echo "reboot-required at boot; refusing to start the ephemeral runner" >&2
  cat /var/run/reboot-required >&2 || true
  exit 1
fi

if [ -f "$RUNNER_IMAGE_MARKER" ]; then
  echo "baked runner image detected; skipping package installation"
  for tool in docker node auditd curl git jq xz; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "baked runner image is missing $tool; rebuild the image" >&2
      exit 1
    fi
  done
else
  # Legacy stock cloud image: install the dependencies once without upgrading
  # packages already present, then clear the package lists so a later apt call
  # cannot silently patch the host.
  apt-get update -y
  apt-get install -y --no-install-recommends --no-upgrade \\
    ca-certificates curl git jq docker.io libicu-dev xz-utils
  if ! apt-get install -y --no-install-recommends --no-upgrade auditd; then
    echo "auditd unavailable; reboot audit disabled" >&2
  fi
  rm -rf /var/lib/apt/lists/*
fi
systemctl enable --now docker

# M8 #345: keep the evidence needed to name whoever rebooted a cold spot
# instance. auditd records execve of systemctl/reboot/shutdown and writes to
# /var/run/reboot-required; a persistent journal keeps the lines before the
# stop across a userspace or kernel restart.
mkdir -p /etc/audit/rules.d
cat > /etc/audit/rules.d/eval-reboot.rules <<'AUDIT_RULES'
-w /var/run/reboot-required -p wa -k eval_reboot_required
-a always,exit -F arch=b64 -S execve -F exe=/usr/bin/systemctl -k eval_systemctl_exec
-a always,exit -F arch=b64 -S execve -F exe=/usr/sbin/reboot -k eval_reboot_exec
-a always,exit -F arch=b64 -S execve -F exe=/usr/sbin/shutdown -k eval_shutdown_exec
AUDIT_RULES
if command -v augenrules >/dev/null 2>&1; then
  augenrules --load >/dev/null 2>&1 || true
fi
systemctl enable --now auditd >/dev/null 2>&1 || true
mkdir -p /var/log/journal
if [ -f /etc/systemd/journald.conf ]; then
  sed -i 's/^#*Storage=.*/Storage=persistent/' /etc/systemd/journald.conf
  if ! grep -q '^Storage=' /etc/systemd/journald.conf; then
    printf '\n[Journal]\nStorage=persistent\n' >> /etc/systemd/journald.conf
  fi
fi
systemctl restart systemd-journald >/dev/null 2>&1 || true

# The eval-shard workflow runs Node on the runner host: the prepare job
# validates the dispatch contract and plans the matrix (contracts.mjs,
# plan-cases.mjs) and every execute job runs real-task.mjs. The runner image
# bakes Node 22; a stock cloud image still downloads it from the npmmirror CDN
# because NodeSource/apt rate-limits a burst of five fresh instances sharing
# one NAT, which left most boots without Node. Keep the download bounded so a
# slow mirror cannot hang cloud-init past the runner registration timeout; a
# bootstrap failure leaves a diagnosable register_timeout instead of a silently
# idle instance.
if [ -f "$RUNNER_IMAGE_MARKER" ]; then
  node --version
else
  NODE_VERSION=22.11.0
  curl -fsSL --max-time 300 --retry 3 --retry-delay 5 -o /tmp/node.tar.xz \\
    "https://npmmirror.com/mirrors/node/v${{NODE_VERSION}}/node-v${{NODE_VERSION}}-linux-x64.tar.xz"
  tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
  rm -f /tmp/node.tar.xz
  node --version
fi

# M8-47 (#312): pre-seed the platform runner scripts bundle before the GitHub
# runner can pick a job. An image may already carry the content-addressed file
# at /opt/eval/runner-scripts/<name>.tar.gz; otherwise download the same object
# from a short-lived private OSS presigned URL. The GitHub job then reads local
# disk and survives a flaky platform scripts endpoint. The URL is a capability,
# not a stored credential: no OSS key or platform token is written here, and
# cleanup removes this user-data file after boot. A preload failure must not
# fail bootstrap; the workflow still has the OSS/proxy/checkout fallbacks.
mkdir -p /opt/eval/runner-scripts
if [ -n "$RUNNER_SCRIPTS_BUNDLE_NAME" ]; then
  target="/opt/eval/runner-scripts/$RUNNER_SCRIPTS_BUNDLE_NAME"
  if [ -f "$target" ] \\
    && printf '%s  %s\n' "$RUNNER_SCRIPTS_BUNDLE_SHA256" "$target" | sha256sum -c - >/dev/null 2>&1; then
    echo "runner scripts already baked into the image: $RUNNER_SCRIPTS_BUNDLE_NAME"
  elif [ -n "$RUNNER_SCRIPTS_BUNDLE_URL" ]; then
    tmp="$(mktemp /tmp/runner-scripts-XXXXXX.tar.gz)"
    if curl -fsSL --max-time 60 --retry 2 --retry-delay 2 -o "$tmp" "$RUNNER_SCRIPTS_BUNDLE_URL" \\
      && printf '%s  %s\n' "$RUNNER_SCRIPTS_BUNDLE_SHA256" "$tmp" | sha256sum -c - >/dev/null 2>&1; then
      chmod 0644 "$tmp"
      mv "$tmp" "$target"
      echo "runner scripts preloaded: $RUNNER_SCRIPTS_BUNDLE_NAME"
    else
      rm -f "$tmp"
      echo "runner scripts preload failed; the workflow will use OSS or checkout" >&2
    fi
  fi
fi

mkdir -p /opt/actions-runner /etc/eval
cd /opt/actions-runner
# Runner tarball: the deploy pipeline caches it in OSS and the platform's fixed
# proxy endpoint streams it. GitHub releases are slow or unreachable from
# Aliyun, and a 3-minute cap used to abort the download for whole instance
# batches; allow 10 minutes with retries before giving up. The proxy path
# carries the heartbeat token, while an external mirror URL gets no credential.
if [ -n "$RUNNER_TARBALL_TOKEN" ]; then
  curl -fsSL --max-time 600 --retry 3 --retry-delay 5 \\
    -H "authorization: Bearer $RUNNER_TARBALL_TOKEN" \\
    -o runner.tar.gz "{runner_tarball_url}"
else
  curl -fsSL --max-time 600 --retry 3 --retry-delay 5 \\
    -o runner.tar.gz "{runner_tarball_url}"
fi
unset RUNNER_TARBALL_TOKEN
tar -xzf runner.tar.gz
rm -f runner.tar.gz

cat > /etc/systemd/system/github-runner.service <<'RUNNER_UNIT'
[Unit]
Description=GitHub Actions ephemeral runner
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/eval/runner-jit.env
Environment=RUNNER_ALLOW_RUNASROOT=1
WorkingDirectory=/opt/actions-runner
# M8 #348: hold a shutdown/sleep inhibitor for the lifetime of the one job so a
# package script or an operator command cannot reboot/suspend the host behind
# the runner. The inhibitor is released as soon as the runner exits, before
# ExecStopPost powers the instance off.
ExecStart=/usr/bin/systemd-inhibit --what=shutdown:sleep --who=eval --why=job \\
  --mode=block /bin/bash -lc 'exec ./run.sh --jitconfig "$JIT_CONFIG"'
Restart=no
# M8-34 zero-resident spot pool: the runner is one-job and exits as soon as the
# job completes. ExecStopPost runs only after the unit stops (never on a normal
# start), so powering the instance off here leaves no online/offline orphan for
# the capacity controller to wait on.
ExecStopPost=/bin/sh -c 'shutdown -h now 2>/dev/null || systemctl poweroff'

[Install]
WantedBy=multi-user.target
RUNNER_UNIT

cat > /etc/systemd/system/eval-heartbeat.service <<'HEARTBEAT_UNIT'
[Unit]
Description=AI Eval Platform spot runner heartbeat
# The GitHub runner is ephemeral: it exits after one job. Bind the heartbeat to
# its lifetime so the platform stops seeing a phantom online runner once the
# runner is gone (otherwise the capacity gate keeps counting a runner GitHub no
# longer has, and queued execute jobs never get picked).
BindsTo=github-runner.service
After=network-online.target github-runner.service
Wants=network-online.target

[Service]
ExecStart=/bin/bash /etc/eval/heartbeat.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
HEARTBEAT_UNIT

cat > /etc/systemd/system/runner-cleanup.service <<'CLEANUP_UNIT'
[Unit]
Description=Remove one-time runner registration material
After=github-runner.service

[Service]
Type=oneshot
ExecStart=/bin/bash /etc/eval/cleanup.sh
CLEANUP_UNIT

systemctl daemon-reload
# The reboot-required file can also appear during the legacy dependency
# install. Never hand such a host a job: the platform will register a runner
# timeout instead of losing a running Agent to an unrelated reboot.
if [ -f /var/run/reboot-required ]; then
  echo "reboot-required before runner start; refusing to start the ephemeral runner" >&2
  cat /var/run/reboot-required >&2 || true
  exit 1
fi
# Start the runner first: the heartbeat unit is bound to it and must not linger
# after the ephemeral runner exits.
systemctl enable --now github-runner.service
systemctl enable --now eval-heartbeat.service
systemctl enable --now runner-cleanup.service
"""


def _cleanup_script() -> str:
    return """#!/bin/bash
set -u
sleep 90
shred -u /etc/eval/runner-jit.env 2>/dev/null || rm -f /etc/eval/runner-jit.env
rm -f /var/lib/cloud/instance/user-data.txt
rm -f /var/lib/cloud/instance/scripts/* 2>/dev/null || true
"""


def render_cloud_init(
    *,
    platform_base_url: str,
    runner_name: str,
    resource_pool_id: str,
    labels: Sequence[str],
    encoded_jit: str,
    heartbeat_token: str | None,
    runner_tarball_url: str,
    runner_tarball_token: str | None = None,
    scripts_bundle: Mapping[str, object] | None = None,
    scripts_bundle_url: str | None = None,
) -> str:
    """Return a cloud-config document (base64 payload + runcmd).

    ``scripts_bundle`` is the frozen content-addressed bundle metadata and
    ``scripts_bundle_url`` its short-lived private OSS GET URL. Both are
    optional so deployments without object storage keep the older cloud-init
    and the workflow falls back to its own acquisition path.
    """
    heartbeat = _heartbeat_script(
        platform_base_url=platform_base_url,
        runner_name=runner_name,
        resource_pool_id=resource_pool_id,
        labels=labels,
        heartbeat_token=heartbeat_token,
    )
    bootstrap = _bootstrap_script(
        runner_tarball_url=runner_tarball_url,
        runner_tarball_token=runner_tarball_token,
        scripts_bundle=scripts_bundle,
        scripts_bundle_url=scripts_bundle_url,
    )
    cleanup = _cleanup_script()
    # Cloud-init write_files runs as a single document; keep each file in a
    # base64 block so shell quoting and YAML never collide. The three scripts
    # travel as one gzipped tar so the UserData 16 KiB limit survives a real
    # GitHub JIT config; the blob is extracted once by runcmd.
    scripts = {
        "heartbeat.sh": heartbeat,
        "bootstrap.sh": bootstrap,
        "cleanup.sh": cleanup,
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, content in scripts.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o700
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    jit_env = f"JIT_CONFIG={encoded_jit}\n"
    extract = (
        "tar -xzf /etc/eval/scripts.tar.gz -C /etc/eval && "
        "rm -f /etc/eval/scripts.tar.gz && "
        "chmod 0700 /etc/eval/heartbeat.sh /etc/eval/bootstrap.sh /etc/eval/cleanup.sh && "
        "bash /etc/eval/bootstrap.sh"
    )
    return f"""#cloud-config
write_files:
  - path: /etc/eval/scripts.tar.gz
    permissions: '0600'
    encoding: b64
    content: {base64.b64encode(buf.getvalue()).decode()}
  - path: /etc/eval/runner-jit.env
    permissions: '0600'
    encoding: b64
    content: {base64.b64encode(jit_env.encode()).decode()}
runcmd:
  - [bash, -c, "{extract}"]
"""
