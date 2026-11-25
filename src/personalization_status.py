"""Consumer acknowledgements, separate from selecting a release."""
import datetime as dt
import json
import socket
import subprocess


def required_consumers(role, values, grants=None):
    legacy = (role or {}).get("adapter") == "legacy"
    workflows = (role or {}).get("workflows") or {}
    def enabled(name):
        declared = legacy or workflows.get(name, {}).get("enabled") is True
        if grants is None:
            return declared
        if not declared or grants.get("runtime_enabled") is not True:
            return False
        caps = grants.get("capabilities") or {}
        action = {"reply": "auto_reply", "image": "image_generate", "voice": "voice_transcribe", "caption": "image_caption", "content": "proactive"}.get(name, name)
        granted = caps.get(action) is True
        if name == "reply":
            granted = (granted and caps.get("inbound") is True) or caps.get("manual_send") is True
        if name in {"image", "voice", "caption"}:
            native = grants.get("native_service") or {}
            granted = granted and bool(native.get("url") and native.get("token_file"))
        return granted
    required = []
    if enabled("reply"):
        required.append("chat")
    if enabled("proactive") and values.get("proactive.enabled"):
        required.append("proactive")
    if enabled("image"):
        required.append("image")
    if enabled("reflection") and values.get("reflection.enabled"):
        required.append("reflection")
    if (legacy or enabled("content")) and (values.get("content.platforms") or values.get("content.campus_sources")):
        required.append("content")
    if enabled("voice") and values.get("voice_asr.enabled"):
        required.append("voice")
    if enabled("caption") and values.get("image_caption.enabled"):
        required.append("caption")
    return required


def grants_for(store, role, *, qq=None):
    if (role or {}).get("adapter") == "legacy":
        return None
    if store.is_companion:
        active = bool(qq and qq["enabled"])
        return {"runtime_enabled": active, "capabilities": {"inbound": active, "auto_reply": active}}
    from personalization_deploy import binding
    from personalization_store import read_json
    from pathlib import Path
    service = binding(store)
    if not service:
        return {"runtime_enabled": False, "capabilities": {}}
    path = Path(service["credentials_root"]) / store.environment / store.persona / store.relation / "grants.json"
    value = read_json(path, {})
    if any(value.get(k) != v for k, v in {"role_id": store.persona, "relation_id": store.relation, "environment": store.environment}.items()):
        return {"runtime_enabled": False, "capabilities": {}}
    return value


def capability_states(role, values, grants):
    required = set(required_consumers(role, values, grants))
    specs = [("chat", "聊天", "reply", True), ("image", "生成图片", "image", True),
             ("proactive", "主动互动", "proactive", bool(values.get("proactive.enabled"))),
             ("reflection", "日记与反思", "reflection", bool(values.get("reflection.enabled"))),
             ("caption", "理解收到的图片", "caption", bool(values.get("image_caption.enabled"))),
             ("voice", "听取语音", "voice", bool(values.get("voice_asr.enabled")))]
    legacy = (role or {}).get("adapter") == "legacy"
    result = []
    for consumer, name, workflow, selected in specs:
        supported = legacy or ((role or {}).get("workflows") or {}).get(workflow, {}).get("enabled") is True
        status = "conditional" if consumer in required else "disabled"
        note = ("已配置，等待该工作流实际使用并回报版本" if consumer in required else
                "人物选择已关闭" if not selected else "当前人物未提供此工作流" if not supported else
                "当前环境尚未授权或未配置对应服务")
        result.append(dict(id=consumer, name=name, status=status, selected=selected, supported=supported,
                           authorized=consumer in required, note=note))
    return result


def external_live(store, report):
    """A container's PID is meaningful only in its own running incarnation."""
    from personalization_store import read_json
    from role_service import command
    service = read_json(store.directory / "service.json", {})
    deployment = service.get("deployment_id")
    if not deployment:
        return False
    try:
        ids = command(["docker", "ps", "--filter", "label=role.deployment=" + deployment, "-q"]).split()
        containers = json.loads(command(["docker", "inspect", *ids])) if ids else []
        for item in containers:
            if item["Config"].get("Hostname") != report.get("host"):
                continue
            env = dict(value.split("=", 1) for value in item["Config"].get("Env", []) if "=" in value)
            expected = {"ROLE_ID": store.persona, "ROLE_RELATION": store.relation, "ROLE_ENVIRONMENT": store.environment}
            started = dt.datetime.fromisoformat(item["State"]["StartedAt"].replace("Z", "+00:00"))
            return (all(env.get(k) == value for k, value in expected.items()) and item["State"].get("Running") is True
                    and not item["State"].get("Restarting") and dt.datetime.fromisoformat(report["at"]) >= started)
    except (OSError, KeyError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
        pass
    return False


def report_status(store, report, *, external=False):
    from personalization_runtime import process_started
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(report["at"])).total_seconds()
        local = report.get("host") == socket.gethostname()
        started = report.get("process_started")
        live = bool(started and local and process_started(report["pid"]) == started)
        if external and not local:
            live = external_live(store, report)
        # A completed one-shot task confirms actual use; it is not advertised as
        # a currently running process. All reports expire and are version-bound.
        confirmed = 0 <= age <= (86400 if report.get("usage") == "run" else 3600) and (
            live or (report.get("usage") == "run" and bool(started) and local))
        return {**report, "live": live, "confirmed": confirmed}
    except (KeyError, ValueError, TypeError):
        return {**report, "live": False, "confirmed": False}
