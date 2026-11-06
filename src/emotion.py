"""PAD 情绪插件（思路参考 pioneerjeff-labs/emotion-engine, MIT；自行实现精简版，未拷贝代码）。
另参考：ATRI intimacy decay、eros-engine warmth/absence decay、SillyTavern Prompt Petals / BetterSimTracker
（情绪状态注入提示词）、RisuAI emotion bias + affection thresholds。

状态存在联系人 STATE.json 的 "emotion" 字段：
  pad: {pleasure,arousal,dominance} ∈ [-1,1]，随消息被 appraisal 推动，随时间按半衰期回归 baseline；
  intimacy: 0~100，慢变量（类似 emotion-engine 的 trust / ATRI intimacy），明确事件小幅变化，久不聊向地板衰减；
派生：mood(0~10)=f(pleasure)，energy(0~10)=f(arousal, 作息)。不会用亲密度去要求或惩罚对方。
sweet_mode：把「日常 / 想你升糖 / 难过关心」门控成一行提示词，避免 24/7 同一甜度。
进阶旋钮（soft only）：
  - schedule_soft_bias：只消费 review→freeze→sync 写入的 STATE.life_* 结构化字段
    （kind/bucket/energy/photo_bias），slot-bound view 轻推 PAD/门控（聊天+表情 cue）；
    outing/fun 可开心，class/busy 疲惫向量；禁止硬标签或剧本宣告；
  - hour_soft_bias：墙钟时段软偏（白天 tease+playful；傍晚→睡前渐进甜/亲密）；
    合入 emotion_view_for + sweet_mode tip lean；保持嘴损心软；禁止「现在晚上所以更甜」类宣告；
  - sweetness cap tiers + consecutive-sweet cooldown + DAILY_SWEET_MAX：限制连甜/单日甜蜜；
  - DAILY_HAPPY_SLOT_MAX：单日开心槽位数封顶，避免一天都笑。"""
import math, datetime
import nickname

# 日常情侣基线：偏平静正常，不要永远「心情不错+高亲昵」
BASELINE = {"pleasure": 0.12, "arousal": 0.22, "dominance": 0.55}
HALF_LIFE_H = 4.0  # 更快回到日常基线，制造波动（原 6h）
INTIMACY_DEFAULT = 55.0
INTIMACY_FLOOR = 45.0
INTIMACY_DECAY_PER_DAY = 1.5  # 久不聊向地板降温（参考 ATRI 每 3 天向 0 衰减 1）
ABSENCE_WARM_HOURS = 6.0  # 隔这么久再聊，允许略甜一点（eros-engine absence cool/warm）

# ----- schedule soft bias (consume freeze-feed fields as-is; no free-text classifier) -----
# Hook: reviewed_schedule_feed(st) → life_today + resolve_life_now(st,now) (live slot_at; life_now cache same day+slot only)
# after reasonableness review → schedule_pipeline.freeze_month. Never invent parallel labels.
SCHEDULE_BIAS_REQUIRE_REVIEWED = True  # reject pure auto generate_day spills
SCHEDULE_BIAS_ALLOW_DRAFT = True       # accept life_plan_meta.source=="draft" even if month not yet frozen
SCHEDULE_BIAS_REQUIRE_FROZEN = False   # set True to require schedule_pipeline.is_frozen(on=date)
# Busy day/slot kinds & buckets already emitted by life_calendar / reviewed table
SCHEDULE_BUSY_KINDS = frozenset({"exam_week", "thesis_defense", "finals", "exam_review"})
SCHEDULE_BUSY_BUCKETS = frozenset({"school_library", "school_rehearsal", "school_class", "work_internship"})
# Outing/fun = clearly happier (chat+photo); weekend_home = mild free only (not all-day smile)
SCHEDULE_OUTING_BUCKETS = frozenset({"city_fun", "outdoor_outing", "campus_fun"})
SCHEDULE_FREE_BUCKETS = SCHEDULE_OUTING_BUCKETS | frozenset({"weekend_home"})
# Soft deltas on a view copy — never mode locks / hard labels. Sized so outing clears smile
# gate (mood≥6.5) and class/busy lands in fatigue (low pleasure + tired eyes).
SCHEDULE_BIAS_PLEASURE_BUSY = -0.22
SCHEDULE_BIAS_AROUSAL_BUSY = -0.30
SCHEDULE_BIAS_INTIMACY_BUSY = -2.0
SCHEDULE_BIAS_PLEASURE_OUTING = 0.22
SCHEDULE_BIAS_AROUSAL_OUTING = 0.12
SCHEDULE_BIAS_INTIMACY_OUTING = 0.6
SCHEDULE_BIAS_PLEASURE_FREE = 0.05   # weekend_home mild
SCHEDULE_BIAS_AROUSAL_FREE = 0.03
SCHEDULE_BIAS_INTIMACY_FREE = 0.3
# Slot.energy is already numeric on the freeze feed (-4..+1); scale into PAD nudge
SCHEDULE_BIAS_ENERGY_TO_AROUSAL = 0.04  # arousal += slot.energy * this
SCHEDULE_BIAS_ENERGY_TO_PLEASURE = 0.02
# Clip bounds (outing needs headroom above old +0.10 to clear smile gate)
SCHEDULE_BIAS_PLEASURE_CLIP = (-0.30, 0.28)
SCHEDULE_BIAS_AROUSAL_CLIP = (-0.40, 0.22)
SCHEDULE_BIAS_INTIMACY_CLIP = (-4.0, 1.5)

