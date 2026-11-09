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

login_acr_if_configured

ensure_secret() {
  local key="$1"
  if ! grep -q "^${key}=..*" "$ENV_FILE" 2>/dev/null; then
    set_env "$key" "$(openssl rand -hex 32)"
    echo "deploy: generated ${key}" >&2
  fi
}

ensure_fernet_secret() {
  local key="$1"
  if ! grep -q "^${key}=..*" "$ENV_FILE" 2>/dev/null; then
    # Fernet keys are urlsafe-base64 of exactly 32 random bytes.
    set_env "$key" "$(openssl rand -base64 32 | tr '+/' '-_')"
    echo "deploy: generated ${key}" >&2
  fi
}

# Session signing secret is server-local and stable across rollouts.
ensure_secret AUTH_SESSION_SECRET
# M3-02: provider credentials are encrypted at rest with a host-local key and
# the gateway signs runner tokens with its own secret.
ensure_fernet_secret SECRET_BOX_KEY
ensure_secret GATEWAY_TOKEN_SECRET
if ! grep -q '^AUTH_BOOTSTRAP_ADMIN_PASSWORD=.' "$ENV_FILE" 2>/dev/null; then
  echo "deploy: WARNING AUTH_BOOTSTRAP_ADMIN_PASSWORD is not set; the console will have no login account until it is provisioned" >&2
fi

set_env API_IMAGE "$API_IMAGE"
set_env WEB_IMAGE "$WEB_IMAGE"
set_env GIT_COMMIT "$GIT_COMMIT"
set_env DEPLOY_ENVIRONMENT "$DEPLOY_ENVIRONMENT"

on_error() {
  echo "deploy: failed, rolling back" >&2
  record_deployment failed
  if [ -f "$DEPLOY_DIR/.state/previous.env" ]; then
    bash "$DEPLOY_DIR/deploy/rollback.sh" || true
  else
    echo "deploy: no previous images; leaving the current stack untouched" >&2
  fi
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
