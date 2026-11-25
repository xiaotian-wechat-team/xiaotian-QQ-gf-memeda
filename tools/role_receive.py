#!/usr/bin/env python3
"""Receive one role bundle over stdin, verify and install without live writes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from role_release import activate
from role_transfer import receive_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--select-release", action="store_true")
    parser.add_argument("--expected-release")
    args = parser.parse_args()
    manifest, target = receive_bundle(sys.stdin.buffer, args.destination)
    selection = None
    if args.select_release:
        selection = str(activate(args.destination, manifest["role_id"], manifest["release_id"],
                                 expected_release=args.expected_release))
    print(json.dumps({"role_id": manifest["role_id"], "release_id": manifest["release_id"],
                      "path": str(target), "selection": selection, "services_restarted": False}))


if __name__ == "__main__":
    main()
