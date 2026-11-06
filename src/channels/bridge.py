"""通道 → brain 桥：入站归一化后走共享 contact_id 的记忆/回复，再经通道发出。

不启动 OCR daemon；ilink / onebot 各自的 runner 调用 handle_inbound。
"""
from __future__ import annotations
import datetime
import os
import random
import sys
import time
import threading
import functools
import copy
import hashlib
import uuid
from typing import Callable, Optional
from preview_mode import preview
from .types import Inbound, Outbound
from .persona_map import resolve_contact_id, allowed_peer
from .send_receipts import send_with_confirmation

# brain.LAST, the local vector store and send bookkeeping are shared. Keep the
# complete turn (including delayed media/send consumers) on one worker at a time.
_TURN_LOCK = threading.RLock()


def _serial_turn(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _TURN_LOCK, preview(kwargs.get("dry", True)):
            return fn(*args, **kwargs)
    return wrapped


def _sleep_split_gap(style: str = "normal"):
    """(split_kwargs, gap_kwargs) for bubble_split under sleep_chat style."""
    try:
        import sleep_chat as _sc
        if style in ("pre_sleep", "asleep"):
            return _sc.split_params(style), _sc.gap_params(style)
    except Exception:
        pass
    return {}, {}



def _interruptible_sleep(sec: float, should_cancel=None) -> bool:
    """Sleep up to sec; return True if cancelled mid-wait."""
    if sec <= 0:
        return bool(should_cancel and should_cancel())
    end = time.monotonic() + float(sec)
    while True:
        if should_cancel and should_cancel():
            return True
        left = end - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.35, left))


def _apply_nick_bubbles(bubbles, nick: str | None):
    try:
        from bubble_split import merge_sole_nick_bubbles
        return merge_sole_nick_bubbles(bubbles, nick)
    except Exception:
        return list(bubbles or [])


def _human_wait_for_send(elapsed: float, text: str, *, now: datetime.datetime | None = None,
                         activity=None) -> float:
    """短拟人垫：think 一律用 awake cap；打字手感扣掉 LLM 已耗时。

    不 import daemon（会拉起 mac_wechat_io）。
    已去掉睡觉态长等待（旧 think_max_sleeping_sec / 睡觉槽 60–300s），
    避免深夜回复垫几十秒～几分钟、typing 一直亮。
    """
    import schedule
    from config import load
    cfg = load()
    rc = {
        "human_typing": True,
        "typing_sec_per_char": 0.3,
        "typing_min_sec": 2,
        "typing_max_sec": 15,
        "think_max_awake_sec": 5,
    }
    rc.update(cfg.get("reply") or {})
    now = now or __import__("timeutil").now_local()
    # 睡觉/醒着同一短 cap；不再读 think_max_sleeping_sec
    cap = float(rc.get("think_max_awake_sec") or 5)
    max_delay = float(cfg.get("max_reply_delay_sec") or 300)
    think = min(schedule.reply_delay(now), cap, max_delay)
    if rc.get("human_typing"):
        typing = len(text or "") * rc["typing_sec_per_char"] + random.uniform(0.5, 1.5)
        typing = max(rc["typing_min_sec"], min(rc["typing_max_sec"], typing))
    else:
        typing = 0.0
    e = max(0.0, float(elapsed or 0.0))
    wait = max(0.0, think - e) + max(0.0, typing - e)
    from reply_engagement import pacing
    return min(wait, pacing(activity).get("wait_cap_sec", wait))


def _mem_release() -> None:
    """与 daemon._reply_and_release 对齐：释放 mem0 Qdrant 本地锁，便于 reflect/proactive。"""
    try:
        import mem
        mem.release()
    except Exception as e:  # pragma: no cover
        print(f"[bridge] mem.release: {e!r}", file=sys.stderr, flush=True)


