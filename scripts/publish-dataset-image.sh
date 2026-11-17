#!/usr/bin/env bash
# Publish one dataset image as a compressed tarball to the platform OSS bucket
# (issue #273). Run on the production host (Docker + compose stack), e.g.:
#
#   bash scripts/publish-dataset-image.sh \
#     swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
#
# The key is derived from the exact image reference and matches the backend's
# task descriptor. The upload uses a short-lived presigned PUT against the
# internal OSS endpoint, then verifies the object with a presigned HEAD.
set -euo pipefail

IMAGE="${1:?usage: publish-dataset-image.sh <image-ref> [compose-dir]}"
COMPOSE_DIR="${2:-/opt/ai-eval-platform}"
DC=(docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_DIR/deploy/docker-compose.prod.yml")

cd "$COMPOSE_DIR"
docker image inspect "$IMAGE" >/dev/null

meta="$("${DC[@]}" exec -T api python -m app.manage dataset-image --image "$IMAGE" --method put --ttl 7200)"
key="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')"
url="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])')"
if [ -z "$key" ] || [ -z "$url" ]; then
  echo "publish: 未能获取 OSS key/URL（检查 S3_* 环境变量与 API 容器）" >&2
  exit 1
fi

tmp="$(mktemp /tmp/dataset-image-XXXXXX.tar.gz)"
trap 'rm -f "$tmp"' EXIT

echo "publish: exporting $IMAGE -> $key"
docker save "$IMAGE" | gzip -1 > "$tmp"
size="$(stat -c%s "$tmp")"
echo "publish: uploading ${size} bytes (compressed)"

# UNSIGNED-PAYLOAD presign: the bucket is private; the short-lived URL is the
# only credential and is never printed by this script.
curl -fsS -X PUT --upload-file "$tmp" "$url" >/dev/null
echo "publish: upload complete"

"${DC[@]}" exec -T api python -m app.manage dataset-image --image "$IMAGE" --method head
echo "publish: verified $key"
