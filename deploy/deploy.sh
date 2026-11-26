#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-eval-platform}"
: "${API_IMAGE:?API_IMAGE required}"
: "${WEB_IMAGE:?WEB_IMAGE required}"
: "${PROXY_IMAGE:?PROXY_IMAGE required}"
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
set_env RUNNER_EGRESS_PROXY_IMAGE "$PROXY_IMAGE"
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

"${COMPOSE[@]}" pull api web proxy
stage_begin database_migration
"${COMPOSE[@]}" --profile tools run --rm migrate
MIGRATION_REVISION="$(current_database_revision)"
if [ -z "$MIGRATION_REVISION" ]; then
  MIGRATION_REVISION="unknown"
fi
stage_finish success "Alembic ${MIGRATION_REVISION}"

stage_begin rolling_update
# M8 (#290): keep orphan removal off here. The image-prep-dind/image-prep
# services run under the "image-prep" profile and are not in the default
# service set, so deleting orphans would remove a long-running worker and any
# in-progress pull on every ordinary deploy.
"${COMPOSE[@]}" up -d
stage_finish success "API/Web 容器替换完成"

# M8-16: the proxy is not part of the API health check, so a squid that fails
# to start used to leave only a "Started" line and closed ports. Emit the
# container state, its logs and a loopback probe so the CI log carries the
# diagnosis.
stage_begin proxy_diagnostics
"${COMPOSE[@]}" ps proxy || true
"${COMPOSE[@]}" logs --tail=120 proxy || true
for port in 3128 3129; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "deploy: proxy 127.0.0.1:$port open"
  else
    echo "deploy: proxy 127.0.0.1:$port closed" >&2
  fi
done
curl -sS -m 5 -o /dev/null -w "deploy: proxy 3129 no-token http=%{http_code}\n" -x http://127.0.0.1:3129 https://github.com || true
stage_finish success "代理诊断完成"

stage_begin health_check
if ! wait_ready 30; then
  echo "deploy: health check failed" >&2
  false
fi
stage_finish success "/readyz 通过"

# M8 (#436): the image-prep worker is the only consumer of the dataset image
# queue. Start it when the required data disk qualifies, and update it on deploy
# only when no image is actively claimed: recreating the container mid-pull
# would restart the queue from zero. A missing disk, too little free space or an
# undecidable active-claim query stays a skipped stage (the launch gate reports
# the offline worker explicitly) instead of failing the whole deployment.
stage_begin image_prep_residency
prep_data_dir="$(sed -n 's/^DATASET_IMAGE_PREP_DATA_DIR=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
prep_data_dir="${prep_data_dir:-/data/ai-eval-image-prep}"
prep_min_free="$(sed -n 's/^DATASET_IMAGE_PREP_MIN_FREE_BYTES=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
prep_min_free="${prep_min_free:-8589934592}"
prep_parent="$(dirname "$prep_data_dir")"
prep_available="$(df -B1 --output=avail "$prep_parent" 2>/dev/null | tail -1 | tr -d ' ' || true)"
prep_running=0
if "${COMPOSE[@]}" --profile image-prep ps --status running --services image-prep 2>/dev/null | grep -qx 'image-prep'; then
  prep_running=1
fi
prep_active=""
if [ "$prep_running" = "1" ] && [ -d "$prep_parent" ]; then
  prep_user="$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
  prep_db="$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
  prep_active="$("${COMPOSE[@]}" exec -T postgres psql -U "${prep_user:-eval}" -d "${prep_db:-eval}" -tAc "SELECT count(*) FROM dataset_image_artifact WHERE status='preparing' AND claimed_at IS NOT NULL AND next_attempt_at IS NOT NULL AND next_attempt_at > now()" 2>/dev/null | tr -d '[:space:]' || true)"
  case "$prep_active" in
    ''|*[!0-9]*) prep_active="" ;;
  esac
fi
if [ -d "$prep_parent" ] && [ -n "$prep_available" ] && [ "$prep_available" -ge "$prep_min_free" ]; then
  if [ "$prep_running" = "1" ] && [ -z "$prep_active" ]; then
    stage_finish skipped "无法确认是否有进行中的镜像准备；保持现有 image-prep 不重建"
  elif [ "${prep_active:-0}" -gt 0 ]; then
    stage_finish skipped "${prep_active} 个镜像正在准备；保持现有 image-prep，下次部署再更新"
  elif "${COMPOSE[@]}" --profile image-prep up -d image-prep-dind image-prep; then
    stage_finish success "已启动/更新 image-prep（数据盘 ${prep_parent}，可用 ${prep_available} 字节）"
  else
    stage_finish failed "image-prep 启动失败；部署保持成功，请检查数据盘与 dind 日志"
  fi
else
  stage_finish skipped "数据盘 ${prep_parent} 不存在或可用空间低于 ${prep_min_free} 字节；未启动 image-prep"
fi

# M8-23: bound the local image cache only after the new stack is healthy. The
# cleanup keeps the running images, the rollback digest and the newest N
# versions, and is called in a condition so its best-effort failures never
# trigger the rollback trap or fail an otherwise successful deployment.
stage_begin image_prune
if prune_old_images; then
  stage_finish success "保留 ${PRUNE_KEPT} 个镜像，删除 ${PRUNE_DELETED} 个（失败 ${PRUNE_FAILED}）；${PRUNE_DF}"
else
  stage_finish skipped "镜像清理失败，已跳过；部署保持成功"
fi

stage_begin deployment_record
stage_finish success "部署结果已写入记录"
record_deployment succeeded
echo "deploy: succeeded ${GIT_COMMIT}"
