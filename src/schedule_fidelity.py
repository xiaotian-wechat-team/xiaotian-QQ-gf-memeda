"""Outbound schedule fidelity + outbox freshness + no-default-user-internship.

Code rules (not prompt bloat):
- Force claims / WANT_IMAGE scenes to match current life_schedule slot.
- Ban 「下课/回宿舍」等 during work_internship (and other bucket mismatches).
- Flush must drop mocked / expired / slot-stale outbox items.
- Never assume USER has 实习 (persona has none).
- Shared day mark user_day_curiosity.last_asked = 「今日已问安排」/「回执绿」
  (schedule_brief/activity_end ask_him ↔ ask_his_day mutual exclusion).
  Set only on real send success (immediate SENT or outbox flush ok) — never on HOLD/enqueue.
  Mark only when ask planned (ask_him / ask_his_day); never by beat id alone or text regex.
- image_pendulum_allow: deterministic time-bucket roll so proactive
  baobei/meal/activity_end images are optional (~p), not forced.
- 查岗-style outbox asks (silence_nudge / 在干嘛…) drop when stale vs current slot.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
from typing import Any

from conversation_semantics import clauses, is_current_self_claim

# Bucket → phrases she must NOT claim about herself right now
_FORBIDDEN_BY_BUCKET: dict[str, tuple[re.Pattern, ...]] = {
    "work_internship": (
        re.compile(r"(刚)?下课|下完课|课间|回宿舍|在宿舍|宿舍躺|宿舍追剧|"
                   r"上课呢|在教室|图书馆自习|排练排到|刚排完|基训完"),
    ),
    "school_class": (
        re.compile(r"(刚)?下班|实习收工|剪辑室|片场|跟组|在公司实习|"
                   r"回宿舍躺|宿舍追剧"),
    ),
    "school_rehearsal": (
        re.compile(r"(刚)?下课(?!后)|实习收工|剪辑室|片场|回宿舍躺"),
    ),
    "school_library": (
        re.compile(r"(刚)?下课|剪辑室|片场|回宿舍躺着刷"),
    ),
    "sleep": (
        re.compile(r"下课|上课|实习|排练|逛街|在外面|刚到|出发"),
    ),
}

# Soft: activity keywords that match a bucket (for scene/text affinity)
_BUCKET_OK_HINTS: dict[str, tuple[str, ...]] = {
    "work_internship": ("实习", "剪辑", "片场", "场记", "跟组", "公司", "资料", "粗剪"),
    "school_class": ("课", "教室", "编导", "技法", "导演", "舞蹈史"),
    "school_rehearsal": ("排练", "基训", "剧目", "练功"),
    "school_library": ("自习", "图书馆", "阐述", "作业"),
    "campus_life": ("食堂", "校园", "赶课", "午饭", "晚饭", "早饭"),
    "weekend_home": ("宿舍", "追剧", "摸鱼", "洗漱", "准备睡", "宅"),
    "city_fun": ("逛", "探店", "剧本杀", "livehouse", "展", "出门"),
    "outdoor_outing": ("徒步", "爬山", "公园", "郊游", "户外"),
    "sleep": ("睡", "困", "晚安"),
}

# Outbound assuming HE has an internship (USER persona has none)
_USER_INTERNSHIP_ASK = re.compile(
    r"你(?:现在|今天|那边)?(?:在)?实习"           # 你实习…
    r"|实习忙不忙|实习怎么样|实习还好吗|实习累不累|实习顺利吗"
    r"|实习下班了吗|今天实习吗"
    r"|实习忙就|实习就说忙|实习忙的话"
    r"|(?<![我])实习忙(?![到成疯死哭着得])"      # bare 实习忙…；排除 我实习忙 / 实习忙到
)

# Outbox: mocked / test / too old
_MOCK_REASON = re.compile(r"mocked|mock\b|dry[-_]?run|test\b", re.I)
_DEFAULT_MAX_AGE_HOURS = 4.0


def now_local(now: dt.datetime | None = None) -> dt.datetime:
    if now is not None:
        return now
    try:
        import life_calendar as lc
        return lc.now_local()
    except Exception:
        return dt.datetime.now()


def resolve_slot(st: dict | None = None, now: dt.datetime | None = None) -> dict | None:
    now = now_local(now)
    try:
        import life_calendar as lc
        return lc.resolve_life_now(st or {}, now)
    except Exception:
        return None


def forbidden_patterns(bucket: str | None) -> tuple[re.Pattern, ...]:
    if not bucket:
        return ()
    return _FORBIDDEN_BY_BUCKET.get(str(bucket), ())


def text_violates_slot(text: str | None, slot: dict | None) -> bool:
    """True if outbound text claims a scene incompatible with current slot."""
    t = (text or "").strip()
    if not t or not slot:
        return False
    bucket = slot.get("bucket")
    for part in clauses(t):
        for rx in forbidden_patterns(bucket):
            if any(is_current_self_claim(part, m.start()) for m in rx.finditer(part)):
                return True
    return False


def user_internship_assumed(text: str | None) -> bool:
    """True if outbound asks/assumes USER (他) has an internship."""
    t = (text or "").strip()
    if not t:
        return False
    # Her talking about her own internship is fine
    if re.search(r"(我|咱家姑娘).{0,6}实习|实习中|实习刚|电影公司实习", t):
        if not re.search(r"你实习|实习忙不忙|你那边实习", t):
            return False
    return bool(_USER_INTERNSHIP_ASK.search(t))


def _fallback_line(slot: dict | None) -> str:
    if not slot:
        return "嗯"
    act = (slot.get("activity") or "").strip()
    bucket = slot.get("bucket") or ""
    if bucket == "work_internship":
        return "我这会儿在实习"
    if bucket == "school_class":
        return "我这会儿有课"
    if bucket == "school_rehearsal":
        return "我这会儿在排练"
    if "吃" in act or "食堂" in act:
        return "我这会儿在吃饭"
    if bucket == "weekend_home":
        return "我在宿舍"
    if bucket == "sleep":
        # A scheduled sleep slot cannot prove that a person replying is asleep.
        return "我在呢"
    if act:
        # keep short: first 8 chars of activity as soft claim
        short = act.split("·")[0][:10]
        return f"在{short}" if not short.startswith("在") else short
    return "嗯"


def enforce_outbound_text(
    text: str | None,
    slot: dict | None,
    *,
    drop_user_internship_ask: bool = True,
) -> str:
    """Repair only incompatible clauses; keep other content and image intent."""
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    want = [l for l in lines if re.match(r"(?i)^WANT_IMAGE\s*[:：]", l)]
    body = [l for l in lines if not re.match(r"(?i)^WANT_IMAGE\s*[:：]", l)]
    kept: list[str] = []
    for l in body:
        repaired = []
        for part in clauses(l):
            if drop_user_internship_ask and user_internship_assumed(part):
                continue
            if text_violates_slot(part, slot):
                # Preserve its punctuation and the rest of the response.
                tail = re.search(r"[，,。.!！?？；;\n]+$", part)
                repaired.append(_fallback_line(slot) + (tail.group() if tail else ""))
            else:
                repaired.append(part)
        line = "".join(repaired).strip(" ，,；;\n")
        if line:
            kept.append(line)
    if not kept and body:
        kept = ["你今天忙什么呢"]
    return "\n".join(kept + want[:1]).strip()


_SCHOOL_ALIASES = {
    "北京舞蹈学院": ("北京舞蹈学院", "北舞"),
    "北京大学": ("北京大学", "北大"),
    "清华大学": ("清华大学", "清华"),
    "中国人民大学": ("中国人民大学", "人大"),
    "北京航空航天大学": ("北京航空航天大学", "北航"),
}


def _schools(text: str) -> set[str]:
    names = {name for name, aliases in _SCHOOL_ALIASES.items()
             if any(alias in text for alias in aliases)}
    # Also reject unrecognized named universities instead of silently treating
    # all campuses as interchangeable. Generic "电影公司/学院" are not locations.
    for match in re.finditer(r"[\u4e00-\u9fff]{2,14}(?:大学|学院)", text):
        value = match.group()
        if any(alias in value for aliases in _SCHOOL_ALIASES.values() for alias in aliases):
            continue
        if value not in ("我们学院", "我的学院", "创意学院"):
            names.add(value)
    return names


def _slot_school(slot: dict) -> str | None:
    location = str(slot.get("location") or "")
    evidence = " ".join([location, str(slot.get("activity") or "")])
    named = _schools(evidence)
    if named:
        return sorted(named)[0]
    bucket = str(slot.get("bucket") or "")
    on_campus = bucket.startswith("school_") or (
        bucket in ("campus_life", "campus_fun", "weekend_home")
        and (not location or re.search(r"学校|校园|宿舍|食堂", evidence)))
    if not on_campus:
        return None
    try:
        dossier = json.loads((pathlib.Path(__file__).resolve().parents[1]
                              / "data/persona_dossier.json").read_text())
        return dossier["school"]["institution"]
    except (OSError, ValueError, KeyError):
        return None


def scene_matches_slot(scene: str | None, slot: dict | None, *, imagined: bool = False) -> bool:
    s = (scene or "").strip()
    if imagined or not s or not slot:
        return True
    school = _slot_school(slot)
    named = _schools(s)
    if school and named and named != {school}:
        return False
    # Image scene descriptions are the pictured action, not dialogue about
    # someone else's future. Keep the stricter bucket check for scene prompts.
    if any(rx.search(s) for rx in forbidden_patterns(slot.get("bucket"))):
        return False
    bucket = slot.get("bucket") or ""
    hints = list(slot.get("scene_hints") or []) + list(_BUCKET_OK_HINTS.get(bucket, ()))
    act = slot.get("activity") or ""
    if act and any(p and p in s for p in (act, act.split("·")[0])):
        return True
    if hints and any(h and str(h) in s for h in hints):
        return True
    # No positive hint and no violation → soft ok for generic selfie words
    if re.match(r"^(自拍|随手拍|照片|一张自拍)$", s):
        return True
    # If bucket has strong hints and scene has none → mismatch
    if bucket in ("work_internship", "school_class", "school_rehearsal") and hints:
        return False
    return True


def force_scene_for_slot(slot: dict | None, scene: str | None = None, *,
                         imagined: bool = False) -> str | None:
    """Repair real scenes; imagined=True requires explicit user intent upstream.

    This function never writes a scene into the real-life calendar/state.
    """
    if imagined or not slot:
        return scene
    school = _slot_school(slot)
    if scene and school and _schools(scene) - {school}:
        repaired = scene
        for name, aliases in _SCHOOL_ALIASES.items():
            if name != school:
                for alias in aliases:
                    repaired = repaired.replace(alias, school)
        if scene_matches_slot(repaired, slot):
            return repaired
    if scene and scene_matches_slot(scene, slot):
        return scene
    hints = list(slot.get("scene_hints") or [])
    act = (slot.get("activity") or "").split("·")[0]
    bucket = slot.get("bucket") or ""
    place = f"{school}·" if school else ""
    if hints:
        return f"{place}{hints[0]}{('·' + act) if act else ''}"
    ok = _BUCKET_OK_HINTS.get(bucket) or ()
    if ok and act:
        return f"{place}{ok[0]} {act}"
    return (place + act) if act else scene


def parse_created(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    s = str(ts).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def outbox_item_stale(
    item: dict,
    *,
    now: dt.datetime | None = None,
    slot: dict | None = None,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
) -> tuple[bool, str]:
    """Return (stale, reason). Drop mocked / expired / slot-mismatched pending items."""
    now = now_local(now)
    reason = str(item.get("reason") or "")
    note = str(item.get("note") or "")
    if _MOCK_REASON.search(reason) or _MOCK_REASON.search(note):
        return True, "mocked"
    created = parse_created(item.get("created"))
    if created is not None:
        age_h = (now - created).total_seconds() / 3600.0
        if age_h > float(max_age_hours):
            return True, f"expired age_h={age_h:.1f}>{max_age_hours}"
        # created on a different calendar day → stale vs current slot
        if created.date() != now.date():
            return True, "expired other_day"
    text = (item.get("text") or "").strip()
    if slot is None:
        slot = resolve_slot(now=now)
    if text and text_violates_slot(text, slot):
        return True, f"slot_mismatch bucket={(slot or {}).get('bucket')}"
    # image/scene caption mismatch (optional fields)
    scene = item.get("scene") or item.get("want_image")
    if scene and slot and not scene_matches_slot(str(scene), slot):
        return True, "scene_slot_mismatch"
    # Stored slot no longer matches current life_schedule slot
    item_bucket = item.get("slot_bucket") or item.get("created_bucket")
    if item_bucket and slot and slot.get("bucket") and str(item_bucket) != str(slot.get("bucket")):
        return True, "slot_changed"
    item_start = item.get("slot_start")
    if item_start and slot and slot.get("start") and str(item_start) != str(slot.get("start")):
        return True, "slot_changed"
    # 查岗 / silence_nudge: never send original interrogation when aged / slot-drifted
    beat_id = str(item.get("beat_id") or "")
    chagang = (
        is_interrogation_ask(text)
        or bool(re.search(r"silence_nudge", reason, re.I))
        or beat_id == "silence_nudge"
    )
    if chagang and created is not None:
        age_min = (now - created).total_seconds() / 60.0
        if age_min > 90:
            return True, "interrogation_expired"
        if (re.search(r"silence_nudge", reason, re.I) or beat_id == "silence_nudge") and age_min > 45:
            return True, "silence_nudge_stale"
        if is_interrogation_ask(text) and created.hour != now.hour and age_min > 30:
            return True, "interrogation_slot_drift"
    return False, ""


# 查岗-style asks queued from an earlier slot/day — drop (prefer) rather than resend
_INTERROGATION_ASK = re.compile(
    r"在干嘛|怎么不找我|怎么不理|都不回|不理我|消失了|"
    r"怎么不回|都不找我|查岗|想你了你怎么|怎么都不理"
)


def is_interrogation_ask(text: str | None) -> bool:
    """True if outbound looks like a 查岗 / silence interrogation line."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_INTERROGATION_ASK.search(t))


