#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-eval-platform}"
source "$DEPLOY_DIR/deploy/common.sh"
cd "$DEPLOY_DIR"

if [ ! -f .state/previous.env ]; then
  echo "rollback: no previous image state" >&2
  exit 1
fi
# shellcheck disable=SC1091
. ./.state/previous.env
login_acr_if_configured
set_env API_IMAGE "$API_IMAGE"
set_env WEB_IMAGE "$WEB_IMAGE"

"${COMPOSE[@]}" pull api web
"${COMPOSE[@]}" up -d --remove-orphans
if ! wait_ready 30; then
  echo "rollback: health check failed" >&2
  record_deployment failed
  exit 1
fi
record_deployment rolled_back
echo "rollback: restored previous images"
