"""Sleep / pre-sleep messaging mode — distinct from normal chat density.

Modes
  normal    日常密度
  pre_sleep 睡前/刚道晚安：字数↓ 气泡↓ 间隔↑
  asleep    会话已睡：主动强压制；若被 ping 也极短回，直到醒来

Asleep detection (config.sleep_chat.asleep_after_sec, default 600s)
  (a) 他发晚安后，她 10 分钟内没回 → asleep
  (b) 她发晚安后，他 10 分钟内没回 → asleep

晚安即停止主动，直到这次晚安之后的日程起床时间。夜里被 ping 仍可短回。
Knobs: config.json → sleep_chat（见 docs/sleep-chat.md）。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

STATE_KEY = "sleep_chat"

# Shared explicit sign-off semantics live in conversation_semantics. Topic
# words such as 闭眼/早点睡 must never directly create persistent sleep state.
WAKE_RE = re.compile(r"(早安|早呀|早啊|起床|醒了|睡醒|早上好|晒太阳)", re.I)
# 「活动了？/还活动」= 还没睡/还在玩，不是日程活动
AWAKE_CHECK_RE = re.compile(
    r"(还活动了|活动了\s*[？?]?|还活动|还在活动|还不睡|还没睡)",
    re.I,
)

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "asleep_after_sec": 600,
    "daytime_sleep_max_sec": 7200,
    "_note": (
        "睡前/已睡密度独立于日常。pre_sleep：短回少气泡长间隔；"
        "asleep：压制主动 + 极简回复。asleep_after_sec=道晚安后对方无响应判睡秒数。"
    ),
    "pre_sleep": {
        "max_bubbles": 3,
        "max_chars_total": 40,
        "max_bubble_chars": 14,
        "gap_scale": 1.75,
        "gap_min_sec": 1.5,
        "gap_max_sec": 4.2,
        "prompt_tip": (
            "睡前/刚道晚安：整轮≤2～3短气泡就收；别展开别连问别发图。"
            "「活动/还活动了？」=他还没睡的调侃，绝不是日程活动；禁止追问啥活动、禁止新开行程梗。"
            "可回晚安后收住。"
        ),
    },
    "asleep": {
        "max_bubbles": 2,
        "max_chars_total": 24,
        "max_bubble_chars": 12,
        "gap_scale": 2.2,
        "gap_min_sec": 1.8,
        "gap_max_sec": 4.8,
        "suppress_proactive": True,
        "prompt_tip": (
            "会话已睡/他夜里又戳：极短回一两句就停。「活动/还活动了」=还没睡，别问行程别追问活动；别发图别开新话题。"
        ),
    },
}


def _now(now: dt.datetime | None = None) -> dt.datetime:
    from schedule import TZ, now_local
    now = now or now_local()
    if now.tzinfo is not None:
        now = now.astimezone(TZ).replace(tzinfo=None)
    return now.replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(s: Any) -> dt.datetime | None:
    if not s:
        return None
    if isinstance(s, dt.datetime):
        return s.replace(microsecond=0)
    text = str(s).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def cfg(full: dict | None = None) -> dict:
    try:
        from config import load
        root = full if full is not None else load()
    except Exception:
        root = full or {}
    raw = dict(DEFAULTS)
    user = (root or {}).get("sleep_chat") or {}
    if isinstance(user, dict):
        raw.update({k: v for k, v in user.items() if k not in ("pre_sleep", "asleep")})
        for arm in ("pre_sleep", "asleep"):
            base = dict(DEFAULTS.get(arm) or {})
            base.update((user.get(arm) or {}) if isinstance(user.get(arm), dict) else {})
            raw[arm] = base
    return raw


def is_goodnight_text(text: str | None) -> bool:
    from conversation_semantics import is_explicit_goodnight
    return is_explicit_goodnight(text)


def is_wake_text(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(WAKE_RE.search(t))


def is_awake_check_text(text: str | None) -> bool:
    """夜里「活动了？/还活动」= still awake，不是问行程/社团活动。"""
    t = (text or "").strip()
    if not t:
        return False
    # bare 「活动」 alone often meant this tonight; prefer with 了/还
    if t in ("活动", "活动？", "活动?"):
        return True
    return bool(AWAKE_CHECK_RE.search(t))


def blob(st: dict | None) -> dict:
    b = ((st or {}).get(STATE_KEY) or {}) if isinstance(st, dict) else {}
    return dict(b) if isinstance(b, dict) else {}


def scheduled_sleep(st: dict | None, now: dt.datetime) -> bool:
    """Recompute the current slot; never trust a queued beat's old slot."""
    import life_calendar as lc
    import schedule
    slot = lc.resolve_life_now(st or {}, now)
    if slot:
        return slot.get("bucket") == "sleep" or slot.get("activity") == "睡觉"
    return schedule.is_sleeping(now)


