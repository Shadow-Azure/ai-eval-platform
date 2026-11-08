#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TOKEN:?RUNNER_TOKEN (GitHub 一次性 registration token) required}"
: "${PLATFORM_BASE_URL:?PLATFORM_BASE_URL required}"
: "${RUNNER_HEARTBEAT_TOKEN:?RUNNER_HEARTBEAT_TOKEN required}"

RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
RUNNER_NAME="${RUNNER_NAME:-fixed-linux-eval}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,eval}"
REPO_URL="${REPO_URL:-https://github.com/Shadow-Azure/ai-eval-platform}"
INSTALL_DIR="${INSTALL_DIR:-/opt/ai-eval-platform-runner}"
HEARTBEAT_SOURCE="${HEARTBEAT_SOURCE:-/opt/ai-eval-platform/runner/scripts/heartbeat.mjs}"
RUNNER_USER="${RUNNER_USER:-runner}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
if ! command -v node >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
fi

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$RUNNER_USER"
fi
mkdir -p "$INSTALL_DIR"
chown -R "$RUNNER_USER":"$RUNNER_USER" "$INSTALL_DIR"
cd "$INSTALL_DIR"
if [ ! -f ./config.sh ]; then
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz
  rm -f runner.tar.gz
fi
./bin/installdependencies.sh

if [ ! -f .runner ]; then
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u "$RUNNER_USER" -- ./config.sh \
      --url "$REPO_URL" \
      --token "$RUNNER_TOKEN" \
      --name "$RUNNER_NAME" \
      --labels "$RUNNER_LABELS" \
      --work _work \
      --unattended \
      --replace
  else
    ./config.sh \
      --url "$REPO_URL" \
      --token "$RUNNER_TOKEN" \
      --name "$RUNNER_NAME" \
      --labels "$RUNNER_LABELS" \
      --work _work \
      --unattended \
      --replace
  fi
fi
./svc.sh install "$RUNNER_USER" || true
./svc.sh start

install -d -m 750 -o root -g "$RUNNER_USER" /etc/eval-platform
cat > /etc/eval-platform/runner.env <<ENV
PLATFORM_BASE_URL=$PLATFORM_BASE_URL
RUNNER_HEARTBEAT_TOKEN=$RUNNER_HEARTBEAT_TOKEN
RUNNER_ID=$RUNNER_NAME
RUNNER_NAME=$RUNNER_NAME
RUNNER_LABELS=$RUNNER_LABELS
HEARTBEAT_INTERVAL_SECONDS=30
ENV
chown "$RUNNER_USER":"$RUNNER_USER" /etc/eval-platform/runner.env
chmod 600 /etc/eval-platform/runner.env

install -m 644 "$SCRIPT_DIR/eval-platform-heartbeat.service" /etc/systemd/system/eval-platform-heartbeat.service
install -m 644 "$HEARTBEAT_SOURCE" "$INSTALL_DIR/heartbeat.mjs"
systemctl daemon-reload
systemctl enable --now eval-platform-heartbeat.service
echo "runner: registered $RUNNER_NAME with labels $RUNNER_LABELS"
