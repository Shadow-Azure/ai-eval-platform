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

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
if ! command -v node >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
if [ ! -f ./config.sh ]; then
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz
  rm -f runner.tar.gz
fi
./bin/installdependencies.sh

if [ ! -f .runner ]; then
  ./config.sh \
    --url "$REPO_URL" \
    --token "$RUNNER_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    --work _work \
    --unattended \
    --replace
fi
./svc.sh install root
./svc.sh start

install -d -m 700 /etc/eval-platform
cat > /etc/eval-platform/runner.env <<ENV
PLATFORM_BASE_URL=$PLATFORM_BASE_URL
RUNNER_HEARTBEAT_TOKEN=$RUNNER_HEARTBEAT_TOKEN
RUNNER_ID=$RUNNER_NAME
RUNNER_NAME=$RUNNER_NAME
RUNNER_LABELS=$RUNNER_LABELS
HEARTBEAT_INTERVAL_SECONDS=30
ENV
chmod 600 /etc/eval-platform/runner.env

install -m 644 "$(dirname "$0")/eval-platform-heartbeat.service" /etc/systemd/system/eval-platform-heartbeat.service
install -m 644 "$HEARTBEAT_SOURCE" "$INSTALL_DIR/heartbeat.mjs"
systemctl daemon-reload
systemctl enable --now eval-platform-heartbeat.service
echo "runner: registered $RUNNER_NAME with labels $RUNNER_LABELS"
