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
import json
import shlex
from collections.abc import Sequence


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
    encoded_jit: str,
    runner_tarball_url: str,
) -> str:
    # The JIT blob is a single-use GitHub registration credential. It is
    # written 0600 and removed by the cleanup service after the runner reads
    # it; it is never echoed or written to a log.
    return f"""#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl git jq docker.io libicu-dev
systemctl enable --now docker

mkdir -p /opt/actions-runner /etc/eval
cd /opt/actions-runner
curl -fsSL --max-time 600 --retry 3 -o runner.tar.gz \\
  "{runner_tarball_url}"
tar -xzf runner.tar.gz
rm -f runner.tar.gz

cat > /etc/eval/runner-jit.env <<'JIT_ENV'
JIT_CONFIG={encoded_jit}
JIT_ENV
chmod 600 /etc/eval/runner-jit.env

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
ExecStart=/bin/bash -lc 'exec ./run.sh --jitconfig "$JIT_CONFIG"'
Restart=no

[Install]
WantedBy=multi-user.target
RUNNER_UNIT

cat > /etc/systemd/system/eval-heartbeat.service <<'HEARTBEAT_UNIT'
[Unit]
Description=AI Eval Platform spot runner heartbeat
After=network-online.target
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
systemctl enable --now eval-heartbeat.service
systemctl enable --now github-runner.service
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
) -> str:
    """Return a cloud-config document (base64 payload + runcmd).

    """
    heartbeat = _heartbeat_script(
        platform_base_url=platform_base_url,
        runner_name=runner_name,
        resource_pool_id=resource_pool_id,
        labels=labels,
        heartbeat_token=heartbeat_token,
    )
    bootstrap = _bootstrap_script(
        encoded_jit=encoded_jit,
        runner_tarball_url=runner_tarball_url,
    )
    cleanup = _cleanup_script()
    # Cloud-init write_files runs as a single document; keep each file in a
    # base64 block so shell quoting and YAML never collide.
    return f"""#cloud-config
write_files:
  - path: /etc/eval/heartbeat.sh
    permissions: '0700'
    encoding: b64
    content: {base64.b64encode(heartbeat.encode()).decode()}
  - path: /etc/eval/bootstrap.sh
    permissions: '0700'
    encoding: b64
    content: {base64.b64encode(bootstrap.encode()).decode()}
  - path: /etc/eval/cleanup.sh
    permissions: '0700'
    encoding: b64
    content: {base64.b64encode(cleanup.encode()).decode()}
runcmd:
  - [bash, /etc/eval/bootstrap.sh]
"""
