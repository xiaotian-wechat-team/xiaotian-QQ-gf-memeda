"""Reciprocal chat pace. Persona SSOT: data/persona_dossier.json.

Count real inbound messages before burst merging, including across reply turns.
Only IDs and timestamps are retained; the score is not an emotion/persona fact.
"""
import datetime as dt
import hashlib
import math
import re
from zoneinfo import ZoneInfo

STATE_KEY = "reply_engagement"
DEFAULTS = {
    "enabled": True,
    "window_sec": 180,
    "half_life_sec": 60,
    "active_score": 2.5,
    "dense_score": 5,
    "collect_quiet_sec": 2,
    "collect_max_sec": 12,
    "day_start_hour": 7,
    "evening_hour": 17,
    "internship_enthusiastic_probability": .3,
}
TZ = ZoneInfo("Asia/Shanghai")


def options(cfg=None):
    if cfg is None:
        from config import load
        cfg = load()
    return {**DEFAULTS, **((cfg.get("reply") or {}).get("engagement") or {})}


def _epoch(now=None):
    return (now or dt.datetime.now()).timestamp()


def _recent(blob, now, opts):
    events = []
    for event in (blob or {}).get("events") or []:
        try:
            age = now - float(event["at"])
            if 0 <= age <= float(opts["window_sec"]):
                events.append({"id": str(event["id"]), "at": float(event["at"])})
        except (KeyError, TypeError, ValueError):
            continue
    return events[-128:]


def record(blob, messages, now=None, cfg=None):
    """Pure merge: receipt IDs prevent retransmits from inflating chat demand."""
    opts = options(cfg)
    at = _epoch(now)
    events = _recent(blob, at, opts)
    # Keep one draw across a continuous conversation, even as old events decay
    # out of the score window. A full window without inbound starts a new session.
    session_start = ((blob or {}).get("session_started_at") or events[0]["at"]) if events else None
    seen = {e["id"] for e in events}
    for index, msg in enumerate(messages):
        # Keep the sender's timestamp: a delayed backlog isn't rapid live chat.
        stamp = getattr(msg, "ts", "")
        try:
            if not stamp:
                sent = at
            else:
                try:
                    # OneBot supplies Unix seconds; iLink normally supplies ISO.
                    sent = float(stamp)
                    if sent > 1e11:
                        sent /= 1000
                except (ValueError, TypeError):
                    sent = dt.datetime.fromisoformat(str(stamp)).timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
        if not math.isfinite(sent) or sent > at + 5 or at - sent > float(opts["window_sec"]):
            continue
        sent = min(at, sent)
        identity = getattr(msg, "msg_id", "") or f"{stamp or at}:{index}:{msg.text}"
        key = hashlib.sha256(f"{msg.alias_key}:{identity}".encode()).hexdigest()[:24]
        if key not in seen:
            events.append({"id": key, "at": sent})
            seen.add(key)
    events = sorted(events, key=lambda e: e["at"])[-128:]
    result = {"events": events}
    if events:
        result["session_started_at"] = session_start if session_start is not None else events[0]["at"]
    return result


def profile(blob, now=None, cfg=None):
    opts = options(cfg)
    at = _epoch(now)
    events = _recent(blob, at, opts) if opts["enabled"] else []
    half = max(1., float(opts["half_life_sec"]))
    score = sum(math.exp2(-(at - e["at"]) / half) for e in events)
    level = "dense" if score >= float(opts["dense_score"]) else (
        "active" if score >= float(opts["active_score"]) else "normal")
    return {"level": level, "score": round(score, 2), "recent_messages": len(events),
            "session_started_at": ((blob or {}).get("session_started_at") or events[0]["at"]) if events else None}


def snapshot(cid):
    """Read-only seed for the next collection window; missing state is normal."""
    import memory
    return profile(memory.read_state(cid).get(STATE_KEY))


def is_closing(text):
    """Explicit sign-off, not mentioning/negating goodnight while asking to chat."""
    from conversation_semantics import is_explicit_closing
    return is_explicit_closing(text)


