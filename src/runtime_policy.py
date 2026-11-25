"""Live capability checks shared by workflow dispatch and queue recovery."""


def allowed(action, cfg=None):
    if cfg is None:
        from config import load
        cfg = load()
    scope = cfg.get("identity_runtime") or {}
    if not scope:
        return True  # Legacy production stays unchanged during migration.
    caps = scope.get("capabilities") or {}
    if scope.get("schema_version") == 2:
        workflow = {"dry_run": "reply", "inbound": "reply", "auto_reply": "reply",
                    "manual_send": "reply", "image_generate": "image", "image_send": "image",
                    "image_caption": "caption", "voice_transcribe": "voice",
                    "avatar_update": "avatar", "reflection": "reflection", "proactive": "proactive"}.get(action)
        if workflow is None or not workflow_enabled(workflow, cfg):
            return False
    if action == "dry_run":
        return caps.get(action) is True
    if scope.get("runtime_enabled") is not True:
        return False
    if action == "auto_reply":
        return caps.get("inbound") is True and caps.get("auto_reply") is True
    return caps.get(action) is True


def workflow_enabled(name, cfg):
    if (cfg.get("identity_runtime") or {}).get("schema_version") != 2:
        return True
    return ((cfg.get("role_workflows") or {}).get(name) or {}).get("enabled") is True


def require(action, cfg=None):
    if not allowed(action, cfg):
        raise PermissionError(f"identity capability disabled: {action}")


def require_queued_send(item, *, image=False):
    # Historical records have no reliable authorization provenance. A scoped
    # identity must not reinterpret them as automatically authorized replies.
    action = item.get("authorization_action")
    from config import load
    cfg = load()
    if cfg.get("identity_runtime") and action not in ("auto_reply", "proactive"):
        raise PermissionError("queued operation lacks renewable authorization")
    require(action or "auto_reply", cfg)
    if image:
        require("image_send", cfg)
