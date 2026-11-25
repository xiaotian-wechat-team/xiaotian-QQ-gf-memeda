#!/usr/bin/env python3
"""Native image adapter. Persona SSOT: data/persona_dossier.json."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from native_image_provider import generate


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    args = parser.parse_args()
    print(json.dumps(generate(json.load(sys.stdin), args.codex)))