def next_wake_after(at: dt.datetime, st: dict | None = None) -> dt.datetime:
    """Actual wake boundary after a bedtime, including bedtimes after midnight."""
    import life_calendar as lc
    import schedule
    for offset in (0, 1):
        day = at.date() + dt.timedelta(days=offset)
        today = (st or {}).get("life_today") or {}
        if today.get("date") != day.isoformat():
            today = lc.get_day(day) or {}
        wake = (today.get("sleep_bounds") or {}).get("wake")
        if not wake:
            slots = today.get("slots") or []
            if slots and slots[0].get("bucket") == "sleep":
                wake = slots[0].get("end")
        if wake:
            hour, minute = map(int, wake.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError(f"无效起床时间: {wake}")
            minutes = hour * 60 + minute
        else:
            minutes = int(schedule.day_plan(day)["wake"])
        boundary = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minutes)
        if boundary > at:
            return boundary
    raise ValueError("日程缺少有效起床时间")


def daytime_sleep(at: dt.datetime, st: dict | None = None) -> bool:
    return (7 <= at.hour < 20 and not scheduled_sleep(st, at)
            and not in_pre_sleep_window(at, st))


def conversation_sleep_interval(st: dict | None) -> tuple[dt.datetime, dt.datetime] | None:
    """Bound daytime sign-offs, including legacy pauses incorrectly set to tomorrow."""
    b = blob(st)
    at = (_parse_ts(b.get("goodnight_at"))
          or _parse_ts((b.get("pending_goodnight") or {}).get("at"))
          or _parse_ts(b.get("asleep_since")))
    if not at:
        old = b.get("last_sleep") or {}
        start, end = _parse_ts(old.get("start")), _parse_ts(old.get("end"))
        return (start, end) if start and end and end > start else None
    if 7 <= at.hour < 20:
        import life_calendar as lc
        slot = lc.resolve_life_now(st or {}, at) or {}
        if (slot.get("bucket") == "sleep" or slot.get("activity") == "睡觉") and slot.get("end"):
            h, m = map(int, slot["end"].split(":"))
            end = dt.datetime.combine(at.date(), dt.time()) + dt.timedelta(hours=h, minutes=m)
            if end > at:
                return at, end
    if daytime_sleep(at, st):
        seconds = min(4 * 3600, max(300, int(cfg().get("daytime_sleep_max_sec", 7200))))
        return at, at + dt.timedelta(seconds=seconds)
    return at, next_wake_after(at, st)


def _end_sleep(b, at, until, now, reason):
    b.update(mode="normal", asleep_since=None, pending_goodnight=None,
             goodnight_at=None, proactive_sleep_until=None,
             last_sleep={"start": _iso(at), "end": _iso(min(until, now))},
             woke_at=_iso(now), wake_reason=reason)
    return b


def in_pre_sleep_window(now: dt.datetime | None = None, st: dict | None = None) -> bool:
    """日程睡前窗：准备睡活动，或末段非睡槽末端，或已在睡觉槽。"""
    now = _now(now)
    try:
        if scheduled_sleep(st, now):
            return True
    except Exception:
        pass
    try:
        import life_calendar as lc
        import proactive_beats as pb
        from config import load
        today = st.get("life_today") if st and isinstance(st.get("life_today"), dict) else None
        if not today or today.get("date") != now.date().isoformat():
            today = lc.get_day(now.date())
        cur = None
        if hasattr(lc, "resolve_life_now"):
            try:
                info = lc.resolve_life_now(st or {}, now) or {}
                cur = info.get("slot") or info
                today = today or info.get("today")
            except Exception:
                pass
        act = str((cur or {}).get("activity") or "")
        if getattr(pb, "PREP_SLEEP_RE", None) and pb.PREP_SLEEP_RE.search(act):
            return True
        bcfg = pb.cfg_beats((load().get("proactive") or {}).get("beats") or {})
        win = int(bcfg.get("pre_sleep_window_min", 50))
        slots = [s for s in (today or {}).get("slots") or [] if isinstance(s, dict) and s.get("bucket") != "sleep"]
        last = slots[-1] if slots else None
        if last and cur and cur.get("start") == last.get("start") and hasattr(pb, "_near_slot_end"):
            if pb._near_slot_end(cur, now, win):
                return True
    except Exception:
        pass
    return False


