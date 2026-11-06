#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
WS_PORT="${XIAOTIAN_DEMO_WS_PORT:-39001}"
HTTP_PORT="${XIAOTIAN_DEMO_HTTP_PORT:-39002}"

if [[ "$PYTHON" != */* ]]; then
  PYTHON="$(command -v "$PYTHON" || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "Python environment not found. Create .venv and install requirements first." >&2
  exit 2
fi
if [[ ! -f config.json ]]; then
  cp config.example.json config.json
fi

server=""
client=""
cleanup() {
  if [[ -n "$client" ]]; then kill "$client" 2>/dev/null || true; fi
  if [[ -n "$server" ]]; then kill "$server" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

"$PYTHON" tools/onebot_mock_server.py --port "$WS_PORT" --http-port "$HTTP_PORT" &
server=$!
sleep 1
if ! kill -0 "$server" 2>/dev/null; then
  echo "Mock server failed to start; ports $WS_PORT/$HTTP_PORT may already be in use." >&2
  exit 1
fi

"$PYTHON" tools/onebot_qq_run.py --probe --ws "ws://127.0.0.1:$WS_PORT"
"$PYTHON" tools/onebot_qq_run.py --dry-run --once --stub-reply '嗯？' --ws "ws://127.0.0.1:$WS_PORT" &
client=$!
sleep 1
"$PYTHON" - "$HTTP_PORT" <<'PY'
import json
import sys
import urllib.request

request = urllib.request.Request(
    f"http://127.0.0.1:{sys.argv[1]}/inject",
    data=json.dumps({"user_id": 10001, "text": "mock 在吗"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.read().decode().strip())
PY
wait "$client"
client=""
echo "Demo passed: local OneBot probe and dry-run reply completed without external accounts."