# ----- hour-band soft bias (day/night circadian lean; soft only) -----
# Wall-clock Asia/Shanghai minutes. Day → tease+playful; evening→bedtime → sweet/intimate.
# View + sweet-gate tip lean only. Never announce 白天/晚上/时段. Keeps 嘴损心软.
# Bands progressive on sweet_lean; PAD deltas sized not to dominate schedule soft-bias.
HOUR_BANDS = (
    # (name, start_min inclusive, end_min exclusive)
    ("late_night", 0, 6 * 60),             # 00–06：偏亲密、低损（困）
    ("morning", 6 * 60, 11 * 60),           # 06–11：轻 tease+playful
    ("day", 11 * 60, 17 * 60 + 30),         # 11–17:30：日间 tease+playful 峰
    ("dusk", 17 * 60 + 30, 20 * 60),        # 17:30–20：过渡，玩闹仍在、甜起
    ("evening", 20 * 60, 22 * 60),          # 20–22：甜/亲密抬
    ("pre_bed", 22 * 60, 24 * 60),          # 22–24：睡前渐进黏/亲密
)
# PAD / intimacy view deltas by band (ephemeral; no STATE writeback)
HOUR_BIAS = {
    "late_night": {"pleasure": 0.04, "arousal": -0.10, "intimacy": 1.0,
                   "tease_lean": 0.15, "playful_lean": 0.10, "sweet_lean": 0.85},
    "morning":    {"pleasure": 0.02, "arousal": 0.06, "intimacy": 0.0,
                   "tease_lean": 0.55, "playful_lean": 0.50, "sweet_lean": 0.10},
    "day":        {"pleasure": 0.03, "arousal": 0.10, "intimacy": -0.2,
                   "tease_lean": 0.85, "playful_lean": 0.80, "sweet_lean": 0.05},
    "dusk":       {"pleasure": 0.04, "arousal": 0.04, "intimacy": 0.4,
                   "tease_lean": 0.55, "playful_lean": 0.55, "sweet_lean": 0.35},
    "evening":    {"pleasure": 0.06, "arousal": -0.04, "intimacy": 0.9,
                   "tease_lean": 0.30, "playful_lean": 0.25, "sweet_lean": 0.65},
    "pre_bed":    {"pleasure": 0.08, "arousal": -0.08, "intimacy": 1.3,
                   "tease_lean": 0.15, "playful_lean": 0.15, "sweet_lean": 0.90},
}
# Tip lean thresholds (fragments only — never name the clock)
HOUR_TEASE_TIP_MIN = 0.50
HOUR_SWEET_TIP_MIN = 0.55
# Clip after merge with schedule (slightly wider than schedule-alone)
HOUR_MERGE_PLEASURE_CLIP = (-0.32, 0.32)
HOUR_MERGE_AROUSAL_CLIP = (-0.42, 0.28)
HOUR_MERGE_INTIMACY_CLIP = (-4.0, 2.5)

# ----- sweetness cap tiers (by intimacy) + consecutive-sweet forced cooldown -----
# Tiers only soften tip intensity / warm eligibility; care (sad/angry) always bypasses cooldown.
SWEET_CAP_TIER_EDGES = ((50, "low"), (70, "mid"), (85, "high"), (101, "max"))
# low: warm only on explicit coquet (not mere absence); mid+: absence may warm
SWEET_CAP_ABSENCE_OK = {"low": False, "mid": True, "high": True, "max": True}
CONSECUTIVE_SWEET_MAX = 2          # after this many warm in a row → force idle
CONSECUTIVE_SWEET_COOLDOWN = 2     # idle turns to clear streak after forced cooldown
# Same-day hard caps (real teasing couple — not 24/7 candy / all-day smile)
DAILY_SWEET_MAX = 3                # warm messages per calendar day; then force idle (care ok)
DAILY_HAPPY_SLOT_MAX = 2           # distinct outing/free slots that may keep happy bias / day
# nickname.emotion 标签 -> PAD 冲量 + 亲密度增量
APPRAISAL = {
    "happy":   ({"pleasure": .22, "arousal": .12, "dominance": .0}, .2),
    "coquet":  ({"pleasure": .28, "arousal": .12, "dominance": -.05}, 1.2),  # 想你/抱抱：明显升糖
    "tease":   ({"pleasure": .08, "arousal": .12, "dominance": .1}, .05),
    "sad":     ({"pleasure": -.22, "arousal": -.05, "dominance": -.1}, .25),
    "serious": ({"pleasure": -.05, "arousal": .05, "dominance": -.05}, .1),
    "angry":   ({"pleasure": -.25, "arousal": .22, "dominance": -.1}, -.4),
    "neutral": ({"pleasure": .0,  "arousal": .0,  "dominance": .0}, .0),  # 闲聊不堆亲密度
}
STAGES = [(20, "刚在一起"), (50, "热恋磨合"), (80, "稳定亲密"), (101, "老夫老妻")]

def _clip(x, lo=-1.0, hi=1.0): return max(lo, min(hi, x))

def default():
    return {"pad": dict(BASELINE), "intimacy": INTIMACY_DEFAULT, "updated": None}

