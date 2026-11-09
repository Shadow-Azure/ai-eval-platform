# syntax=docker/dockerfile:1
#
# Fixed-runner evaluation image (M3-03). It bundles the Claude Code CLI, the
# task toolchain (git/patch/python/pytest) and no provider credential. The
# runner launches it with an isolated HOME + CLAUDE_CONFIG_DIR and a
# short-lived platform gateway token; provider keys stay in the control plane.
ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE}

ARG CLAUDE_CODE_VERSION=2.1.319
ARG DEBIAN_MIRROR=deb.debian.org
ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="ai-eval-claude-code" \
      org.opencontainers.image.description="Claude Code evaluation runtime for the fixed Linux runner" \
      org.opencontainers.image.source="https://github.com/Shadow-Azure/ai-eval-platform"

RUN if [ "${DEBIAN_MIRROR}" != "deb.debian.org" ]; then \
        sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        patch \
        procps \
        python3 \
        python3-pip \
        python3-pytest \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && claude --version

RUN useradd --create-home --uid 10001 --shell /bin/bash eval \
    && mkdir -p /workspace /runtime-home \
    && chown -R eval:eval /workspace /runtime-home

USER eval
WORKDIR /workspace

# No ENTRYPOINT: the runner supplies the reviewed argv. No secrets at build.
CMD ["bash"]
