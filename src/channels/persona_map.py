"""通道 peer → 人格联系人 id。

硬规则（P0）：
- 仅白名单情侣 contact（默认 demo_contact / allowlisted peers）走情侣小甜人设 + digital-life。
- 未映射陌生人 → classmate:<channel>:<peer>，独立同学记忆，禁止情侣语气/发图。
- 禁止把未登记 peer 回落到 primary（旧行为会泄漏情侣人设）。
"""
from __future__ import annotations
from typing import Any, Optional
from config import load


CLASSMATE_PREFIX = "classmate:"


def allowed_peer(channel: str, peer_id: str, cfg: dict | None = None,
                 bot_account_id: str | None = None) -> bool:
    cfg = cfg or load()
    runtime = cfg.get("identity_runtime") or {}
    if runtime.get("schema_version") == 2:
        return any(r.get("channel") == channel
                   and r.get("bot_account_id") == str(bot_account_id or "")
                   and r.get("peer_id") == str(peer_id)
                   and r.get("peer_type") == "private"
                   for r in runtime.get("routes", []))
    if resolve_contact_id(channel, str(peer_id), cfg, bot_account_id) != couple_contact_id(cfg):
        return False
    if channel == "qq_onebot":
        ch = (persona_cfg(cfg).get("channels") or {}).get("qq") or {}
        allow = ch.get("allow_user_ids") or ch.get("allow_from") or []
        if isinstance(allow, (str, int)):
            allow = [allow]
        if str(peer_id) not in {str(x).strip() for x in allow}:
            return False
        expected_bot = str(ch.get("bot_account_id") or "")
        if expected_bot and str(bot_account_id or "") != expected_bot:
            return False
    return True


def require_allowed_peer(channel: str, peer_id: str, cfg: dict | None = None,
                         bot_account_id: str | None = None):
    if not allowed_peer(channel, peer_id, cfg, bot_account_id):
        raise PermissionError("Only the exact persona/channel/bot/peer route may receive messages")


def persona_cfg(cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    p = dict(cfg.get("persona") or {})
    contact = cfg.get("contact") or {}
    primary = p.get("primary_contact_id") or contact.get("id") or "demo_contact"
    aliases = dict(p.get("contact_aliases") or {})
    # 内置：当前 Mac/OCR 微信联系人（明确 id，不用 * 通配）
    if not cfg.get("identity_runtime"):
        aliases.setdefault(f"wechat_mac:{primary}", str(primary))
        aliases.setdefault(f"wechat:{primary}", str(primary))
        if contact.get("id"):
            aliases.setdefault(f"wechat:{contact['id']}", str(primary))
    return {
        "id": p.get("id") or "xiaotian",
        "primary_contact_id": str(primary),
        "contact_aliases": aliases,
        "channels": p.get("channels") or {},
        "classmate": dict(p.get("classmate") or {}),
    }


def couple_contact_id(cfg: dict | None = None) -> str:
    return str(persona_cfg(cfg)["primary_contact_id"])


def is_couple_contact(cid: str, cfg: dict | None = None) -> bool:
    return str(cid) == couple_contact_id(cfg) and not str(cid).startswith(CLASSMATE_PREFIX)


def classmate_contact_id(channel: str, peer_id: str) -> str:
    return f"{CLASSMATE_PREFIX}{channel}:{peer_id}"


def is_classmate_contact(cid: str) -> bool:
    return str(cid).startswith(CLASSMATE_PREFIX)


def resolve_contact_id(channel: str, peer_id: str, cfg: dict | None = None,
                       bot_account_id: str | None = None) -> str:
    """Resolve an exact route; never fall back to another persona's partner."""
    cfg = cfg or load()
    if (cfg.get("identity_runtime") or {}).get("schema_version") == 2:
        if allowed_peer(channel, peer_id, cfg, bot_account_id):
            return str(cfg["persona"]["primary_contact_id"])
        return classmate_contact_id(channel, peer_id)
    p = persona_cfg(cfg)
    primary = str(p["primary_contact_id"])
    aliases = p["contact_aliases"]
    if bot_account_id is not None:
        full_key = f"{channel}:{bot_account_id}:{peer_id}"
        if full_key in aliases:
            return str(aliases[full_key])
        if p.get("id") != "xiaotian":
            return classmate_contact_id(channel, peer_id)
    key = f"{channel}:{peer_id}"
    if key in aliases:
        return str(aliases[key])
    star = f"{channel}:*"
    if star in aliases:
        mapped = str(aliases[star])
        if mapped != primary:
            return mapped
    return classmate_contact_id(channel, peer_id)


def classmate_display_name(peer_id: str, raw: Optional[dict] = None) -> str:
    """Prefer QQ sender.nickname / card; else short peer id."""
    raw = raw or {}
    sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
    for k in ("card", "nickname", "name"):
        v = (sender.get(k) or raw.get(k) or "").strip()
        if v:
            # strip trailing 同学 to avoid 同学同学
            if v.endswith("同学"):
                v = v[:-2]
            return v[:16] or str(peer_id)[-6:]
    pid = str(peer_id)
    return pid[-6:] if len(pid) > 6 else pid


def classmate_address(peer_id: str, raw: Optional[dict] = None) -> str:
    name = classmate_display_name(peer_id, raw)
    return f"{name}同学"


def register_alias(channel: str, peer_id: str, contact_id: str | None = None,
                   cfg_path: str | None = None) -> dict:
    """把通道 peer 绑到 contact（写回 config.json 的 persona.contact_aliases）。
    仅工具调用；daemon 路径不要自动写配置。"""
    import json, pathlib
    from config import ROOT
    path = pathlib.Path(cfg_path) if cfg_path else ROOT / "config.json"
    data = json.loads(path.read_text())
    p = data.setdefault("persona", {})
    primary = p.get("primary_contact_id") or (data.get("contact") or {}).get("id") or "demo_contact"
    p.setdefault("id", "xiaotian")
    p["primary_contact_id"] = str(primary)
    aliases = p.setdefault("contact_aliases", {})
    aliases[f"{channel}:{peer_id}"] = str(contact_id or primary)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return aliases
