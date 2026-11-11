#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-eval-platform}"
cd "$DEPLOY_DIR"
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile tools run --rm --no-deps \
  -e DEPLOY_STATUS="${DEPLOY_STATUS:-deploying}" \
  -e GIT_COMMIT="${GIT_COMMIT:-unknown}" \
  -e DEPLOY_IMAGE_DIGEST="${DEPLOY_IMAGE_DIGEST:-unknown}" \
  -e DEPLOY_MIGRATION_REVISION="${DEPLOY_MIGRATION_REVISION:-unknown}" \
  -e DEPLOY_TRIGGERED_BY="${DEPLOY_TRIGGERED_BY:-manual}" \
  -e DEPLOY_ENVIRONMENT="${DEPLOY_ENVIRONMENT:-production}" \
  -e DEPLOY_STARTED_AT="${DEPLOY_STARTED_AT:-}" \
  -e DEPLOY_ROLLBACK_OF_ID="${DEPLOY_ROLLBACK_OF_ID:-}" \
  -e DEPLOY_STAGES="${DEPLOY_STAGES:-}" \
  -e DEPLOY_STAGE="${DEPLOY_STAGE:-}" \
  -e DEPLOY_RUN_ID="${DEPLOY_RUN_ID:-}" \
  -e DEPLOY_RUN_URL="${DEPLOY_RUN_URL:-}" \
  -e DEPLOY_DETAIL_API_IMAGE="${DEPLOY_DETAIL_API_IMAGE:-unknown}" \
  -e DEPLOY_DETAIL_WEB_IMAGE="${DEPLOY_DETAIL_WEB_IMAGE:-unknown}" \
  -e DEPLOY_DETAIL_GIT_COMMIT="${DEPLOY_DETAIL_GIT_COMMIT:-unknown}" \
  --entrypoint python api migrations/record_deployment.py
