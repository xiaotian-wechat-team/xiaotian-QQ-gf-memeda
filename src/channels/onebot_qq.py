"""QQ OneBot v11 适配器（SnowLuma / NapCat → 小甜 brain）。

- 协议：OneBot v11 正向 WebSocket（连协议端 ws://host:3001）
- 可选：HTTP 事件上报监听（协议端 POST）+ HTTP API 发私聊
- 不依赖 DSH；Mac 无原生 SnowLuma，需 Docker/Linux 或 NapCat 等协议端
- 默认 dry-run：打印将发送的回复，不 write
- 记忆：白名单 uin → 情侣 contact demo_contact；其余 → classmate 独立库（不进情侣记忆）

用法（仓库根）：
  .venv/bin/python tools/onebot_qq_run.py --self-test
  .venv/bin/python tools/onebot_qq_run.py --inject '在吗' --stub-reply '嗯？'
  .venv/bin/python tools/onebot_qq_run.py --probe
  .venv/bin/python tools/onebot_qq_run.py --dry-run --once
  .venv/bin/python tools/onebot_qq_run.py --send
  .venv/bin/python tools/onebot_qq_run.py --http-listen 127.0.0.1:8765 --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from .types import Inbound, Outbound
from .persona_map import resolve_contact_id, persona_cfg, allowed_peer, require_allowed_peer
from .send_receipts import AckTracker, DeliveryFailed, DeliveryUnknown, checked_receipt
from .outbox_store import serialized as _serialize_outbox
from proactive_limiter import serialized_retry as _serialize_proactive_retry

try:
    from config import ROOT as _CFG_ROOT
    ROOT = Path(_CFG_ROOT)
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[2]

OUTBOX_FILE = ROOT / "data" / "qq_outbox.json"  # legacy; runtime uses outbox_store SQLite
MEDIA_IN_DIR = ROOT / "data" / "media" / "qq_in"

try:
    import websocket  # websocket-client
except ImportError:
    websocket = None  # type: ignore

CHANNEL = "qq_onebot"
_re_voice_bare = re.compile(r"\[语音(?:\s*\d+秒)?\]")

def _qq_allow_ids(cfg: dict | None = None) -> set[str]:
    """仅白名单 QQ 号可进 brain；默认空=不放行任何人（防误聊）。"""
    p = persona_cfg(cfg)
    ch = (p.get("channels") or {}).get("qq") or {}
    raw = ch.get("allow_user_ids") or ch.get("allow_from") or []
    if isinstance(raw, (str, int)):
        raw = [raw]
    return {str(x).strip() for x in raw if str(x).strip()}


def _is_allowed_peer(peer_id: str, cfg: dict | None = None) -> bool:
    return allowed_peer(CHANNEL, peer_id, cfg)


def _classmate_media_flags(cfg: dict | None = None) -> tuple[bool, bool]:
    """persona.classmate.allow_image / allow_voice from config.json (default False)."""
    cm = persona_cfg(cfg).get("classmate") or {}
    allow_image = bool(cm.get("allow_image"))
    # voice defaults false; optional allow_voice key if ever enabled
    allow_voice = bool(cm.get("allow_voice", False))
    return allow_image, allow_voice


def _outbound_media_permitted(peer_id: str, kind: str, cfg: dict | None = None) -> bool:
    """Only the explicitly allowlisted peer mapped to contact demo_contact may receive media."""
    return _is_allowed_peer(peer_id, cfg)


def _gated_send_image(send_image, peer_id: str, path: str, caption=None):
    """Wrap outbox/flush image send: reject non-couple media."""
    if send_image is None:
        return
    if not _outbound_media_permitted(peer_id, "image"):
        print(
            f"[onebot] REJECT image to non-couple peer={peer_id} (allow_image=false)",
            flush=True,
        )
        raise PermissionError(f"image forbidden for non-couple peer={peer_id}")
    send_image(peer_id, path, caption=caption)



def _load_outbox() -> dict:
    from .outbox_store import load as _ob_load
    return _ob_load("onebot")


def _save_outbox(data: dict) -> None:
    from .outbox_store import save as _ob_save
    _ob_save("onebot", data or {"items": []})



def _outbox_is_goodnight(text: str) -> bool:
    try:
        import sleep_chat as _sc
        return _sc.is_goodnight_text(text)
    except Exception:
        return bool(text) and ("晚安" in text or "我先睡" in text or "睡了" in text)


def _is_night_hour(now=None) -> bool:
    """Night/quiet window for stale-goodnight drop; reads proactive.quiet_hours."""
    import timeutil as _tu
    return _tu.in_quiet_hours(now)


def enqueue_outbox(
    *,
    peer: str,
    text: str,
    contact: str = "demo_contact",
    reason: str = "",
    item_id: str | None = None,
    image_path: str | None = None,
) -> str:
    """Shared QQ outbox enqueue (proactive / fail-closed send)."""
    require_allowed_peer(CHANNEL, peer)
    if str(contact) != "demo_contact":
        raise PermissionError("Only contact demo_contact may enter the outbox")
    ob = _load_outbox()
    oid = item_id or f"qq-{int(time.time())}"
    ids = {x.get("id") for x in (ob.get("items") or [])}
    if oid in ids:
        return oid
    it = {
        "id": oid,
        "peer": str(peer),
        "contact": contact,
        "text": text or "",
        "reason": reason,
        "created": __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
        "channel": "onebot",
    }
    if image_path:
        it["image_path"] = str(image_path)
    ob.setdefault("items", []).append(it)
    _save_outbox(ob)
    return oid


@_serialize_outbox("onebot")
@_serialize_proactive_retry
def flush_qq_outbox(
    send_text,
    send_image=None,
    *,
    peer_id: str,
    after_inbound_text: str | None = None,
    now=None,
) -> list[dict]:
    """Flush pending QQ outbox AFTER current inbound reply.

    send_text(peer, text) / send_image(peer, path, caption=None) callables.
    Stale goodnight at night / while in-chat → drop (align iLink).
    require_media / image_path: never degrade to text-only if image cannot send.
    Persists each sent bubble; resumes from sent_bubbles (no prefix resend).
    """
    if not peer_id or send_text is None or not _is_allowed_peer(peer_id):
        return []
    data = _load_outbox()
    items = data.get("items") or []
    if not items:
        return []
    import datetime as _dt
    from pathlib import Path as _P
    from channels.outbox_send import (
        ProactiveSleepBlocked,
        check_proactive_sleep,
        already_sent_count,
        channel_tag,
        contact_for_item,
        is_test_pollution,
        persist_outbound_bubble,
        record_sent_bubble,
        split_outbound_bubbles,
    )
    fixed_now = now
    send_now = lambda: fixed_now if fixed_now is not None else __import__("timeutil").now_local()
    now = send_now()
    in_chat = bool((after_inbound_text or "").strip())
    night = _is_night_hour(now)
    ch_tag = channel_tag("onebot")
    results = []
    changed = False
    for it in items:
        if it.get("status") not in (None, "pending", "hold"):
            continue
        if it.get("peer") and str(it["peer"]) != str(peer_id):
            continue
        def _sleep_guard():
            check_proactive_sleep(it, send_now())

        try:
            _sleep_guard()
        except ProactiveSleepBlocked as exc:
            it.update(status="dropped", note=str(exc), dropped_at=send_now().isoformat(sep=" "))
            results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": str(exc)})
            changed = True
            continue
        from proactive_limiter import retry_reason
        limited = retry_reason(it, now)
        if limited:
            if limited == "主动轮次已确认发送":
                it.update(status="sent", sent_at=now.strftime("%Y-%m-%d %H:%M:%S"))
            elif limited == "分享内容重复，禁止补发":
                it["status"] = "dropped"
            elif limited == "主动草稿已过期":
                it["status"] = "expired"
            it["note"] = limited
            changed = True
            continue
        dirty, dw = is_test_pollution(it)
        if dirty:
            it["status"] = "dropped"
            it["note"] = f"test pollution drop: {dw}"
            it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[onebot] outbox drop pollution id={it.get('id')} why={dw}", flush=True)
            results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": dw})
            changed = True
            continue
        text = (it.get("text") or "").strip()
        image = it.get("image_path")
        require_media = bool(it.get("require_media") or it.get("surf_require_media"))
        if image and not _P(str(image)).is_file():
            if require_media:
                it["status"] = "dropped"
                it["note"] = "surf/require_media: image missing on flush"
                it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                results.append({"id": it.get("id"), "ok": False, "dropped": True})
                changed = True
                continue
            image = None
        if not text and not image:
            it["status"] = "dropped"
            it["note"] = "empty"
            changed = True
            continue
        try:
            import schedule_fidelity as _sf
            _slot = _sf.resolve_slot(now=now)
            stale, why = _sf.outbox_item_stale(it, now=now, slot=_slot)
            if stale:
                it["status"] = "dropped"
                it["note"] = f"fidelity drop: {why}"
                it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[onebot] outbox drop stale id={it.get('id')} why={why}",
                    flush=True,
                )
                results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": why})
                changed = True
                continue
        except Exception as _fe:
            it["status"] = "dropped"
            it["note"] = f"fidelity fail-closed: {_fe!r}"
            it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[onebot] outbox fidelity fail-closed id={it.get('id')}: {_fe!r}", flush=True)
            results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": "fidelity_error"})
            changed = True
            continue
        if text and _outbox_is_goodnight(text) and (night or in_chat):
            it["status"] = "dropped"
            it["note"] = (
                f"stale goodnight dropped (night={night} in_chat={in_chat}); "
                "current them already answered first"
            )
            it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[onebot] outbox drop stale goodnight id={it.get('id')} "
                f"night={night} in_chat={in_chat}",
                flush=True,
            )
            results.append({"id": it.get("id"), "ok": False, "dropped": True})
            changed = True
            continue

        cid = contact_for_item(it)
        if cid != "demo_contact":
            continue
        it["status"] = "sending"
        _save_outbox(data)
        oid = str(it.get("id") or "out")

        def _deliver(fn, value, index, kind="text", item_ref=it, delivery_cid=cid, delivery_oid=oid, stamp=now):
            # Freeze the item id and payload in this per-operation closure.
            operation = f"outbox:{delivery_oid}:{index}"
            worker_ident = threading.get_ident()
            def record(target):
                if kind == "image":
                    if target.get("image_sent"):
                        return
                    target["image_sent"] = True
                    piece = "[图片]"
                elif kind == "link":
                    if target.get("link_sent"):
                        return
                    target["link_sent"] = True
                    piece = value
                else:
                    if already_sent_count(target) > int(index):
                        return
                    record_sent_bubble(target, value)
                    piece = value
                persist_outbound_bubble(delivery_cid, ch_tag, piece, occ=operation, type_="outbox")
                original = target.get("proactive_round") or {}
                pending = (original.get("meta") or {}).get("pending_delivery")
                if pending:
                    import brain
                    brain.commit_delivery(delivery_cid, pending, delivery_id=f"proactive:{delivery_oid}", sent_text=piece)
                complete = (not target.get("text") and
                            (not target.get("link_url") or target.get("link_sent")) and
                            (not target.get("image_path") or target.get("image_sent")))
                target["status"] = "sent" if complete else "hold"
                if complete:
                    target["sent_at"] = __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S")
                    from proactive_limiter import record_outbox_success
                    record_outbox_success(target, stamp)
            def confirmed():
                if threading.get_ident() == worker_ident:
                    record(item_ref)
                    _save_outbox(data)
                else:
                    from .outbox_store import channel_lock
                    with channel_lock("onebot"):
                        latest = _load_outbox()
                        target = next((x for x in latest.get("items", []) if str(x.get("id")) == delivery_oid), None)
                        if target is not None:
                            record(target)
                            _save_outbox(latest)
            it["status"] = "sending"
            _save_outbox(data)
            kwargs = {"caption": None} if kind == "image" else {}
            if getattr(fn, "ack_aware", False):
                return fn(peer_id, value, on_confirmed=confirmed, operation_id=operation, **kwargs)
            result = fn(peer_id, value, **kwargs)
            if isinstance(result, dict) and "retcode" in result:
                checked_receipt(result)
            confirmed()
            return result

        try:
            from bubble_split import bubble_gap_sec
        except Exception:
            def bubble_gap_sec(*_a, **_k):  # type: ignore
                return 0.4

        try:
            if image:
                if send_image is None:
                    it["status"] = "hold"
                    it["note"] = "image present but send_image unavailable (no text-only)"
                    print(
                        f"[onebot] outbox HOLD id={it.get('id')}: no send_image",
                        flush=True,
                    )
                    results.append({"id": it.get("id"), "ok": False,
                                    "error": "no send_image", "require_media": require_media})
                    changed = True
                    continue
                if text:
                    if it.get("orig_text") is None:
                        it["orig_text"] = text
                    bubbles = split_outbound_bubbles(it.get("orig_text") or text)
                    skip = already_sent_count(it)
                    for i, bub in enumerate(bubbles):
                        if i < skip:
                            continue
                        if i:
                            time.sleep(float(bubble_gap_sec(bub) or 0.4))
                        _sleep_guard()
                        _deliver(send_text, bub, i)
                        changed = True
                    if bubbles:
                        time.sleep(float(bubble_gap_sec(bubbles[-1]) or 0.8))
                link = (it.get("link_url") or "").strip()
                if link and not it.get("link_sent"):
                    _sleep_guard()
                    _deliver(send_text, link, "link", "link")
                    changed = True
                    time.sleep(0.8)
                _sleep_guard()
                if not it.get("image_sent"):
                    _deliver(send_image, str(image), "image", "image")
            elif text:
                if it.get("orig_text") is None:
                    it["orig_text"] = text
                bubbles = split_outbound_bubbles(it.get("orig_text") or text)
                skip = already_sent_count(it)
                for i, bub in enumerate(bubbles):
                    if i < skip:
                        continue
                    if i:
                        time.sleep(float(bubble_gap_sec(bub) or 0.4))
                    _sleep_guard()
                    _deliver(send_text, bub, i)
                    changed = True
            link = (it.get("link_url") or "").strip()
            if link and not it.get("link_sent"):
                _sleep_guard()
                _deliver(send_text, link, "link", "link")
                changed = True
            it["status"] = "sent"
            it["sent_at"] = __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S")
            it["note"] = f"outbox flush after_inbound={bool(after_inbound_text)}"
            it["text"] = ""
            print(f"[onebot] outbox flushed id={it.get('id')}", flush=True)
            results.append({"id": it.get("id"), "ok": True})
            try:
                import schedule_fidelity as _sf
                from proactive_limiter import record_outbox_success
                record_outbox_success(it, now)
                _sf.mark_asked_his_day_on_flush_success(it, now=now)
            except Exception as _me:
                print(f"[onebot] ask mark on flush skip: {_me!r}", flush=True)
        except ProactiveSleepBlocked as e:
            it.update(status="dropped", note=str(e), dropped_at=send_now().isoformat(sep=" "))
            results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": str(e)})
        except Exception as e:
            it["status"] = "hold" if getattr(e, "delivery_status", "unknown") == "failed" else "unknown"
            it["note"] = f"outbox flush fail: {e!r}"
            print(
                f"[onebot] outbox flush FAIL id={it.get('id')}: {e!r} "
                f"sent_bubbles={already_sent_count(it)}",
                flush=True,
            )
            results.append({"id": it.get("id"), "ok": False, "error": repr(e),
                            "sent_bubbles": already_sent_count(it)})
        changed = True
    if changed:
        _save_outbox(data)
    return results



def _seg_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(str((seg.get("data") or {}).get("text") or ""))
            elif isinstance(seg, str):
                parts.append(seg)
        return "".join(parts)
    return str(message or "")


def _seg_media(message: Any) -> tuple[list[dict], list[dict]]:
    """Return (images, records) segment data dicts from OneBot message array."""
    images: list[dict] = []
    records: list[dict] = []
    if not isinstance(message, list):
        return images, records
    for seg in message:
        if not isinstance(seg, dict):
            continue
        typ = seg.get("type")
        data = seg.get("data") or {}
        if typ == "image":
            images.append(dict(data))
        elif typ in ("record", "voice"):
            records.append(dict(data))
    return images, records


def _maybe_cache_file(url_or_path: str, *, kind: str) -> str:
    """Best-effort download http(s) media into data/media/qq_in; return local path or original."""
    s = str(url_or_path or "").strip()
    if not s:
        return s
    if s.startswith("base64://"):
        return s
    p = Path(s).expanduser()
    if p.is_file():
        return str(p)
    if not (s.startswith("http://") or s.startswith("https://")):
        return s
    try:
        MEDIA_IN_DIR.mkdir(parents=True, exist_ok=True)
        ext = ".bin"
        low = s.split("?", 1)[0].lower()
        for e in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".silk", ".amr", ".mp3", ".wav"):
            if low.endswith(e):
                ext = e
                break
        if kind == "image" and ext == ".bin":
            ext = ".jpg"
        if kind == "record" and ext == ".bin":
            ext = ".silk"
        dest = MEDIA_IN_DIR / f"{kind}_{int(time.time())}_{abs(hash(s)) % 10_000_000}{ext}"
        req = urllib.request.Request(s, headers={"User-Agent": "xiaotian-onebot/0.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            dest.write_bytes(resp.read())
        return str(dest)
    except Exception as e:  # pragma: no cover
        print(f"[onebot] media cache skip: {e!r}", file=sys.stderr, flush=True)
        return s


def normalize_event(ev: dict) -> Optional[Inbound]:
    """OneBot 事件 → Inbound；私聊 text / image / record。"""
    if ev.get("post_type") != "message":
        return None
    if ev.get("message_type") not in ("private", "friend"):
        return None  # 群聊暂不接
    user_id = str(ev.get("user_id") or (ev.get("sender") or {}).get("user_id") or "")
    if not user_id or not _is_allowed_peer(user_id):
        return None
    message = ev.get("message") or ev.get("raw_message") or ""
    plain = _seg_text(message).strip()
    images, records = _seg_media(message)
    text = plain
    meta_extra: dict[str, Any] = {}
    if images:
        paths = []
        for im in images:
            ref = im.get("url") or im.get("file") or im.get("path") or ""
            if ref:
                paths.append(_maybe_cache_file(str(ref), kind="image"))
        meta_extra["image_paths"] = paths
        meta_extra["image_count"] = len(images)
        tag = "[图片]" if len(images) == 1 else f"[图片×{len(images)}]"
        text = f"{text} {tag}".strip() if text else tag
    if records:
        paths = []
        transcripts: list[str] = []
        duration_sec: int | None = None
        for rc in records:
            ref = rc.get("url") or rc.get("file") or rc.get("path") or ""
            if ref:
                paths.append(_maybe_cache_file(str(ref), kind="record"))
            # NapCat/QQ may already attach STT on the record segment after translate
            for k in ("text", "transcript", "translate", "ptt_text", "asr_text"):
                t = rc.get(k)
                if isinstance(t, str) and t.strip():
                    transcripts.append(t.strip())
                    break
            for dk in ("duration", "file_duration", "time"):
                dv = rc.get(dk)
                if dv is None:
                    continue
                try:
                    # NapCat duration often seconds (float/int) or ms if large
                    n = float(dv)
                    if n > 1000:  # likely ms
                        n = n / 1000.0
                    if n > 0:
                        duration_sec = max(1, int(round(n)))
                        break
                except (TypeError, ValueError):
                    pass
        meta_extra["voice_paths"] = paths
        meta_extra["voice_count"] = len(records)
        if ev.get("message_id") is not None:
            meta_extra["message_id"] = ev.get("message_id")
        if transcripts:
            meta_extra["voice_transcript"] = transcripts[0]
            meta_extra["qq_stt_source"] = "record_segment"
        # Prefer duration tag when known: [语音 3秒]
        if "[语音" not in text:
            tag = f"[语音 {duration_sec}秒]" if duration_sec else "[语音]"
            text = f"{text} {tag}".strip() if text else tag
        # Soft-enrich when segment already carries QQ STT (bridge ASR will no-op)
        if transcripts:
            bare = _re_voice_bare.search(text)
            if bare:
                try:
                    from voice_asr import format_tag_with_transcript

                    text = (
                        text[: bare.start()]
                        + format_tag_with_transcript(bare.group(0), transcripts[0])
                        + text[bare.end() :]
                    )
                except Exception:
                    pass

    if not text.strip():
        return None
    if records and not images and not plain:
        msg_type = "record"
    elif images and not plain and not records:
        msg_type = "image"
    else:
        msg_type = "text"
    raw = dict(ev)
    if meta_extra:
        raw = {**raw, "_xiaotian_media": meta_extra}
    return Inbound(
        channel=CHANNEL,
        peer_id=user_id,
        text=text.strip(),
        msg_id=str(ev.get("message_id") or ""),
        ts=str(ev.get("time") or ""),
        msg_type=msg_type,
        raw=raw,
    )


def make_private_event(user_id: str, text: str, message_id: int = 1, ts: int | None = None) -> dict:
    """构造一条可 normalize 的私聊事件（inject / mock）。"""
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": int(user_id) if str(user_id).isdigit() else user_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "message_id": message_id,
        "time": int(ts if ts is not None else time.time()),
        "sender": {"user_id": int(user_id) if str(user_id).isdigit() else user_id},
    }


def send_private_text_ws(ws, user_id: str, text: str, echo: str = "xiaotian", *,
                         tracker=None, on_confirmed=None, operation_id=None) -> dict:
    require_allowed_peer(CHANNEL, user_id)
    payload = {
        "action": "send_private_msg",
        "params": {"user_id": int(user_id) if str(user_id).isdigit() else user_id, "message": text},
        "echo": echo,
    }
    if tracker is None:
        raise DeliveryFailed("OneBot sends require an ACK tracker")
    return tracker.send(ws, payload, on_confirmed=on_confirmed, operation_id=operation_id)


def _checked_send_receipt(raw: str) -> dict:
    """HTTP 200 alone is not delivery: require the OneBot success receipt."""
    try:
        receipt = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise DeliveryUnknown("OneBot send returned invalid JSON") from exc
    return checked_receipt(receipt)


def send_private_text_http(api_base: str, user_id: str, text: str, token: str = "") -> dict:
    """OneBot HTTP API：POST /send_private_msg。"""
    require_allowed_peer(CHANNEL, user_id)
    url = api_base.rstrip("/") + "/send_private_msg"
    body = json.dumps(
        {"user_id": int(user_id) if str(user_id).isdigit() else user_id, "message": text},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return _checked_send_receipt(raw)


def send_private_record_ws(ws, user_id: str, file_ref: str, echo: str = "xiaotian_voice", *,
                           tracker=None, on_confirmed=None, operation_id=None) -> dict:
    """发 QQ 语音条。file_ref: 本地路径 / URL / base64://…；NapCat 会转 SILK。"""
    require_allowed_peer(CHANNEL, user_id)
    payload = {
        "action": "send_private_msg",
        "params": {
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "message": [{"type": "record", "data": {"file": file_ref}}],
        },
        "echo": echo,
    }
    if tracker is None:
        raise DeliveryFailed("OneBot sends require an ACK tracker")
    return tracker.send(ws, payload, on_confirmed=on_confirmed, operation_id=operation_id)



