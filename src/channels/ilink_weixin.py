"""微信 iLink / ClawBot 通道（自建 Python，不装 OpenClaw）。

协议：与 @tencent-weixin/openclaw-weixin 相同的 iLink HTTP JSON
（见 https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md）。
实现：优先用 weixin-ilink SDK；入站 → channels.bridge → 共享 contact demo_contact。

用法（仓库根，PYTHONPATH=src 或 -m）：
  .venv/bin/python tools/ilink_weixin_run.py login
  .venv/bin/python tools/ilink_weixin_run.py --dry-run
  .venv/bin/python tools/ilink_weixin_run.py --send
  .venv/bin/python tools/ilink_weixin_run.py --self-test
"""
from __future__ import annotations
import threading

import argparse
import time
import json
import pathlib
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from .types import Inbound, Outbound
from .persona_map import resolve_contact_id, persona_cfg, is_couple_contact, allowed_peer, require_allowed_peer
from .send_receipts import DeliveryFailed, DeliveryUnknown
from .outbox_store import serialized as _serialize_outbox
from proactive_limiter import serialized_retry as _serialize_proactive_retry

try:
    from config import ROOT
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[2]

CHANNEL = "wechat_ilink"


def _classmate_media_flags(cfg: dict | None = None) -> tuple[bool, bool]:
    """persona.classmate.allow_image / allow_voice (default False) — parity with onebot."""
    cm = persona_cfg(cfg).get("classmate") or {}
    return bool(cm.get("allow_image")), bool(cm.get("allow_voice", False))


def _is_couple_peer(peer_id: str, cfg: dict | None = None) -> bool:
    """True iff peer resolves to primary couple contact (demo_contact)."""
    cid = resolve_contact_id(CHANNEL, peer_id, cfg)
    return is_couple_contact(cid, cfg)


def _outbound_media_permitted(peer_id: str, kind: str, cfg: dict | None = None) -> bool:
    """Defense in depth: non-couple image/voice hard-rejected unless config opts in.

    Couple alias peer unrestricted. Classmate: allow_image / allow_voice (both default false).
    """
    return allowed_peer(CHANNEL, peer_id, cfg)


def _warmup_mem_async() -> None:
    """后台预热 mem0/HF 嵌入（与 daemon.warmup_async 同思路），避免首条回复冷加载。"""
    def _w():
        t = time.monotonic()
        try:
            import mem
            mem.warmup()
            print(f"[ilink] warmup mem0 ready in {time.monotonic()-t:.1f}s", flush=True)
        except Exception as e:  # pragma: no cover
            print(f"[ilink] warmup mem0 skipped: {e}", file=sys.stderr, flush=True)
    threading.Thread(target=_w, name="ilink-warmup", daemon=True).start()

DEFAULT_CREDS = ROOT / "data" / "ilink_creds.json"

CTX_FILE = ROOT / "data" / "ilink_ctx.json"
OUTBOX_FILE = ROOT / "data" / "ilink_outbox.json"  # legacy; runtime uses outbox_store SQLite


def _persist_ctx(peer_id: str, token: str) -> None:
    try:
        data = {}
        if CTX_FILE.is_file():
            data = json.loads(CTX_FILE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}
        if data.get(peer_id) == token:
            return
        data[peer_id] = token
        CTX_FILE.parent.mkdir(parents=True, exist_ok=True)
        CTX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # pragma: no cover
        print(f"[ilink] ctx persist skip: {e}", file=sys.stderr, flush=True)



def _load_outbox() -> dict:
    from .outbox_store import load as _ob_load
    return _ob_load("ilink")


def _save_outbox(data: dict) -> None:
    from .outbox_store import save as _ob_save
    _ob_save("ilink", data or {"items": []})


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


