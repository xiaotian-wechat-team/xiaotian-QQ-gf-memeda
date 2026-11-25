"""Local OneBot binding for companions, reusing the registered-role reply path."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from codex_subscription import child_environment, for_store, lock_before, remaining_time
from personalization_runtime import process_started
from personalization_store import ROOT, Conflict, Store, checked, read_json, timestamp, write_json
from persona_onboarding import list_stores, metadata
from role_registry import Registry

ENABLE_TIMEOUT = 85


def _published(store):
    metadata(store)
    version, _ = store.selected()
    if not version:
        raise ValueError("请先确认创建伙伴")
    return version


def binding(store):
    return read_json(store.directory / "qq-binding.json")


def _save(store, value):
    write_json(store.directory / "qq-binding.json", value)


def status(store):
    version = _published(store)
    return _status(store, version, binding(store))


def _status(store, version, data):
    if not data:
        return {"state": "unbound", "selected_version": version}
    worker = read_json(store.directory / "qq-worker.json", {})
    live = (data["enabled"] and data["generation"] == worker.get("generation")
            and worker.get("started") and process_started(worker["pid"]) == worker["started"])
    latest = read_json(store.directory / "qq-receipt.json", {})
    if latest.get("generation") != data["generation"]:
        latest = None
    changed = data["enabled"] and data["generation"] == worker.get("generation") and worker.get("state") == "account_changed"
    return {**{k: data[k] for k in ("endpoint", "peer", "bot", "name", "checked_at", "enabled", "generation")},
            "state": worker["state"] if live or changed else "starting" if data["enabled"] else "verified",
            "receipt": latest, "selected_version": version}


@contextmanager
def _registry_lock(store, *, deadline=None):
    directory = checked(store.destination / "onboarding")
    directory.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(directory / ".qq.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as lock:
        if deadline is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            lock_before(lock, deadline)
        yield


def _uin(number):
    if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]{4,11}", number):
        raise ValueError("请填写有效的 QQ 号")
    return number


def session_root(store, data):
    bot, peer = _uin(data["bot"]), _uin(data["peer"])
    # Pre-upgrade data in instance/runtime has no verified owner. Leave it in place.
    return checked(store.runtime_root / "qq" / f"{bot}-{peer}")


def probe(endpoint, token, *, deadline=None):
    if os.environ.get("XIAOTIAN_TEST") and urlsplit(endpoint).hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("测试仅连接回环 OneBot 模拟端")
    try:
        import websocket
    except ImportError as exc:
        raise ValueError("当前 Python 缺少 websocket-client") from exc
    client = None
    deadline = time.monotonic() + 10 if deadline is None else deadline
    try:
        client = websocket.create_connection(endpoint, timeout=min(5, remaining_time(deadline)),
                                             header=[f"Authorization: Bearer {token}"])
        client.settimeout(min(5, remaining_time(deadline)))
        client.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "companion_probe"}))
        until = min(deadline, time.monotonic() + 5)
        while time.monotonic() < until:
            client.settimeout(max(0.001, until - time.monotonic()))
            data = json.loads(client.recv())
            if isinstance(data, dict) and data.get("echo") == "companion_probe":
                if data.get("status") == "ok" and data.get("retcode") == 0:
                    return _uin(str((data.get("data") or {}).get("user_id")))
                break
    except (OSError, ValueError, websocket.WebSocketException):
        pass
    finally:
        if client is not None:
            client.close(timeout=max(0, min(1, deadline - time.monotonic())))
    raise ValueError("无法核实 Bot QQ，请检查 OneBot 正向 WebSocket 地址、Access Token 与 Bot 登录状态")


def _route(store, data):
    return dict(environment=store.environment, channel="qq_onebot", peer_type="private",
                bot_account_id=data["bot"], peer_id=data["peer"])


def _check_routes(store, data):
    from config import load
    definitions = []
    for other in list_stores(store):
        config = data if other.persona == store.persona else binding(other)
        if config and config["enabled"]:
            definitions.append({"schema_version": 2, "role_id": other.persona, "dossier": {}, "workflows": {},
                                "relationships": {other.relation: {"kind": "configured", "bindings": [_route(other, config)]}}})
    try:
        Registry(definitions, store.environment)
    except ValueError as exc:
        raise Conflict("这对 Bot QQ 与用户 QQ 已绑定其他伙伴，请先停用原绑定") from exc
    legacy = ((load().get("persona") or {}).get("channels") or {}).get("qq") or {}
    if str(legacy.get("bot_account_id")) == data["bot"] and data["peer"] in {str(v) for v in legacy.get("allow_user_ids", [])}:
        raise Conflict("此私聊已由现有 QQ 通道接收，请使用自己的 Bot 或另一账号")


def verify(store, payload):
    version = _published(store)
    peer = _uin(payload.get("peer"))
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 300 or any(c.isspace() for c in endpoint):
        raise ValueError("请填写 OneBot 正向 WebSocket 地址")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OneBot 地址应为 ws:// 或 wss://，Access Token 在下方单独填写")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("OneBot 端口无效")
    token = payload.get("token")
    name = payload.get("name") or ""
    if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,512}", token):
        raise ValueError("请填写有效的 OneBot Access Token")
    if not isinstance(name, str) or len(name) > 40:
        raise ValueError("称呼最多 40 字")
    bot = probe(endpoint, token)
    if bot == peer:
        raise ValueError("请分别使用 Bot QQ 和你自己的 QQ 号")
    with _registry_lock(store):
        current = binding(store)
        if (current or {}).get("generation") != payload.get("generation"):
            raise Conflict("绑定已在另一个窗口更新，请刷新")
        verified = dict(endpoint=endpoint, token=token, peer=peer, bot=bot, name=name.strip(), enabled=False,
                        generation=uuid.uuid4().hex, checked_at=timestamp())
        _save(store, verified)
    return _status(store, version, verified)


def enable(store, payload):
    deadline = time.monotonic() + ENABLE_TIMEOUT
    version = _published(store)
    current = binding(store)
    if not current or payload.get("generation") != current["generation"] or payload.get("bot") != current["bot"]:
        raise Conflict("请探测连接并核对 Bot QQ")
    if current["enabled"]:
        with _registry_lock(store, deadline=deadline):
            if current != binding(store):
                raise Conflict("绑定已在另一个窗口更新，请刷新")
            worker = _status(store, version, current)
            if worker["state"] == "starting":
                remaining_time(deadline)
                _start(store, current)
            return worker
    profile = for_store(store)
    profile.complete("只输出 OK。", instructions="这是用户发起的 GPT 文本连接检查。只输出 OK。",
                     timeout=min(60, remaining_time(deadline)))
    remaining_time(deadline)
    if probe(current["endpoint"], current["token"], deadline=deadline) != current["bot"]:
        raise Conflict("实际 Bot QQ 已变化，请重新探测并确认")
    with _registry_lock(store, deadline=deadline):
        if current != binding(store):
            raise Conflict("绑定已在另一个窗口更新，请刷新")
        enabled = {**current, "enabled": True, "generation": uuid.uuid4().hex}
        _check_routes(store, enabled)
        remaining_time(deadline)
        _save(store, enabled)
        try:
            _start(store, enabled)
        except OSError:
            _save(store, current)
            raise
    return _status(store, version, enabled)


def disable(store, payload):
    version = _published(store)
    with _registry_lock(store):
        current = binding(store)
        if current and current["generation"] == payload.get("generation"):
            current = {**current, "enabled": False, "generation": uuid.uuid4().hex}
            _save(store, current)
        elif current:
            raise Conflict("绑定已在另一个窗口更新，请刷新")
    return _status(store, version, current)


def worker_environment(store, data):
    env = {**child_environment(), "COMPANION_QQ_ROLE": store.persona, "COMPANION_QQ_GENERATION": data["generation"],
           "XIAOTIAN_PERSONALIZATION_ROOT": str(store.root), "PERSONALIZATION_ENVIRONMENT": store.environment}
    if os.environ.get("XIAOTIAN_TEST"):
        env["XIAOTIAN_TEST"] = "1"
    return env


def _start(store, data):
    worker = subprocess.Popen([sys.executable, "-m", "companion_qq_worker"], cwd=ROOT / "src", env=worker_environment(store, data),
                              start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=worker.wait, name="companion-qq-supervisor", daemon=True).start()


def _scope():
    return Store(environment=os.environ["PERSONALIZATION_ENVIRONMENT"], persona=os.environ["COMPANION_QQ_ROLE"], relation="partner")


def _active(store):
    data = binding(store)
    return data if data and data["enabled"] is True and data["generation"] == os.environ["COMPANION_QQ_GENERATION"] else None


def runtime_config():
    from identity import IdentityConfig, PartnerConfig, RouteConfig, RuntimePaths
    from personalization_runtime import overlay
    store = _scope()
    version, role = store.selected()
    data = _active(store)
    if not version or not data:
        raise PermissionError("伙伴 QQ 私聊已停用或信息已变化")
    label = data["name"] or "你"
    partner = PartnerConfig(store.relation, label, label, label, None, None, None)
    route = RouteConfig(store.persona, store.relation, "qq_onebot", data["bot"], data["peer"], "private")
    root = session_root(store, data)
    paths = RuntimePaths.from_root(root, root / "photos" / "generated")
    paths.validate()
    capabilities = {"inbound": True, "auto_reply": True}
    identity = IdentityConfig(store.persona, role["dossier"]["identity"]["name"], partner, (route,), paths,
                              store.role_dir / "releases" / version / "assets", True, capabilities, role["dossier"])
    cfg = identity.scoped_config({"llm": {"short_backend": "codex_chatgpt", "short_timeout": 120},
                                  "memory": {"contacts": [store.relation]}, "backend": "onebot"})
    cfg["identity_runtime"].update(schema_version=2, environment=store.environment, local_companion=True,
                                   persona_runtime_root=str(store.runtime_root), qq_generation=data["generation"],
                                   routes=[{key: value for key, value in _route(store, data).items() if key != "environment"}])
    cfg["role_workflows"] = copy.deepcopy(role["workflows"])
    cfg = overlay(cfg)
    cfg["role_dossier"]["user_ref"] = {"name": label, "address": label}
    return cfg