def decay(emo, now=None):
    now = now or datetime.datetime.now()
    if emo.get("updated"):
        h = (now - datetime.datetime.fromisoformat(emo["updated"])).total_seconds() / 3600
        h = max(h, 0)
        k = 0.5 ** (h / HALF_LIFE_H)
        emo["pad"] = {d: BASELINE[d] + (v - BASELINE[d]) * k for d, v in emo["pad"].items()}
        # 亲密度慢衰减：只降不升，向 INTIMACY_FLOOR 靠（久不聊降温）
        inti = float(emo.get("intimacy", INTIMACY_DEFAULT))
        if inti > INTIMACY_FLOOR:
            inti = max(INTIMACY_FLOOR, inti - INTIMACY_DECAY_PER_DAY * (h / 24.0))
        emo["intimacy"] = inti
    emo["updated"] = now.isoformat(timespec="seconds")
    return emo

def appraise(emo, msg, now=None):
    """Apply one incoming message. Returns (emo, label)."""
    emo = decay(emo or default(), now)
    label, _ = nickname.emotion(msg)
    # 极短空笑/语气收尾：勿当 happy 抬甜（软安全网；主理解仍靠 prompt cue）
    try:
        import reply_style as _rs
        if label == "happy" and _rs.looks_phatic_soft(msg) == "phatic":
            label = "neutral"
    except Exception:
        pass
    push, di = APPRAISAL.get(label, APPRAISAL["neutral"])
    if di < 0 and not nickname.is_relationship_conflict(msg):
        di = 0.0  # Work/third-party frustration is not a relationship rupture.
    emo["pad"] = {d: _clip(v + push[d]) for d, v in emo["pad"].items()}
    emo["intimacy"] = max(0.0, min(100.0, float(emo.get("intimacy", INTIMACY_DEFAULT)) + di))
    emo["last_label"] = label
    return emo, label

def derived(emo, energy_bias=0.0):
    p, a = emo["pad"]["pleasure"], emo["pad"]["arousal"]
    mood = round(5 + 5 * p, 1)
    energy = round(max(0, min(10, 5 + 5 * a + energy_bias)), 1)
    stage = next(n for t, n in STAGES if emo.get("intimacy", INTIMACY_DEFAULT) < t)
    return {"mood": mood, "energy": energy, "intimacy": round(float(emo.get("intimacy", INTIMACY_DEFAULT)), 1), "stage": stage}

def describe(emo, energy_bias=0.0):
    d = derived(emo, energy_bias)
    m = "心情不错" if d["mood"] >= 6.5 else "心情一般" if d["mood"] >= 4 else "有点低落"
    e = "精神好" if d["energy"] >= 6.5 else "有点累" if d["energy"] >= 3.5 else "很困"
    label = emo.get("last_label") or "neutral"
    return (f"{m}（mood {d['mood']}/10），{e}（energy {d['energy']}/10），"
            f"关系阶段：{d['stage']}（亲密度 {d['intimacy']}），近轮情绪标签：{label}")

def describe_compact(emo, energy_bias=0.0):
    """短状态行（进 prompt）；完整 describe 仍留给调试/别处。"""
    d = derived(emo, energy_bias)
    m = "不错" if d["mood"] >= 6.5 else "一般" if d["mood"] >= 4 else "低落"
    e = "有精神" if d["energy"] >= 6.5 else "有点累" if d["energy"] >= 3.5 else "很困"
    label = emo.get("last_label") or "neutral"
    return f"{m}/{e} mood{d['mood']} energy{d['energy']} 亲密度{d['intimacy']} {label}"


def hours_apart(last_reply_at, now=None):
    """Hours since last_reply_at (str/datetime); None if unknown."""
    now = now or datetime.datetime.now()
    if not last_reply_at:
        return None
    try:
        if isinstance(last_reply_at, datetime.datetime):
            t = last_reply_at
        else:
            s = str(last_reply_at).replace("/", "-")
            try:
                t = datetime.datetime.fromisoformat(s)
            except ValueError:
                t = datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return max(0.0, (now - t).total_seconds() / 3600.0)
    except Exception:
        return None


def zero_schedule_bias():
    return {
        "pleasure_delta": 0.0,
        "arousal_delta": 0.0,
        "intimacy_delta": 0.0,
        "prefer_idle": False,
        "free_room": False,
        "reason_codes": [],  # logging/tests only — never paste into chat tips
        "fields_used": {},
        "tease_lean": 0.0,
        "playful_lean": 0.0,
        "sweet_lean": 0.0,
        "hour_band": None,
    }


