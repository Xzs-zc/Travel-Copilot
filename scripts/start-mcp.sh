#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
TRANSPORT="${1:-stdio}"
export PYTHONDONTWRITEBYTECODE=1
exec "$UV_BIN" run python -m backend.app.mcp.server --transport "$TRANSPORT"
