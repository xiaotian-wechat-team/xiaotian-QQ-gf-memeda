"""Outbox send helpers: chat.db persist, test-pollution reject, partial-bubble resume."""
from __future__ import annotations

import re
from typing import Any, Optional
from sleep_chat import ProactiveSleepBlocked


def check_proactive_sleep(item: dict, now=None) -> None:
    """Guard primary, mirror and legacy proactive retries; direct replies still work."""
    original = item.get("proactive_round") or {}
    if not (original or item.get("beat_id") or
            str(item.get("reason") or "").startswith("proactive ")):
        return
    import memory
    import sleep_chat as sc
    try:
        now = sc._now(now)
        expires = sc._parse_ts(item.get("expires") or original.get("expires"))
        if expires and now > expires:
            raise ProactiveSleepBlocked("主动草稿已过期")
        beat = dict((original.get("meta") or {}).get("beat_meta") or {})
        beat.setdefault("id", item.get("beat_id"))
        cid = contact_for_item(item)
        current = memory.read_state(cid)
        sc.check_proactive_send(current, now, beat)
        if beat.get("id") == "idle_contact":
            import idle_contact
            import proactive_scheduler
            if current.get("proactive_paused"):
                raise ProactiveSleepBlocked("用户要求暂停主动联系")
            delivered = bool(already_sent_count(item) or item.get("image_sent") or item.get("link_sent"))
            rows = [] if delivered else proactive_scheduler.recent(cid)
            reason = idle_contact.stale_reason(beat, current, rows, delivered=delivered)
            if reason:
                raise ProactiveSleepBlocked(reason)
    except ProactiveSleepBlocked:
        raise
    except Exception as exc:
        raise ProactiveSleepBlocked(f"睡眠状态检查失败，停止主动补发: {exc!r}") from exc

_MOCK_RE = re.compile(r"mocked|mock\b|dry[-_]?run|\btest\b", re.I)
_TEST_PEER_RE = re.compile(r"peer_p0_|_p0_test|mockpeer|fake.?peer", re.I)


def is_test_pollution(item: dict | None) -> tuple[bool, str]:
    """Drop pytest / dry-run junk before flush (peer or reason/note)."""
    if not isinstance(item, dict):
        return False, ""
    peer = str(item.get("peer") or "")
    if _TEST_PEER_RE.search(peer):
        return True, "test_peer"
    blob = f"{item.get('reason') or ''} {item.get('note') or ''}"
    if _MOCK_RE.search(blob):
        return True, "mocked"
    return False, ""


def split_outbound_bubbles(text: str | None) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        from bubble_split import split_bubbles
        return list(split_bubbles(text) or [text])
    except Exception:
        return [text]


def bubbles_suffix(text: str | None, sent_n: int) -> str:
    """Join bubbles[sent_n:] with newlines (empty if all sent)."""
    bubbles = split_outbound_bubbles(text)
    n = max(0, int(sent_n or 0))
    rest = bubbles[n:]
    return "\n".join(rest)


def already_sent_count(item: dict | None) -> int:
    if not isinstance(item, dict):
        return 0
    raw = item.get("sent_bubbles")
    if isinstance(raw, list):
        return len(raw)
    try:
        return max(0, int(item.get("sent_bubble_count") or 0))
    except Exception:
        return 0


def record_sent_bubble(item: dict, bub: str) -> None:
    """Append to item['sent_bubbles'] and sync count + remaining text fields."""
    lst = item.get("sent_bubbles")
    if not isinstance(lst, list):
        lst = []
        item["sent_bubbles"] = lst
    lst.append(bub)
    item["sent_bubble_count"] = len(lst)
    # Shrink text to unsent suffix so a naive retry cannot resend prefix.
    orig = item.get("orig_text")
    if orig is None:
        item["orig_text"] = item.get("text") or ""
        orig = item["orig_text"]
    item["text"] = bubbles_suffix(orig, len(lst))


def persist_outbound_bubble(
    cid: str,
    channel: str,
    bub: str,
    *,
    occ: Any = "outbox",
    type_: str = "outbox",
) -> bool:
    """Insert one me-bubble into chat.db (bridge-compatible fingerprint).

    Returns True if inserted, False on dup fp / error. Never raises to callers.
    """
    if cid != "demo_contact" or not (bub or "").strip():
        return False
    try:
        import memory
        c = memory.db()
        # Stable operation identity makes late/duplicate ACK bookkeeping idempotent.
        fp = memory.fingerprint(cid, "me", f"{channel}:{bub}", "[confirmed-outbox]", occ)
        try:
            inserted = bool(memory.insert(c, cid, "me", bub, fp, type=type_))
        finally:
            c.close()
        if inserted:
            import datetime
            memory.append_diary(cid, f"[{channel}] 我发「{bub}」")
            memory.update_state(cid, last_reply_at=__import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S"))
        return inserted
    except Exception as e:
        print(f"[outbox] persist bubble skip: {e!r}", flush=True)
        return False


def contact_for_item(item: dict | None, default: str = "demo_contact") -> str:
    if isinstance(item, dict) and item.get("contact"):
        return str(item["contact"])
    try:
        from config import load
        p = (load().get("persona") or {})
        return str(p.get("primary_contact_id") or default)
    except Exception:
        return default


def channel_tag(kind: str) -> str:
    k = (kind or "").lower()
    if k in ("onebot", "qq", "qq_onebot"):
        return "qq_onebot"
    return "wechat_ilink"