def select_response(activity, st, cid, text="", now=None, cfg=None):
    """Choose content AND pace from the actual slot, never from generic busyness.

    The deterministic uniform draw is scoped to contact/date/slot/conversation;
    retries and successive messages cannot reroll it. Night always bypasses it.
    """
    opts = options(cfg)
    now = now or dt.datetime.now(TZ)
    if now.tzinfo is not None:
        now = now.astimezone(TZ).replace(tzinfo=None)
    chosen = {**(activity or {}), "response_mode": "usual", "reason": "normal",
              "selector_applied": False}
    if not opts["enabled"]:
        return {**chosen, "reason": "disabled"}
    if is_closing(text):
        return {**chosen, "response_mode": "closing", "reason": "user_closing"}
    if now.hour >= int(opts["evening_hour"]) or now.hour < int(opts["day_start_hour"]):
        return {**chosen, "response_mode": "enthusiastic", "reason": "evening"}
    if chosen.get("level") not in ("active", "dense"):
        return chosen
    import schedule_fidelity
    slot = schedule_fidelity.resolve_slot(st, now) or {}
    if slot.get("bucket") != "work_internship":
        return {**chosen, "response_mode": "enthusiastic", "reason": "daytime_engaged"}
    probability = max(0., min(1., float(opts["internship_enthusiastic_probability"])))
    seed = f"{cid}|{now.date()}|{slot.get('start')}|{slot.get('end')}|{chosen.get('session_started_at')}"
    draw = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big") / 2**64
    return {**chosen, "response_mode": "enthusiastic" if draw < probability else "usual",
            "reason": "daytime_internship", "selector_applied": True,
            "enthusiastic_probability": probability, "selector_draw": draw}


def effective_sleep_style(style, activity):
    # This only changes a user-initiated reply. Never clear the persisted sleep
    # state: proactive sends still obey the separate sleep gate.
    if ((activity or {}).get("response_mode") == "enthusiastic"
            and activity.get("reason") == "evening"):
        return "normal"
    return style


def pacing(activity):
    mode = (activity or {}).get("response_mode")
    level = (activity or {}).get("level", "normal")
    if mode == "enthusiastic":
        dense = level == "dense"
        return {"budget_floor": 8, "max_chars": 120, "wait_cap_sec": 1. if dense else 2.,
                "gap": {"min_sec": .35 if dense else .5, "max_sec": 1.7 if dense else 2.4, "scale": .5}}
    if mode == "closing":
        return {"budget_floor": 1, "max_chars": 40, "gap": {}}
    if mode == "usual":
        level = "normal"
    if level == "dense":
        return {"budget_floor": 6, "max_chars": 120, "wait_cap_sec": 1.,
                "gap": {"min_sec": .35, "max_sec": 1.7, "scale": .5}}
    if level == "active":
        return {"budget_floor": 4, "max_chars": 100, "wait_cap_sec": 2.,
                "gap": {"min_sec": .5, "max_sec": 2.4, "scale": .5}}
    return {"budget_floor": 1, "max_chars": 80, "gap": {}}


def prompt_block(activity):
    if not activity:
        return ""
    budget = activity["max_bubbles"]
    text = (f"# 本轮聊天节奏\n本轮他发了{activity['user_bubbles']}条消息。"
            "把本轮连续消息一起理解，接住主要内容；已回应过的旧话题不重答。"
            f"本轮最多{budget}个短气泡、{activity['max_chars']}字，允许少于上限，不凑条数。")
    mode = activity.get("response_mode")
    if mode == "enthusiastic":
        text += ("本轮必须热情陪聊：具体接住他分享的内容和感受，再自然说出自己的反应、"
                 "联想或接着聊的内容，让交流有来有回。不要只回嗯/好/收到，"
                 "不要只把同一句拆多条冒充热情；也不要因他只发一条就按数量配额收场。"
                 "他认真或难过时用认真关心的方式陪聊，不强行欢快或调侃。")
        if activity.get("reason") == "evening":
            text += ("当前是晚间来聊，必须保持热情，不抽工作概率；日程忙碌或原有睡前/已睡状态"
                     "不能让本轮敷衍收短、催睡或主动结束。日程事实照实，别虚构闲下来了。")
    elif activity.get("selector_applied"):
        text += "当前实际日程为白天实习，本轮沿用常规回复节奏：简短但接住主要内容，无需强行展开或密集连发。"
    elif mode == "closing":
        text += "他明确道晚安或结束聊天，自然回应收口，不另开话题、追问或强留他聊天。"
    text += "不要汇报频率、概率或分析他的心理；热情接话不等于提高亲密词或调侃强度，一次最多一问。"
    if activity.get("sleep_style") in ("pre_sleep", "asleep"):
        text += "本轮仍按睡聊限制收短。"
    return text + "\n"