def rewrite_stale_interrogation(text: str | None) -> str | None:
    """Optional soft rewrite for stale 查岗. None → prefer drop (default)."""
    return None


def purge_outbox_items(
    data: dict,
    *,
    now: dt.datetime | None = None,
    slot: dict | None = None,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    clear_terminal_mocked: bool = True,
) -> dict:
    """Mutate outbox dict: drop stale pending; optionally scrub terminal mocked rows."""
    now = now_local(now)
    items = list(data.get("items") or [])
    out = []
    for it in items:
        st = it.get("status")
        if st in (None, "pending", "hold"):
            stale, why = outbox_item_stale(
                it, now=now, slot=slot, max_age_hours=max_age_hours
            )
            if stale:
                it = dict(it)
                it["status"] = "dropped"
                it["note"] = f"fidelity drop: {why}"
                it["dropped_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        elif clear_terminal_mocked and st in ("sent", "dropped", "cleared"):
            blob = f"{it.get('reason') or ''} {it.get('note') or ''}"
            if _MOCK_REASON.search(blob) or text_violates_slot(it.get("text"), slot):
                # scrub history junk (already sent mocked 下课 during internship)
                continue
        out.append(it)
    data["items"] = out
    return data


# ---- USER day curiosity (daily plugin) ---------------------------------

_ASK_GAP_DAYS_DEFAULT = 1
_ASK_P_DEFAULT = 0.72  # high but not every day


def curiosity_cfg(pc: dict | None = None) -> dict:
    b = ((pc or {}).get("beats") or {}) if pc else {}
    # also allow top-level under proactive.beats already merged by caller
    return {
        "enabled": bool(b.get("user_schedule_curiosity_enabled", True)),
        "probability": float(b.get("user_schedule_curiosity_p", _ASK_P_DEFAULT)),
        "min_gap_days": int(b.get("user_schedule_curiosity_min_gap_days", _ASK_GAP_DAYS_DEFAULT)),
        "max_per_day": 1,
    }


def _day_roll(day: dt.date, salt: str = "ask_his_day") -> float:
    import hashlib
    h = int(hashlib.md5(f"{salt}-{day.isoformat()}".encode()).hexdigest()[:8], 16)
    return (h % 10000) / 10000.0


def already_asked_his_day_today(st: dict | None, now: dt.datetime | None = None) -> bool:
    """Shared 「今日已问安排」 mark (brief/activity_end ask_him OR ask_his_day)."""
    now = now_local(now)
    blob = (st or {}).get("user_day_curiosity") or {}
    return blob.get("last_asked") == now.date().isoformat()


def should_ask_his_day(
    now: dt.datetime,
    st: dict | None,
    pc: dict | None = None,
) -> bool:
    """High-probability daily-plugin gate: not every day, ≤1/day, min gap.

    Same-day mutual exclusion with schedule_brief/activity_end ask_him via last_asked.
    """
    cfg = curiosity_cfg(pc)
    if not cfg["enabled"]:
        return False
    if already_asked_his_day_today(st, now):
        return False
    blob = (st or {}).get("user_day_curiosity") or {}
    last = blob.get("last_asked")
    if last:
        try:
            ld = dt.date.fromisoformat(str(last)[:10])
            if (now.date() - ld).days < int(cfg["min_gap_days"]):
                return False
        except Exception:
            pass
    # day-stable high probability (default ~0.72 of calendar days)
    return _day_roll(now.date()) < float(cfg["probability"])


def mark_asked(st_blob: dict | None, now: dt.datetime, *, question: str = "") -> dict:
    """Set shared last_asked = today (「今日已问安排」/「回执绿」).

    Call only from real send-success paths (immediate SENT or outbox flush ok).
    """
    blob = dict(st_blob or {})
    blob["last_asked"] = now.date().isoformat()
    blob["pending_ask"] = True
    blob["pending_question"] = (question or "")[:80]
    blob["asked_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    blob["ask_sent_ok"] = True  # 「回执绿」documentation flag; gate still reads last_asked
    return blob


def beat_meta_marks_ask_his_day(
    bm: dict | None,
    done_key: str | None = None,
) -> bool:
    """True if this beat should green 「今日已问安排」 on real send success.

    ask_his_day OR (schedule_brief|activity_end AND ask_him).
    Never true from activity_end id alone or from sent-text regex.
    """
    if not isinstance(bm, dict):
        bm = {}
    bid = bm.get("id")
    dk = done_key if done_key is not None else bm.get("done_key")
    if bid == "ask_his_day" or str(dk or "").startswith("ask_his_day"):
        return True
    if bid in ("schedule_brief", "activity_end") and bm.get("ask_him"):
        return True
    return False


def mark_asked_his_day(
    st_blob: dict | None,
    now: dt.datetime,
    *,
    question: str = "",
) -> dict:
    """Alias: mark shared day ask (brief/activity_end ask_him or ask_his_day send)."""
    return mark_asked(st_blob, now, question=question)


def outbox_item_marks_ask_his_day(item: dict | None) -> bool:
    """True if this outbox item should green 「今日已问安排」 on flush success."""
    if not isinstance(item, dict):
        return False
    if item.get("ask_him"):
        return True
    bid = str(item.get("beat_id") or "")
    return bid == "ask_his_day" or bid.startswith("ask_his_day")


def mark_asked_his_day_on_flush_success(
    item: dict | None,
    now: dt.datetime | None = None,
) -> bool:
    """On outbox flush SENT: green day-ask receipt into contact memory.

    Returns True if marked. No-op on non-ask items / drop / fail paths.
    Never call on enqueue or HOLD.
    """
    if not outbox_item_marks_ask_his_day(item):
        return False
    now = now_local(now)
    cid = str((item or {}).get("contact") or "demo_contact")
    try:
        import memory
        st = memory.read_state(cid)
        blob = mark_asked_his_day(
            (st or {}).get("user_day_curiosity"),
            now,
            question=str((item or {}).get("text") or "")[:80],
        )
        memory.update_state(cid, user_day_curiosity=blob)
        return True
    except Exception as e:
        print(f"[fidelity] ask mark on flush skip: {e!r}")
        return False


def image_pendulum_allow(
    now: dt.datetime | None,
    beat_id: str,
    *,
    p: float = 0.55,
    bucket_min: int = 30,
) -> bool:
    """Deterministic time pendulum for optional proactive images.

    Hash(date + minute-bucket + beat_id) → stable within ``bucket_min`` so
    ``due_beats`` does not flicker every second. Default p≈0.55 (configurable
    via proactive.beats.proactive_image_p). When False → pure text OK.
    surf_share is not gated here (still requires screenshot).
    """
    now = now_local(now)
    try:
        p = float(p)
    except Exception:
        p = 0.55
    if p <= 0:
        return False
    if p >= 1:
        return True
    bucket = (now.hour * 60 + now.minute) // max(1, int(bucket_min))
    key = f"{now.date().isoformat()}|{bucket}|{beat_id or ''}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)  # module hashlib
    roll = (h % 10000) / 10000.0
    return roll < p


def record_his_answer(
    st_blob: dict | None,
    text: str,
    *,
    now: dt.datetime | None = None,
    cid: str | None = None,
) -> dict:
    """Persist what he said about his day; clear pending_ask."""
    now = now_local(now)
    blob = dict(st_blob or {})
    raw = (text or "").strip()
    if not raw:
        return blob
    answers = list(blob.get("answers") or [])
    entry = {
        "date": now.date().isoformat(),
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "raw": raw[:240],
    }
    answers.append(entry)
    blob["answers"] = answers[-30:]
    blob["summary"] = raw[:120]
    blob["pending_ask"] = False
    blob["pending_question"] = ""
    blob["last_answered"] = now.date().isoformat()
    if cid:
        try:
            import mem
            mem.add(
                cid,
                f"他的行程/安排（{now.date().isoformat()}）：{raw[:160]}",
                importance=6,
                kind="user_schedule",
                source="curiosity",
                ts=now.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            pass
    return blob


def maybe_capture_answer(st: dict | None, user_text: str, *, now: dt.datetime | None = None,
                        cid: str | None = None) -> dict | None:
    """If a curiosity ask is pending, record his reply. Returns new blob or None."""
    blob = (st or {}).get("user_day_curiosity") or {}
    if not blob.get("pending_ask"):
        return None
    t = (user_text or "").strip()
    if not t or len(t) < 2:
        return None
    # ignore pure emoji / 嗯哦
    if re.fullmatch(r"[嗯哦啊哈嘿唉呃吧呀嘛呢\s~.。!！?？~～]+", t):
        return None
    return record_his_answer(blob, t, now=now, cid=cid)


def prompt_lines(st: dict | None) -> list[str]:
    """Lean lines for brain state_block."""
    blob = (st or {}).get("user_day_curiosity") or {}
    lines = []
    # Hard: USER has no internship in persona
    lines.append("关于他：档案无实习设定；禁止默认问「实习忙不忙」或把他当实习生。")
    summary = (blob.get("summary") or "").strip()
    if summary:
        day = blob.get("last_answered") or ""
        lines.append(f"他提过的安排{('('+day+')') if day else ''}：{summary[:100]}")
    return lines


def slot_force_line(slot: dict | None) -> str:
    if not slot:
        return "此刻日程未知：勿编造下课/回宿舍/实习收工。"
    return (
        f"此刻强制槽：{slot.get('activity')}（{slot.get('start')}-{slot.get('end')}，"
        f"{slot.get('bucket')}）。自称场景必须贴这个槽；"
        f"禁止声称与槽冲突的下课/回宿舍/上课/片场等。"
    )