def _plan_want_image(
    scene: str,
    text: str,
    *,
    send,
    peer_channel: str,
    peer_id: str,
    allow_hold_on: bool = False,
    user_text: str | None = None,
    shot_type: str | None = None,
    recent_outbound: list | None = None,
    on_hold_sent=None,
    imagined: bool = False,
):
    """brain.LAST want_image → chat_media.plan_chat_images；可选 hold_on 文本气泡。

    hold_on /「马上发你」仅当 allow_hold_on=True（对方明确要图且正在生图）。
    返回 img_plan dict 或 None（生图失败则退回纯文本）。
    """
    import chat_media
    from config import load
    cfg = load()
    ig = cfg.get("imagegen") or {}
    if (
        allow_hold_on
        and ig.get("hold_on_before_generate", True)
        and os.environ.get("IMAGEGEN_HOLD_ON", "1") not in ("0", "false", "no")
    ):
        try:
            hold = chat_media.hold_on_line(
                user_text=user_text,
                shot_type=shot_type,
                recent_outbound=recent_outbound,
            )
            if hold:
                send_with_confirmation(
                    send, Outbound(channel=peer_channel, peer_id=peer_id, text=hold),
                    lambda: on_hold_sent(hold) if on_hold_sent else None,
                )
                print(f"[bridge] hold_on: {hold}", flush=True)
        except Exception as he:
            print(f"[bridge] hold_on send failed: {he!r}", file=sys.stderr, flush=True)
    try:
        try:
            import timeutil as _tu
            now = _tu.now_local()
        except Exception:
            try:
                import life_calendar as lc
                now = lc.now_local()
            except Exception:
                now = datetime.datetime.now()
        # 把墙钟/life_now 时刻传给生图，供 night lighting（hour≥19）；shot_type 来自 brain.LAST
        shot_type = None
        shot_bits = None
        try:
            import brain as _br
            shot_type = (getattr(_br, "LAST", None) or {}).get("shot_type")
            shot_bits = (getattr(_br, "LAST", None) or {}).get("shot_bits")
        except Exception:
            pass
        return chat_media.plan_chat_images(
            scene, caption=text or None, now=now, shot_type=shot_type, shot_bits=shot_bits,
            user_text=user_text, image_lane="demand", imagined=imagined,
        )
    except Exception as e:
        print(f"[bridge] want_image generate failed, text-only: {e!r}", file=sys.stderr, flush=True)
        return None



def _plan_want_voice(text: str, *, channel: str) -> str | None:
    """brain.LAST want_voice → TTS file path（仅 QQ；缺凭据则 None，不改 TTS 实现）。"""
    if channel != "qq_onebot":
        return None
    try:
        from config import load
        cfg = load()
        voice_cfg = (cfg.get("persona") or {}).get("channels", {}).get("qq", {})
        # optional kill-switch
        if voice_cfg.get("want_voice_enabled") is False:
            return None
    except Exception:
        pass
    # Import existing MVP synthesizer without modifying it
    import importlib.util
    from pathlib import Path as _P
    mvp = _P(__file__).resolve().parents[2] / "tools" / "tts_doubao_mvp.py"
    if not mvp.is_file():
        print("[bridge] want_voice: tts_doubao_mvp.py missing", file=sys.stderr, flush=True)
        return None
    spec = importlib.util.spec_from_file_location("tts_doubao_mvp", mvp)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[bridge] want_voice import TTS fail: {e!r}", file=sys.stderr, flush=True)
        return None
    try:
        import os
        speaker = os.environ.get("DOUBAO_VOICE_SPEAKER", "").strip() or None
        # load env file side-effect via synthesize → _auth_headers
        audio, meta = mod.synthesize(
            (text or "嗯")[:200],
            speaker=speaker,
            fmt="mp3",
            sample_rate=24000,
            pitch=0,
            speech=0,
            loudness=0,
        )
        out_dir = _P(mod.OUT_DIR) if hasattr(mod, "OUT_DIR") else _P(__file__).resolve().parents[2] / "data" / "tts_mvp"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"bridge_voice_{stamp}.mp3"
        out.write_bytes(audio)
        print(f"[bridge] want_voice tts path={out} bytes={len(audio)}", flush=True)
        return str(out)
    except SystemExit as e:
        print(f"[bridge] want_voice TTS unavailable: {e}", file=sys.stderr, flush=True)
        return None
    except Exception as e:
        print(f"[bridge] want_voice TTS fail: {e!r}", file=sys.stderr, flush=True)
        return None