def reviewed_schedule_feed(st, now=None):
    """Hook: consume review→freeze→sync feed only (STATE.life_*).

    Source of truth for entries:
      life_calendar.sync_to_memory → STATE.life_today / life_now / life_plan_meta
      after Mac mini reasonableness review + schedule_pipeline.freeze_month.

    Returns a flat dict of structured fields already on the freeze feed
    (kind, bucket, energy, photo_bias, date) — or None if not eligible.
    Does NOT parse activity/tone free text into new labels.
    """
    now = now or datetime.datetime.now()
    st = st or {}
    today = st.get("life_today")
    # soft-bias 跟此刻槽：缓存仅同日同 start/end 可用（与 life_calendar.resolve_life_now 一致）
    cur = st.get("life_now") if isinstance(st.get("life_now"), dict) else None
    try:
        import life_calendar as _lc
        cur = _lc.resolve_life_now(st, now)
    except Exception:
        pass
    if not isinstance(cur, dict) and not isinstance(today, dict):
        return None
    meta = st.get("life_plan_meta") or {}
    day_date = None
    if isinstance(today, dict) and today.get("date"):
        try:
            day_date = datetime.date.fromisoformat(str(today["date"])[:10])
        except Exception:
            day_date = None
    if day_date is None:
        day_date = now.date()

    if SCHEDULE_BIAS_REQUIRE_REVIEWED:
        try:
            import schedule_pipeline as sp
            frozen_ok = bool(sp.is_frozen(on=day_date))
        except Exception:
            frozen_ok = False
        draft_ok = SCHEDULE_BIAS_ALLOW_DRAFT and meta.get("source") == "draft"
        # notes starting with "auto" mark generate_day spills — never bias from those
        notes = (today or {}).get("notes") if isinstance(today, dict) else None
        if isinstance(notes, str) and notes.startswith("auto"):
            draft_ok = False
        if SCHEDULE_BIAS_REQUIRE_FROZEN:
            if not frozen_ok:
                return None
        elif not frozen_ok and not draft_ok:
            return None

    kind = None
    photo_bias = None
    if isinstance(today, dict):
        kind = today.get("kind")
        photo_bias = today.get("photo_bias")
    if isinstance(cur, dict):
        kind = cur.get("kind") or kind
        photo_bias = cur.get("photo_bias") or photo_bias
        bucket = cur.get("bucket")
        energy = cur.get("energy")
    else:
        bucket = None
        energy = None
    slot_start = (cur or {}).get("start") if isinstance(cur, dict) else None
    return {
        "date": day_date.isoformat(),
        "kind": kind,
        "bucket": bucket,
        "energy": energy,
        "photo_bias": photo_bias,
        "slot_start": slot_start,
        # pass-through for debugging; consumers must not NLP these
        "activity": (cur or {}).get("activity") if isinstance(cur, dict) else None,
        "tone": (cur or {}).get("tone") if isinstance(cur, dict) else None,
        "frozen_gate": True,
        "meta_source": meta.get("source"),
    }


def schedule_soft_bias(feed):
    """Map freeze-feed structured fields → small PAD/intimacy deltas + gate hints.

    Soft only: multipliers/deltas. Never returns hard chat labels or mode locks.
    Uses kind/bucket/energy/photo_bias as-is (no free-text reparse).
    Outing/fun buckets get a stronger happy nudge than mild weekend_home free.
    """
    out = zero_schedule_bias()
    if not feed:
        return out
    kind = feed.get("kind")
    bucket = feed.get("bucket")
    photo_bias = feed.get("photo_bias")
    try:
        slot_e = float(feed["energy"]) if feed.get("energy") is not None else None
    except (TypeError, ValueError):
        slot_e = None
    out["fields_used"] = {
        "kind": kind, "bucket": bucket, "energy": slot_e, "photo_bias": photo_bias,
        "slot_start": feed.get("slot_start"),
    }
    busy = (kind in SCHEDULE_BUSY_KINDS) or (bucket in SCHEDULE_BUSY_BUCKETS)
    # sleep 槽：忽略日级 photo_bias=free（睡觉不因白天玩过就升糖）
    outing_from_bias = (photo_bias in SCHEDULE_OUTING_BUCKETS) and (bucket != "sleep")
    free_from_bias = (photo_bias in SCHEDULE_FREE_BUCKETS) and (bucket != "sleep")
    outing = (bucket in SCHEDULE_OUTING_BUCKETS) or outing_from_bias
    free = (bucket in SCHEDULE_FREE_BUCKETS) or free_from_bias
    if busy:
        out["pleasure_delta"] += SCHEDULE_BIAS_PLEASURE_BUSY
        out["arousal_delta"] += SCHEDULE_BIAS_AROUSAL_BUSY
        out["intimacy_delta"] += SCHEDULE_BIAS_INTIMACY_BUSY
        out["prefer_idle"] = True
        out["reason_codes"].append("busy_schedule")
    elif outing:
        out["pleasure_delta"] += SCHEDULE_BIAS_PLEASURE_OUTING
        out["arousal_delta"] += SCHEDULE_BIAS_AROUSAL_OUTING
        out["intimacy_delta"] += SCHEDULE_BIAS_INTIMACY_OUTING
        out["free_room"] = True
        out["reason_codes"].append("outing_schedule")
    elif free:
        out["pleasure_delta"] += SCHEDULE_BIAS_PLEASURE_FREE
        out["arousal_delta"] += SCHEDULE_BIAS_AROUSAL_FREE
        out["intimacy_delta"] += SCHEDULE_BIAS_INTIMACY_FREE
        out["free_room"] = True
        out["reason_codes"].append("free_schedule")
    if slot_e is not None:
        out["arousal_delta"] += slot_e * SCHEDULE_BIAS_ENERGY_TO_AROUSAL
        out["pleasure_delta"] += slot_e * SCHEDULE_BIAS_ENERGY_TO_PLEASURE
        out["reason_codes"].append("slot_energy")
    plo, phi = SCHEDULE_BIAS_PLEASURE_CLIP
    alo, ahi = SCHEDULE_BIAS_AROUSAL_CLIP
    ilo, ihi = SCHEDULE_BIAS_INTIMACY_CLIP
    out["pleasure_delta"] = _clip(out["pleasure_delta"], plo, phi)
    out["arousal_delta"] = _clip(out["arousal_delta"], alo, ahi)
    out["intimacy_delta"] = max(ilo, min(ihi, out["intimacy_delta"]))
    return out



def hour_band_for(now=None):
    """Return hour-band name for wall-clock now (Asia/Shanghai naive ok)."""
    now = now or datetime.datetime.now()
    mins = now.hour * 60 + now.minute
    for name, lo, hi in HOUR_BANDS:
        if lo <= mins < hi:
            return name
    return "day"


