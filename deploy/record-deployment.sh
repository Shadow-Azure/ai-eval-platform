#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/ai-eval-platform}"
cd "$DEPLOY_DIR"
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile tools run --rm --no-deps \
  --entrypoint python api migrations/record_deployment.py
