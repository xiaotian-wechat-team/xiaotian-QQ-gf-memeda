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
import contextvars
import contextlib
from dataclasses import asdict
from typing import Callable, Optional
from preview_mode import preview
from .types import Inbound, Outbound
from .persona_map import resolve_contact_id, allowed_peer
from .send_receipts import send_with_confirmation

# brain.LAST, the local vector store and send bookkeeping are shared. Keep the
# complete turn (including delayed media/send consumers) on one worker at a time.
_TURN_LOCK = threading.RLock()
_RECOVERY = contextvars.ContextVar('bridge_recovery', default=None)


def _serial_turn(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        from config import load, snapshot
        with _TURN_LOCK, preview(kwargs.get("dry", True)), snapshot(load()):
            state = {}
            token = _RECOVERY.set(state)
            try:
                result = fn(*args, **kwargs)
                if state.get('turn') and state['turn'].phase in ('generating', 'retry_generate'):
                    state['turn'].update('held')  # deliberate SKIP/empty/cancelled draft
                return result
            except Exception:
                if state.get('turn'):
                    state['turn'].failed()
                raise
            finally:
                if state.get('db') is not None:
                    state['db'].close()
                _RECOVERY.reset(token)
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
    now = now or datetime.datetime.now()
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
    返回 img_plan dict 或 None；无图时整轮取消，不保留配文。
    """
    import chat_media
    from config import load
    cfg = load()
    ig = cfg.get("imagegen") or {}
    def sleep_guard():
        if (cfg.get("identity_runtime") or {}).get("schema_version") != 2:
            import memory, sleep_chat
            sleep_chat.check_reply_send(memory.read_state(str((cfg.get("contact") or {}).get("id") or "demo_contact")),
                                        datetime.datetime.now())
    sleep_guard()
    # No hold message or provider call before the schedule gate has passed.
    import schedule_fidelity as sf
    import memory as photo_memory
    now = sf.now_local()
    photo_context, reason = sf.resolve_photo_schedule(
        user_text or "", scene, photo_memory.read_state(str((cfg.get("contact") or {}).get("id") or "demo_contact")), now)
    if reason:
        print(f"[bridge] image schedule gate: {reason}", flush=True)
        return None
    if photo_context["historical"]:
        text = f"按{photo_context['date']}的日程还原给你看"

    if (cfg.get("identity_runtime") or {}).get("schema_version") == 2:
        try:
            from runtime_policy import require
            require("image_send", cfg)
        except Exception as exc:
            print(f"[bridge] registered native image failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            return None
    if (
        allow_hold_on
        and (cfg.get("identity_runtime") or {}).get("schema_version") != 2
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
                    before_send=sleep_guard,
                )
                print(f"[bridge] hold_on: {hold}", flush=True)
        except Exception as he:
            from sleep_chat import ReplySleepBlocked
            if isinstance(he, ReplySleepBlocked):
                raise
            print(f"[bridge] hold_on send failed: {he!r}", file=sys.stderr, flush=True)
    sleep_guard()
    try:
        try:
            import life_calendar as lc
            now = lc.now_local()
        except Exception:
            now = datetime.datetime.now()
        # 传递请求时刻；生图解析器再按日程槽/画面时段确定光照。
        shot_bits = None
        try:
            import brain as _br
            shot_type = shot_type or (getattr(_br, "LAST", None) or {}).get("shot_type")
            shot_bits = (getattr(_br, "LAST", None) or {}).get("shot_bits")
        except Exception:
            pass
        return chat_media.plan_chat_images(
            scene, caption=text or None, cfg=cfg, now=photo_context["now"], shot_type=shot_type, shot_bits=shot_bits,
            user_text=user_text, image_lane="demand", imagined=False,
            slot=photo_context["slot"], today=photo_context["today"], date=photo_context["date"],
            shot_lock=bool(shot_type),
        )
    except Exception as e:
        print(f"[bridge] photo turn cancelled: {e!r}", file=sys.stderr, flush=True)
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


def _silence_turn(cid, inbound_ids, turn):
    """Keep night inputs as context; do not replay their obsolete replies at dawn."""
    import memory
    with contextlib.closing(memory.db()) as db, db:
        db.executemany("UPDATE messages SET replied=1 WHERE contact=? AND id=? AND sender='them'",
                       [(cid, mid) for mid in inbound_ids])
    if turn:
        turn.update('complete')


@_serial_turn
def handle_inbound(
    msg: Inbound,
    *,
    dry: bool = True,
    send: Optional[Callable[[Outbound], None]] = None,
    reply_fn=None,
    persist: Optional[bool] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    _turn_key=None,
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
    from config import load
    cfg = load()
    primary = str((cfg.get("persona") or {}).get("primary_contact_id") or (cfg.get("contact") or {}).get("id") or "")
    cid = resolve_contact_id(msg.channel, msg.peer_id, cfg, msg.bot_account_id)
    if (cid != primary or msg.peer_type != "private" or
            not allowed_peer(msg.channel, msg.peer_id, cfg, msg.bot_account_id)):
        return {"contact_id": cid, "reply": "", "dry": dry, "persist": False,
                "blocked": True, "reason": "unknown_route"}
    runtime = cfg.get("identity_runtime") or {}
    registered_role = runtime.get("schema_version") == 2
    from runtime_policy import allowed, require
    if not allowed("dry_run" if dry else "auto_reply", cfg):
        return {"contact_id": cid, "reply": "", "dry": dry, "persist": False,
                "blocked": True, "reason": "capability_disabled"}
    import memory, sleep_chat
    quiet = (None if registered_role else
             sleep_chat.reply_suppression_reason(memory.read_state(cid), datetime.datetime.now()))
    # Keep each attachment's own metadata while reading a burst, then present
    # all messages together as one conversational turn (and one stored text).
    burst = (msg.raw or {}).get("burst_messages")
    if burst:
        from .reply_burst import combine
        enriched = []
        for part in burst:
            if not dry and not quiet:
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
        msg, _cap_info = _ic.enrich_inbound(msg) if not dry and not burst and not quiet else (msg, {})
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
        msg, _asr_info = _va.enrich_inbound(msg) if not dry and not burst and not quiet else (msg, {})
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
        from .turn_store import TurnStore
        store = TurnStore()
        _RECOVERY.get()['db'] = c
        prev = (memory.tail(c, cid, 1) or [(0, "", "")])[-1][2]
        fp = _turn_key or (hashlib.sha256(f"received:{msg.alias_key}:{msg.msg_id}".encode()).hexdigest()
              if msg.msg_id else memory.fingerprint(cid, "them", f"{msg.channel}:{msg.text}", prev, 0))
        fresh_inbound = memory.insert(c, cid, "them", msg.text, fp, type=msg.msg_type or "text",
                      sent_at=msg.ts or None, commit=False)
        if fresh_inbound:
            turn = store.claim(fp, msg, create=True, connection=c)
        else:
            c.rollback()
            turn = store.claim(fp, msg)
        _RECOVERY.get()['turn'] = turn
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
                c.rollback()
            if turn and turn.plan:
                c.close()
                return _resume_plan(msg, cid, turn, send, should_cancel)
            if completed and turn:
                turn.update('complete')
            if completed or not turn:
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
                                **inbound_patch(msg.text, datetime.datetime.now()),
                                **{k: v for k, v in emotion.derived(emo).items() if k in ("mood", "energy")})
        hist = memory.history(c, cid, 20)
    else:
        # smoke：不碰库，给 brain/stub 一条最小 hist
        hist = [("them", msg.text)]

    inbound_ids = ([r[0] for r in c.execute(
        "SELECT id FROM messages WHERE contact=? AND sender='them' AND replied=0", (cid,))]
        if persist else [])
    if c is not None:
        c.close()
        c = None

    quiet = (None if registered_role else
             sleep_chat.reply_suppression_reason(memory.read_state(cid), datetime.datetime.now()))
    if quiet:
        if persist:
            _silence_turn(cid, inbound_ids, (_RECOVERY.get() or {}).get('turn'))
        return {"contact_id": cid, "reply": "", "dry": dry, "persist": persist,
                "channel": msg.channel, "peer_id": msg.peer_id, "blocked": True,
                "sleep_suppressed": True, "reason": quiet, "delivery_status": "suppressed",
                "bubbles": [], "sent_bubbles": [], "n_images": 0}

    # Preserve the real number and times of messages hidden by burst.combine.
    # Atomic merge also handles simultaneous QQ / iLink arrivals and retries.
    import reply_engagement as engagement
    parts = (msg.raw or {}).get("burst_messages") or [msg]
    activity_now = datetime.datetime.now()
    if persist:
        activity_state = memory.mutate_state(cid, lambda current: {
            engagement.STATE_KEY: engagement.record(current.get(engagement.STATE_KEY), parts, activity_now)})
        activity_blob = activity_state.get(engagement.STATE_KEY)
    else:
        activity_blob = engagement.record({}, parts, activity_now)
        activity_state = memory.read_state(cid)
    activity = {**engagement.profile(activity_blob, activity_now), "user_bubbles": len(parts)}
    if not registered_role:
        activity = engagement.select_response(activity, activity_state, cid, msg.text, activity_now)

    sleep_style = "normal"
    try:
        import sleep_chat as _sc
        if persist and not registered_role:
            st0 = memory.read_state(cid)
            _mode, _blob, sleep_style = _sc.note_inbound(msg.text, st0, datetime.datetime.now())
            memory.update_state(cid, **{_sc.STATE_KEY: _blob})
            brain.LAST["sleep_chat_mode"] = _mode
            brain.LAST["sleep_chat_style"] = sleep_style
        elif not registered_role and (_sc.is_goodnight_text(msg.text) or _sc.in_pre_sleep_window(datetime.datetime.now())):
            sleep_style = "pre_sleep"
    except Exception as e:
        print(f"[bridge] sleep_chat inbound skip: {e!r}", file=sys.stderr, flush=True)
    sleep_style = engagement.effective_sleep_style(sleep_style, activity)

    _t = time.monotonic()
    try:
        if reply_fn is None:
            text = brain.reply(cid, hist, dry=(dry or not persist), now=activity_now, activity=activity,
                               user_text=msg.text,
                               peer=f"{msg.channel}:{msg.peer_id}", channel=msg.channel)
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
    if persist and not registered_role:
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
    quiet = (None if registered_role else
             sleep_chat.reply_suppression_reason(memory.read_state(cid), datetime.datetime.now()))
    if quiet:
        if persist:
            _silence_turn(cid, inbound_ids, (_RECOVERY.get() or {}).get('turn'))
        out.update(reply="", blocked=True, sleep_suppressed=True, reason=quiet, delivery_status="suppressed")
        return out
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
        if persist:
            _silence_turn(cid, inbound_ids, (_RECOVERY.get() or {}).get('turn'))
        out.update(reply="", want_image=None, image_rejected=True)
        return out  # An unverified photo caption cannot become a text-only send.
    if scene and not ((runtime.get("capabilities") or {}).get("image_generate", True)):
        if persist:
            _silence_turn(cid, inbound_ids, (_RECOVERY.get() or {}).get('turn'))
        out.update(reply="", want_image=None, image_generation_disabled=True, delivery_status="suppressed")
        return out

    img_plan = None
    if scene:
        if persist:
            # A hold message can leave the process before image planning ends.
            # Do not regenerate that turn blindly after a crash.
            _RECOVERY.get()['turn'].update('held', release=False)
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
                "last_reply_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

        try:
            img_plan = _plan_want_image(
                scene, text, send=send, peer_channel=msg.channel, peer_id=msg.peer_id,
                allow_hold_on=user_asked_photo, user_text=msg.text, shot_type=_shot_for_hold,
                recent_outbound=_recent_out, on_hold_sent=record_hold, imagined=imagined,
            )
        except sleep_chat.ReplySleepBlocked as exc:
            if persist:
                _silence_turn(cid, inbound_ids, (_RECOVERY.get() or {}).get('turn'))
            out.update(reply="", blocked=True, sleep_suppressed=True, reason=str(exc), delivery_status="suppressed")
            return out

    if persist and img_plan:
        from pathlib import Path
        if any(not Path(p).is_file() for p in (img_plan.get("paths") or [])):
            img_plan = None

    if scene and not (img_plan and img_plan.get("paths")):
        if persist:
            _silence_turn(cid, inbound_ids, (_RECOVERY.get() or {}).get('turn'))
        out.update(reply="", want_image=None, image_generation_failed=True, delivery_status="suppressed")
        return out

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

    planned = []
    def add(piece, **meta):
        operation_id = f"{turn_id}:{len(planned)}"
        outbound_meta = meta
        if runtime.get("local_companion"):
            outbound_meta = {**meta, "persona_version": cfg.get("persona_version")}
        planned.append((piece, Outbound(
            channel=msg.channel, peer_id=msg.peer_id,
            text="" if meta else piece, meta=outbound_meta,
            bot_account_id=msg.bot_account_id,
            persona_id=str((cfg.get("persona") or {}).get("id") or ""),
            partner_id=cid, request_id=turn_id, operation_id=operation_id)))

    if img_plan and img_plan.get("paths"):
        paths = img_plan["paths"]
        out["image_paths"] = [str(p) for p in paths]
        cap = "\n".join(str(t).strip() for t in (img_plan.get("texts") or []) if t)
        for bub in _apply_nick_bubbles(split_bubbles(cap, **_sk) or ([cap] if cap else []), nick):
            add(bub)
        for path in paths:
            add(f"[图片:{'想象场景：' if imagined else ''}{scene}]", image_path=str(path), imagined_scene=imagined, photo_schedule=img_plan.get("photo_schedule"))
    else:
        if voice_path:
            add("[语音]", voice_path=voice_path)
            if want_voice in (True, 1, "1", "voice", "voice_only", "record"):
                text = ""
        for bub in _apply_nick_bubbles(split_bubbles(text, **_sk) or ([text] if text else []), nick):
            add(bub)

    turn = (_RECOVERY.get() or {}).get('turn')
    delivery_day = datetime.datetime.now().date().isoformat()
    if turn:
        turn_id = f"received:{turn.key}"
        # Delivery ids and exact pieces survive a crash; never regenerate a
        # different draft against an existing receipt index.
        for index, (_, outbound) in enumerate(planned):
            outbound.operation_id = f"{turn_id}:{index}"
        turn.update('planned', release=False, plan={
            'planned': [(piece, asdict(outbound)) for piece, outbound in planned],
            'out': out, 'turn_id': turn_id, 'inbound_ids': inbound_ids,
            'pending_delivery': pending_delivery, 'registered_role': registered_role,
            'imagined': imagined, 'img_plan': img_plan, 'gap': _gk, 'delivery_day': delivery_day})
    return _deliver_plan(msg, cid, planned, send=send, persist=persist, out=out,
        turn_id=turn_id, inbound_ids=inbound_ids, pending_delivery=pending_delivery,
        registered_role=registered_role, imagined=imagined, img_plan=img_plan,
        should_cancel=should_cancel, _gk=_gk, turn=turn, delivery_day=delivery_day)


def _resume_plan(msg, cid, turn, send, should_cancel):
    data = turn.plan
    out = {**data['out'], 'recovered': True}
    for key in ('delivery_status', 'delivery_error', 'cancelled'):
        out.pop(key, None)
    return _deliver_plan(msg, cid, [(piece, Outbound(**out)) for piece, out in data['planned']],
        send=send, persist=True, out=out,
        turn_id=data['turn_id'], inbound_ids=data['inbound_ids'], pending_delivery=data['pending_delivery'],
        registered_role=data['registered_role'], imagined=data['imagined'], img_plan=data['img_plan'],
        should_cancel=should_cancel, _gk=data['gap'], turn=turn, delivery_day=data['delivery_day'])


def recover_pending(channel, *, send, reply_fn=None, bot_account_id=None):
    """Called by the existing worker, including after restart. No unknown resend."""
    from .turn_store import TurnStore
    results = []
    for key, msg in TurnStore().pending(channel, bot_account_id=bot_account_id):
        try:
            results.append(handle_inbound(msg, dry=False, send=send, reply_fn=reply_fn, _turn_key=key))
        except Exception as exc:
            print(f"[bridge] pending recovery deferred: {type(exc).__name__}", file=sys.stderr, flush=True)
    return results


def _deliver_plan(msg, cid, planned, *, send, persist, out, turn_id, inbound_ids,
                  pending_delivery, registered_role, imagined, img_plan, should_cancel,
                  _gk, turn=None, delivery_day=None):
    import memory, brain
    from bubble_split import bubble_gap_sec
    from runtime_policy import require
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
        if turn:
            turn.update('complete')

    def confirm(index, piece):
        try:
            record_confirmation(index, piece)
        except Exception:
            if turn:
                # This can run after a timeout on the late-ACK worker. The
                # transport journal is already confirmed, so only bookkeeping
                # (and the never-attempted suffix) may be recovered.
                turn.update('retry_plan', delay=10)
            raise

    def record_confirmation(index, piece):
        with confirm_lock:
            if index in confirmed:
                return
            if turn and index in (turn.plan.get('bookkept') or []):
                confirmed[index] = piece
                mark_complete()
                return
            # Late ACK callbacks run on a worker: never reuse the caller's SQLite connection.
            if persist:
                done_db = memory.db()
                fp = hashlib.sha256(f"{turn_id}:{index}:{piece}".encode()).hexdigest()
                try:
                    memory.insert(done_db, cid, "me", piece, fp)
                finally:
                    done_db.close()
                memory.append_delivery_diary(cid, f"[{msg.channel}] TA说「{msg.text[:40]}」，我回「{piece}」",
                                             f"{turn_id}:{index}", day=delivery_day)
                receipt_time = planned[index][1].meta.get('_confirmed_at')
                confirmed_at = datetime.datetime.fromtimestamp(receipt_time) if receipt_time else datetime.datetime.now()
                def state_patch(current):
                    delivery = hashlib.sha256(f'{turn_id}:{index}'.encode()).hexdigest()
                    recorded = list(current.get('confirmed_delivery_state') or [])
                    if delivery in recorded:
                        return {}
                    patch = {"last_reply_at": max(str(current.get('last_reply_at') or ''),
                                                   confirmed_at.strftime("%Y-%m-%d %H:%M:%S")),
                             'confirmed_delivery_state': (recorded + [delivery])[-256:]}
                    if registered_role:
                        return patch
                    try:
                        import sleep_chat as _sc
                        patch.update(_sc.confirmed_outbound_patch(piece, current, confirmed_at))
                        out["sleep_chat_mode"] = (patch.get(_sc.STATE_KEY) or {}).get("mode")
                    except Exception as exc:
                        print(f"[bridge] sleep_chat outbound skip: {exc!r}", file=sys.stderr, flush=True)
                    return patch
                memory.mutate_state(cid, state_patch)
                if pending_delivery and hasattr(brain, "commit_delivery"):
                    brain.commit_delivery(cid, pending_delivery, delivery_id=turn_id,
                                          sent_text=piece, now=confirmed_at)
                if turn:
                    turn.bookkept(index)
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
            def before_send():
                if not registered_role:
                    from sleep_chat import check_reply_send
                    check_reply_send(memory.read_state(cid), datetime.datetime.now())
                if persist and inbound_ids:
                    # A newer response may have covered this inbound while
                    # generation/bookkeeping was deferred. Repair confirmed
                    # pieces, but do not send its now-obsolete unsent suffix.
                    check_db = memory.db()
                    try:
                        pending = check_db.execute(
                            "SELECT 1 FROM messages WHERE contact=? AND sender='them' AND replied=0 AND id IN ("
                            + ','.join('?' for _ in inbound_ids) + ') LIMIT 1', (cid, *inbound_ids)).fetchone()
                    finally:
                        check_db.close()
                    if not pending:
                        from .send_receipts import DeliveryFailed
                        raise DeliveryFailed('Inbound already covered by a newer response')
                if img_plan and img_plan.get("photo_schedule"):
                    from photo_schedule import check
                    check(img_plan["photo_schedule"])
                if img_plan:
                    from pathlib import Path
                    from photo_schedule import check_file
                    for path in img_plan.get("paths") or []:
                        if not Path(path).is_file():
                            from .send_receipts import DeliveryFailed
                            raise DeliveryFailed("图片结果缺失，取消本轮剩余内容")
                        check_file(path)
                require("auto_reply")
                if outbound.meta.get("image_path"):
                    require("image_send")
            send_with_confirmation(send, outbound, lambda i=index, p=piece: confirm(i, p),
                                   before_send=before_send)
        except Exception as exc:
            from sleep_chat import ReplySleepBlocked
            if isinstance(exc, ReplySleepBlocked):
                out.update(blocked=True, sleep_suppressed=True, reason=str(exc), delivery_status="suppressed")
                if persist:
                    _silence_turn(cid, inbound_ids, turn)
                break
            from photo_schedule import SchedulePhotoError
            if isinstance(exc, SchedulePhotoError):
                out.update(image_rejected=True, reason=str(exc), delivery_status="suppressed")
                if persist:
                    _silence_turn(cid, inbound_ids, turn)
                break
            out["delivery_status"] = getattr(exc, "delivery_status", "unknown")
            if turn:
                if out["delivery_status"] == 'bookkeeping_pending':
                    turn.failed()
                else:
                    turn.update('held')
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
        if turn:
            turn.update('held')
    if "delivery_status" not in out:
        out["delivery_status"] = "confirmed" if len(sent_parts) == len(planned) else "partial"
    return out
