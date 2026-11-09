#!/usr/bin/env bash
# Build the fixed-runner Claude Code image and print its immutable digest.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${IMAGE:-ai-eval-claude-code}"
TAG="${TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo local)}"
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-2.1.319}"
NODE_IMAGE="${NODE_IMAGE:-node:22-bookworm-slim}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-deb.debian.org}"

docker build \
  --build-arg "NODE_IMAGE=${NODE_IMAGE}" \
  --build-arg "DEBIAN_MIRROR=${DEBIAN_MIRROR}" \
  --build-arg "CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION}" \
  --label "org.opencontainers.image.revision=${TAG}" \
  -t "${IMAGE}:${TAG}" \
  "${ROOT}/infra/eval-image"

DIGEST="$(docker image inspect --format '{{.Id}}' "${IMAGE}:${TAG}")"
printf 'image=%s:%s\ndigest=%s\n' "${IMAGE}" "${TAG}" "${DIGEST}"