@_serialize_outbox("ilink")
@_serialize_proactive_retry
def flush_ilink_outbox(
    bot,
    peer_id: str,
    context_token: str | None,
    *,
    after_inbound_text: str | None = None,
    now=None,
) -> list[dict]:
    """Deliver pending outbox AFTER current inbound reply (caller orders this).

    Stale goodnight at night / while in-chat → drop (or mark 补发, not sent ahead
    of the live reply). Fail-closed per bubble. Persists each sent bubble to
    chat.db; resumes from item['sent_bubbles'] so retries skip the prefix.
    Returns result dicts.
    """
    if bot is None or not peer_id or not allowed_peer(CHANNEL, peer_id):
        return []
    data = _load_outbox()
    items = data.get("items") or []
    if not items:
        return []
    import weixin_ilink.api as api
    from bubble_split import split_bubbles, bubble_gap_sec
    import datetime as _dt
    from channels.outbox_send import (
        ProactiveSleepBlocked,
        check_proactive_sleep,
        already_sent_count,
        bubbles_suffix,
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
    ch_tag = channel_tag("ilink")

    results = []
    changed = False
    for it in items:
        if it.get("status") not in (None, "pending", "hold", "补发"):
            continue
        if it.get("peer") and it["peer"] != peer_id:
            continue
        # Hard reject pytest / mock pollution (fail-closed)
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
            print(f"[ilink] outbox drop pollution id={it.get('id')} why={dw}", flush=True)
            results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": dw})
            changed = True
            continue
        text = (it.get("text") or "").strip()
        image = it.get("image_path")
        require_media = bool(it.get("require_media") or it.get("surf_require_media"))
        if image and not pathlib.Path(str(image)).is_file():
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
            it["note"] = "empty text"
            changed = True
            continue
        # P0: drop mocked / expired / slot-mismatched — fidelity errors fail-closed
        try:
            import schedule_fidelity as _sf
            _slot = _sf.resolve_slot(now=now)
            stale, why = _sf.outbox_item_stale(it, now=now, slot=_slot)
            if stale:
                it["status"] = "dropped"
                it["note"] = f"fidelity drop: {why}"
                it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[ilink] outbox drop stale id={it.get('id')} why={why}",
                    flush=True,
                )
                results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": why})
                changed = True
                continue
        except Exception as _fe:
            it["status"] = "dropped"
            it["note"] = f"fidelity fail-closed: {_fe!r}"
            it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[ilink] outbox fidelity fail-closed id={it.get('id')}: {_fe!r}", flush=True)
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
                f"[ilink] outbox drop stale goodnight id={it.get('id')} "
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

        def _persist(bub: str, idx: int) -> None:
            persist_outbound_bubble(
                cid, ch_tag, bub, occ=f"outbox:{oid}:{idx}", type_="outbox"
            )
            pending = ((it.get("proactive_round") or {}).get("meta") or {}).get("pending_delivery")
            if pending:
                import brain
                brain.commit_delivery(cid, pending, delivery_id=f"proactive:{oid}", sent_text=bub)
            _save_outbox(data)

        # Image attachment path (surf_share / proactive HOLD).
        if image:
            try:
                runner = ILinkWeixinRunner(credentials_file=_creds_path(), dry=False)
                runner._bot = bot
                runner._ctx[peer_id] = context_token or ""
                if text:
                    if it.get("orig_text") is None:
                        it["orig_text"] = text
                    bubbles = split_outbound_bubbles(it.get("orig_text") or text)
                    skip = already_sent_count(it)
                    for i, bub in enumerate(bubbles):
                        if i < skip:
                            continue
                        if i and i > skip:
                            time.sleep(float(bubble_gap_sec(bub) or 0.4))
                        elif i and skip == 0:
                            time.sleep(float(bubble_gap_sec(bub) or 0.4))
                        _sleep_guard()
                        runner._send_text_checked(peer_id, bub, context_token)
                        record_sent_bubble(it, bub)
                        _persist(bub, i)
                        changed = True
                    if bubbles:
                        time.sleep(float(bubble_gap_sec(bubbles[-1]) or 0.8))
                link = (it.get("link_url") or "").strip()
                if link and not it.get("link_sent"):
                    _sleep_guard()
                    runner._send_text_checked(peer_id, link, context_token)
                    it["link_sent"] = True
                    _persist(link, "link")
                    changed = True
                    time.sleep(0.8)
                _sleep_guard()
                runner._send_image_checked(
                    peer_id, str(image), context_token, caption=None, before_send=_sleep_guard
                )
                _persist("[图片]", "image")
                it["status"] = "sent"
                it["sent_at"] = __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S")
                it["note"] = f"outbox flush image={image}"
                it["text"] = ""
                print(
                    f"[ilink] outbox flushed image id={it.get('id')} path={image}",
                    flush=True,
                )
                results.append({"id": it.get("id"), "ok": True, "image": True})
                try:
                    import schedule_fidelity as _sf
                    from proactive_limiter import record_outbox_success
                    record_outbox_success(it, now)
                    _sf.mark_asked_his_day_on_flush_success(it, now=now)
                except Exception as _me:
                    print(f"[ilink] ask mark on flush skip: {_me!r}", flush=True)
            except ProactiveSleepBlocked as e:
                it.update(status="dropped", note=str(e), dropped_at=send_now().isoformat(sep=" "))
                results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": str(e)})
            except Exception as e:
                # Keep unsent suffix only (record_sent_bubble already shrank text)
                it["status"] = "hold" if getattr(e, "delivery_status", "unknown") == "failed" else "unknown"
                it["note"] = (
                    f"require_media image flush fail (no text-only): {e!r}"
                    if require_media
                    else f"outbox image flush fail: {e!r}"
                )
                print(
                    f"[ilink] outbox HOLD id={it.get('id')}: {e!r} "
                    f"sent_bubbles={already_sent_count(it)}",
                    flush=True,
                )
                results.append({"id": it.get("id"), "ok": False, "error": repr(e),
                                "require_media": require_media,
                                "sent_bubbles": already_sent_count(it)})
            changed = True
            continue

        # daytime residual: mark 补发 and send once with short prefix
        prefix = ""
        if it.get("status") == "补发" or (
            _outbox_is_goodnight(text) and not night and not in_chat
        ):
            it["status"] = "补发"
            prefix = "（补发）\n"
        base = it.get("orig_text")
        if base is None:
            base = text
        full_for_split = (prefix + base) if prefix else base
        # Resume key must match the bubble list we actually send
        it["orig_text"] = full_for_split
        bubbles = split_outbound_bubbles(full_for_split) or ([full_for_split] if full_for_split else [])
        skip = already_sent_count(it)
        mids = []
        ok = True
        err = ""
        delivery_status = "failed"
        for i, bub in enumerate(bubbles):
            if i < skip:
                continue
            if i and (i > skip or skip == 0):
                time.sleep(bubble_gap_sec(bub))
            msg = {
                "from_user_id": "",
                "to_user_id": peer_id,
                "client_id": bot.client._generate_client_id(),
                "message_type": 2,  # BOT
                "message_state": 2,  # FINISH
                "context_token": context_token or "",
                "item_list": [{"type": 1, "text_item": {"text": bub}}],
            }
            try:
                from weixin_ilink.types import MessageType, MessageState, MessageItemType
                msg["message_type"] = int(MessageType.BOT)
                msg["message_state"] = int(MessageState.FINISH)
                msg["item_list"] = [
                    {"type": int(MessageItemType.TEXT), "text_item": {"text": bub}}
                ]
            except Exception:
                pass
            try:
                _sleep_guard()
                res = api.send_message(bot.client.opts, msg)
            except ProactiveSleepBlocked as e:
                it.update(status="dropped", note=str(e), dropped_at=send_now().isoformat(sep=" "))
                ok = False
                err = str(e)
                break
            except Exception as e:
                ok = False
                err = repr(e)
                delivery_status = getattr(e, "delivery_status", "unknown")
                break
            ret = res.get("ret")
            errmsg = str(res.get("errmsg") or "")
            mid = res.get("message_id")
            bad = (ret not in (None, 0, "0")) or ("prepare failed" in errmsg.lower())
            if bad or mid is None:
                ok = False
                delivery_status = "failed" if bad else "unknown"
                err = f"ret={ret!r} errmsg={errmsg!r} mid={mid!r}"
                break
            mids.append(mid)
            record_sent_bubble(it, bub)
            _persist(bub, i)
            changed = True
        if ok:
            link = (it.get("link_url") or "").strip()
            if link and not it.get("link_sent"):
                try:
                    from weixin_ilink.types import MessageType, MessageState, MessageItemType
                    msg = {
                        "from_user_id": "",
                        "to_user_id": peer_id,
                        "client_id": bot.client._generate_client_id(),
                        "message_type": int(MessageType.BOT),
                        "message_state": int(MessageState.FINISH),
                        "context_token": context_token or "",
                        "item_list": [
                            {"type": int(MessageItemType.TEXT), "text_item": {"text": link}}
                        ],
                    }
                    _sleep_guard()
                    res = api.send_message(bot.client.opts, msg)
                    ret = res.get("ret")
                    errmsg = str(res.get("errmsg") or "")
                    mid = res.get("message_id")
                    bad = (ret not in (None, 0, "0")) or ("prepare failed" in errmsg.lower())
                    if bad or mid is None:
                        delivery_status = "failed" if bad else "unknown"
                        raise RuntimeError(f"link ret={ret!r} errmsg={errmsg!r} mid={mid!r}")
                    mids.append(mid)
                    it["link_sent"] = True
                    _persist(link, "link")
                except ProactiveSleepBlocked as e:
                    it.update(status="dropped", note=str(e), dropped_at=send_now().isoformat(sep=" "))
                    ok = False
                    err = str(e)
                except Exception as e:
                    ok = False
                    delivery_status = getattr(e, "delivery_status", "unknown")
                    err = f"link send fail: {e!r}"
        if ok:
            it["status"] = "sent"
            it["sent_at"] = __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S")
            it["note"] = f"outbox flush mid={mids[-1] if mids else 'resume'}"
            it["text"] = ""
            print(
                f"[ilink] outbox flushed id={it.get('id')} mid={mids[-1] if mids else '-'} "
                f"bubbles={already_sent_count(it)}",
                flush=True,
            )
            results.append({"id": it.get("id"), "ok": True,
                            "message_id": mids[-1] if mids else None})
            try:
                import schedule_fidelity as _sf
                from proactive_limiter import record_outbox_success
                record_outbox_success(it, now)
                _sf.mark_asked_his_day_on_flush_success(it, now=now)
            except Exception as _me:
                print(f"[ilink] ask mark on flush skip: {_me!r}", flush=True)
        elif it.get("status") == "dropped":
            results.append({"id": it.get("id"), "ok": False, "dropped": True, "why": err})
        else:
            it["status"] = "hold" if delivery_status == "failed" else "unknown"
            it["note"] = f"outbox flush fail: {err}"
            print(
                f"[ilink] outbox flush FAIL id={it.get('id')}: {err} "
                f"sent_bubbles={already_sent_count(it)} suffix={it.get('text')!r:.40}",
                flush=True,
            )
            results.append({"id": it.get("id"), "ok": False, "error": err,
                            "sent_bubbles": already_sent_count(it)})
        changed = True
    if changed:
        _save_outbox(data)
    return results