def hour_soft_bias(now=None):
    """Map wall-clock hour band → soft PAD/intimacy deltas + tip leans.

    Soft only. Never returns hard chat labels or「现在晚上所以更甜」script lines.
    Daytime: tease+playful lean. Evening→pre_bed: progressive sweet/intimate.
    """
    now = now or datetime.datetime.now()
    band = hour_band_for(now)
    spec = HOUR_BIAS.get(band) or HOUR_BIAS["day"]
    out = zero_schedule_bias()
    out["hour_band"] = band
    out["pleasure_delta"] = float(spec["pleasure"])
    out["arousal_delta"] = float(spec["arousal"])
    out["intimacy_delta"] = float(spec["intimacy"])
    out["tease_lean"] = float(spec["tease_lean"])
    out["playful_lean"] = float(spec["playful_lean"])
    out["sweet_lean"] = float(spec["sweet_lean"])
    out["reason_codes"] = [f"hour_{band}"]
    out["fields_used"] = {
        "hour": now.hour,
        "minute": now.minute,
        "band": band,
    }
    return out


def merge_soft_bias(a, b):
    """Additively merge two soft-bias dicts (schedule + hour). Busy prefer_idle wins."""
    a = a or zero_schedule_bias()
    b = b or zero_schedule_bias()
    out = zero_schedule_bias()
    out["pleasure_delta"] = float(a.get("pleasure_delta") or 0) + float(b.get("pleasure_delta") or 0)
    out["arousal_delta"] = float(a.get("arousal_delta") or 0) + float(b.get("arousal_delta") or 0)
    out["intimacy_delta"] = float(a.get("intimacy_delta") or 0) + float(b.get("intimacy_delta") or 0)
    out["prefer_idle"] = bool(a.get("prefer_idle") or b.get("prefer_idle"))
    out["free_room"] = bool(a.get("free_room") or b.get("free_room"))
    codes = list(a.get("reason_codes") or []) + list(b.get("reason_codes") or [])
    # de-dupe preserve order
    seen = set()
    out["reason_codes"] = [c for c in codes if c not in seen and not seen.add(c)]
    fu = dict(a.get("fields_used") or {})
    fu.update(b.get("fields_used") or {})
    out["fields_used"] = fu
    out["tease_lean"] = max(float(a.get("tease_lean") or 0), float(b.get("tease_lean") or 0))
    out["playful_lean"] = max(float(a.get("playful_lean") or 0), float(b.get("playful_lean") or 0))
    out["sweet_lean"] = max(float(a.get("sweet_lean") or 0), float(b.get("sweet_lean") or 0))
    out["hour_band"] = b.get("hour_band") or a.get("hour_band")
    plo, phi = HOUR_MERGE_PLEASURE_CLIP
    alo, ahi = HOUR_MERGE_AROUSAL_CLIP
    ilo, ihi = HOUR_MERGE_INTIMACY_CLIP
    out["pleasure_delta"] = _clip(out["pleasure_delta"], plo, phi)
    out["arousal_delta"] = _clip(out["arousal_delta"], alo, ahi)
    out["intimacy_delta"] = max(ilo, min(ihi, out["intimacy_delta"]))
    return out


def apply_soft_pad(emo, bias, *, mutate=False):
    """Return emo view with schedule soft deltas applied. Default: non-mutating copy."""
    emo = emo or default()
    bias = bias or zero_schedule_bias()
    base = emo if mutate else {
        "pad": dict(emo.get("pad") or BASELINE),
        "intimacy": float(emo.get("intimacy", INTIMACY_DEFAULT)),
        "updated": emo.get("updated"),
        "last_label": emo.get("last_label"),
        "sweet_streak": emo.get("sweet_streak", 0),
        "sweet_cooldown_left": emo.get("sweet_cooldown_left", 0),
    }
    if not mutate:
        # keep other keys shallow
        for k, v in emo.items():
            if k not in base:
                base[k] = v
        base["pad"] = dict(base["pad"])
    pad = base["pad"]
    for d, key in (("pleasure", "pleasure_delta"), ("arousal", "arousal_delta"), ("dominance", None)):
        if key:
            pad[d] = _clip(float(pad.get(d, BASELINE[d])) + float(bias.get(key, 0.0)))
        else:
            pad[d] = _clip(float(pad.get(d, BASELINE[d])))
    base["pad"] = pad
    base["intimacy"] = max(0.0, min(100.0,
        float(base.get("intimacy", INTIMACY_DEFAULT)) + float(bias.get("intimacy_delta", 0.0))))
    return base


def _day_key(now=None):
    now = now or datetime.datetime.now()
    return now.date().isoformat()


def _slot_key(bias_or_feed):
    """Stable within-slot id for daily happy-slot accounting (date|start|bucket)."""
    if not bias_or_feed:
        return None
    fu = bias_or_feed.get("fields_used") if isinstance(bias_or_feed.get("fields_used"), dict) else None
    start = None
    bucket = None
    day = None
    if fu:
        start = fu.get("slot_start")
        bucket = fu.get("bucket")
    start = start or bias_or_feed.get("slot_start")
    bucket = bucket or bias_or_feed.get("bucket")
    day = (fu or {}).get("date") or bias_or_feed.get("date")
    if not start and not bucket:
        return None
    return f"{day or ''}|{start or ''}|{bucket or ''}"


def daily_sweet_count(emo, now=None):
    emo = emo or {}
    if str(emo.get("daily_sweet_date") or "") != _day_key(now):
        return 0
    try:
        return max(0, int(emo.get("daily_sweet_count") or 0))
    except (TypeError, ValueError):
        return 0