def style_for(mode: str, sc: dict | None = None) -> dict:
    sc = sc or cfg()
    if mode == "asleep":
        return dict(sc.get("asleep") or DEFAULTS["asleep"])
    if mode == "pre_sleep":
        return dict(sc.get("pre_sleep") or DEFAULTS["pre_sleep"])
    return {
        "max_bubbles": 6,
        "max_chars_total": 80,
        "max_bubble_chars": 18,
        "gap_scale": 1.0,
        "gap_min_sec": 0.85,
        "gap_max_sec": 2.6,
        "prompt_tip": "",
    }


def prompt_tip(mode: str, sc: dict | None = None) -> str:
    tip = (style_for(mode, sc).get("prompt_tip") or "").strip()
    return tip


def state_line(st: dict | None, now: dt.datetime | None = None) -> str:
    """One short line for brain state_block."""
    mode, _ = peek_mode(st, now)
    if mode == "asleep":
        return "睡聊：已睡（少说话，主动停）"
    if mode == "pre_sleep":
        return "睡聊：睡前（短回、少气泡）"
    return ""


def peek_mode(st: dict | None, now: dt.datetime | None = None) -> tuple[str, dict]:
    """Read mode after applying timeout rules in-memory (no disk write)."""
    sc = cfg()
    if not sc.get("enabled", True):
        return "normal", blob(st)
    now = _now(now)
    b = blob(st)
    b, mode = _apply_timeouts(b, now, sc)
    if mode == "asleep":
        return "asleep", b
    if b.get("mode") == "pre_sleep" or in_pre_sleep_window(now, st):
        return "pre_sleep", b
    return "normal", b


def _apply_timeouts(b: dict, now: dt.datetime, sc: dict) -> tuple[dict, str]:
    b = dict(b or {})
    pending = b.get("pending_goodnight")
    if b.get("mode") == "asleep" and b.get("asleep_since"):
        return b, "asleep"
    if not isinstance(pending, dict):
        return b, str(b.get("mode") or "normal")
    at = _parse_ts(pending.get("at"))
    if not at:
        return b, str(b.get("mode") or "normal")
    wait = float(sc.get("asleep_after_sec") or 600)
    if (now - at).total_seconds() < wait:
        # still waiting → pre_sleep density
        b["mode"] = "pre_sleep"
        return b, "pre_sleep"
    who = pending.get("from")
    last_me = _parse_ts(b.get("last_me_at"))
    last_them = _parse_ts(b.get("last_them_at"))
    asleep = False
    if who == "them":
        # (a) 他晚安后她没回
        if last_me is None or last_me < at:
            asleep = True
    elif who == "me":
        # (b) 她晚安后他没回
        if last_them is None or last_them < at:
            asleep = True
    if asleep:
        b["mode"] = "asleep"
        b["asleep_since"] = _iso(at + dt.timedelta(seconds=wait))
        b["pending_goodnight"] = None
        b["last_eval_at"] = _iso(now)
        return b, "asleep"
    # other side already replied → clear pending
    b["pending_goodnight"] = None
    if b.get("mode") == "asleep":
        return b, "asleep"
    b["mode"] = "pre_sleep" if in_pre_sleep_window(now) else "normal"
    return b, str(b.get("mode") or "normal")


def refresh(st: dict | None, now: dt.datetime | None = None) -> tuple[str, dict]:
    """Return (mode, patch) — patch is sleep_chat blob to write if changed."""
    sc = cfg()
    if not sc.get("enabled", True):
        return "normal", blob(st)
    now = _now(now)
    original = blob(st)
    # Retain the original bedtime when upgrading an old pending/asleep state.
    at = (_parse_ts(original.get("goodnight_at"))
          or _parse_ts((original.get("pending_goodnight") or {}).get("at"))
          or _parse_ts(original.get("asleep_since")))
    mode, b2 = peek_mode(st, now)
    if at:
        _, wake = conversation_sleep_interval(st)
        b2.update(goodnight_at=_iso(at), proactive_sleep_until=_iso(wake))
        if now >= wake and not scheduled_sleep(st, now):
            _end_sleep(b2, at, wake, now, "daytime_sleep_expired" if daytime_sleep(at, st) else "schedule_morning")
            return "normal", b2
    return mode, b2


