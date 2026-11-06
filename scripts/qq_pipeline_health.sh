#!/bin/bash
# QQ OneBot/NapCat full-pipeline health check (read-only, non-spam).
# Exit: 0 critical OK | 1 critical FAIL | 2 script error
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "qq_pipeline_health: no python found" >&2
  exit 2
fi
exec "$PY" "$ROOT/scripts/qq_pipeline_health.py" "$@"