def daily_happy_slots(emo, now=None):
    emo = emo or {}
    if str(emo.get("daily_happy_date") or "") != _day_key(now):
        return []
    slots = emo.get("daily_happy_slots") or []
    return [str(s) for s in slots if s]


def apply_daily_caps(bias, emo, now=None):
    """Damp outing/free happy bias after DAILY_HAPPY_SLOT_MAX distinct slots today.

    Slot-bound: current slot already counted still keeps its happy view; a new
    free/outing slot after the cap is flattened (class/busy unchanged).
    Does not mutate PAD in STATE — view-only.
    """
    bias = dict(bias or zero_schedule_bias())
    bias["reason_codes"] = list(bias.get("reason_codes") or [])
    if not bias.get("free_room"):
        return bias
    sk = _slot_key(bias)
    used = daily_happy_slots(emo, now)
    if sk and sk in used:
        return bias
    if len(used) >= DAILY_HAPPY_SLOT_MAX:
        # flatten happy nudge; keep prefer_idle false but no free_room candy
        bias["pleasure_delta"] = min(0.0, float(bias.get("pleasure_delta") or 0.0))
        bias["arousal_delta"] = min(0.0, float(bias.get("arousal_delta") or 0.0))
        bias["intimacy_delta"] = min(0.0, float(bias.get("intimacy_delta") or 0.0))
        bias["free_room"] = False
        bias["reason_codes"].append("daily_happy_cap")
    return bias


def bump_daily_sweet(emo, now=None):
    """Return emo copy with daily warm-message counter +1 (calendar day)."""
    emo = dict(emo or default())
    day = _day_key(now)
    if str(emo.get("daily_sweet_date") or "") != day:
        emo["daily_sweet_date"] = day
        emo["daily_sweet_count"] = 0
    emo["daily_sweet_count"] = int(emo.get("daily_sweet_count") or 0) + 1
    return emo


def bump_daily_happy_slot(emo, bias, now=None):
    """Record that this slot used outing/free happy bias (at most once per slot/day)."""
    emo = dict(emo or default())
    if not (bias or {}).get("free_room"):
        return emo
    sk = _slot_key(bias)
    if not sk:
        return emo
    day = _day_key(now)
    if str(emo.get("daily_happy_date") or "") != day:
        emo["daily_happy_date"] = day
        emo["daily_happy_slots"] = []
    slots = [str(s) for s in (emo.get("daily_happy_slots") or []) if s]
    if sk not in slots:
        slots.append(sk)
        emo["daily_happy_slots"] = slots
    emo["daily_happy_date"] = day
    return emo


def emotion_view_for(st, now=None, emo=None):
    """Slot-bound soft-biased emotion view for chat prompts AND image expression_cue.

    Merges schedule_soft_bias + hour_soft_bias (day tease / evening sweet).
    Never persists PAD writeback — only a view. Daily happy-slot cap may flatten
    free/outing boosts. Returns (emo_view, bias).
    """
    now = now or datetime.datetime.now()
    emo = emo if emo is not None else ((st or {}).get("emotion") or default())
    feed = reviewed_schedule_feed(st, now)
    bias = schedule_soft_bias(feed) if feed else zero_schedule_bias()
    bias = apply_daily_caps(bias, emo, now)
    bias = merge_soft_bias(bias, hour_soft_bias(now))
    return apply_soft_pad(emo, bias), bias


def sweet_cap_tier(intimacy):
    """Return tier name for intimacy (low|mid|high|max)."""
    inti = float(intimacy)
    for edge, name in SWEET_CAP_TIER_EDGES:
        if inti < edge:
            return name
    return "max"


