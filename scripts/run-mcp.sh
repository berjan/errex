#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source "$root/credentials/.env.mcp"
set +a
exec "$root/.venv-mcp/bin/python" "$root/scripts/errex_mcp.py"
