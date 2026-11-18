# syntax=docker/dockerfile:1

# M8 (#290): the image preparation worker drives a dedicated Docker-in-Docker
# daemon through the docker CLI. The CLI is a static binary, so copy it from
# the pinned upstream image instead of adding an OS package to the runtime.
FROM docker:27-cli AS dockercli

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

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app/backend
RUN chmod +x ./docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["api"]