def sweet_mode(emo, msg, last_reply_at=None, now=None,
               schedule_bias=None, consecutive_sweet=None, sleep_style=None):
    """Intimacy / sweetness gate for prompt injection (eros warmth gate + ATRI intimacy).

    Returns dict: mode idle|warm|care, tip, label, hours_apart, intimacy,
    cap_tier, sweet_streak_next, cooldown_forced, schedule_prefer_idle.
    idle = ordinary couple chat (default); warm = miss/absence cue; care = sad/angry.

    Soft schedule bias may prefer idle / trim absence-warm; never announces
    「今天考试所以冷淡」or similar hard labels.
    Hour-band lean (tease_lean / sweet_lean) only softens tip fragments — never
    announces 白天/晚上; keep 嘴损心软. Cap tiers + consecutive-sweet cooldown
    limit 24/7 candy. sleep_style=pre_sleep|asleep：强制 idle（sad/angry 仍走 care）。
    """
    now = now or datetime.datetime.now()
    emo = emo or default()
    bias = schedule_bias or zero_schedule_bias()
    label, _ = nickname.emotion(msg or "")
    h = hours_apart(last_reply_at, now)
    inti = float(emo.get("intimacy", INTIMACY_DEFAULT))
    tier = sweet_cap_tier(inti)
    absence = h is not None and h >= ABSENCE_WARM_HOURS
    if consecutive_sweet is None:
        consecutive_sweet = int(emo.get("sweet_streak") or 0)
    cooldown_left = int(emo.get("sweet_cooldown_left") or 0)
    prefer_idle = bool(bias.get("prefer_idle"))
    free_room = bool(bias.get("free_room"))
    sweet_today = daily_sweet_count(emo, now)
    daily_sweet_forced = sweet_today >= DAILY_SWEET_MAX
    _sleep = (sleep_style or "").strip()
    if _sleep in ("pre_sleep", "asleep"):
        prefer_idle = True
        free_room = False  # 睡前/已睡：不因 free_room 升糖

    cooldown_forced = False
    if label in ("sad", "angry"):
        mode = "care"
        tip = "gate=care 关心：收嘴损短陪；禁亲密词连发；整轮最多叫一次昵称"
        streak_next, cool_next = 0, 0  # care resets candy streak
    else:
        want_warm = False
        why = ""
        # sleep_chat：睡前/已睡不强推 warm（示弱也压成 idle）
        if _sleep in ("pre_sleep", "asleep"):
            mode = "idle"
            tip = ("gate=idle 睡前收住不升糖；禁宝贝/爱你连发"
                   if _sleep == "pre_sleep"
                   else "gate=idle 已睡极简不升糖；禁宝贝/爱你连发")
            streak_next, cool_next = 0, max(0, cooldown_left - 1) if cooldown_left else 0
            return {
                "mode": mode,
                "tip": tip,
                "label": label,
                "hours_apart": h,
                "intimacy": inti,
                "cap_tier": tier,
                "sweet_streak_next": streak_next,
                "sweet_cooldown_left_next": cool_next,
                "cooldown_forced": True,
                "daily_sweet_count": sweet_today,
                "daily_sweet_forced": daily_sweet_forced,
                "schedule_prefer_idle": True,
                "sleep_style": _sleep,
            }
        if label == "coquet":
            want_warm = True
            why = "他示弱/想你"
        elif absence and SWEET_CAP_ABSENCE_OK.get(tier, True):
            # soft: busy schedule suppresses absence-only warm (still allows coquet)
            if prefer_idle and not free_room:
                want_warm = False
            else:
                want_warm = True
                why = f"隔了约{int(h)}小时"
        # consecutive-sweet forced cooldown
        if want_warm and (consecutive_sweet >= CONSECUTIVE_SWEET_MAX or cooldown_left > 0):
            want_warm = False
            cooldown_forced = True
        # same-day hard cap: after DAILY_SWEET_MAX warms → idle/tease, not sweet
        if want_warm and daily_sweet_forced:
            want_warm = False
            cooldown_forced = True
            why = why or "单日甜蜜上限"
        if want_warm:
            mode = "warm"
            # cap tier softens tip intensity (not a hard personality flip)
            if tier == "low":
                tip = f"gate=warm 可略软（{why}·档{tier}）：接一下；禁亲密词连发"
            elif tier == "mid":
                tip = f"gate=warm 可略甜（{why}·档{tier}）：嘴硬心软点一下；禁连发"
            else:
                tip = f"gate=warm 可略甜（{why}·档{tier}）：嘴硬心软点一下；禁连发"
            streak_next = consecutive_sweet + 1
            cool_next = 0
        else:
            mode = "idle"
            tip = "gate=idle 日常闲聊不升糖；禁宝贝/爱你连发"
            if daily_sweet_forced and cooldown_forced:
                tip += "（单日甜蜜上限）"
            elif cooldown_forced:
                tip += "（连甜冷却）"
            if cooldown_left > 0:
                cool_next = max(0, cooldown_left - 1)
                streak_next = 0
            elif cooldown_forced:
                cool_next = CONSECUTIVE_SWEET_COOLDOWN
                streak_next = 0
            else:
                cool_next = 0
                streak_next = 0
    # hour-band tip lean (fragments only; never name 白天/晚上/时段)
    tease_lean = float(bias.get("tease_lean") or 0.0)
    sweet_lean = float(bias.get("sweet_lean") or 0.0)
    playful_lean = float(bias.get("playful_lean") or 0.0)
    if mode == "idle" and not prefer_idle:
        if tease_lean >= HOUR_TEASE_TIP_MIN and "tease" not in tip:
            tip += "；略抬 tease+playful，嘴损心软"
        elif sweet_lean >= HOUR_SWEET_TIP_MIN and "略黏" not in tip:
            tip += "；可略黏，亲密一点，仍嘴硬心软"
    elif mode == "warm" and sweet_lean >= HOUR_SWEET_TIP_MIN and "略黏" not in tip:
        tip += "；可略黏"
    return {
        "mode": mode,
        "tip": tip,
        "label": label,
        "hours_apart": h,
        "intimacy": inti,
        "cap_tier": tier,
        "sweet_streak_next": streak_next,
        "sweet_cooldown_left_next": cool_next,
        "cooldown_forced": cooldown_forced,
        "daily_sweet_count": sweet_today,
        "daily_sweet_forced": daily_sweet_forced,
        "schedule_prefer_idle": prefer_idle,
        "sleep_style": _sleep or None,
        "schedule_free_room": free_room,
        "schedule_reason_codes": list(bias.get("reason_codes") or []),
        "hour_band": bias.get("hour_band"),
        "tease_lean": tease_lean,
        "playful_lean": playful_lean,
        "sweet_lean": sweet_lean,
    }


