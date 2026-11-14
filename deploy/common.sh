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
MIGRATION_REVISION="${MIGRATION_REVISION:-unknown}"
# M5-18: newline-separated "key|status|started_at|finished_at|detail" records
# rendered by the deployments page. Older rows without stages stay unknown.
DEPLOY_STAGES="${DEPLOY_STAGES:-}"
CURRENT_STAGE="${CURRENT_STAGE:-}"
STAGE_STARTED_AT=""
STAGE_OPEN=0

stage_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

stage_begin() {
  CURRENT_STAGE="$1"
  STAGE_STARTED_AT="$(stage_now)"
  STAGE_OPEN=1
}

stage_finish() {
  local status="$1"
  local detail="${2:-}"
  if [ "$STAGE_OPEN" != "1" ] || [ -z "$CURRENT_STAGE" ]; then
    return 0
  fi
  DEPLOY_STAGES="${DEPLOY_STAGES}${CURRENT_STAGE}|${status}|${STAGE_STARTED_AT}|$(stage_now)|${detail}"$'\n'
  STAGE_OPEN=0
}

stage_record() {
  # stage_record <key> <status> <started_at> <finished_at> <detail>
  DEPLOY_STAGES="${DEPLOY_STAGES}${1}|${2}|${3}|${4}|${5:-}"$'\n'
}

current_database_revision() {
  # Alembic is the only source of truth for the applied migration revision;
  # the old hard-coded default drifted from the actual head. Read the role and
  # database from the same .env Compose interpolates so custom names still work.
  local user db
  user="$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
  db="$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE" 2>/dev/null | tail -1)"
  "${COMPOSE[@]}" exec -T postgres psql \
    -U "${user:-eval}" -d "${db:-eval}" \
    -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]' || true
}

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
  DEPLOY_STAGES="$DEPLOY_STAGES" \
  DEPLOY_STAGE="${CURRENT_STAGE:-}" \
  DEPLOY_RUN_ID="${DEPLOY_RUN_ID:-}" \
  DEPLOY_RUN_URL="${DEPLOY_RUN_URL:-}" \
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

# M8-23: keep the local image cache bounded. Every deploy pulls a brand-new
# immutable api/web digest, so without a retention policy the host accumulates
# one version per deployment until the system disk fills up. The prune below
# keeps the images used by running containers, the digest recorded in
# .state/previous.env (the rollback target) and the newest
# IMAGE_RETENTION_VERSIONS versions of each platform repository.
#
# The version window is intentionally bounded: a typo such as "50" must not
# silently disable cleanup on a 40G disk.
IMAGE_RETENTION_VERSIONS="${IMAGE_RETENTION_VERSIONS:-3}"
IMAGE_PRUNE_DF_PATH="${IMAGE_PRUNE_DF_PATH:-/}"

image_short_id() {
  local id="${1:-}"
  id="${id#sha256:}"
  printf '%s' "${id:0:12}"
}

app_image_repositories() {
  # Strip "@sha256:..." or ":tag" from the configured application images and
  # emit each repository once. Base images such as postgres/redis never match,
  # so pruning cannot touch images this deployment does not own.
  local ref repo last
  for ref in "${API_IMAGE:-}" "${WEB_IMAGE:-}" "${RUNNER_EGRESS_PROXY_IMAGE:-}" "${PROXY_IMAGE:-}"; do
    [ -n "$ref" ] || continue
    repo="${ref%%@*}"
    last="${repo##*/}"
    case "$last" in
      *:*) repo="${repo%:*}" ;;
    esac
    printf '%s\n' "$repo"
  done | awk '!seen[$0]++'
}

previous_image_references() {
  local file="$DEPLOY_DIR/.state/previous.env"
  [ -f "$file" ] || return 0
  sed -n 's/^API_IMAGE=//p; s/^WEB_IMAGE=//p' "$file"
}

