"""Per-persona local Codex homes, following native-role-service isolation SOP.

No credentials are copied or login started. Provision/login is deployment-owned.
"""
import contextlib
import fcntl
import os
from pathlib import Path


def paths(cfg):
    scope = cfg.get("identity_runtime") or {}
    role = scope.get("persona_id") or "xiaotian"
    if scope:
        root = Path(scope["runtime_root"]).expanduser().resolve() / "runtime" / "imagegen"
    else:
        from config import ROOT
        session = Path((cfg.get("imagegen") or {}).get("session_state") or ROOT / "data/codex_session.json").expanduser()
        root = session.resolve().parent / "imagegen" / role
    return root / "home", root / "codex"


def environment(cfg):
    home, codex = paths(cfg)
    env = dict(os.environ)
    env.update(HOME=str(home), CODEX_HOME=str(codex))
    return env


@contextlib.contextmanager
def locked(cfg):
    home, codex = paths(cfg)
    if home.is_symlink() or codex.is_symlink():
        raise ValueError("Image generation homes must not be shared through symlinks")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    codex.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (codex.parent / "generation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def forbid_xiaosu_fallback(cfg):
    role = (cfg.get("identity_runtime") or {}).get("persona_id") or (cfg.get("persona") or {}).get("id")
    if role == "xiaosu":
        raise RuntimeError("Xiaosu emergency image generation is disabled; local failure must not use MacBook or relay")