@_serial_turn
def handle_inbound(
    msg: Inbound,
    *,
    dry: bool = True,
    send: Optional[Callable[[Outbound], None]] = None,
    reply_fn=None,
    persist: Optional[bool] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """处理一条私聊入站。默认 dry：只算回复不发送。

    reply_fn(cid, hist) -> str；默认 brain.reply。
    persist: 是否写 chat.db / STATE / diary。默认随非 dry 开启；dry 始终不持久化。

    若 brain.LAST["want_image"]：经 chat_media 生图；先独立文字气泡，再 image_path 出站
    （ilink / onebot 的 _send 各自发图）；配文/互动短句走 text 气泡。

    should_cancel：报告同一联系人有更新入站；普通节奏停发未发草稿。
    活跃聊天先完成当前短文字回复，新入站留队下一轮，避免持续来信导致饿死。
    图片/语音及睡聊仍允许取消（已发气泡立即入库）。
    """
    persist = False if dry else (True if persist is None else persist)
    cid = resolve_contact_id(msg.channel, msg.peer_id)
    if cid != "demo_contact" or not allowed_peer(msg.channel, msg.peer_id):
        return {"contact_id": cid, "reply": "", "dry": dry, "persist": False,
                "blocked": True, "reason": "contact_not_allowed"}
    # Keep each attachment's own metadata while reading a burst, then present
    # all messages together as one conversational turn (and one stored text).
    burst = (msg.raw or {}).get("burst_messages")
    if burst:
        from .reply_burst import combine
        enriched = []
        for part in burst:
            if not dry:
                for module in ("image_caption", "voice_asr"):
                    try:
                        part, _ = __import__(module).enrich_inbound(part)
                    except Exception as exc:
                        print(f"[bridge] burst {module} skip: {exc!r}", flush=True)
            enriched.append(part)
        msg = combine(enriched)
    # After channel image cache / before brain: Magpie caption → enrich [图片]
    try:
        import image_caption as _ic
        msg, _cap_info = _ic.enrich_inbound(msg) if not dry and not burst else (msg, {})
        if _cap_info.get("ok") and _cap_info.get("caption"):
            print(
                f"[bridge] image_caption ok cached={_cap_info.get('cached')} "
                f"hash={(_cap_info.get('hash') or '')[:12]}…",
                flush=True,
            )
        elif _cap_info.get("error") not in (None, "no_tag", "disabled", "already_enriched"):
            print(f"[bridge] image_caption soft-fail: {_cap_info.get('error')}", flush=True)
    except Exception as e:
        print(f"[bridge] image_caption skip: {e!r}", file=sys.stderr, flush=True)

    # After channel voice cache / before brain: local ASR → enrich [语音]
    try:
        import voice_asr as _va
        msg, _asr_info = _va.enrich_inbound(msg) if not dry and not burst else (msg, {})
        if _asr_info.get("ok") and _asr_info.get("text"):
            print(
                f"[bridge] voice_asr ok source={_asr_info.get('source')} "
                f"cached={_asr_info.get('cached')} "
                f"hash={(_asr_info.get('hash') or '')[:12]}…",
                flush=True,
            )
        elif _asr_info.get("error") not in (None, "no_tag", "disabled", "already_enriched"):
            print(f"[bridge] voice_asr soft-fail: {_asr_info.get('error')}", flush=True)
    except Exception as e:
        print(f"[bridge] voice_asr skip: {e!r}", file=sys.stderr, flush=True)

    import memory, brain, emotion
    c = memory.db() if persist else None
    hist: list = []
    emo = None
    if persist:
        prev = (memory.tail(c, cid, 1) or [(0, "", "")])[-1][2]
        fp = (hashlib.sha256(f"received:{msg.alias_key}:{msg.msg_id}".encode()).hexdigest()
              if msg.msg_id else memory.fingerprint(cid, "them", f"{msg.channel}:{msg.text}", prev, 0))
        fresh_inbound = memory.insert(c, cid, "them", msg.text, fp, type=msg.msg_type or "text",
                      sent_at=msg.ts or None)
        if not fresh_inbound:
            # A UNIQUE conflict leaves SQLite's transaction open. Release it
            # before any receipt-journal writer can run, then stop this turn:
            # regenerating could bind an old delivery index to a different draft.
            try:
                c.rollback()
                original = c.execute(
                    "SELECT replied FROM messages WHERE contact=? AND sender='them' AND fp=?",
                    (cid, fp),
                ).fetchone()
                completed = bool(original and original[0])
            finally:
                c.close()
            return {"contact_id": cid, "reply": "", "dry": dry, "persist": True,
                    "channel": msg.channel, "peer_id": msg.peer_id, "duplicate": True,
                    "reason": "duplicate_inbound", "inbound_replied": completed,
                    "delivery_status": "confirmed" if completed else "recovery_needed",
                    "bubbles": [], "sent_bubbles": [], "n_images": 0}
        st = memory.read_state(cid)
        emo = st.get("emotion")
        if fresh_inbound:
            emo, _ = emotion.appraise(emo, msg.text)
            # 他开口 → 未回主动轮次归零（按轮次不是气泡）
            from proactive_limiter import inbound_patch
            memory.update_state(cid, emotion=emo,
                                **inbound_patch(msg.text, __import__("timeutil").now_local()),
                                **{k: v for k, v in emotion.derived(emo).items() if k in ("mood", "energy")})
        hist = memory.history(c, cid, 20)
    else:
        # smoke：不碰库，给 brain/stub 一条最小 hist
        hist = [("them", msg.text)]

    inbound_ids = ([r[0] for r in c.execute(
        "SELECT id FROM messages WHERE contact=? AND sender='them' AND replied=0", (cid,))]
        if persist else [])

    # Preserve the real number and times of messages hidden by burst.combine.
    # Atomic merge also handles simultaneous QQ / iLink arrivals and retries.
    import reply_engagement as engagement
    parts = (msg.raw or {}).get("burst_messages") or [msg]
    activity_now = __import__("timeutil").now_local()
    if persist:
        activity_state = memory.mutate_state(cid, lambda current: {
            engagement.STATE_KEY: engagement.record(current.get(engagement.STATE_KEY), parts, activity_now)})
        activity_blob = activity_state.get(engagement.STATE_KEY)
    else:
        activity_blob = engagement.record({}, parts, activity_now)
        activity_state = memory.read_state(cid)
    activity = {**engagement.profile(activity_blob, activity_now), "user_bubbles": len(parts)}
    activity = engagement.select_response(activity, activity_state, cid, msg.text, activity_now)

    sleep_style = "normal"
    try:
        import sleep_chat as _sc
        if persist:
            st0 = memory.read_state(cid)
            _mode, _blob, sleep_style = _sc.note_inbound(msg.text, st0, __import__("timeutil").now_local())
            memory.update_state(cid, **{_sc.STATE_KEY: _blob})
            brain.LAST["sleep_chat_mode"] = _mode
            brain.LAST["sleep_chat_style"] = sleep_style
        elif _sc.is_goodnight_text(msg.text) or _sc.in_pre_sleep_window(__import__("timeutil").now_local()):
            sleep_style = "pre_sleep"
    except Exception as e:
        print(f"[bridge] sleep_chat inbound skip: {e!r}", file=sys.stderr, flush=True)
    sleep_style = engagement.effective_sleep_style(sleep_style, activity)

    _t = time.monotonic()
    try:
        if reply_fn is None:
            text = brain.reply(cid, hist, dry=(dry or not persist), now=activity_now, activity=activity,
                               user_text=msg.text)
        else:
            text = reply_fn(cid, hist)
    finally:
        _mem_release()
    brain_ms = int((time.monotonic() - _t) * 1000)
    text = (text or "").strip()
    pending_delivery = copy.deepcopy((getattr(brain, "LAST", None) or {}).get("pending_delivery"))
    turn_id = f"{msg.alias_key}:{msg.msg_id or msg.ts or uuid.uuid4().hex}"
    imagined = bool((getattr(brain, "LAST", None) or {}).get("imagined_scene"))
    scene = (getattr(brain, "LAST", None) or {}).get("want_image")
    want_voice = (getattr(brain, "LAST", None) or {}).get("want_voice")
    out = {"contact_id": cid, "reply": text, "dry": dry, "channel": msg.channel, "peer_id": msg.peer_id,
           "brain_ms": brain_ms, "t_ms": getattr(brain, "LAST", {}).get("t_ms"),
           "persist": persist, "want_image": scene, "want_voice": bool(want_voice),
           "reply_engagement": activity}
    search = (getattr(brain, "LAST", None) or {}).get("search")
    if isinstance(search, dict) and search:
        import re
        from urllib.parse import urlsplit, urlunsplit
        evidence = {k: str(search[k])[:80] for k in ("status", "provider") if search.get(k)}
        if search.get("reason"):
            reason = str(search["reason"])
            evidence["reason"] = reason if re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", reason) else "search_provider_error"
        urls = []
        for source in (search.get("sources") or [])[:5]:
            try:
                parsed = urlsplit(str(source.get("url") or ""))
                if parsed.scheme in ("http", "https") and parsed.hostname:
                    # Diagnostic links omit credentials, query signatures and
                    # fragments; no query, snippets or browser session payload.
                    url = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", ""))
                    if url not in urls:
                        urls.append(url)
            except (AttributeError, TypeError, ValueError):
                continue
        evidence["source_urls"] = urls
        out["search_evidence"] = evidence
    if text == "[SKIP]":
        return out
    if (not text) and not scene:
        return out
    if dry or send is None:
        return out
    try:
        import sleep_chat as _sc
        sleep_style = (getattr(brain, "LAST", None) or {}).get("sleep_chat_style") or sleep_style
        sleep_style = engagement.effective_sleep_style(sleep_style, activity)
        if sleep_style in ("pre_sleep", "asleep"):
            if text:
                text = _sc.tighten_text(text, sleep_style)
                out["reply"] = text
            # 睡前/已睡：不主动发图（他强要图仍可能经 photo_scene；此处收住 want_image）
            if sleep_style == "asleep":
                scene = None
                out["want_image"] = None
            out["sleep_chat_style"] = sleep_style
    except Exception as e:
        print(f"[bridge] sleep_chat tighten skip: {e!r}", file=sys.stderr, flush=True)
    chat_pace = engagement.pacing(activity if sleep_style == "normal" else None)
    if (sleep_style == "normal" and activity.get("response_mode") != "closing"
            and (activity["level"] in ("active", "dense") or activity.get("response_mode") == "enthusiastic")
            and text and not scene and not want_voice
            and not (getattr(brain, "LAST", None) or {}).get("weather_tools")):
        # The callback means newer inbound, not an operator stop. Finish this
        # bounded text turn; the runner retains all newer messages for its next turn.
        should_cancel = None
    # 真聊（persist）才垫作息延时；smoke/stub 的 persist=False 不睡，避免测试卡死
    if persist:
        try:
            delay = _human_wait_for_send(time.monotonic() - _t, text or ("[图片]" if scene else ""),
                                         activity=activity if sleep_style == "normal" else None)
            out["send_delay_sec"] = round(delay, 2)
            if delay > 0:
                print(f"[bridge] human wait {delay:.1f}s before send", flush=True)
                if _interruptible_sleep(delay, should_cancel):
                    print("[bridge] cancel during human wait → drop unsent draft", flush=True)
                    out["cancelled"] = True
                    out["reply"] = ""
                    return out
        except Exception as e:  # pragma: no cover
            print(f"[bridge] schedule delay skip: {e!r}", file=sys.stderr, flush=True)

    # 发送前再看一眼：新消息 → 丢弃本轮未发草稿，交给上层重生成
    if should_cancel and should_cancel():
        print("[bridge] cancel before send → drop unsent draft", flush=True)
        out["cancelled"] = True
        out["reply"] = ""
        return out

    nick = (getattr(brain, "LAST", None) or {}).get("nickname")

    # 底层门闩：闲聊不得生图/hold_on；仅对方明确要照片时才走图（reply_fn stub 也拦）
    user_asked_photo = False
    try:
        import photo_scene as _ps
        user_asked_photo = _ps.allow_inbound_image_gen(msg.text)
    except Exception as e:
        print(f"[bridge] photo ask gate skip: {e!r}", file=sys.stderr, flush=True)
    if scene and not user_asked_photo:
        print(
            f"[bridge] drop want_image (no explicit photo ask) text={msg.text!r:.40}",
            flush=True,
        )
        scene = None
        out["want_image"] = None

    img_plan = None
    if scene:
        _shot_for_hold = (getattr(brain, "LAST", None) or {}).get("shot_type")
        # 最近己方气泡供 hold_on LLM 去重（与 hold_on_line.recent_outbound 对齐）
        _recent_out = [t for s, t in (hist or []) if s == "me" and t][-8:]
        if persist:
            _recent_out += list(memory.read_state(cid).get("recent_photo_holds") or [])

        def record_hold(hold):
            if not persist:
                return
            hold_db = memory.db()
            fp = hashlib.sha256(f"{turn_id}:hold:{hold}".encode()).hexdigest()
            try:
                inserted = memory.insert(hold_db, cid, "me", hold, fp, type="photo_hold")
            finally:
                hold_db.close()
            if not inserted:
                return
            # Acknowledging the photo does not mark the full request replied.
            memory.mutate_state(cid, lambda current: {
                "recent_photo_holds": (list(current.get("recent_photo_holds") or []) + [hold])[-8:],
                "last_reply_at": __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S")})

        img_plan = _plan_want_image(
            scene,
            text,
            send=send,
            peer_channel=msg.channel,
            peer_id=msg.peer_id,
            allow_hold_on=user_asked_photo,
            user_text=msg.text,
            shot_type=_shot_for_hold,
            recent_outbound=_recent_out,
            on_hold_sent=record_hold, imagined=imagined,
        )

    if persist and img_plan:
        from pathlib import Path
        img_plan = {**img_plan, "paths": [p for p in (img_plan.get("paths") or []) if Path(p).is_file()]}

    voice_path = None
    if want_voice and not img_plan:
        voice_path = _plan_want_voice(text, channel=msg.channel)
        out["voice_path"] = voice_path

    from bubble_split import split_bubbles, bubble_gap_sec
    _sk, _gk = _sleep_split_gap(sleep_style)
    if sleep_style == "normal":
        _gk = {**_gk, **chat_pace["gap"]}
    # Use the content policy chosen before generation, including any sleep cap.
    try:
        _budget = (getattr(brain, "LAST", None) or {}).get("reply_bubble_budget")
        if _budget:
            _b = max(1, int(_budget))
            if _sk.get("max_bubbles"):
                _sk = {**_sk, "max_bubbles": min(int(_sk["max_bubbles"]), _b)}
            else:
                _sk = {**_sk, "max_bubbles": _b}
    except Exception:
        pass

    # A failed generation must never reuse a caption claiming the photo exists.
    if scene and not (img_plan and img_plan.get("paths")):
        text = "照片这次没弄好，没发成"
        out["image_generation_failed"] = True

    planned = []
    def add(piece, **meta):
        planned.append((piece, Outbound(channel=msg.channel, peer_id=msg.peer_id,
                                       text="" if meta else piece, meta=meta)))

    if img_plan and img_plan.get("paths"):
        paths = img_plan["paths"]
        out["image_paths"] = [str(p) for p in paths]
        cap = "\n".join(str(t).strip() for t in (img_plan.get("texts") or []) if t)
        for bub in _apply_nick_bubbles(split_bubbles(cap, **_sk) or ([cap] if cap else []), nick):
            add(bub)
        for path in paths:
            add(f"[图片:{'想象场景：' if imagined else ''}{scene}]", image_path=str(path), imagined_scene=imagined)
    else:
        if voice_path:
            add("[语音]", voice_path=voice_path)
            if want_voice in (True, 1, "1", "voice", "voice_only", "record"):
                text = ""
        for bub in _apply_nick_bubbles(split_bubbles(text, **_sk) or ([text] if text else []), nick):
            add(bub)

    confirmed = {}
    confirm_lock = threading.RLock()
    finished = False

    def mark_complete():
        # Only the inbound snapshot belongs to this response; later messages stay pending.
        if not persist or not finished or len(confirmed) != len(planned) or not planned:
            return
        done_db = memory.db()
        try:
            done_db.executemany("UPDATE messages SET replied=1 WHERE contact=? AND id=? AND sender='them'",
                                [(cid, mid) for mid in inbound_ids])
            done_db.commit()
        finally:
            done_db.close()

    def confirm(index, piece):
        with confirm_lock:
            if index in confirmed:
                return
            # Late ACK callbacks run on a worker: never reuse the caller's SQLite connection.
            if persist:
                done_db = memory.db()
                fp = hashlib.sha256(f"{turn_id}:{index}:{piece}".encode()).hexdigest()
                try:
                    inserted = memory.insert(done_db, cid, "me", piece, fp)
                finally:
                    done_db.close()
                if inserted:
                    memory.append_diary(cid, f"[{msg.channel}] TA说「{msg.text[:40]}」，我回「{piece}」")
                confirmed_at = datetime.datetime.now()
                def state_patch(current):
                    patch = {"last_reply_at": confirmed_at.strftime("%Y-%m-%d %H:%M:%S")}
                    try:
                        import sleep_chat as _sc
                        mode, blob = _sc.note_outbound(piece, current, confirmed_at)
                        patch[_sc.STATE_KEY] = blob
                        out["sleep_chat_mode"] = mode
                    except Exception as exc:
                        print(f"[bridge] sleep_chat outbound skip: {exc!r}", file=sys.stderr, flush=True)
                    return patch
                memory.mutate_state(cid, state_patch)
                if pending_delivery and hasattr(brain, "commit_delivery"):
                    brain.commit_delivery(cid, pending_delivery, delivery_id=turn_id,
                                          sent_text=piece, now=confirmed_at)
            confirmed[index] = piece
            mark_complete()

    cancelled_mid = False
    for index, (piece, outbound) in enumerate(planned):
        if index and _interruptible_sleep(bubble_gap_sec(planned[index - 1][0], **_gk), should_cancel):
            cancelled_mid = True
            break
        if should_cancel and should_cancel():
            cancelled_mid = True
            break
        outbound.meta.update(delivery_id=f"{turn_id}:{index}", pending_delivery=pending_delivery, persist_delivery=persist, imagined_scene=imagined)
        try:
            send_with_confirmation(send, outbound, lambda i=index, p=piece: confirm(i, p))
        except Exception as exc:
            out["delivery_status"] = getattr(exc, "delivery_status", "unknown")
            out["delivery_error"] = str(exc)
            print(f"[bridge] send {out['delivery_status']}; confirmed={len(confirmed)}: {exc!r}",
                  file=sys.stderr, flush=True)
            break
    with confirm_lock:
        finished = True
        mark_complete()
        sent_parts = [confirmed[i] for i in sorted(confirmed)]
    out["reply"] = " / ".join(sent_parts)
    out["bubbles"] = list(sent_parts)
    out["sent_bubbles"] = list(sent_parts)
    out["n_images"] = sum(piece.startswith("[图片:") for piece in sent_parts)
    if cancelled_mid:
        out["cancelled"] = True
    if "delivery_status" not in out:
        out["delivery_status"] = "confirmed" if len(sent_parts) == len(planned) else "partial"
    if c is not None:
        c.close()
    return out
