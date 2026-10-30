#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN="$HOME/.local/bin/uv"
fi
NPM_BIN="${NPM_BIN:-$(command -v npm || true)}"
if [ -z "$NPM_BIN" ] && [ -x /opt/homebrew/bin/npm ]; then
  NPM_BIN=/opt/homebrew/bin/npm
fi
export PYTHONDONTWRITEBYTECODE=1

if [ -z "$UV_BIN" ] || [ ! -x "$UV_BIN" ]; then
  echo "uv not found; install uv or set UV_BIN." >&2
  exit 1
fi
if [ -z "$NPM_BIN" ] || [ ! -x "$NPM_BIN" ]; then
  echo "npm not found; install Node.js or set NPM_BIN." >&2
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules is missing; run scripts/build.sh first." >&2
  exit 1
fi

echo "Building frontend..." >&2
(cd frontend && PATH="$(dirname "$NPM_BIN"):$PATH" npm run build)
exec "$UV_BIN" run uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