def send_private_record_http(
    api_base: str, user_id: str, file_ref: str, token: str = ""
) -> dict:
    """OneBot HTTP API：POST /send_private_msg（record 段）。"""
    require_allowed_peer(CHANNEL, user_id)
    ref = _file_ref_for_onebot(file_ref)
    url = api_base.rstrip("/") + "/send_private_msg"
    body = json.dumps(
        {
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "message": [{"type": "record", "data": {"file": ref}}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return _checked_send_receipt(raw)


def _file_ref_for_onebot(path_or_url: str) -> str:
    """本地文件 → base64://…；已是 URL / base64 / file:// 则原样。"""
    from pathlib import Path as _P
    import base64 as _b64
    s = str(path_or_url or "")
    if not s:
        return s
    if s.startswith(("base64://", "http://", "https://", "file://")):
        return s
    p = _P(s).expanduser()
    if p.is_file():
        return "base64://" + _b64.b64encode(p.read_bytes()).decode("ascii")
    # NapCat 也接受绝对路径；给 file:// 更稳
    if p.is_absolute():
        return p.as_uri()
    return s


def send_private_image_ws(
    ws,
    user_id: str,
    image_ref: str,
    *,
    caption: str | None = None,
    echo: str = "xiaotian_image",
    tracker=None, on_confirmed=None, operation_id=None,
) -> dict:
    """发私聊图片（OneBot v11 image 段；可选同条 text 配文）。"""
    require_allowed_peer(CHANNEL, user_id)
    ref = _file_ref_for_onebot(image_ref)
    segs: list[dict] = []
    cap = (caption or "").strip()
    if cap:
        segs.append({"type": "text", "data": {"text": cap}})
    segs.append({"type": "image", "data": {"file": ref}})
    payload = {
        "action": "send_private_msg",
        "params": {
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "message": segs,
        },
        "echo": echo,
    }
    if tracker is None:
        raise DeliveryFailed("OneBot sends require an ACK tracker")
    return tracker.send(ws, payload, on_confirmed=on_confirmed, operation_id=operation_id)


def send_private_image_http(
    api_base: str,
    user_id: str,
    image_ref: str,
    *,
    caption: str | None = None,
    token: str = "",
) -> dict:
    """OneBot HTTP API：POST /send_private_msg（image 段）。"""
    require_allowed_peer(CHANNEL, user_id)
    ref = _file_ref_for_onebot(image_ref)
    segs: list[dict] = []
    cap = (caption or "").strip()
    if cap:
        segs.append({"type": "text", "data": {"text": cap}})
    segs.append({"type": "image", "data": {"file": ref}})
    url = api_base.rstrip("/") + "/send_private_msg"
    body = json.dumps(
        {
            "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            "message": segs,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return _checked_send_receipt(raw)


def process_inbound(
    inbound: Inbound,
    *,
    dry: bool,
    send: Optional[Callable[[Outbound], None]] = None,
    reply_fn=None,
    persist: Optional[bool] = None,
    should_cancel=None,
) -> dict:
    from .bridge import handle_inbound

    cid = resolve_contact_id(inbound.channel, inbound.peer_id)
    print(
        f"[onebot] in peer={inbound.peer_id} → contact={cid} text={inbound.text[:80]!r}",
        flush=True,
    )
    result = handle_inbound(inbound, dry=dry, send=send, reply_fn=reply_fn, persist=persist, should_cancel=should_cancel)
    print(
        f"[onebot] reply dry={dry} persist={result.get('persist')}: {result.get('reply')!r}",
        flush=True,
    )
    return result


class OneBotQQRunner:
    """正向 WS 客户端：连协议端，收事件 → bridge → 可选回发。"""

    def __init__(
        self,
        ws_uri: str,
        token: str = "",
        *,
        dry: bool = True,
        once: bool = False,
        reconnect: bool = True,
        reply_fn=None,
    ):
        self.ws_uri = ws_uri
        self.token = token or ""
        self.dry = dry
        self.once = once
        self.reconnect = reconnect
        self.reply_fn = reply_fn
        self._ws = None
        self._got = 0
        self._stop = False
        self._login_info: dict = {}
        self._burst_lock = threading.Lock()
        self._burst_events = []
        self._burst_worker = None
        self._receipts = AckTracker()

    def _headers(self) -> list[str]:
        if not self.token:
            return []
        return [f"Authorization: Bearer {self.token}"]

    def _uri(self) -> str:
        uri = self.ws_uri
        if self.token and "access_token=" not in uri:
            sep = "&" if "?" in uri else "?"
            uri = f"{uri}{sep}access_token={self.token}"
        return uri

    def on_open(self, ws):
        print(f"[onebot] ws open {self.ws_uri}", flush=True)
        try:
            ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "login"}))
        except Exception as e:  # pragma: no cover
            print(f"[onebot] get_login_info send fail: {e}", file=sys.stderr)

    def on_message(self, _ws, message: str):
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            return
        if ev.get("echo") == "login" and isinstance(ev.get("data"), dict):
            self._login_info = ev["data"]
            print(
                f"[onebot] login_info uin={self._login_info.get('user_id')} "
                f"nick={self._login_info.get('nickname')!r}",
                flush=True,
            )
            return
        if ev.get("post_type") == "meta_event":
            return
        # API 回执 → short-lived local cache (NOT couple memory)
        if "echo" in ev and ev.get("post_type") is None and "message_type" not in ev:
            if self._receipts.resolve(ev):
                return
            try:
                from .qq_receipts import record as _recv_rec
                _recv_rec(
                    "api_echo",
                    ev,
                    echo=str(ev.get("echo") or ""),
                    status=str(ev.get("status") or ev.get("retcode") or ""),
                )
            except Exception as _re:
                print(f"[onebot] receipt cache skip: {_re!r}", flush=True)
            return
        # notice 事件也进短缓存，不进情侣记忆
        if ev.get("post_type") == "notice":
            if not _is_allowed_peer(str(ev.get("user_id") or "")):
                return
            try:
                from .qq_receipts import record as _recv_rec
                _recv_rec(
                    "notice",
                    ev,
                    peer=str(ev.get("user_id") or ev.get("group_id") or ""),
                    status=str(ev.get("notice_type") or ""),
                )
            except Exception as _re:
                print(f"[onebot] notice receipt skip: {_re!r}", flush=True)
            return
        # 好友申请：只同意白名单
        if ev.get("post_type") == "request" and ev.get("request_type") == "friend":
            flag = ev.get("flag") or ""
            uid = str(ev.get("user_id") or "")
            approve = _is_allowed_peer(uid)
            if not approve:
                return
            print(
                f"[onebot] friend_request from={uid} approve={approve}",
                flush=True,
            )
            if flag and self._ws is not None:
                try:
                    self._ws.send(
                        json.dumps(
                            {
                                "action": "set_friend_add_request",
                                "params": {
                                    "flag": flag,
                                    "approve": approve,
                                    "remark": "小熊" if approve else "",
                                },
                                "echo": "friend_req",
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception as e:  # pragma: no cover
                    print(f"[onebot] friend_req send fail: {e}", file=sys.stderr)
            return
        peer = str(ev.get("user_id") or (ev.get("sender") or {}).get("user_id") or "")
        if (ev.get("post_type") == "message" and ev.get("message_type") in ("private", "friend")
                and _is_allowed_peer(peer)):
            # Keep the websocket callback free to receive the rest of the turn.
            with self._burst_lock:
                self._burst_events.append(ev)
                if self._burst_worker is None:
                    self._burst_worker = threading.Thread(target=self._run_bursts, daemon=True,
                                                          name="onebot-reply-bursts")
                    self._burst_worker.start()
            return
        # Other private peers are outside the runtime scope; do not read/store/respond.
        return

    @staticmethod
    def _event_peer(ev):
        return str(ev.get("user_id") or (ev.get("sender") or {}).get("user_id") or "")

    def _drain_peer(self, peer):
        with self._burst_lock:
            items = [e for e in self._burst_events if self._event_peer(e) == peer]
            self._burst_events = [e for e in self._burst_events if self._event_peer(e) != peer]
            return items

    def _has_newer(self, peer):
        with self._burst_lock:
            return any(self._event_peer(e) == peer for e in self._burst_events)

    def _run_bursts(self):
        from .reply_burst import collect, combine
        from reply_engagement import snapshot
        while True:
            with self._burst_lock:
                if not self._burst_events or self._stop:
                    self._burst_worker = None
                    return
                first = self._burst_events.pop(0)
            try:
                peer = self._event_peer(first)
                events = collect(first, lambda: self._drain_peer(peer), activity=snapshot("demo_contact"))
                messages = [m for e in events if (m := normalize_event(e)) is not None]
                if messages:
                    print(f"[onebot] reply burst messages={len(messages)}", flush=True)
                    self._handle_event(events[-1], inbound=combine(messages))
            except Exception as exc:
                print(f"[onebot] reply burst error: {exc!r}", file=sys.stderr, flush=True)

    def _handle_event(self, ev, inbound=None):
        inbound = inbound or normalize_event(ev)
        if not inbound:
            return
        # Contact scope is checked before any reply or media access.
        couple_peer = _is_allowed_peer(inbound.peer_id)
        if not couple_peer:
            return

        def _send(out: Outbound):
            require_allowed_peer(CHANNEL, out.peer_id)
            ack = dict(tracker=self._receipts,
                       on_confirmed=(out.meta or {}).get("_on_confirmed"),
                       operation_id=(out.meta or {}).get("delivery_id"))
            # Acknowledged text, image and voice use the same receipt tracker.
            voice = (out.meta or {}).get("voice_path") or (out.meta or {}).get("record_path")
            if voice:
                if not _outbound_media_permitted(out.peer_id, "voice"):
                    print(
                        f"[onebot] REJECT voice to non-couple peer={out.peer_id}",
                        flush=True,
                    )
                    if out.text:
                        send_private_text_ws(self._ws, out.peer_id, out.text)
                    return
                from pathlib import Path as _P
                import base64 as _b64
                p = _P(str(voice))
                if p.is_file():
                    ref = "base64://" + _b64.b64encode(p.read_bytes()).decode("ascii")
                else:
                    ref = str(voice)
                return send_private_record_ws(self._ws, out.peer_id, ref, **ack)
            image = (out.meta or {}).get("image_path")
            if image:
                if not _outbound_media_permitted(out.peer_id, "image"):
                    print(
                        f"[onebot] REJECT image to non-couple peer={out.peer_id}",
                        flush=True,
                    )
                    if out.text:
                        send_private_text_ws(self._ws, out.peer_id, out.text)
                    return
                # Prefer independent text bubble then image-only (no fused caption)
                if (out.text or "").strip():
                    send_private_text_ws(self._ws, out.peer_id, out.text, tracker=self._receipts)
                return send_private_image_ws(
                    self._ws, out.peer_id, str(image), caption=None, **ack
                )
            if out.text:
                return send_private_text_ws(self._ws, out.peer_id, out.text, **ack)

        result = process_inbound(
            inbound,
            dry=self.dry,
            send=None if self.dry else _send,
            reply_fn=self.reply_fn,
            should_cancel=lambda: self._has_newer(inbound.peer_id),
        )
        if not self.dry and not result.get("cancelled") and not result.get("delivery_error") and not self._has_newer(inbound.peer_id):
            try:
                def outbox_text(peer, txt, **ack):
                    return send_private_text_ws(self._ws, peer, txt, tracker=self._receipts, **ack)
                def outbox_image(peer, path, caption=None, **ack):
                    return send_private_image_ws(self._ws, peer, path, caption=caption, tracker=self._receipts, **ack)
                outbox_text.ack_aware = outbox_image.ack_aware = True
                flush_qq_outbox(
                    outbox_text, outbox_image,
                    peer_id=inbound.peer_id,
                    after_inbound_text=inbound.text,
                )
            except Exception as e:
                print(f"[onebot] outbox flush error: {e!r}", file=sys.stderr, flush=True)
        self._got += 1
        if self.once and self._ws and not result.get("cancelled"):
            self._stop = True
            try:
                self._ws.close()
            except Exception:
                pass

    def run(self) -> int:
        if websocket is None:
            print("需要 websocket-client：.venv/bin/pip install websocket-client", file=sys.stderr)
            return 2
        print(
            f"[onebot] connect {self.ws_uri} dry={self.dry} persona={persona_cfg()['id']}",
            flush=True,
        )
        backoff = 1.0
        while not self._stop:
            self._ws = websocket.WebSocketApp(
                self._uri(),
                header=self._headers(),
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=lambda _w, e: print(f"[onebot] err {e}", file=sys.stderr),
                on_close=lambda *_: print("[onebot] closed", flush=True),
            )
            t = threading.Thread(
                target=lambda: self._ws.run_forever(ping_interval=20, ping_timeout=10),
                daemon=True,
            )
            t.start()
            if self.once:
                t.join(timeout=120)
                return 0
            try:
                while t.is_alive() and not self._stop:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self._stop = True
                if self._ws:
                    self._ws.close()
                return 0
            if self._stop or not self.reconnect:
                break
            print(f"[onebot] reconnect in {backoff:.0f}s…", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        return 0


def probe_ws(ws_uri: str, token: str = "", timeout: float = 8.0) -> int:
    """连一下正向 WS，拉 get_login_info，打印结果后退出。"""
    if websocket is None:
        print("需要 websocket-client：.venv/bin/pip install websocket-client", file=sys.stderr)
        return 2
    uri = ws_uri
    if token and "access_token=" not in uri:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}access_token={token}"
    headers = [f"Authorization: Bearer {token}"] if token else []
    box: dict[str, Any] = {"ok": False, "err": None, "login": None}

    def on_open(ws):
        ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "probe"}))

    def on_message(ws, message: str):
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            return
        if ev.get("echo") == "probe":
            box["login"] = ev.get("data") or ev
            box["ok"] = True
            ws.close()
        elif ev.get("post_type") == "meta_event" and ev.get("meta_event_type") == "lifecycle":
            # 有的实现先推 lifecycle；继续等 login 回执
            pass

    def on_error(_ws, e):
        box["err"] = str(e)

    print(f"[onebot] probe {ws_uri} …", flush=True)
    app = websocket.WebSocketApp(
        uri, header=headers, on_open=on_open, on_message=on_message, on_error=on_error
    )
    t = threading.Thread(target=lambda: app.run_forever(ping_interval=None), daemon=True)
    t.start()
    t.join(timeout=timeout)
    try:
        app.close()
    except Exception:
        pass
    if box["ok"]:
        login = box["login"] or {}
        print(
            f"[onebot] probe OK uin={login.get('user_id')} nick={login.get('nickname')!r}",
            flush=True,
        )
        return 0
    print(
        f"[onebot] probe FAIL err={box['err']!r} "
        f"— 协议端未起或 ws_uri/token 不对。Mac 无原生 SnowLuma，见 docs/channels-ilink-qq.md",
        file=sys.stderr,
    )
    return 1


def inject_text(
    text: str,
    *,
    peer: str = "10001",
    dry: bool = True,
    stub_reply: Optional[str] = None,
    persist: bool = False,
    send: Optional[Callable[[Outbound], None]] = None,
) -> dict:
    """不连网：伪造私聊 → bridge（默认不写共享记忆）。"""
    ev = make_private_event(peer, text)
    inbound = normalize_event(ev)
    assert inbound is not None
    reply_fn = (lambda _cid, _hist: stub_reply) if stub_reply is not None else None
    return process_inbound(
        inbound,
        dry=dry,
        send=send,
        reply_fn=reply_fn,
        persist=persist,
    )


class _HttpEventHandler(BaseHTTPRequestHandler):
    """OneBot HTTP 事件上报：POST JSON 事件体。"""

    server_version = "XiaotianOneBot/0.1"

    def log_message(self, fmt, *args):  # quieter
        print(f"[onebot-http] {self.address_string()} {fmt % args}", flush=True)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            ev = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"status":"bad json"}')
            return
        runner: "OneBotHttpRunner" = self.server.runner  # type: ignore[attr-defined]
        inbound = normalize_event(ev)
        if inbound:

            def _send(out: Outbound):
                try:
                    voice = (out.meta or {}).get("voice_path") or (out.meta or {}).get("record_path")
                    if voice:
                        if not _outbound_media_permitted(out.peer_id, "voice"):
                            print(
                                f"[onebot-http] REJECT voice to non-couple peer={out.peer_id}",
                                flush=True,
                            )
                            if out.text:
                                send_private_text_http(
                                    runner.api_base, out.peer_id, out.text, token=runner.token
                                )
                            return
                        send_private_record_http(
                            runner.api_base, out.peer_id, str(voice), token=runner.token
                        )
                        return
                    image = (out.meta or {}).get("image_path")
                    if image:
                        if not _outbound_media_permitted(out.peer_id, "image"):
                            print(
                                f"[onebot-http] REJECT image to non-couple peer={out.peer_id}",
                                flush=True,
                            )
                            if out.text:
                                send_private_text_http(
                                    runner.api_base, out.peer_id, out.text, token=runner.token
                                )
                            return
                        if (out.text or "").strip():
                            send_private_text_http(
                                runner.api_base, out.peer_id, out.text, token=runner.token
                            )
                        send_private_image_http(
                            runner.api_base,
                            out.peer_id,
                            str(image),
                            caption=None,
                            token=runner.token,
                        )
                    elif out.text:
                        send_private_text_http(
                            runner.api_base, out.peer_id, out.text, token=runner.token
                        )
                except Exception as e:
                    print(f"[onebot-http] send fail: {e}", file=sys.stderr)
                    raise

            process_inbound(
                inbound,
                dry=runner.dry,
                send=None if runner.dry else _send,
                reply_fn=runner.reply_fn,
            )
            if not runner.dry:
                try:
                    flush_qq_outbox(
                        lambda peer, txt: send_private_text_http(
                            runner.api_base, peer, txt, token=runner.token
                        ),
                        lambda peer, path, caption=None: _gated_send_image(
                            lambda p, ph, caption=None: send_private_image_http(
                                runner.api_base, p, ph, caption=caption, token=runner.token
                            ),
                            peer,
                            path,
                            caption,
                        ),
                        peer_id=inbound.peer_id,
                        after_inbound_text=inbound.text,
                    )
                except Exception as e:
                    print(f"[onebot-http] outbox flush error: {e!r}", file=sys.stderr)
            runner.got += 1
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"xiaotian onebot http event sink ok\n")


