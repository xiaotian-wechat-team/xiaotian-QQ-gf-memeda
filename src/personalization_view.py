"""Read-only console projections. Persona SSOT: data/persona_dossier.json."""
import datetime as dt
import json
import os
from pathlib import Path


def live_state(store, *, qq_root=None):
    from personalization_deploy import binding
    from personalization_store import checked
    unavailable = dict(available=False, read_only=True, note="尚未连接本人物的运行状态")
    if store.is_companion:
        if qq_root is None:
            return unavailable
        path = qq_root / "runtime" / "memory" / "contacts" / store.relation / "STATE.json"
    elif service := binding(store):
        path = (Path(service["state_root"]) / store.environment / store.persona / "relationships" /
                store.relation / "runtime" / "memory" / "contacts" / store.relation / "STATE.json")
    else:
        # Tests must opt into a synthetic config explicitly, never inspect a
        # developer's live digital-life directory simply by opening the UI.
        if os.environ.get("XIAOTIAN_TEST") and not os.environ.get("XIAOTIAN_CONFIG_FILE"):
            return unavailable
        from config import load
        cfg = load()
        role = (cfg.get("persona") or {}).get("id") or "xiaotian"
        env = (cfg.get("personalization") or {}).get("environment") or os.environ.get("PERSONALIZATION_ENVIRONMENT", "dev")
        cid = str((cfg.get("contact") or {}).get("id") or "")
        if role != store.persona or env != store.environment or store.relation != "partner" or cid != "demo_contact":
            return unavailable
        path = Path(cfg["digital_life_dir"]).expanduser() / "contacts" / cid / "STATE.json"
    try:
        path = checked(path)
        if not path.exists():
            return unavailable
        state = json.loads(path.read_text())
        emotion = state.get("emotion") or {}
        if not isinstance(emotion, dict):
            return unavailable
        pad = emotion.get("pad") or {}
        if not isinstance(pad, dict):
            pad = {}
        return dict(available=True, read_only=True,
                    updated_at=dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(),
                    emotion={**{key:pad.get(key) for key in ("pleasure", "arousal", "dominance")},
                             **{key:emotion.get(key) for key in ("intimacy", "last_label")}},
                    mood=state.get("mood"), energy=state.get("energy"),
                    sleep=(state.get("sleep_chat") or {}).get("mode"))
    except (OSError, ValueError, TypeError, AttributeError):
        return {**unavailable, "note": "运行状态暂不可读取，原文件保持不变"}


def applied_asset(store, name, *, version=None):
    from personalization_store import checked
    from appearance_tasks import VIEWS
    if name not in {f"{kind}/{view}.png" for kind in ("face", "body") for view in ("sheet", *VIEWS)}:
        raise ValueError("invalid applied image")
    current, _ = store.selected()  # Also verifies the complete release manifest.
    if not current:
        raise ValueError("尚未应用标准素材")
    if version is not None and version != current:
        from personalization_store import Conflict
        raise Conflict("人物版本已变化，请刷新档案")
    kind, filename = name.split("/")
    root = store.role_dir / "releases" / current / "assets" / "canonical"
    path = checked(root / ("body" if kind == "body" else "") / filename)
    if not path.is_file():
        raise ValueError("此人物版本尚无标准素材")
    return path
