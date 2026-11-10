#!/usr/bin/env bash
# Shared helpers for deploy.sh / rollback.sh. Caller sets -euo pipefail.
DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-eval-platform}"
ENV_FILE="$DEPLOY_DIR/.env"
COMPOSE_FILE="$DEPLOY_DIR/deploy/docker-compose.prod.yml"
COMPOSE_VPC_FILE="$DEPLOY_DIR/deploy/docker-compose.vpc.yml"
# M5-08: add the VPC callback binding only when the host .env pins a
# non-loopback address. Reading .env directly (instead of the shell env) keeps
# deploy.sh, rollback.sh and manual compose runs in agreement.
API_BIND_ADDRESS="${API_BIND_ADDRESS:-$(sed -n 's/^API_BIND_ADDRESS=//p' "$ENV_FILE" 2>/dev/null | tail -1)}"
COMPOSE_FILES=(-f "$COMPOSE_FILE")
if [ -n "$API_BIND_ADDRESS" ] && [ "$API_BIND_ADDRESS" != "127.0.0.1" ] && [ "$API_BIND_ADDRESS" != "localhost" ]; then
  COMPOSE_FILES+=(-f "$COMPOSE_VPC_FILE")
fi
COMPOSE=(docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}")
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DEPLOY_ENVIRONMENT="${DEPLOY_ENVIRONMENT:-production}"
DEPLOY_TRIGGERED_BY="${DEPLOY_TRIGGERED_BY:-github-actions}"
MIGRATION_REVISION="${MIGRATION_REVISION:-0002_deployment}"

set_env() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' \
      "$ENV_FILE" > "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

wait_ready() {
  local attempts="${1:-30}"
  local index
  for index in $(seq 1 "$attempts"); do
    if curl -fsS http://127.0.0.1:8080/readyz >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

record_deployment() {
  local status="$1"
  DEPLOY_STATUS="$status" \
  DEPLOY_STARTED_AT="$STARTED_AT" \
  DEPLOY_IMAGE_DIGEST="${API_IMAGE:-unknown}" \
  DEPLOY_MIGRATION_REVISION="$MIGRATION_REVISION" \
  DEPLOY_TRIGGERED_BY="$DEPLOY_TRIGGERED_BY" \
  DEPLOY_ENVIRONMENT="$DEPLOY_ENVIRONMENT" \
  DEPLOY_ROLLBACK_OF_ID="${DEPLOY_ROLLBACK_OF_ID:-}" \
  DEPLOY_DETAIL_API_IMAGE="${API_IMAGE:-unknown}" \
  DEPLOY_DETAIL_WEB_IMAGE="${WEB_IMAGE:-unknown}" \
  DEPLOY_DETAIL_GIT_COMMIT="${GIT_COMMIT:-unknown}" \
  bash "$DEPLOY_DIR/deploy/record-deployment.sh" || true
}

normalize_images() {
  if [ -n "${ACR_REGISTRY:-}" ]; then
    ACR_REGISTRY_VPC="${ACR_REGISTRY_VPC:-${ACR_REGISTRY/\.cn-/-vpc.cn-}}"
    API_IMAGE="${API_IMAGE//$ACR_REGISTRY/$ACR_REGISTRY_VPC}"
    WEB_IMAGE="${WEB_IMAGE//$ACR_REGISTRY/$ACR_REGISTRY_VPC}"
  fi
}

login_acr_if_configured() {
  if [ -f "$DEPLOY_DIR/.acr.env" ]; then
    # shellcheck disable=SC1091
    . "$DEPLOY_DIR/.acr.env"
    normalize_images
    for registry in "${ACR_REGISTRY:-}" "${ACR_REGISTRY_VPC:-}"; do
      if [ -n "$registry" ] && [ -n "${ACR_USERNAME:-}" ] && [ -n "${ACR_PASSWORD:-}" ]; then
        printf '%s' "$ACR_PASSWORD" | docker login --username "$ACR_USERNAME" --password-stdin "$registry" >/dev/null
      fi
    done
  fi
}
