"""Resolve mutable resources inside the selected identity; legacy is explicit.

This is application-level validation, not a replacement for OS isolation.
"""
from pathlib import Path


def resource(name, legacy, cfg=None):
    if cfg is None:
        from config import load
        cfg = load()
    if name == "life_schedule":
        from shared_schedule import settings
        shared = settings(cfg)
        if shared:
            return shared["path"]
    scope = cfg.get("identity_runtime") or {}
    if not scope:
        return Path(legacy)
    root = Path(scope["runtime_root"]).expanduser().resolve()
    value = scope.get(name)
    if not value:
        raise ValueError(f"missing identity resource: {name}")
    path = Path(value).expanduser().resolve()
    if root not in path.parents:
        raise ValueError(f"identity resource escapes runtime: {name}")
    return path
