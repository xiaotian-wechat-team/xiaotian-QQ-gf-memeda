#!/usr/bin/env python3
"""Native appearance provider. Persona SSOT: data/persona_dossier.json.

Deployment selects this fixed command and Codex binary. The gateway binds the
relationship and sandbox; no global image directory is scanned for results.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from appearance_tasks import codex_provider
from native_gateway import validate_payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True, help="deployment-owned absolute executable")
    args = parser.parse_args()
    if not Path(args.codex).is_absolute():
        raise ValueError("absolute Codex executable required")
    request = json.load(sys.stdin)
    if request["operation"] != "appearance_generate":
        raise ValueError("wrong native provider")
    payload = request["payload"]
    validate_payload("appearance_generate", payload)
    job = Path(request["output_dir"]).resolve()
    refs = []
    for i, encoded in enumerate(payload["refs"]):
        path = job / f"reference-{i}.png"
        path.write_bytes(base64.b64decode(encoded, validate=True))
        refs.append(path)
    output = job / "result.png"
    codex_provider(args.codex, payload["prompt"], refs, output, dict(os.environ))
    print(json.dumps({"image_file":"result.png"}))


if __name__ == "__main__":
    main()
