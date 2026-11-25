"""Prepare/check only the selected identity's Codex home; never copy credentials."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config import load
from imagegen_scope import environment, locked, paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    cfg = load()
    home, codex = paths(cfg)
    if args.prepare:
        with locked(cfg):
            pass
    result = {"persona": (cfg.get("identity_runtime") or {}).get("persona_id", "xiaotian"),
              "home": str(home), "codex_home": str(codex),
              "config_present": (codex / 'config.toml').is_file(), "login_ok": False}
    if codex.is_dir():
        try:
            check = subprocess.run([(cfg.get('imagegen') or {}).get('cmd') or 'codex', 'login', 'status'],
                                   env=environment(cfg), capture_output=True, text=True, timeout=20)
            result['login_ok'] = check.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            result['login_check_failed'] = True
    result['ready'] = result['config_present'] and result['login_ok']
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
