#!/usr/bin/env python3
"""Plan/install selected role releases without touching live runtime data.

Bundle layout: <bundles>/<role_id>/{definition/role.json,assets/,workflows/}.
Destination may be local or an explicit SSH target. Bundle transfer verifies
content hashes before installation; running services are never restarted here.
"""
import argparse
import json
import sys
import shlex
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from role_registry import identifier
from role_release import plan, install, activate
from role_transfer import write_bundle


def bundle_files(root, role_id):
    base = Path(root).expanduser().resolve()
    source = base / identifier(role_id)
    if source.is_symlink() or source.resolve().parent != base:
        raise ValueError("role bundle escapes selected root")
    files = {}
    for section in ("definition", "assets", "workflows"):
        directory = source / section
        if directory.is_symlink():
            raise ValueError("symlinked bundle sections forbidden")
        for path in sorted(directory.rglob("*")) if directory.exists() else []:
            if path.is_symlink():
                raise ValueError("symlinks forbidden in release bundles")
            if path.is_file():
                if source.resolve() not in path.resolve().parents:
                    raise ValueError("bundle file escapes role")
                files[path.relative_to(source).as_posix()] = path
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--role", action="append", required=True)
    parser.add_argument("--engine-sha256", required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--select-release", action="store_true", help="persist selected version; does not restart services")
    parser.add_argument("--expected-release", help="required previous release ID when a selection already exists")
    parser.add_argument("--ssh-host", help="explicit SSH host/alias for Mac mini; uses existing authentication")
    parser.add_argument("--receiver", help="absolute remote path to tools/role_receive.py")
    args = parser.parse_args(argv)
    if len(set(args.role)) != len(args.role):
        parser.error("duplicate role selection")
    if args.apply and args.destination is None:
        parser.error("--apply requires --destination")
    if args.select_release and (not args.apply or len(args.role) != 1):
        parser.error("--select-release requires --apply and exactly one --role")
    if args.ssh_host and (args.ssh_host.startswith("-") or any(c.isspace() for c in args.ssh_host)):
        parser.error("invalid SSH host")
    if args.ssh_host and (not args.receiver or not args.receiver.startswith("/")):
        parser.error("--ssh-host requires an absolute --receiver path")
    prepared = []
    for role_id in args.role:
        files = bundle_files(args.bundles, role_id)
        prepared.append((plan(role_id, args.engine_sha256, files), files))
    results = []
    for manifest, files in prepared:
        entry = {"manifest": manifest, "action": "plan"}
        if args.apply and args.ssh_host:
            command = ["python3", args.receiver, "--destination", str(args.destination)]
            if args.select_release:
                command += ["--select-release"]
                if args.expected_release:
                    command += ["--expected-release", args.expected_release]
            with tempfile.TemporaryFile() as payload:
                write_bundle(manifest, files, payload)
                payload.seek(0)
                result = subprocess.run(["ssh", "-o", "BatchMode=yes", "--", args.ssh_host,
                                         shlex.join(command)], stdin=payload, capture_output=True,
                                        text=True, timeout=1800, check=True)
            remote = json.loads(result.stdout)
            if (remote.get("role_id"), remote.get("release_id")) != (manifest["role_id"], manifest["release_id"]):
                raise ValueError("remote receiver returned a different role/release")
            entry.update(action="installed_remote", remote=remote)
        elif args.apply:
            entry.update(action="installed", path=str(install(manifest, files, args.destination)))
        if args.select_release and not args.ssh_host:
            entry["selection"] = str(activate(args.destination, manifest["role_id"], manifest["release_id"],
                                               expected_release=args.expected_release))
        results.append(entry)
    print(json.dumps({"roles": results, "selection_changed": args.select_release,
                      "services_restarted": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
