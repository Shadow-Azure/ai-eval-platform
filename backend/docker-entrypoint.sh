#!/bin/sh
set -eu

mode="${1:-api}"

case "$mode" in
  api)
    alembic -c alembic.ini upgrade head
    python -m app.seed
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
    ;;
  worker)
    exec python -m app.worker.main
    ;;
  *)
    exec "$@"
    ;;
esac