def _load_ctx_cache() -> dict[str, str]:
    try:
        if not CTX_FILE.is_file():
            return {}
        data = json.loads(CTX_FILE.read_text(encoding="utf-8") or "{}")
        return {str(k): str(v) for k, v in data.items() if k and v} if isinstance(data, dict) else {}
    except Exception:
        return {}


# message_type: 1=user, 2=bot（协议）
_MSG_USER = 1
# item type: 1=text, 2=image, …
_ITEM_TEXT = 1
_ITEM_IMAGE = 2
_ITEM_VOICE = 3
_ITEM_FILE = 4
_ITEM_VIDEO = 5


def _creds_path(cfg: dict | None = None) -> Path:
    p = persona_cfg(cfg)
    ch = (p.get("channels") or {}).get("wechat_ilink") or {}
    raw = ch.get("credentials_file") or "data/ilink_creds.json"
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def extract_text_from_msg(msg: dict) -> str:
    """从原始 WeixinMessage 抽出可读文本（含语音 ASR）。"""
    parts: list[str] = []
    for item in msg.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == _ITEM_TEXT:
            parts.append(str((item.get("text_item") or {}).get("text") or ""))
        elif t == _ITEM_VOICE:
            # 语音转写在 voice_item.text
            parts.append(str((item.get("voice_item") or {}).get("text") or ""))
    return "".join(parts).strip()


