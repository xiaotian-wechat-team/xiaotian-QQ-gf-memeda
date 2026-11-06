"""Awake-time contact deadline. Persona SSOT: data/persona_dossier.json.

The queue owns the clock; only actual inbound/confirmed outbound resets it.
Pure scheduling helpers never send, call a model, or write contact state.
"""
import datetime as dt
import hashlib

BEAT = "idle_contact"


def stamp(value):
    if not value:
        return None
    try:
        at = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
        if at.tzinfo:
            from schedule import TZ
            at = at.astimezone(TZ).replace(tzinfo=None)
        return at.replace(microsecond=0)
    except (ValueError, TypeError):
        return None


def iso(at):
    return at.isoformat(sep=" ", timespec="seconds")


def last_contact(st, rows=()):
    candidates = [stamp(st.get(k)) for k in
                  ("last_inbound_at", "last_reply_at", "last_proactive_at")]
    candidates += [stamp(r[2]) for r in rows if len(r) >= 3 and r[0] in ("me", "them")]
    return max((t for t in candidates if t), default=None)


def _draw(key):
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def _sleep_ranges(start, end, st):
    import life_calendar as lc
    import sleep_chat
    import schedule
    ranges = []
    day = start.date()
    while day <= end.date():
        midnight = dt.datetime.combine(day, dt.time())
        today = st.get("life_today") or {}
        if today.get("date") != day.isoformat() or not today.get("slots"):
            today = lc.get_day(day) or {}
        slots = today.get("slots") or []
        if slots:
            for slot in slots:
                if slot.get("bucket") != "sleep" and slot.get("activity") != "睡觉":
                    continue
                a, b = (slot.get("start") or "00:00"), (slot.get("end") or "24:00")
                def minutes(hm):
                    h, m = map(int, hm.split(":"))
                    if not (0 <= h <= 24 and 0 <= m < 60 and (h != 24 or m == 0)):
                        raise ValueError("invalid sleep boundary")
                    return h * 60 + m
                lo, hi = minutes(a), minutes(b)
                if hi <= lo:
                    hi += 1440
                ranges.append((midnight + dt.timedelta(minutes=lo), midnight + dt.timedelta(minutes=hi)))
        else:
            # Missing persisted days use the same deterministic fallback as schedule.current.
            plan = schedule.day_plan(day)
            previous = schedule.day_plan(day - dt.timedelta(days=1))
            ranges.extend([(midnight + dt.timedelta(minutes=max(0, previous["sleep"] - 1440)),
                            midnight + dt.timedelta(minutes=plan["wake"])),
                           (midnight + dt.timedelta(minutes=plan["sleep"]), midnight + dt.timedelta(days=1))])
        day += dt.timedelta(days=1)
    conversation = sleep_chat.conversation_sleep_interval(st)
    if conversation:
        ranges.append(conversation)
    merged = []
    for a, b in sorted((max(a, start), min(b, end)) for a, b in ranges if a < end and b > start):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def awake_seconds(start, end, st):
    if end <= start:
        return 0
    return max(0, int((end - start).total_seconds()
                     - sum((b - a).total_seconds() for a, b in _sleep_ranges(start, end, st))))


