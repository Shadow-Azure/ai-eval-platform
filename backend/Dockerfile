# syntax=docker/dockerfile:1

# M8 (#290): the image preparation worker drives a dedicated Docker-in-Docker
# daemon through the docker CLI. Download the official static binary instead of
# pulling docker:27-cli from Docker Hub during the build: the deploy build hung
# on that extra Docker Hub pull, while download.docker.com is reachable.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY backend ./backend
# Runner contract schemas are loaded at runtime by app/runner_contracts.py.
COPY runner-contract ./runner-contract

ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" .

ARG DOCKER_CLI_VERSION=27.5.1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/docker /tmp/docker.tgz; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
RUN chmod +x ./docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["api"]