prune_old_images() {
  # Sets PRUNE_KEPT, PRUNE_DELETED, PRUNE_FAILED and PRUNE_DF so deploy.sh can
  # record a stage detail. Cleanup is best effort: every failure is logged and
  # skipped, because a successful deployment must never fail or roll back only
  # because old images could not be removed. Call it as `if prune_old_images`,
  # which also keeps the caller's ERR trap from treating cleanup as fatal.
  PRUNE_KEPT=0
  PRUNE_DELETED=0
  PRUNE_FAILED=0
  PRUNE_DF=""

  local limit="${IMAGE_RETENTION_VERSIONS:-3}"
  case "$limit" in
    '' | *[!0-9]*) limit=3 ;;
  esac
  if [ "$limit" -lt 1 ]; then
    limit=3
  elif [ "$limit" -gt 10 ]; then
    limit=10
  fi

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/ai-eval-image-prune.XXXXXX")" || {
    echo "deploy: image prune: cannot create a temporary directory; skipping cleanup" >&2
    return 1
  }

  app_image_repositories >"$work/repositories"
  if [ ! -s "$work/repositories" ]; then
    echo "deploy: image prune: no application image references configured; skipping cleanup"
    rm -rf "$work"
    return 0
  fi

  if ! docker image ls --digests \
    --format '{{.Repository}}|{{.Tag}}|{{.Digest}}|{{.ID}}|{{.CreatedAt}}' \
    >"$work/images" 2>"$work/ls.err"; then
    echo "deploy: image prune: docker image ls failed: $(tr '\n' ' ' <"$work/ls.err"); skipping cleanup" >&2
    rm -rf "$work"
    return 1
  fi
  # Newest first: the first N distinct ids per repository form the rollback
  # window. CreatedAt is an ISO-like fixed-width stamp, so a textual sort is a
  # chronological sort for the single host timezone.
  sort -t'|' -k5,5r "$work/images" >"$work/images.sorted"

  local id repo_name tag digest created
  : >"$work/candidates"
  while IFS='|' read -r repo_name tag digest id created; do
    [ -n "$repo_name" ] || continue
    grep -Fxq -- "$repo_name" "$work/repositories" || continue
    printf '%s|%s|%s|%s\n' "$id" "$repo_name" "$tag" "$digest" >>"$work/candidates"
  done <"$work/images.sorted"

  : >"$work/keep_ids"

  # 1. Images used by running containers: api, worker, web and the proxy all
  #    point at a local image id; deleting those would break the live stack.
  local cid image_id
  while IFS= read -r cid; do
    [ -n "$cid" ] || continue
    image_id="$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
    image_id="$(image_short_id "$image_id")"
    [ -n "$image_id" ] && printf '%s\n' "$image_id" >>"$work/keep_ids"
  done < <(docker ps -q 2>/dev/null || true)

  # 2. The rollback target saved by the previous deployment. Match by digest
  #    (the normal digest-pinned form) and also by repository:tag for hosts
  #    migrated from tag-based deploys.
  : >"$work/keep_digests"
  : >"$work/keep_refs"
  local previous
  while IFS= read -r previous; do
    [ -n "$previous" ] || continue
    case "$previous" in
      *@*) printf '%s\n' "${previous##*@}" >>"$work/keep_digests" ;;
      *) printf '%s\n' "$previous" >>"$work/keep_refs" ;;
    esac
  done < <(previous_image_references)

  while IFS='|' read -r id repo_name tag digest; do
    [ -n "$id" ] || continue
    if [ -n "$digest" ] && [ "$digest" != "<none>" ] &&
      grep -Fxq -- "$digest" "$work/keep_digests"; then
      printf '%s\n' "$id" >>"$work/keep_ids"
      continue
    fi
    if grep -Fxq -- "${repo_name}:${tag}" "$work/keep_refs"; then
      printf '%s\n' "$id" >>"$work/keep_ids"
    fi
  done <"$work/candidates"

  # 3. Newest N distinct versions per repository. Deduplicate by image id so a
  #    digest pull with an empty tag does not consume the whole window.
  : >"$work/versions"
  local seen
  while IFS='|' read -r id repo_name tag digest; do
    [ -n "$id" ] || continue
    grep -Fxq -- "$repo_name|$id" "$work/versions" && continue
    printf '%s|%s\n' "$repo_name" "$id" >>"$work/versions"
    seen="$(awk -F'|' -v target="$repo_name" '$1 == target { count++ } END { print count + 0 }' "$work/versions")"
    if [ "$seen" -le "$limit" ]; then
      printf '%s\n' "$id" >>"$work/keep_ids"
    fi
  done <"$work/candidates"
  sort -u "$work/keep_ids" -o "$work/keep_ids"

  cut -d'|' -f1 "$work/candidates" | sort -u >"$work/candidate_ids"
  awk -F'|' 'NR == FNR { keep[$1] = 1; next } !($1 in keep)' \
    "$work/keep_ids" "$work/candidate_ids" >"$work/delete_ids"

  local candidate_count delete_count
  candidate_count="$(wc -l <"$work/candidate_ids" | tr -d ' ')"
  delete_count="$(wc -l <"$work/delete_ids" | tr -d ' ')"
  PRUNE_KEPT="$((candidate_count - delete_count))"

  local remove_id
  while IFS= read -r remove_id; do
    [ -n "$remove_id" ] || continue
    if docker image rm "$remove_id" >/dev/null 2>"$work/rm.err"; then
      PRUNE_DELETED="$((PRUNE_DELETED + 1))"
      echo "deploy: image prune: removed ${remove_id}"
    else
      # A stopped container can pin an old image; report it and keep going so
      # the next deploy retries with a fresh view instead of failing.
      PRUNE_FAILED="$((PRUNE_FAILED + 1))"
      echo "deploy: image prune: could not remove ${remove_id}: $(tr '\n' ' ' <"$work/rm.err")" >&2
    fi
  done <"$work/delete_ids"

  PRUNE_DF="$(df -h "$IMAGE_PRUNE_DF_PATH" 2>/dev/null | tail -n 1 | tr -s ' ')"
  echo "deploy: image prune: retention=${limit} candidates=${candidate_count} kept=${PRUNE_KEPT} removed=${PRUNE_DELETED} failed=${PRUNE_FAILED}"
  if [ -n "$PRUNE_DF" ]; then
    echo "deploy: image prune: df -h ${IMAGE_PRUNE_DF_PATH}: ${PRUNE_DF}"
  else
    echo "deploy: image prune: df -h ${IMAGE_PRUNE_DF_PATH} unavailable" >&2
  fi

  rm -rf "$work"
  return 0
}
