"""Explicitly shared calendar source; persona state and relationship memory stay local."""
import copy
from pathlib import Path


def settings(cfg=None):
    if cfg is None:
        from config import load
        cfg = load()
    value = cfg.get("shared_life_schedule") or {}
    if not value:
        return None
    if not isinstance(value, dict) or not value.get("path") or not value.get("owner"):
        raise ValueError("shared_life_schedule requires path and owner")
    path = Path(value["path"]).expanduser()
    if not path.is_absolute():
        raise ValueError("shared_life_schedule.path must be absolute")
    role = (cfg.get("identity_runtime") or {}).get("persona_id") or (cfg.get("persona") or {}).get("id") or "xiaotian"
    return {"path": path.resolve(), "owner": value["owner"], "role": role}


def enabled(cfg=None):
    return settings(cfg) is not None


def calendar_view(doc, cfg=None):
    shared = settings(cfg)
    if not shared or shared["owner"] == shared["role"]:
        return doc
    view = copy.deepcopy(doc)
    view.pop("memory_ledger", None)
    for day in view.get("days", []):
        day.pop("memory", None)
    return view


def check_write(path, cfg=None):
    shared = settings(cfg)
    if shared and Path(path).resolve() == shared["path"] and shared["owner"] != shared["role"]:
        raise PermissionError("shared calendar is maintained by its owner; persona overrides stay separate")