class OneBotHttpRunner:
    """收 HTTP POST 事件；发信用 OneBot HTTP API。"""

    def __init__(
        self,
        listen: str,
        api_base: str,
        token: str = "",
        *,
        dry: bool = True,
        reply_fn=None,
    ):
        host, _, port_s = listen.partition(":")
        self.host = host or "127.0.0.1"
        self.port = int(port_s or "8765")
        self.api_base = api_base
        self.token = token or ""
        self.dry = dry
        self.reply_fn = reply_fn
        self.got = 0

    def run(self) -> int:
        httpd = ThreadingHTTPServer((self.host, self.port), _HttpEventHandler)
        httpd.runner = self  # type: ignore[attr-defined]
        print(
            f"[onebot-http] listen http://{self.host}:{self.port}/ "
            f"api={self.api_base} dry={self.dry}",
            flush=True,
        )
        print(
            "[onebot-http] 在 SnowLuma/NapCat 配 HTTP 事件上报指向此地址；"
            "发信用 api_base /send_private_msg",
            flush=True,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("[onebot-http] stop", flush=True)
        finally:
            httpd.server_close()
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="QQ OneBot → 小甜 brain")
    ap.add_argument("--ws", default=None, help="OneBot 正向 WS，默认 config.persona.channels.qq.ws_uri")
    ap.add_argument("--token", default=None)
    ap.add_argument("--http-api", default=None, help="OneBot HTTP API 根，默认 http://127.0.0.1:3000")
    ap.add_argument("--http-listen", default=None, help="收事件 POST，如 127.0.0.1:8765")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--send", action="store_true", help="真正发送（关闭 dry-run）")
    ap.add_argument("--once", action="store_true", help="处理一条后退出（仅 WS）")
    ap.add_argument("--no-reconnect", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="不连网，只测 normalize / alias")
    ap.add_argument("--probe", action="store_true", help="探测正向 WS + get_login_info")
    ap.add_argument("--inject", default=None, metavar="TEXT", help="伪造一条私聊走 bridge（默认不写库）")
    ap.add_argument("--peer", default="10001", help="inject 用的 QQ uin")
    ap.add_argument("--stub-reply", default=None, help="固定回复、不调 LLM（inject / WS dry-run 冒烟）")
    ap.add_argument("--persist", action="store_true", help="inject 时写入共享记忆（慎用，会进 demo_contact）")
    args = ap.parse_args(argv)

    if args.self_test:
        qq_cfg = (persona_cfg().get("channels") or {}).get("qq") or {}
        allow = qq_cfg.get("allow_user_ids") or [10001]
        if isinstance(allow, (str, int)):
            allow = [allow]
        peer = str(allow[0])
        ev = make_private_event(peer, "在吗")
        m = normalize_event(ev)
        assert m and m.peer_id == peer and m.text == "在吗"
        assert resolve_contact_id(m.channel, m.peer_id)
        assert normalize_event({"post_type": "meta_event"}) is None
        ev_img = {
            "post_type": "message",
            "message_type": "private",
            "user_id": peer,
            "message": [{"type": "image", "data": {"file": "file://dummy.jpg"}}],
            "message_id": 2,
            "time": int(time.time()),
        }
        mi = normalize_event(ev_img)
        assert mi and mi.msg_type == "image" and "[图片]" in mi.text
        ev_voice = {
            "post_type": "message",
            "message_type": "private",
            "user_id": peer,
            "message": [{"type": "record", "data": {"file": "file://dummy.silk"}}],
            "message_id": 3,
            "time": int(time.time()),
        }
        mv = normalize_event(ev_voice)
        assert mv and mv.msg_type == "record" and "[语音]" in mv.text
        print("OK self-test")
        return 0

    cfg = persona_cfg()
    qq = (cfg.get("channels") or {}).get("qq") or {}
    uri = args.ws or qq.get("ws_uri") or "ws://127.0.0.1:3001"
    token = args.token if args.token is not None else qq.get("ws_token") or ""
    api = args.http_api or qq.get("http_api") or "http://127.0.0.1:3000"
    dry = not args.send

    if args.probe:
        return probe_ws(uri, token)

    if args.inject is not None:
        stub = args.stub_reply if args.stub_reply is not None else "（stub）收到"
        # inject 默认 stub，避免误调 LLM；若显式 --stub-reply '' 且不要 stub… 保持有 stub
        r = inject_text(
            args.inject,
            peer=args.peer,
            dry=True,
            stub_reply=stub,
            persist=bool(args.persist),
        )
        print(json.dumps({k: r.get(k) for k in ("contact_id", "reply", "dry", "persist", "channel", "peer_id")}, ensure_ascii=False))
        return 0

    if args.http_listen:
        return OneBotHttpRunner(args.http_listen, api, token, dry=dry).run()

    reply_fn = None
    if args.stub_reply is not None:
        _stub = args.stub_reply
        reply_fn = lambda _cid, _hist, _s=_stub: _s
    return OneBotQQRunner(
        uri,
        token,
        dry=dry,
        once=args.once,
        reconnect=not args.no_reconnect,
        reply_fn=reply_fn,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
