#!/bin/bash
# Persona SSOT: data/persona_dossier.json. Existing ChatGPT quota; layered recipe + High.
# Usage: imagegen_macbook.sh "scene" [output directory] [outfit] [realism]
# The sending host supplies PHOTO_REQUEST_JSON with its recorded scene/lighting/shots.
set -euo pipefail
SCRIPT="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT" ]; do
  SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT")" && pwd)"
  SCRIPT="$(readlink "$SCRIPT")"
  [[ "$SCRIPT" = /* ]] || SCRIPT="$SCRIPT_DIR/$SCRIPT"
done
REPO="${REPO:-$(cd -P "$(dirname "$SCRIPT")/.." && pwd)}"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
[ -x "$PYTHON" ] || { echo "Missing repository Python: $PYTHON" >&2; exit 2; }
exec "$PYTHON" "$REPO/src/macbook_imagegen.py" "$@"