def normalize_raw_msg(msg: dict) -> Optional[Inbound]:
    """协议 WeixinMessage → Inbound；只接用户私聊文本/语音转写。"""
    if not isinstance(msg, dict):
        return None
    if int(msg.get("message_type") or 0) != _MSG_USER:
        return None
    peer = str(msg.get("from_user_id") or "").strip()
    if not peer or not allowed_peer(CHANNEL, peer):
        return None
    text = extract_text_from_msg(msg)
    # 纯图/文件暂记占位，便于后续扩展；无文本则跳过 brain
    items = msg.get("item_list") or []
    has_media = any(
        isinstance(it, dict) and it.get("type") in (_ITEM_IMAGE, _ITEM_FILE, _ITEM_VIDEO)
        for it in items
    )
    if not text and has_media:
        text = "[图片]" if any(isinstance(it, dict) and it.get("type") == _ITEM_IMAGE for it in items) else "[文件]"
    if not text:
        return None
    mid = msg.get("message_id") or msg.get("seq") or ""
    ts = ""
    if msg.get("create_time_ms"):
        try:
            import datetime
            ts = datetime.datetime.fromtimestamp(int(msg["create_time_ms"]) / 1000).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (TypeError, ValueError, OSError):
            ts = str(msg.get("create_time_ms"))
    return Inbound(
        channel=CHANNEL,
        peer_id=peer,
        text=text,
        msg_id=str(mid),
        ts=ts,
        msg_type="text",
        raw=msg,
    )


def normalize_sdk_msg(msg: Any) -> Optional[Inbound]:
    """weixin_ilink IncomingMessage → Inbound。"""
    if msg is None:
        return None
    # 已是 Inbound
    if isinstance(msg, Inbound):
        return msg if allowed_peer(CHANNEL, msg.peer_id) else None
    if isinstance(msg, dict):
        return normalize_raw_msg(msg)
    peer = str(getattr(msg, "from_user", "") or "").strip()
    if not peer or not allowed_peer(CHANNEL, peer):
        return None
    text = (getattr(msg, "text", None) or "").strip()
    mid = getattr(msg, "message_id", None) or ""
    msg_type = "text"
    media_extra: dict[str, Any] = {}
    # Cache IMAGE bytes for Magpie caption (bridge enrich_inbound).
    if getattr(msg, "is_image", False):
        msg_type = "image"
        if not text:
            text = "[图片]"
        try:
            dest_dir = ROOT / "data" / "media" / "ilink_in"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"image_{mid or int(time.time())}.jpg"
            if hasattr(msg, "save"):
                msg.save(dest)
                if dest.is_file():
                    media_extra["image_paths"] = [str(dest)]
                    media_extra["image_count"] = 1
        except Exception as e:  # pragma: no cover
            print(f"[ilink] inbound image cache skip: {e!r}", file=sys.stderr, flush=True)
    elif getattr(msg, "is_voice", False):
        msg_type = "record"
        # WeChat often fills voice_item.text (ASR). Prefer that as plain text.
        # If empty, place [语音] and cache silk for local mlx-whisper.
        if not text:
            text = "[语音]"
            try:
                dest_dir = ROOT / "data" / "media" / "ilink_in"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"voice_{mid or int(time.time())}.silk"
                if hasattr(msg, "save"):
                    msg.save(dest)
                    if dest.is_file():
                        media_extra["voice_paths"] = [str(dest)]
                        media_extra["voice_count"] = 1
            except Exception as e:  # pragma: no cover
                print(f"[ilink] inbound voice cache skip: {e!r}", file=sys.stderr, flush=True)
        else:
            # Keep ASR text; optionally tag so hist/type_note know it was voice.
            if not text.startswith("["):
                text = f"[语音：{text}]"
    elif not text:
        if getattr(msg, "is_file", False) or getattr(msg, "is_video", False):
            text = "[文件]"
        else:
            return None
    raw = {
        "from_user_id": peer,
        "context_token": getattr(msg, "context_token", None),
        "message_id": mid,
        "session_id": getattr(msg, "session_id", None),
    }
    if media_extra:
        raw = {**raw, "_xiaotian_media": media_extra}
    return Inbound(
        channel=CHANNEL,
        peer_id=peer,
        text=text,
        msg_id=str(mid) if mid is not None else "",
        msg_type=msg_type,
        raw=raw,
    )


def _require_sdk():
    try:
        from weixin_ilink import WeixinBot, login  # noqa: F401
        return True
    except ImportError:
        print(
            "需要 weixin-ilink：.venv/bin/pip install 'weixin-ilink[qr]'",
            file=sys.stderr,
        )
        return False


def do_login(*, save_to: Path | None = None, show_url: bool = True) -> dict:
    """终端扫码登录，凭据写入 data/ilink_creds.json。"""
    if not _require_sdk():
        raise SystemExit(2)
    from weixin_ilink import login

    path = save_to or _creds_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def on_qr(url: str):
        print("\n======== 请用微信扫码绑定小甜 iLink ========", flush=True)
        # 保留 SDK 默认 ASCII 二维码
        try:
            from weixin_ilink.bot import _default_qr_renderer
            _default_qr_renderer(url)
        except Exception:
            try:
                import qrcode
                qr = qrcode.QRCode(border=1)
                qr.add_data(url)
                qr.make(fit=True)
                qr.print_ascii(invert=True)
            except Exception:
                pass
        if show_url:
            print(f"若终端二维码不清，浏览器打开：\n{url}\n", flush=True)

    def on_status(s: str):
        print(f"[ilink] qr status: {s}", flush=True)

    info = login(save_to=str(path), on_qrcode=on_qr, on_status_change=on_status)
    # 扫码人 → 人格别名（精确 peer，通配仍保留）
    user_id = (info or {}).get("userId") or ""
    if user_id:
        try:
            from .persona_map import register_alias
            register_alias(CHANNEL, user_id, "demo_contact")
            print(f"[ilink] alias {CHANNEL}:{user_id} → demo_contact", flush=True)
        except Exception as e:  # pragma: no cover
            print(f"[ilink] alias 写入跳过: {e}", file=sys.stderr)
    print(f"[ilink] credentials saved → {path}", flush=True)
    print(
        f"[ilink] account={(info or {}).get('accountId')} user={user_id}",
        flush=True,
    )
    return info