def expression_cue(emo=None, energy_bias=0.0):
    """生图表情提示（与 PAD→mood/energy、last_label 一致），缓解僵硬面瘫。

    返回 {"text": 中文提示, "mood", "energy", "intimacy", "stage", "label",
          "pad": {pleasure,arousal,dominance}}。
    emo 为空时用 default()（日常平静浅笑，不是面瘫）。
    """
    emo = emo or default()
    pad = dict(BASELINE)
    pad.update((emo.get("pad") or {}))
    # 用带 pad 的副本算 derived，避免缺字段
    emo2 = {**emo, "pad": {k: float(pad.get(k, BASELINE[k])) for k in BASELINE}}
    d = derived(emo2, energy_bias)
    mood, energy = d["mood"], d["energy"]
    label = emo.get("last_label") or "neutral"
    parts = []
    # pleasure / mood → 嘴角与笑意（主权重）
    if mood >= 8.0:
        parts.append("嘴角自然上扬，眼睛带笑意，轻松愉快")
    elif mood >= 6.5:
        parts.append("表情放松柔和，轻微浅笑")
    elif mood >= 5.0:
        parts.append("神情平静自然，嘴角放松")
    elif mood >= 3.5:
        parts.append("眼神偏安静，嘴角放松略低，不勉强笑")
    else:
        parts.append("眉眼柔软低沉，几乎不笑，情绪偏沉")
    # arousal / energy → 眼睛精神度
    if energy >= 7.5:
        parts.append("目光清醒有神")
    elif energy >= 5.5:
        parts.append("眼神自然清醒")
    elif energy >= 3.5:
        parts.append("眼神略松弛，带一点倦意")
    else:
        parts.append("眼皮微沉、眼神疲惫")
    # last_label 轻量点缀（与 appraisal 标签一致，不另起情绪轴）
    accent = {
        "happy": "轻松愉悦",
        "coquet": "带一点撒娇的软萌感",
        "tease": "眼神里有点促狭笑意",
        "sad": "更柔软低沉",
        "serious": "表情专注认真",
        "angry": "眉心略蹙但不夸张",
        "neutral": "",
    }.get(label, "")
    if accent:
        parts.append(accent)
    # intimacy 极高时略多一点亲昵松弛（很轻）
    if d["intimacy"] >= 80 and mood >= 6.0:
        parts.append("相处松弛亲昵")
    text = ("表情：" + "；".join(parts) +
            "。要像随手抓拍的真人微表情，忌僵硬摆拍假笑或面无表情。")
    return {
        "text": text,
        "mood": d["mood"],
        "energy": d["energy"],
        "intimacy": d["intimacy"],
        "stage": d["stage"],
        "label": label,
        "pad": {k: round(float(pad[k]), 4) for k in BASELINE},
    }


# ----- unanswered proactive-turn miss (soft; not freeze) -----
# Trigger ~unanswered_miss_at proactive TURNS (caption+image = 1), day-jitter softens cliff.
UNANSWERED_MISS_PAD = {"pleasure": -0.16, "arousal": 0.08, "dominance": -0.10}
UNANSWERED_MISS_INTIMACY = 0.15  # tiny care bump, not cling-punish


def miss_threshold(cfg=None, now=None):
    """Nominal miss_at with day-stable ±1 jitter so it is not a robotic exact-5 cliff."""
    cfg = cfg or {}
    base = int(cfg.get("unanswered_miss_at", 5) or 5)
    base = max(3, min(12, base))
    now = now or datetime.datetime.now()
    try:
        import hashlib
        h = int(hashlib.md5(f"miss-at-{now.date().isoformat()}".encode()).hexdigest()[:8], 16)
        off = (h % 3) - 1  # -1,0,+1
    except Exception:
        off = 0
    return max(3, base + off)


def miss_language_tip(*, hours=None, turns=0, threshold=5):
    """Internal vector guide — never paste numbers/templates into chat."""
    bits = [
        "语气：情侣轻嗔/嘴硬想他（怎么不找我·在忙什么气质），禁催读查岗固定句空招呼在吗",
        "向量：tease+playful 略抬，sweet/intimacy 随 gate 档，serious 压低；像真的有点空不是计数器",
    ]
    if isinstance(hours, (int, float)) and hours >= 2:
        bits.append("已隔一阵，可更软一点但仍禁剧本腔")
    if turns >= threshold:
        bits.append("他连续几轮没接你主动，心情略沉仍嘴硬")
    return "；".join(bits) + "。"


def apply_unanswered_miss(emo, *, turns, threshold=5, now=None, applied_day=None):
    """Soft PAD dip when proactive-turn miss crosses soft threshold. Once per calendar day.

    Returns (emo, meta) meta keys: applied(bool), tip(str|None), day(str), skipped(str|None).
    Does not anger-spike; teasing-couple low + slight restless arousal.
    """
    now = now or datetime.datetime.now()
    day = now.strftime("%Y-%m-%d")
    emo = decay(emo or default(), now)
    meta = {"applied": False, "tip": None, "day": day, "skipped": None, "turns": int(turns or 0),
            "threshold": int(threshold)}
    if int(turns or 0) < int(threshold):
        meta["skipped"] = "below_threshold"
        return emo, meta
    if applied_day == day or (emo.get("unanswered_miss_day") == day):
        meta["skipped"] = "already_today"
        meta["tip"] = miss_language_tip(turns=turns, threshold=threshold)
        return emo, meta
    pad = dict(emo.get("pad") or BASELINE)
    for k, dlt in UNANSWERED_MISS_PAD.items():
        pad[k] = _clip(float(pad.get(k, BASELINE[k])) + dlt)
    emo["pad"] = pad
    emo["intimacy"] = max(0.0, min(100.0,
        float(emo.get("intimacy", INTIMACY_DEFAULT)) + UNANSWERED_MISS_INTIMACY))
    emo["last_label"] = emo.get("last_label") or "coquet"
    emo["unanswered_miss_day"] = day
    emo["unanswered_miss_turns"] = int(turns)
    meta["applied"] = True
    meta["tip"] = miss_language_tip(turns=turns, threshold=threshold)
    return emo, meta
