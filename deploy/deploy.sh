#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-eval-platform}"
: "${API_IMAGE:?API_IMAGE required}"
: "${WEB_IMAGE:?WEB_IMAGE required}"
: "${GIT_COMMIT:?GIT_COMMIT required}"

source "$DEPLOY_DIR/deploy/common.sh"
cd "$DEPLOY_DIR"
mkdir -p .state

if [ -f "$ENV_FILE" ]; then
  previous_api="$(sed -n 's/^API_IMAGE=//p' "$ENV_FILE" | tail -1)"
  previous_web="$(sed -n 's/^WEB_IMAGE=//p' "$ENV_FILE" | tail -1)"
  if [ -n "$previous_api" ] && [ -n "$previous_web" ]; then
    printf 'API_IMAGE=%s\nWEB_IMAGE=%s\n' "$previous_api" "$previous_web" > .state/previous.env
    chmod 600 .state/previous.env
  fi
fi

set_env API_IMAGE "$API_IMAGE"
set_env WEB_IMAGE "$WEB_IMAGE"
set_env GIT_COMMIT "$GIT_COMMIT"
set_env DEPLOY_ENVIRONMENT "$DEPLOY_ENVIRONMENT"
login_acr_if_configured

on_error() {
  echo "deploy: failed, rolling back" >&2
  record_deployment failed
  bash "$DEPLOY_DIR/deploy/rollback.sh" || true
  exit 1
}
trap on_error ERR

"${COMPOSE[@]}" pull api web
"${COMPOSE[@]}" --profile tools run --rm migrate
"${COMPOSE[@]}" up -d --remove-orphans

if ! wait_ready 30; then
  echo "deploy: health check failed" >&2
  false
fi

record_deployment succeeded
echo "deploy: succeeded ${GIT_COMMIT}"
