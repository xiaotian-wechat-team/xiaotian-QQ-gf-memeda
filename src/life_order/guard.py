"""Hard gates for life_order. Model / config cannot raise the ¥50 cap."""
from __future__ import annotations

import math
import re
from typing import Any

# Code-enforced hard ceiling — config may only tighten, never exceed.
AMOUNT_CAP_YUAN = 50.0
DEFAULT_CONFIRM_EXPIRY_SEC = 180
ALLOWED_CONTACT_ID = "demo_contact"
ALLOWED_PEERS = frozenset({
    "wechat_ilink:demo@im.wechat",
    "qq_onebot:10001",
})

_DEFAULT_CONFIRM = ("好", "求你", "嗯", "下吧")
_DEFAULT_CANCEL = ("算了", "不要")
_DEFAULT_SOFT = ("你求求我嘛～", "那我给你下单了哦")


def load_life_order_cfg(cfg: dict | None = None) -> dict:
    """Return life_order section; fall back to defaults without importing config if absent."""
    from .config_defaults import DEFAULTS
    blob = dict(DEFAULTS)
    if cfg is None:
        try:
            from config import load
            cfg = load()
        except Exception:
            cfg = {}
    section = (cfg or {}).get("life_order") or {}
    if isinstance(section, dict):
        blob.update(section)
    return blob


def is_enabled(cfg: dict | None = None) -> bool:
    return bool(load_life_order_cfg(cfg).get("enabled"))


def is_dry_run(cfg: dict | None = None) -> bool:
    """Default ON — missing key means dry-run."""
    return bool(load_life_order_cfg(cfg).get("dry_run", True))


def effective_cap_yuan(cfg: dict | None = None) -> float:
    """Config can only lower the cap; never above AMOUNT_CAP_YUAN."""
    raw = load_life_order_cfg(cfg).get("amount_cap_yuan", AMOUNT_CAP_YUAN)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = AMOUNT_CAP_YUAN
    if not math.isfinite(val) or val <= 0:
        val = AMOUNT_CAP_YUAN
    return min(val, AMOUNT_CAP_YUAN)


def amount_allowed(amount_yuan: Any, *, cfg: dict | None = None) -> tuple[bool, str]:
    """Reject missing, non-finite, non-positive, or above-cap amounts."""
    try:
        amt = float(amount_yuan)
    except (TypeError, ValueError):
        return False, "金额无效"
    if not math.isfinite(amt) or amt <= 0:
        return False, "金额无效"
    cap = effective_cap_yuan(cfg)
    if amt > cap:
        return False, f"单笔不能超过{cap:g}元"
    return True, ""


def _normalize_peer(peer: str | None) -> str:
    return (peer or "").strip()


def identity_allowed(
    contact_id: str | None,
    peer: str | None,
    *,
    cfg: dict | None = None,
) -> tuple[bool, str]:
    """Only fixed contact demo_contact on a deployment-approved peer.

    The peer list remains configurable so a deployment can replace its QQ
    account, but the life_order contact itself is never configurable.
    """
    lo = load_life_order_cfg(cfg)
    configured_cid = str(lo.get("allowed_contact_id") or ALLOWED_CONTACT_ID).strip()
    if configured_cid != ALLOWED_CONTACT_ID:
        return False, "life_order 只允许联系人 demo_contact"
    want_cid = ALLOWED_CONTACT_ID
    peers = lo.get("allowed_peers")
    if isinstance(peers, (list, tuple, set)) and peers:
        allow = frozenset(str(p).strip() for p in peers)
    else:
        allow = ALLOWED_PEERS
    cid = str(contact_id or "").strip()
    if cid != want_cid:
        return False, "联系人不在点单白名单"
    p = _normalize_peer(peer)
    if p not in allow:
        return False, "会话不在点单白名单"
    return True, ""


def proactive_blocked(is_proactive: bool) -> tuple[bool, str]:
    """Return (blocked, reason). Proactive messages must never start an order."""
    if is_proactive:
        return True, "主动消息不能点单"
    return False, ""


def _word_hit(text: str, words: tuple[str, ...] | list[str]) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    compact = re.sub(r"[\s~.。!！?？~～…]+", "", t)
    one_char_suffixes = ("的", "了", "呀", "啊", "吧", "嘞", "滴", "嘟", "哦", "噢", "呐", "哇", "嗯", "哒", "咯")
    for raw in words:
        w = str(raw or "").strip()
        if not w:
            continue
        if compact == w:
            return True
        if len(w) == 1:
            if compact.startswith(w) and compact[len(w):] in one_char_suffixes:
                return True
            continue
        if ((compact.startswith(w) or compact.endswith(w))
                and len(compact) <= max(8, len(w) + 4)):
            return True
        if w in ("求你", "下吧", "算了", "不要") and w in compact:
            return True
    return False


def parse_confirm_reply(text: str, *, cfg: dict | None = None) -> str:
    """Return 'confirm' | 'cancel' | 'neither'. Cancel wins if both match."""
    lo = load_life_order_cfg(cfg)
    confirm_words = tuple(lo.get("confirm_words") or _DEFAULT_CONFIRM)
    cancel_words = tuple(lo.get("cancel_words") or _DEFAULT_CANCEL)
    raw = (text or "").strip()
    compact = re.sub(r"[\s~.。!！?？~～…]+", "", raw)
    if _word_hit(raw, cancel_words):
        return "cancel"
    if re.match(r"^(?:不好|不太好|不行|不用|不必|不想|先别|别|取消)", compact):
        return "cancel"
    if re.search(r"[?？]|吗\s*$", raw) or re.search(r"[不别没]", compact):
        return "neither"
    if _word_hit(raw, confirm_words):
        return "confirm"
    return "neither"


def soft_confirm_phrase(index: int = 0, *, cfg: dict | None = None) -> str:
    lo = load_life_order_cfg(cfg)
    phrases = list(lo.get("soft_confirm_phrases") or _DEFAULT_SOFT)
    if not phrases:
        phrases = list(_DEFAULT_SOFT)
    return phrases[int(index) % len(phrases)]


def confirm_expiry_sec(cfg: dict | None = None) -> int:
    lo = load_life_order_cfg(cfg)
    try:
        sec = int(lo.get("confirm_expiry_sec", DEFAULT_CONFIRM_EXPIRY_SEC))
    except (TypeError, ValueError):
        sec = DEFAULT_CONFIRM_EXPIRY_SEC
    return max(1, sec)
