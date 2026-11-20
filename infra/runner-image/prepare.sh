#!/bin/bash
# Prepare an Aliyun ECS Ubuntu host as the immutable cold spot runner image.
#
# Run as root on a throwaway builder instance. The script installs every
# runtime dependency the cloud-init bootstrap would otherwise apt-install at
# boot (docker, Node 22, auditd, curl/git/jq/xz), disables runtime package
# upgrades and needrestart service restarts, and writes the image marker plus a
# version record. A generated image is reused by every new spot instance, so
# security patching happens here instead of during an evaluation.
#
# Keep the hardening block in sync with backend/app/services/provisioning/
# spot_cloud_init.py; tests/runner-image.test.mjs cross-checks both.
#
# Required only when the caller wants a specific record: RUNNER_IMAGE_VERSION.
# NODE_VERSION defaults to the version pinned by cloud-init.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
NODE_VERSION="${NODE_VERSION:-22.11.0}"
IMAGE_VERSION="${RUNNER_IMAGE_VERSION:-$(date -u +%Y%m%dT%H%M%SZ)}"
IMAGE_MARKER="/etc/eval/runner-image-baked"
IMAGE_RECORD="/etc/eval/runner-image.json"

if [ "$(id -u)" -ne 0 ]; then
  echo "prepare.sh: must run as root" >&2
  exit 1
fi

# --- runtime auto-update hardening (same contract as cloud-init) ------------
mkdir -p /etc/apt/apt.conf.d /etc/needrestart/conf.d
cat > /etc/apt/apt.conf.d/99-eval-no-auto-upgrade <<'APT_NO_AUTO_UPGRADE'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
APT_NO_AUTO_UPGRADE
cp /etc/apt/apt.conf.d/99-eval-no-auto-upgrade /etc/apt/apt.conf.d/20auto-upgrades
cat > /etc/needrestart/conf.d/99-eval-no-restart.conf <<'NEEDRESTART_NO_RESTART'
# A one-job evaluation runner may not restart a service behind a running job.
$nrconf{restart} = 'l';
NEEDRESTART_NO_RESTART
systemctl stop apt-daily.timer apt-daily-upgrade.timer apt-daily.service \
  apt-daily-upgrade.service unattended-upgrades.service ua-timer.timer >/dev/null 2>&1 || true
systemctl mask apt-daily.timer apt-daily-upgrade.timer apt-daily.service \
  apt-daily-upgrade.service unattended-upgrades.service ua-timer.timer >/dev/null 2>&1 || true

# --- dependencies ----------------------------------------------------------
# Package lists are updated only here, on the builder. Instances never run apt.
apt-get update -y
apt-get install -y --no-install-recommends \
  ca-certificates curl git jq docker.io libicu-dev xz-utils auditd

if ! command -v node >/dev/null 2>&1 || [ "$(node --version 2>/dev/null)" != "v${NODE_VERSION}" ]; then
  curl -fsSL --max-time 300 --retry 3 --retry-delay 5 -o /tmp/node.tar.xz \
    "https://npmmirror.com/mirrors/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz"
  tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
  rm -f /tmp/node.tar.xz
fi
node --version

# --- reboot audit ----------------------------------------------------------
# Keep the same evidence collection as cloud-init so a SIGTERM still names its
# initiator even before the platform rotates fully to this image.
mkdir -p /etc/audit/rules.d
cat > /etc/audit/rules.d/eval-reboot.rules <<'AUDIT_RULES'
-w /var/run/reboot-required -p wa -k eval_reboot_required
-a always,exit -F arch=b64 -S execve -F exe=/usr/bin/systemctl -k eval_systemctl_exec
-a always,exit -F arch=b64 -S execve -F exe=/usr/sbin/reboot -k eval_reboot_exec
-a always,exit -F arch=b64 -S execve -F exe=/usr/sbin/shutdown -k eval_shutdown_exec
AUDIT_RULES
systemctl enable docker.service >/dev/null 2>&1 || true
systemctl enable auditd.service >/dev/null 2>&1 || true
mkdir -p /var/log/journal
if [ -f /etc/systemd/journald.conf ]; then
  sed -i 's/^#*Storage=.*/Storage=persistent/' /etc/systemd/journald.conf
  if ! grep -q '^Storage=' /etc/systemd/journald.conf; then
    printf '\n[Journal]\nStorage=persistent\n' >> /etc/systemd/journald.conf
  fi
fi

# --- image record and cleanup ---------------------------------------------
# A pending reboot means the image cannot be snapshotted yet. build.sh reboots
# the builder and reruns this script; the second pass finds no pending reboot.
if [ -f /var/run/reboot-required ]; then
  echo "prepare.sh: reboot required after dependency installation" >&2
  cat /var/run/reboot-required >&2 || true
  exit 2
fi

# Remove the builder's cloud-init state so every instance from this image runs
# its own cloud-init user-data (JIT config, heartbeat, cleanup).
if command -v cloud-init >/dev/null 2>&1; then
  cloud-init clean --logs --seed >/dev/null 2>&1 || true
fi

mkdir -p /etc/eval
cat > "$IMAGE_RECORD" <<IMAGE_JSON
{
  "baked": true,
  "version": "${IMAGE_VERSION}",
  "artifact": "ai-eval-runner",
  "node_version": "v${NODE_VERSION}",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "infra/runner-image/prepare.sh"
}
IMAGE_JSON
touch "$IMAGE_MARKER"

apt-get clean
rm -rf /var/lib/apt/lists/*
rm -f /var/log/apt/*.log /var/log/unattended-upgrades/*.log 2>/dev/null || true
echo "prepare.sh: runner image ready (${IMAGE_VERSION})"
