#!/usr/bin/env sh
set -eu

case "${1:-api}" in
  api)
    .venv/bin/alembic upgrade head
    exec .venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8765
    ;;
  worker)
    exec .venv/bin/python -m backend.app.worker
    ;;
  *)
    exec "$@"
    ;;
esac