def advance(now, st, rows, cfg, previous=None):
    opts = cfg.get("idle_contact") or {}
    if not opts.get("enabled"):
        return None
    previous = previous or {}
    contact = last_contact(st, rows)
    anchor = contact or stamp(previous.get("anchor_at")) or now
    # A later observed contact cannot be undone by a stale state snapshot.
    old_anchor = stamp(previous.get("anchor_at"))
    if old_anchor and old_anchor > anchor:
        anchor = old_anchor
    same = previous.get("anchor_at") == iso(anchor)
    lo, hi = sorted(int(v) for v in opts.get("interval_sec", [5400, 9000]))
    if lo < 60 or hi > 86400:
        raise ValueError("idle contact interval out of bounds")
    target = int(previous["target_seconds"]) if same and previous.get("target_seconds") else (
        lo + _draw("idle-delay:" + iso(anchor)) % (hi - lo + 1))
    checked = stamp(previous.get("checked_at")) if same else None
    checked = max(anchor, checked or anchor)
    elapsed = (int(previous.get("awake_seconds", 0)) if same else 0) + awake_seconds(checked, now, st)
    due = elapsed >= target and now >= anchor
    return {"anchor_at": iso(anchor), "checked_at": iso(max(checked, now)),
            "target_seconds": target, "awake_seconds": elapsed, "due": due,
            "remaining_awake_seconds": max(0, target - elapsed),
            "due_since": (previous.get("due_since") if same else None) or (iso(now) if due else None)}


def candidate(clock, st, now):
    if not clock or not clock.get("due"):
        return None
    import proactive_beats as pb
    import proactive_media
    today, slot = pb._life(st, now)
    key = BEAT + "@" + hashlib.sha256(clock["anchor_at"].encode()).hexdigest()[:16]
    if key in pb._beats_state(st, now).get("done", {}):
        return None
    style = ("ask", "share", "photo")[_draw("idle-style:" + clock["anchor_at"]) % 3]
    beat = {"id": BEAT, "done_key": key, "slot": slot, "today": today,
            "idle_clock": dict(clock), "idle_style": style}
    if style == "photo":
        usable = proactive_media.photo_slot(beat, now)
        if usable:
            import schedule_shot
            beat["image"] = schedule_shot.image_hint_for_beat("life_photo", usable, today)
            beat["image_slot"] = usable
        if not (beat.get("image") or {}).get("scene"):
            beat["idle_style"] = "share"
    return beat


def stale_reason(beat, st, rows=(), *, delivered=False):
    if (beat or {}).get("id") != BEAT:
        return None
    clock = beat.get("idle_clock") or {}
    anchor = stamp(clock.get("anchor_at"))
    if not anchor or not clock.get("due"):
        return "久未联系计时凭据缺失"
    latest = stamp(st.get("last_inbound_at")) if delivered else last_contact(st, rows)
    if latest and latest > anchor:
        return "已有新的实际联系，取消旧的久未联系草稿"
    return None


def task(beat):
    act = ((beat.get("slot") or {}).get("activity") or "")
    style = beat.get("idle_style")
    opening = ("这次想撒娇找男朋友聊一下，可以自然问‘在干嘛呢’、‘怎么半天没理我呀’这类话。"
               if style == "ask" else "这次只分享自己的当前近况或感受，不必提问、不必带照片。")
    return ("清醒时已经有一阵子没有互相联系了，现在主动联系一次。" + opening
            + (f"此刻已确认的日程：{act}。" if act else "没有当前活动证据，不编造刚做过什么。")
            + "结合最近聊天用自己的话说1～2句，不复读上次的问题。不知道他在干什么就问。"
              "他先前情绪不好时温柔关心，不用调侃或抱怨口吻。"
              "撒娇是亲昵语气，不把未回当作生气或不爱你的事实，不编造他的行踪。"
              "本轮文字即可，不输出WANT_IMAGE，不提定时器、时长阈值、程序或配额；"
              "不要因忙碌、未回或没有新鲜话题输出[SKIP]，简单表达想找他也成立。")


def fallback(rows, anchor):
    options = ["小熊，在忙什么呀，想找你说说话", "怎么半天没理我呀，想你了🥺",
               "你那边忙完没，来陪我说两句呀", "想你了，跑来找你贴一下", "小熊，忙完记得来找我玩呀"]
    import memory
    recent = {memory.norm(r[1]) for r in rows[-30:] if r[0] == "me"}
    start = _draw("idle-fallback:" + anchor) % len(options)
    for i in range(len(options)):
        choice = options[(start + i) % len(options)]
        if memory.norm(choice) not in recent:
            return choice
    return options[start]