class ILinkWeixinRunner:
    """长轮询 + bridge；默认 dry-run 不算发送。"""

    def __init__(
        self,
        *,
        credentials_file: Path | None = None,
        dry: bool = True,
        once: bool = False,
        typing: bool = True,
    ):
        self.credentials_file = Path(credentials_file or _creds_path())
        self.dry = dry
        self.once = once
        self.typing = typing
        self._bot = None
        self._got = 0
        # peer → context_token（进程内；SDK 也会缓存）
        self._ctx: dict[str, str] = _load_ctx_cache()

    def _build_bot(self):
        if not _require_sdk():
            raise SystemExit(2)
        from weixin_ilink import WeixinBot

        if not self.credentials_file.is_file():
            print(
                f"无凭据 {self.credentials_file}；先跑：\n"
                f"  .venv/bin/python tools/ilink_weixin_run.py login",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return WeixinBot(
            credentials_file=str(self.credentials_file),
            auto_emoji=True,
        )

    def _refresh_ctx(self, peer_id: str) -> str | None:
        """Reload disk cache so KeepAlive inbound can freshen proactive/E2E sends."""
        disk = _load_ctx_cache()
        tok = disk.get(peer_id)
        if tok:
            self._ctx[peer_id] = tok
        return self._ctx.get(peer_id)

    def _send_image_checked(
        self, peer_id: str, image: str, ctx: str | None, caption: str | None,
        *, before_send=None,
    ) -> None:
        """send_image with fail-closed: prepare failed / caption-only mid ≠ success."""
        require_allowed_peer(CHANNEL, peer_id)
        bot = self._bot
        if bot is None:
            raise DeliveryFailed("iLink is disconnected")
        # Prefer freshest token from disk; refuse if still missing.
        ctx = ctx or self._refresh_ctx(peer_id)
        if not ctx:
            raise DeliveryFailed(
                f"[ilink] send_image refused: missing context_token for {peer_id}"
            )
        import weixin_ilink.api as api

        captures: list[tuple[dict, dict]] = []
        _orig = api.send_message

        def _wrap(opts, msg, *a, **kw):
            # SDK sends caption text then media back-to-back; pause so they don't pile.
            if captures:
                try:
                    from bubble_split import bubble_gap_sec
                    prev = captures[-1][0]
                    prev_txt = ""
                    for it in prev.get("item_list") or []:
                        if isinstance(it, dict) and it.get("type") == 1:
                            prev_txt = ((it.get("text_item") or {}).get("text") or "")
                            break
                    time.sleep(bubble_gap_sec(prev_txt or caption))
                except Exception:
                    time.sleep(1.1)
            if before_send:
                before_send()
            res = _orig(opts, msg, *a, **kw)
            captures.append((msg if isinstance(msg, dict) else {}, res if isinstance(res, dict) else {"raw": res}))
            return res

        api.send_message = _wrap  # type: ignore
        try:
            bot.send_image(peer_id, image, caption=caption, context_token=ctx)
        finally:
            api.send_message = _orig  # type: ignore

        image_mids: list = []
        for msg, res in captures:
            items = msg.get("item_list") or []
            is_image = any(
                isinstance(it, dict) and it.get("type") == 2 for it in items
            )
            ret = res.get("ret")
            errmsg = str(res.get("errmsg") or res.get("error") or "")
            bad = (ret not in (None, 0, "0")) or ("prepare failed" in errmsg.lower())
            if bad and (is_image or "prepare failed" in errmsg.lower()):
                raise DeliveryFailed(
                    f"[ilink] send_image fail ret={ret!r} errmsg={errmsg!r} "
                    f"(treat prepare failed as fail)"
                )
            if is_image and res.get("message_id") is not None:
                image_mids.append(res.get("message_id"))
        if not image_mids:
            raise DeliveryUnknown(
                f"[ilink] send_image fail: no IMAGE message_id "
                f"(caption-only mid is not ok; captures={len(captures)})"
            )
        print(
            f"[ilink] image sent peer={peer_id[:24]}… mid={image_mids[-1]}",
            flush=True,
        )


    def _send_text_checked(self, peer_id: str, text: str, ctx: str | None) -> None:
        """send_text fail-closed: non-zero ret / prepare failed / missing message_id → raise."""
        require_allowed_peer(CHANNEL, peer_id)
        bot = self._bot
        if bot is None:
            raise DeliveryFailed("iLink is disconnected")
        ctx = ctx or self._refresh_ctx(peer_id)
        import weixin_ilink.api as api

        captures: list[dict] = []
        _orig = api.send_message

        def _wrap(opts, msg, *a, **kw):
            res = _orig(opts, msg, *a, **kw)
            captures.append(res if isinstance(res, dict) else {"raw": res})
            return res

        api.send_message = _wrap  # type: ignore
        try:
            bot.send_text(peer_id, text, context_token=ctx)
        finally:
            api.send_message = _orig  # type: ignore

        if not captures:
            raise DeliveryUnknown("[ilink] send_text fail: no sendmessage response")
        for res in captures:
            ret = res.get("ret")
            errmsg = str(res.get("errmsg") or res.get("error") or "")
            mid = res.get("message_id")
            bad = (ret not in (None, 0, "0")) or ("prepare failed" in errmsg.lower())
            if bad:
                raise DeliveryFailed(
                    f"[ilink] send_text fail ret={ret!r} errmsg={errmsg!r} "
                    f"(treat prepare failed as fail; need inbound to refresh context_token)"
                )
            if mid is None:
                raise DeliveryUnknown(
                    f"[ilink] send_text fail: missing message_id ret={ret!r}"
                )
        print(
            f"[ilink] text sent peer={peer_id[:24]}… mid={captures[-1].get('message_id')}",
            flush=True,
        )


    def _send(self, out: Outbound) -> None:
        require_allowed_peer(CHANNEL, out.peer_id)
        bot = self._bot
        if bot is None:
            raise DeliveryFailed("iLink is disconnected")
        # Fresh disk token before any outbound (stale ctx → prepare failed on IMAGE)
        disk_tok = self._refresh_ctx(out.peer_id)
        ctx = (out.meta or {}).get("context_token") or disk_tok
        image = (out.meta or {}).get("image_path")
        voice = (out.meta or {}).get("voice_path")
        # classmate may send text; image/voice hard-rejected for non-couple (parity onebot)
        if voice:
            if not _outbound_media_permitted(out.peer_id, "voice"):
                print(
                    f"[ilink] REJECT voice to non-couple peer={out.peer_id}",
                    flush=True,
                )
                if out.text:
                    self._send_text_checked(out.peer_id, out.text, ctx)
                return
            meta = out.meta or {}
            # iLink：原生 VOICE 客户端会静默丢弃；默认改发文件（wav/mp3/silk 均可）
            if meta.get("force_native_voice"):
                self._send_voice(
                    out.peer_id, voice, ctx, playtime_ms=meta.get("playtime_ms")
                )
            else:
                self._send_audio_file(
                    out.peer_id, voice, ctx, caption=out.text or None
                )
            return
        if image:
            if not _outbound_media_permitted(out.peer_id, "image"):
                print(
                    f"[ilink] REJECT image to non-couple peer={out.peer_id}",
                    flush=True,
                )
                if out.text:
                    self._send_text_checked(out.peer_id, out.text, ctx)
                return
            # Prefer independent text bubble then image-only (no fused caption)
            if (out.text or "").strip():
                self._send_text_checked(out.peer_id, out.text, ctx)
            self._send_image_checked(
                out.peer_id, image, ctx, caption=None
            )
            return
        if out.text:
            self._send_text_checked(out.peer_id, out.text, ctx)

    def _checked_media_call(self, kind, call):
        """Keep existing audio delivery; only count an acknowledged media message."""
        import weixin_ilink.api as api
        original = api.send_message
        captures = []
        def capture(opts, msg, *args, **kwargs):
            result = original(opts, msg, *args, **kwargs)
            captures.append((msg, result))
            return result
        api.send_message = capture
        try:
            call()
        finally:
            api.send_message = original
        media = [result for msg, result in captures
                 if any(it.get("type") == kind for it in msg.get("item_list", []))]
        if not media:
            raise DeliveryUnknown("iLink media has no acknowledgement")
        for result in media:
            if result.get("ret") not in (None, 0, "0"):
                raise DeliveryFailed(f"iLink media rejected: {result!r}")
            if result.get("message_id") is None:
                raise DeliveryUnknown("iLink media acknowledgement has no message_id")

    def _send_audio_file(
        self, peer_id: str, voice_path: str, ctx: str | None, caption: str | None = None
    ) -> None:
        """TTS/音频走 send_file（用户侧显示为文件，可播放）。"""
        from pathlib import Path as _P

        require_allowed_peer(CHANNEL, peer_id)
        pth = _P(voice_path)
        if not pth.is_file():
            raise DeliveryFailed(f"[ilink] audio missing: {pth}")
        try:
            self._checked_media_call(4, lambda: self._bot.send_file(
                peer_id,
                pth,
                file_name=pth.name,
                caption=caption,
                context_token=ctx,
            ))
            print(f"[ilink] audio file sent peer={peer_id[:24]}… name={pth.name}", flush=True)
        except (DeliveryFailed, DeliveryUnknown):
            raise
        except Exception as e:
            raise DeliveryUnknown(f"[ilink] audio file send unconfirmed: {e}") from e

    def _send_voice(self, peer_id: str, voice_path: str, ctx: str | None, playtime_ms: Any = None) -> None:
        """实验性原生语音条；iLink 通常丢弃，仅 force_native_voice 时调用。"""
        from pathlib import Path as _P
        from weixin_ilink.types import VoiceEncodeType

        require_allowed_peer(CHANNEL, peer_id)
        p = _P(voice_path)
        if not p.is_file():
            raise DeliveryFailed(f"[ilink] voice missing: {p}")
        ms = int(playtime_ms or 0)
        if ms <= 0:
            # 同名 .pcm 推算；否则按文件大小粗估
            pcm = p.with_suffix(".pcm")
            if pcm.is_file():
                ms = max(1, int(pcm.stat().st_size / (24000 * 2) * 1000))
            else:
                ms = 1000
        token = ctx or self._ctx.get(peer_id) or ""
        try:
            if token:
                self._checked_media_call(3, lambda: self._bot.send_voice(
                    peer_id, p, playtime_ms=ms, sample_rate=24000,
                    encode_type=VoiceEncodeType.SILK, context_token=token,
                ))
            else:
                # 协议侧允许缺 token 仍发；绕过 SDK _ctx 校验
                self._checked_media_call(3, lambda: self._bot.client.send_voice(
                    peer_id, p, "",
                    playtime_ms=ms, sample_rate=24000, encode_type=VoiceEncodeType.SILK,
                ))
            print(f"[ilink] voice sent peer={peer_id[:24]}… ms={ms}", flush=True)
        except (DeliveryFailed, DeliveryUnknown):
            raise
        except Exception as e:
            raise DeliveryUnknown(f"[ilink] voice send unconfirmed: {e}") from e


    def _typing_set(self, peer_id: str, ctx: str | None, *, on: bool) -> None:
        """点亮/取消「对方正在输入」。无 ctx 时也可走空 token（get_config 仍给 ticket）。"""
        bot = self._bot
        if bot is None or self.dry or not self.typing:
            return
        token = (ctx or self._ctx.get(peer_id) or "")
        try:
            from weixin_ilink.types import TypingStatus
            cfg = bot.client.get_config(peer_id, token)
            ticket = (cfg or {}).get("typing_ticket")
            if not ticket:
                return
            from weixin_ilink import api as _api
            st = int(TypingStatus.TYPING if on else TypingStatus.CANCEL)
            _api.send_typing(bot.client.opts, peer_id, ticket, st)
        except Exception as e:  # pragma: no cover
            print(f"[ilink] typing {'on' if on else 'off'} skip: {e}", file=sys.stderr, flush=True)

    def _typing_pulse(self, peer_id: str, ctx: str | None, stop: "threading.Event", *, every: float = 4.0) -> None:
        """生成回复期间周期性续亮，避免微信几秒后熄灭。"""
        self._typing_set(peer_id, ctx, on=True)
        while not stop.wait(every):
            self._typing_set(peer_id, ctx, on=True)

    def handle_one(self, sdk_msg: Any, *, batch=False) -> dict:
        inbound = normalize_sdk_msg(sdk_msg)
        if not inbound:
            return {}
        if batch and resolve_contact_id(inbound.channel, inbound.peer_id) == "demo_contact":
            from .reply_burst import collect, combine
            from reply_engagement import snapshot
            messages = collect(sdk_msg, lambda: self._take_newer(inbound.peer_id),
                               activity=snapshot("demo_contact"))
            inbound = combine([m for raw in messages if (m := normalize_sdk_msg(raw)) is not None])
            sdk_msg = messages[-1]
            print(f"[ilink] reply burst messages={len(messages)}", flush=True)
        ctx = getattr(sdk_msg, "context_token", None) or (inbound.raw or {}).get(
            "context_token"
        )
        if ctx:
            self._ctx[inbound.peer_id] = str(ctx)
            _persist_ctx(inbound.peer_id, str(ctx))
        cid = resolve_contact_id(inbound.channel, inbound.peer_id)
        print(
            f"[ilink] in peer={inbound.peer_id[:24]}… → contact={cid} "
            f"text={inbound.text[:80]!r}",
            flush=True,
        )
        from .bridge import handle_inbound

        stop_typing = threading.Event()
        pulse = None
        if self.typing and not self.dry and self._bot is not None:
            pulse = threading.Thread(
                target=self._typing_pulse,
                args=(inbound.peer_id, str(ctx) if ctx else None, stop_typing),
                name="ilink-typing",
                daemon=True,
            )
            pulse.start()

        def _send(out: Outbound):
            meta = dict(out.meta or {})
            if ctx:
                meta.setdefault("context_token", str(ctx))
            self._send(
                Outbound(
                    channel=out.channel,
                    peer_id=out.peer_id,
                    text=out.text,
                    meta=meta,
                )
            )

        _t0 = time.monotonic()
        result = {}
        try:
            # 等待/分气泡期间若同 peer 有更新入站 → 取消未发草稿，按最新 them 重生成
            rounds = 0
            while True:
                rounds += 1
                peer = inbound.peer_id

                def _should_cancel(_peer=peer):
                    return self._peer_has_newer(_peer)

                result = handle_inbound(
                    inbound,
                    dry=self.dry,
                    send=None if self.dry else _send,
                    should_cancel=None if self.dry else _should_cancel,
                )
                if not result.get("cancelled"):
                    break
                # Leave newer messages queued for the main worker once this turn
                # hits its regeneration budget. Never drain a batch we cannot handle.
                if rounds >= 3:
                    break
                newer = self._take_newer(peer)
                if not newer:
                    break
                from .reply_burst import collect, combine
                if batch:
                    first, rest = newer[0], newer[1:]
                    def drain():
                        nonlocal rest
                        found, rest = rest, []
                        return found + self._take_newer(peer)
                    from reply_engagement import snapshot
                    newer = collect(first, drain, activity=snapshot("demo_contact"))
                inbound = combine([m for raw in newer if (m := normalize_sdk_msg(raw)) is not None])
                ctx2 = getattr(newer[-1], "context_token", None) or (
                    (inbound.raw or {}).get("context_token") if inbound else None
                )
                if ctx2:
                    ctx = ctx2
                    self._ctx[inbound.peer_id] = str(ctx)
                    _persist_ctx(inbound.peer_id, str(ctx))
                print(
                    f"[ilink] regen from newer inbound x{len(newer)} round={rounds}",
                    flush=True,
                )
        finally:
            stop_typing.set()
            if pulse is not None:
                pulse.join(timeout=1.0)
            self._typing_set(inbound.peer_id, str(ctx) if ctx else None, on=False)

        # 当前 them 已回完 → 再 flush outbox；陈旧晚安夜里/正聊直接丢
        if (not self.dry and self._bot is not None and ctx and not result.get("cancelled")
                and not self._peer_has_newer(inbound.peer_id)):
            try:
                flush_ilink_outbox(
                    self._bot,
                    inbound.peer_id,
                    str(ctx),
                    after_inbound_text=inbound.text,
                )
            except Exception as e:
                print(f"[ilink] outbox flush error: {e!r}", file=sys.stderr, flush=True)

        wall_ms = int((time.monotonic() - _t0) * 1000)
        print(
            f"[ilink] reply dry={self.dry}: {result.get('reply')!r} "
            f"wall_ms={wall_ms} brain_ms={result.get('brain_ms')} t_ms={result.get('t_ms')} "
            f"cancelled={result.get('cancelled')}",
            flush=True,
        )
        self._got += 1
        if self.once and self._bot is not None:
            self._bot.stop()
        return result

    def _ensure_inbox(self) -> None:
        import queue as _queue
        if getattr(self, "_inbox", None) is None:
            self._inbox = _queue.Queue()
            self._inbox_lock = __import__("threading").Lock()

    def _peer_has_newer(self, peer_id: str) -> bool:
        """Non-destructive: True if inbox already has another USER msg from peer."""
        self._ensure_inbox()
        try:
            with self._inbox_lock:
                for item in list(self._inbox.queue):
                    try:
                        inb = normalize_sdk_msg(item)
                    except Exception:
                        inb = None
                    if inb and inb.peer_id == peer_id:
                        return True
        except Exception:
            return False
        return False

    def _take_newer(self, peer_id: str) -> list:
        """Drain same-peer messages from inbox (order preserved); requeue others."""
        self._ensure_inbox()
        import queue as _queue
        newer, kept = [], []
        with self._inbox_lock:
            while True:
                try:
                    item = self._inbox.get_nowait()
                except _queue.Empty:
                    break
                try:
                    inb = normalize_sdk_msg(item)
                except Exception:
                    inb = None
                if inb and inb.peer_id == peer_id:
                    newer.append(item)
                else:
                    kept.append(item)
            for item in kept:
                self._inbox.put(item)
        return newer

    def run(self) -> int:
        import queue as _queue
        import threading as _threading
        self._bot = self._build_bot()
        self._ensure_inbox()
        p = persona_cfg()
        print(
            f"[ilink] poll creds={self.credentials_file} dry={self.dry} "
            f"persona={p['id']} primary={p['primary_contact_id']}",
            flush=True,
        )
        _warmup_mem_async()
        stop = _threading.Event()
        self._feeder_err = None
        self._user_interrupt = False

        def _feeder():
            try:
                for msg in self._bot.messages():
                    if stop.is_set():
                        break
                    self._inbox.put(msg)
            except Exception as e:
                self._feeder_err = e
                print(f"[ilink] feeder fatal: {e!r}", flush=True)
            finally:
                self._inbox.put(None)  # sentinel

        feed_th = _threading.Thread(target=_feeder, name="ilink-feeder", daemon=True)
        feed_th.start()
        try:
            while True:
                msg = self._inbox.get()
                if msg is None:
                    break
                self.handle_one(msg, batch=True)
                if self.once and self._got:
                    break
        except KeyboardInterrupt:
            self._user_interrupt = True
            print("[ilink] interrupted", flush=True)
            if self._bot:
                self._bot.stop()
        finally:
            stop.set()
            if self._bot:
                try:
                    self._bot.stop()
                except Exception:
                    pass
        # P0: fatal disconnect → non-zero so launchd KeepAlive (SuccessfulExit=false) restarts
        if self._user_interrupt:
            return 0
        if self.once and self._got:
            return 0
        if self._feeder_err is not None:
            print(f"[ilink] exit 1 (fatal disconnect): {self._feeder_err!r}", flush=True)
            return 1
        # feeder ended without once-success and without user interrupt → treat as fatal
        if not (self.once and self._got):
            print("[ilink] exit 1 (feeder ended unexpectedly)", flush=True)
            return 1
        return 0


def self_test() -> int:
    """离线：归一化 + persona 映射，不连网。"""
    raw = {
        "message_type": 1,
        "from_user_id": "demo@im.wechat",
        "message_id": 99,
        "create_time_ms": 1700000000000,
        "context_token": "tok",
        "item_list": [{"type": 1, "text_item": {"text": "在吗"}}],
    }
    m = normalize_raw_msg(raw)
    assert m and m.channel == CHANNEL and m.peer_id == "demo@im.wechat"
    assert m.text == "在吗" and m.msg_id == "99"
    assert normalize_raw_msg({"message_type": 2, "from_user_id": "x"}) is None
    assert resolve_contact_id(CHANNEL, "demo@im.wechat")
    # 语音 ASR
    v = normalize_raw_msg(
        {
            "message_type": 1,
            "from_user_id": "demo@im.wechat",
            "item_list": [{"type": 3, "voice_item": {"text": "语音内容"}}],
        }
    )
    assert v and v.text == "语音内容"
    print("OK self-test")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="微信 iLink 自建通道 → 小甜 brain")
    ap.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "login", "check", "self-test"],
        help="run=长轮询; login=扫码; check=依赖/凭据; self-test=离线",
    )
    ap.add_argument("--creds", default=None, help="凭据 JSON，默认 data/ilink_creds.json")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--send", action="store_true", help="真正发送（关闭 dry-run）")
    ap.add_argument("--once", action="store_true", help="处理一条后退出")
    ap.add_argument("--no-typing", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="同 command=self-test")
    args = ap.parse_args(argv)

    if args.self_test or args.command == "self-test":
        return self_test()

    if args.command == "check":
        from .ilink_smoke import check

        r = check()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1

    if args.command == "login":
        do_login(save_to=Path(args.creds) if args.creds else None)
        return 0

    dry = not args.send
    return ILinkWeixinRunner(
        credentials_file=Path(args.creds) if args.creds else None,
        dry=dry,
        once=args.once,
        typing=not args.no_typing,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
