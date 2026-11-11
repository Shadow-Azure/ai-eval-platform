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

MIGRATION_REVISION="$(current_database_revision)"
if [ -z "$MIGRATION_REVISION" ]; then
  MIGRATION_REVISION="unknown"
fi

on_error() {
  echo "rollback: failed" >&2
  stage_finish failed "回滚失败"
  record_deployment failed
  exit 1
}
trap on_error ERR

# A controlled rollback only restores the previous application images. The
# database keeps the current (forward-compatible) schema; no downgrade runs.
stage_record build success "" "" "沿用已构建的上一版本镜像"
stage_record database_migration skipped "" "" "数据库保持向前兼容，不执行 downgrade"
stage_begin rolling_update
"${COMPOSE[@]}" pull api web
"${COMPOSE[@]}" up -d --remove-orphans
stage_finish success "API/Web 容器替换完成"

stage_begin health_check
if ! wait_ready 30; then
  echo "rollback: health check failed" >&2
  false
fi
stage_finish success "/readyz 通过"

stage_begin deployment_record
stage_finish success "回滚结果已写入记录"
record_deployment rolled_back
echo "rollback: restored previous images"