def note_inbound(
    text: str,
    st: dict | None = None,
    now: dt.datetime | None = None,
) -> tuple[str, dict, str]:
    """Process his message. Returns (mode_for_reply, new_blob, style_mode).

    style_mode may stay asleep for one turn after wake so the first ping stays minimal.
    """
    sc = cfg()
    now = _now(now)
    if not sc.get("enabled", True):
        return "normal", blob(st), "normal"
    _, b = refresh(st, now)
    was_asleep = b.get("mode") == "asleep"
    b["last_them_at"] = _iso(now)
    b["last_eval_at"] = _iso(now)
    style_mode = "asleep" if was_asleep else None

    from conversation_semantics import is_nap_signoff
    signing_off = is_goodnight_text(text) or is_nap_signoff(text)
    bedtime = _parse_ts(b.get("goodnight_at"))
    if bedtime and daytime_sleep(bedtime, st) and not signing_off:
        # A new real message ends his nap immediately; do not keep yesterday's pause.
        _end_sleep(b, bedtime, now, now, "them_ping")
        was_asleep, style_mode = False, None

    if was_asleep:
        if signing_off:
            # stay asleep
            b["mode"] = "asleep"
            b["pending_goodnight"] = {"from": "them", "at": _iso(now)}
            b["goodnight_at"] = _iso(now)
            return "asleep", b, "asleep"
        if is_wake_text(text) or len((text or "").strip()) >= 1:
            b["mode"] = "pre_sleep" if in_pre_sleep_window(now, st) else "normal"
            b["asleep_since"] = None
            b["pending_goodnight"] = None
            b["woke_at"] = _iso(now)
            b["wake_reason"] = "them_ping"
            # first reply after sleep stays minimal
            return b["mode"], b, "asleep"

    if signing_off:
        b["mode"] = "pre_sleep"
        b["goodnight_at"] = _iso(now)
        b["pending_goodnight"] = {"from": "them", "at": _iso(now)}
        return "pre_sleep", b, "pre_sleep"

    # clear his-side pending if he talks again without goodnight
    pending = b.get("pending_goodnight")
    if isinstance(pending, dict) and pending.get("from") == "me":
        # he replied to her goodnight within window → not asleep
        b["pending_goodnight"] = None
    if in_pre_sleep_window(now, st) or b.get("mode") == "pre_sleep":
        b["mode"] = "pre_sleep"
        return "pre_sleep", b, "pre_sleep"
    b["mode"] = "normal"
    return "normal", b, style_mode or "normal"


def note_outbound(
    text: str,
    st: dict | None = None,
    now: dt.datetime | None = None,
) -> tuple[str, dict]:
    """Process her sent text (joined bubbles ok)."""
    sc = cfg()
    now = _now(now)
    if not sc.get("enabled", True):
        return "normal", blob(st)
    _, b = refresh(st, now)
    b["last_me_at"] = _iso(now)
    b["last_eval_at"] = _iso(now)
    from conversation_semantics import unquoted, is_nap_signoff
    raw = unquoted(text or "")
    advice_only = (daytime_sleep(now, st) and re.search(r"睡吧", raw)
                   and not re.search(r"晚安|我.{0,3}(?:先睡|去睡|睡了|睡啦)", raw))
    if (is_goodnight_text(text) and not advice_only) or is_nap_signoff(text):
        b["mode"] = "pre_sleep"
        b["goodnight_at"] = _iso(now)
        b["pending_goodnight"] = {"from": "me", "at": _iso(now)}
        return "pre_sleep", b
    # she replied after his goodnight → clear (a) pending
    pending = b.get("pending_goodnight")
    if isinstance(pending, dict) and pending.get("from") == "them":
        b["pending_goodnight"] = None
    if b.get("mode") == "asleep":
        return "asleep", b
    if in_pre_sleep_window(now, st) or b.get("mode") == "pre_sleep":
        b["mode"] = "pre_sleep"
        return "pre_sleep", b
    b["mode"] = "normal"
    return "normal", b


def should_suppress_proactive(st: dict | None, now: dt.datetime | None = None,
                              beat: dict | None = None) -> str | None:
    """Sleep is a hard gate, including must/bypass_quiet/force and retries.

    Before selecting a beat, omit beat. Once selected, only the closing
    pre_sleep beat may start during preparation, and only before goodnight.
    """
    now = _now(now)
    sc = cfg()
    if sc.get("enabled", True):
        mode, b = refresh(st, now)
        if mode == "asleep":
            return "会话已睡（sleep_chat.asleep）"
        until = _parse_ts(b.get("proactive_sleep_until"))
        if until and now < until:
            return f"已道晚安，主动暂停至日程起床 {_iso(until)}"
    if scheduled_sleep(st, now):
        return "小甜在睡觉"
    if beat is not None and beat.get("id") != "pre_sleep" and in_pre_sleep_window(now, st):
        return "日程已进入睡前准备，随机分享让步于睡眠"
    return None


