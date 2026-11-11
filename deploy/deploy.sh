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

require_secret() {
  local key="$1"
  if ! grep -q "^${key}=..*" "$ENV_FILE" 2>/dev/null; then
    echo "deploy: ${key} is required from GitHub Secrets; refusing to generate a host-local replacement" >&2
    return 1
  fi
}

# Long-lived keys must be injected by the deploy workflow before this script
# runs. Reusing the same values on a replacement host keeps encrypted model and
# cloud credentials decryptable.
require_secret SECRET_BOX_KEY
require_secret POSTGRES_PASSWORD
require_secret AUTH_SESSION_SECRET
require_secret GATEWAY_TOKEN_SECRET
require_secret RUNNER_TASK_SECRET
require_secret RUNNER_HEARTBEAT_TOKEN
require_secret RUNNER_ADMIN_TOKEN
if ! grep -q '^AUTH_BOOTSTRAP_ADMIN_PASSWORD=.' "$ENV_FILE" 2>/dev/null; then
  echo "deploy: WARNING AUTH_BOOTSTRAP_ADMIN_PASSWORD is not set; the console will have no login account until it is provisioned" >&2
fi

set_env API_IMAGE "$API_IMAGE"
set_env WEB_IMAGE "$WEB_IMAGE"
set_env GIT_COMMIT "$GIT_COMMIT"
set_env DEPLOY_ENVIRONMENT "$DEPLOY_ENVIRONMENT"

on_error() {
  echo "deploy: failed, rolling back" >&2
  stage_finish failed "部署失败"
  record_deployment failed
  if [ -f "$DEPLOY_DIR/.state/previous.env" ]; then
    bash "$DEPLOY_DIR/deploy/rollback.sh" || true
  else
    echo "deploy: no previous images; leaving the current stack untouched" >&2
  fi
  exit 1
}
trap on_error ERR

# The host script only runs after the GitHub Actions build job pushed the
# images, so the build stage is a real precondition rather than a guess.
stage_record build success "" "" "GitHub Actions 构建成功后触发部署"

"${COMPOSE[@]}" pull api web
stage_begin database_migration
"${COMPOSE[@]}" --profile tools run --rm migrate
MIGRATION_REVISION="$(current_database_revision)"
if [ -z "$MIGRATION_REVISION" ]; then
  MIGRATION_REVISION="unknown"
fi
stage_finish success "Alembic ${MIGRATION_REVISION}"

stage_begin rolling_update
"${COMPOSE[@]}" up -d --remove-orphans
stage_finish success "API/Web 容器替换完成"

stage_begin health_check
if ! wait_ready 30; then
  echo "deploy: health check failed" >&2
  false
fi
stage_finish success "/readyz 通过"

stage_begin deployment_record
stage_finish success "部署结果已写入记录"
record_deployment succeeded
echo "deploy: succeeded ${GIT_COMMIT}"
