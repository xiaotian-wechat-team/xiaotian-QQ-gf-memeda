#!/usr/bin/env python3
"""Plan/reconcile only the selected role's services; preserve all runtime data."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from role_service import deployment_plan, apply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("release-root", "role", "environment", "state-root", "credentials-root", "bindings"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = deployment_plan(args.release_root, args.role, args.environment, args.state_root,
                           args.credentials_root, json.loads(Path(args.bindings).read_text()))
    print(json.dumps(apply(plan) if args.apply else plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