class ProactiveSleepBlocked(RuntimeError):
    """Terminal cancellation of a proactive send, never a transport retry."""


def check_proactive_send(st: dict, now=None, beat=None) -> None:
    try:
        from schedule_execution import beat_blocked
        if beat_blocked(beat, _now(now), st):
            raise ProactiveSleepBlocked('外出计划未确认或已取消')
        reason = should_suppress_proactive(st, now, beat=beat)
    except ProactiveSleepBlocked:
        raise
    except Exception as exc:
        raise ProactiveSleepBlocked(f"睡眠状态检查失败，停止主动发送: {exc!r}") from exc
    if reason:
        raise ProactiveSleepBlocked(reason)


def tighten_text(text: str | None, mode: str, sc: dict | None = None) -> str:
    """Keep complete sentence groups within sleep limits, never cut a claim."""
    if mode not in ("pre_sleep", "asleep"):
        return (text or "").strip()
    sty = style_for(mode, sc)
    max_b = int(sty.get("max_bubbles") or 3)
    max_c = int(sty.get("max_bubble_chars") or 14)
    max_total = int(sty.get("max_chars_total") or 40)
    raw = (text or "").strip()
    if not raw:
        return raw
    # 睡前禁行程梗；「还活动」≠日程
    _ITIN = re.compile(
        r"(行程|安排|明天|后天|下周|周末去|约你|一起去|几点出门|路线|打卡|爬长城|上课|排练日程)"
    )
    from conversation_semantics import clauses
    # A contrast/retraction can start in the next bubble. Keep the whole
    # thought or omit it: leaving only "我不想你" from "我不想你，才怪" reverses it.
    continuation = re.compile(r"^(?:但是|但|不过|可是|然而|只是|而是|其实|却|仍然|还是|就是|才怪|骗你的|逗你的|开玩笑|所以|因此|才会|就会)")
    open_pair = re.compile(r"^(?:虽然|尽管|如果|假如|要是|除非|只有|不是|并不是|不只是)")
    groups, pending = [], ""
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if re.match(r"(?i)^WANT_IMAGE\s*[:：]", ln):
            continue
        for part in clauses(ln + "\n"):
            pending += part
            if part.endswith(tuple("。.!！?？；;\n")):
                group = pending.strip()
                if not group:
                    pending = ""
                    continue
                if groups and (continuation.match(group) or (
                    open_pair.match(groups[-1]) and not re.search(r"[，,].*(?:但|却|还|而是|只是|就是|就|才)", groups[-1])
                )):
                    groups[-1] += "\n" + group
                else:
                    groups.append(group)
                pending = ""
    if pending.strip():
        groups.append(pending.strip())
    body, had_text = [], False
    for group in groups:
        if _ITIN.search(group) and not re.search(r"(晚安|睡|困|熬)", group):
            continue
        had_text = True
        pieces, current, fits = [], "", True
        for part in clauses(group):
            part = part.strip()
            if not part:
                continue
            if len(part) > max_c:
                fits = False
                break
            if current and len(current + part) > max_c:
                pieces.append(current)
                current = ""
            current += part
        if current:
            pieces.append(current)
        candidate = body + pieces
        if fits and len(candidate) <= max_b and len("\n".join(candidate)) <= max_total:
            body = candidate
    # No complete thought fits: acknowledge without inventing a bedtime,
    # sentiment, promise or a truncated explanation.
    return "\n".join(body).strip() or ("我在呢" if had_text and max_c >= 3 and max_total >= 3 and max_b else "")


def gap_params(mode: str, sc: dict | None = None) -> dict:
    sty = style_for(mode, sc)
    return {
        "scale": float(sty.get("gap_scale") or 1.0),
        "min_sec": float(sty.get("gap_min_sec") or 0.85),
        "max_sec": float(sty.get("gap_max_sec") or 2.6),
    }


def split_params(mode: str, sc: dict | None = None) -> dict:
    sty = style_for(mode, sc)
    return {
        "max_bubbles": int(sty.get("max_bubbles") or 6),
        "max_chars": int(sty.get("max_bubble_chars") or 18),
    }
