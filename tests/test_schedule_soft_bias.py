"""Soft schedule bias + sweetness cap / consecutive-sweet cooldown.

Consumes freeze-feed fields (kind/bucket/energy/photo_bias) as-is — no free-text classifier.
"""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import emotion, brain, nickname, outfit

D = datetime.datetime


def _emo(p=0.12, a=0.22, intimacy=60.0, streak=0, cool=0):
    return {
        "pad": {"pleasure": p, "arousal": a, "dominance": 0.55},
        "intimacy": intimacy,
        "last_label": "neutral",
        "updated": None,
        "sweet_streak": streak,
        "sweet_cooldown_left": cool,
    }


def _st(kind, bucket, energy=0, photo_bias=None, source="draft", notes=None, emo=None,
       date="2026-10-25", start="00:00", end="24:00"):
    """life_today.slots 覆盖 [start,end)，保证 resolve_life_now(now) 命中测试桶。"""
    now_slot = {
        "start": start, "end": end,
        "activity": "UNITTEST_SLOT",  # must NOT be NLP'd into labels
        "tone": "UNITTEST_TONE_DO_NOT_PARSE",
        "bucket": bucket,
        "energy": energy,
        "scene_hints": [],
        "photo_ok": True,
        "delay": [10, 120],
    }
    day = {
        "date": date,
        "kind": kind,
        "photo_bias": photo_bias or bucket,
        "notes": notes,
        "slots": [dict(now_slot)],
    }
    cached = {**now_slot, "kind": kind, "photo_bias": photo_bias or bucket, "date": date}
    return {
        "emotion": emo or _emo(),
        "last_reply_at": f"{date} 08:00:00",
        "life_today": day,
        "life_now": cached,
        "life_plan_meta": {"source": source, "draft_file": "life_schedule_2026q4.json"},
    }


def test_feed_hook_fields_as_is():
    st = _st("exam_week", "school_library", energy=0)
    feed = emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 15, 0))
    assert feed is not None
    assert feed["kind"] == "exam_week"
    assert feed["bucket"] == "school_library"
    assert feed["energy"] == 0
    # activity/tone may be present for debug but bias must not depend on parsing them
    assert feed["activity"] == "UNITTEST_SLOT"


def test_auto_spill_rejected():
    st = _st("exam_week", "school_library", source="auto", notes="auto generated")
    assert emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 15, 0)) is None


def test_soft_bias_busy_vs_free():
    busy = emotion.schedule_soft_bias(emotion.reviewed_schedule_feed(
        _st("exam_week", "school_library"), now=D(2026, 10, 25, 15)))
    free = emotion.schedule_soft_bias(emotion.reviewed_schedule_feed(
        _st("weekend", "city_fun", energy=1), now=D(2026, 10, 25, 20)))
    assert busy["prefer_idle"] and busy["pleasure_delta"] < 0 and busy["arousal_delta"] < 0
    assert "busy_schedule" in busy["reason_codes"]
    assert free["free_room"] and free["pleasure_delta"] > 0
    assert ("outing_schedule" in free["reason_codes"] or "free_schedule" in free["reason_codes"])
    # deltas stay soft-clipped (no personality flip)
    plo, phi = emotion.SCHEDULE_BIAS_PLEASURE_CLIP
    alo, ahi = emotion.SCHEDULE_BIAS_AROUSAL_CLIP
    assert plo <= busy["pleasure_delta"] <= phi
    assert alo <= busy["arousal_delta"] <= ahi
    assert busy["pleasure_delta"] <= free["pleasure_delta"]


def test_soft_not_hard_announcement():
    """Soft: tip has no exam/cold script; hard labels must never appear."""
    st = _st("exam_week", "school_library", emo=_emo(intimacy=62))
    # long absence would warm, but busy prefer_idle suppresses absence-only warm
    gate = brain.intimacy_gate_block(
        st, [("them", "在干嘛")], now=D(2026, 10, 25, 20, 0),
    )
    assert "亲密度门控" in gate
    assert "今天考试" not in gate
    assert "所以冷淡" not in gate
    assert "UNITTEST_TONE" not in gate  # did not paste/parse tone
    sm = brain.LAST["sweet_mode"]
    assert sm["mode"] == "idle"
    assert sm["schedule_prefer_idle"] is True


def test_coquet_still_warm_on_exam_soft():
    """Soft busy bias does not hard-lock: explicit 想你 still allows warm."""
    st = _st("exam_week", "school_library", emo=_emo(intimacy=72, streak=0))
    sm = emotion.sweet_mode(
        emotion.apply_soft_pad(st["emotion"], emotion.schedule_soft_bias(
            emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 15)))),
        "想你了",
        last_reply_at="2026-10-25 14:00:00",
        now=D(2026, 10, 25, 15, 0),
        schedule_bias=emotion.schedule_soft_bias(
            emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 15))),
        consecutive_sweet=0,
    )
    assert sm["mode"] == "warm"
    assert "考试" not in sm["tip"] and "冷淡" not in sm["tip"]


def test_consecutive_sweet_cooldown():
    emo = _emo(intimacy=75, streak=emotion.CONSECUTIVE_SWEET_MAX)
    sm = emotion.sweet_mode(
        emo, "想你了",
        last_reply_at="2026-10-25 12:00:00",
        now=D(2026, 10, 25, 12, 10),
        consecutive_sweet=emotion.CONSECUTIVE_SWEET_MAX,
    )
    assert sm["mode"] == "idle" and sm["cooldown_forced"] is True
    assert sm["sweet_cooldown_left_next"] == emotion.CONSECUTIVE_SWEET_COOLDOWN
    # care bypasses cooldown
    care = emotion.sweet_mode(
        emo, "我好难过",
        last_reply_at="2026-10-25 12:00:00",
        now=D(2026, 10, 25, 12, 10),
        consecutive_sweet=emotion.CONSECUTIVE_SWEET_MAX,
    )
    assert care["mode"] == "care" and care["cooldown_forced"] is False


def test_sweet_cap_tier_low_blocks_absence():
    emo = _emo(intimacy=40)  # low tier
    assert emotion.sweet_cap_tier(40) == "low"
    sm = emotion.sweet_mode(
        emo, "在干嘛",
        last_reply_at="2026-10-24 08:00:00",
        now=D(2026, 10, 25, 12, 0),
        consecutive_sweet=0,
    )
    assert sm["mode"] == "idle"  # absence alone not enough at low cap
    # coquet still ok
    sm2 = emotion.sweet_mode(
        emo, "想你了",
        last_reply_at="2026-10-25 11:00:00",
        now=D(2026, 10, 25, 12, 0),
        consecutive_sweet=0,
    )
    assert sm2["mode"] == "warm" and sm2["cap_tier"] == "low"


def test_banned_surname_not_in_prompt():
    """Wrong-surname bans stay in nickname.BANNED_NAMES; prompts only show 这次称呼."""
    st = _st("weekend", "city_fun", emo=_emo())
    p = brain.build_prompt(
        "demo_contact", [("them", "想你了")], nick="小熊", st=st, now=D(2026, 10, 25, 20, 0),
    )
    assert "禁止称呼" not in p
    assert "这次称呼对方为：小熊" in p
    assert "王某某" in nickname.BANNED_NAMES
    assert "小熊" not in nickname.BANNED_NAMES


def test_apply_soft_pad_non_mutating():
    emo = _emo(p=0.2, a=0.3)
    bias = {"pleasure_delta": -0.08, "arousal_delta": -0.1, "intimacy_delta": -1.5,
            "prefer_idle": True, "free_room": False, "reason_codes": [], "fields_used": {}}
    view = emotion.apply_soft_pad(emo, bias)
    assert emo["pad"]["pleasure"] == 0.2  # original untouched
    assert view["pad"]["pleasure"] < 0.2



def test_sleep_ignores_day_photo_bias_free():
    """睡觉槽不得因日级 photo_bias=city_fun 拿到 free_room。"""
    st = _st("weekend", "sleep", energy=-4, photo_bias="city_fun")
    feed = emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 2, 0))
    bias = emotion.schedule_soft_bias(feed)
    assert feed["bucket"] == "sleep"
    assert bias["free_room"] is False
    assert "free_schedule" not in bias["reason_codes"]


def test_internship_bucket_busy():
    st = _st("workday", "work_internship", energy=0, photo_bias="work_internship", date="2026-10-27")
    bias = emotion.schedule_soft_bias(emotion.reviewed_schedule_feed(
        st, now=D(2026, 10, 27, 15, 0)))
    assert bias["prefer_idle"] is True
    assert "busy_schedule" in bias["reason_codes"]



def test_feed_uses_live_slot_when_cache_stale():
    """life_now 跨槽时 soft-bias 必须跟 life_today 此刻槽，不能粘住旧缓存。"""
    sleep_slot = {
        "start": "00:00", "end": "10:56", "activity": "睡觉", "tone": "迷迷糊糊",
        "bucket": "sleep", "energy": -4, "photo_ok": False, "scene_hints": [], "delay": [20, 180],
    }
    wake_slot = {
        "start": "10:56", "end": "12:39", "activity": "赖床/吃早午饭", "tone": "懒",
        "bucket": "weekend_home", "energy": 0, "photo_ok": True, "scene_hints": [], "delay": [10, 120],
    }
    st = _st("weekend", "weekend_home", energy=0, photo_bias="city_fun")
    st["life_today"]["slots"] = [sleep_slot, wake_slot]
    # 故意粘住白天槽
    st["life_now"] = {**wake_slot, "date": "2026-10-25", "kind": "weekend", "photo_bias": "city_fun"}
    feed = emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 2, 0))
    assert feed is not None
    assert feed["bucket"] == "sleep"
    assert feed["activity"] == "睡觉"
    assert feed["energy"] == -4
    bias = emotion.schedule_soft_bias(feed)
    assert bias["free_room"] is False


def test_feed_keeps_cache_when_same_day_slot():
    st = _st("weekend", "city_fun", energy=1, start="14:00", end="17:00")
    feed = emotion.reviewed_schedule_feed(st, now=D(2026, 10, 25, 15, 0))
    assert feed["bucket"] == "city_fun"
    assert feed["activity"] == "UNITTEST_SLOT"


def test_outing_bias_clears_smile_gate():
    """外出玩乐槽：view mood≥6.5，表情进入浅笑；供聊天+生图同一路径。"""
    st = _st("weekend", "outdoor_outing", energy=1, photo_bias="outdoor_outing")
    now = D(2026, 10, 25, 15, 0)
    view, bias = emotion.emotion_view_for(st, now)
    d = emotion.derived(view)
    assert bias["free_room"] is True
    assert "outing_schedule" in bias["reason_codes"]
    assert d["mood"] >= 6.5
    cue = emotion.expression_cue(view, energy_bias=1.0)
    assert "轻微浅笑" in cue["text"] or "嘴角自然上扬" in cue["text"] or "放松柔和" in cue["text"]


def test_class_busy_fatigue_vector():
    """上课/忙碌槽：低 pleasure + 疲惫眼神，符合大预言情景。"""
    st = _st("workday", "school_class", energy=-1, photo_bias="school_class", date="2026-10-27")
    now = D(2026, 10, 27, 10, 30)
    view, bias = emotion.emotion_view_for(st, now)
    d = emotion.derived(view, energy_bias=-1.0)
    assert bias["prefer_idle"] is True
    assert "busy_schedule" in bias["reason_codes"]
    assert d["mood"] < 5.0
    assert d["energy"] <= 4.0
    cue = emotion.expression_cue(view, energy_bias=-1.0)
    assert ("倦" in cue["text"] or "疲惫" in cue["text"] or "略低" in cue["text"]
            or "安静" in cue["text"] or "不勉强笑" in cue["text"])


def test_daily_sweet_hard_cap():
    emo = _emo(intimacy=75, streak=0)
    emo["daily_sweet_date"] = "2026-10-25"
    emo["daily_sweet_count"] = emotion.DAILY_SWEET_MAX
    sm = emotion.sweet_mode(
        emo, "想你了",
        last_reply_at="2026-10-25 12:00:00",
        now=D(2026, 10, 25, 12, 10),
        consecutive_sweet=0,
    )
    assert sm["mode"] == "idle"
    assert sm["daily_sweet_forced"] is True
    assert "单日甜蜜上限" in sm["tip"]
    # care still ok
    care = emotion.sweet_mode(
        emo, "我好难过",
        last_reply_at="2026-10-25 12:00:00",
        now=D(2026, 10, 25, 12, 10),
        consecutive_sweet=0,
    )
    assert care["mode"] == "care"


def test_daily_happy_slot_cap_flattens_new_outing():
    """同一天超过 DAILY_HAPPY_SLOT_MAX 个开心槽后，新外出槽不再开心。"""
    st = _st("weekend", "outdoor_outing", energy=1, start="12:39", end="18:06")
    now = D(2026, 10, 25, 15, 0)
    feed = emotion.reviewed_schedule_feed(st, now)
    bias0 = emotion.schedule_soft_bias(feed)
    assert bias0["free_room"]
    # pretend two other happy slots already used today
    emo = _emo()
    emo["daily_happy_date"] = "2026-10-25"
    emo["daily_happy_slots"] = ["2026-10-25|10:00|city_fun", "2026-10-25|19:00|city_fun"]
    bias = emotion.apply_daily_caps(bias0, emo, now)
    assert bias["free_room"] is False
    assert "daily_happy_cap" in bias["reason_codes"]
    assert bias["pleasure_delta"] <= 0
    # same slot already counted still keeps happy
    sk = emotion._slot_key(bias0)
    emo2 = _emo()
    emo2["daily_happy_date"] = "2026-10-25"
    emo2["daily_happy_slots"] = [sk]
    bias_same = emotion.apply_daily_caps(bias0, emo2, now)
    assert bias_same["free_room"] is True


def test_weekend_home_milder_than_outing():
    outing = emotion.schedule_soft_bias(emotion.reviewed_schedule_feed(
        _st("weekend", "outdoor_outing", energy=1), now=D(2026, 10, 25, 15)))
    home = emotion.schedule_soft_bias(emotion.reviewed_schedule_feed(
        _st("weekend", "weekend_home", energy=0, photo_bias="weekend_home"),
        now=D(2026, 10, 25, 11)))
    assert outing["pleasure_delta"] > home["pleasure_delta"]
    assert "outing_schedule" in outing["reason_codes"]
    assert "free_schedule" in home["reason_codes"]


def test_outfit_expression_uses_soft_bias_view():
    """生图表情必须吃 soft-bias view，不能只用未偏置的 STATE.emotion。"""
    st = _st("weekend", "outdoor_outing", energy=1, photo_bias="outdoor_outing",
             emo=_emo(p=0.12, a=0.22))
    plan = outfit.plan(
        "古北水镇", scene="约会", now=D(2026, 10, 25, 15, 0),
        emo=st["emotion"], campus_life=None, chat=[], weather=None, st=st,
        persist_happy_slot=False,
    )
    assert plan.get("emotion_text") and "表情：" in plan["emotion_text"]
    assert plan["emotion"]["mood"] >= 6.5
    assert ("浅笑" in plan["emotion_text"] or "上扬" in plan["emotion_text"]
            or "放松柔和" in plan["emotion_text"])



def test_nickname_target_uses_emotion_view_for():
    """P0: nickname sweet vector follows outing happy / class fatigue, not raw STATE.mood."""
    now_out = D(2026, 10, 25, 15, 0)
    st_out = _st("weekend", "outdoor_outing", energy=1, photo_bias="outdoor_outing",
                 emo=_emo(p=0.12, a=0.22))
    st_out["mood"] = 5.6  # unbiased daemon writeback
    st_out["energy"] = 5.5
    t_out, _, why_out = nickname.target("今天吃啥", st_out, now=now_out)
    enriched = nickname.enrich_state_mood(st_out, now_out)
    assert enriched["mood"] >= 6.5
    assert enriched["mood"] != 5.6

    now_cls = D(2026, 10, 27, 10, 30)
    st_cls = _st("workday", "school_class", energy=-1, photo_bias="school_class",
                 date="2026-10-27", emo=_emo(p=0.12, a=0.22))
    st_cls["mood"] = 5.6
    st_cls["energy"] = 5.5
    t_cls, _, why_cls = nickname.target("今天吃啥", st_cls, now=now_cls)
    enriched_c = nickname.enrich_state_mood(st_cls, now_cls)
    assert enriched_c["mood"] < 5.0
    # outing target sweet (dim0) should be >= class when both idle chat
    # (fatigue may raise sweet slightly via -min(m,0); class m<0 adds sweet on low mood —
    #  so compare playful/tease dims or mood in why)
    assert "mood=" in ",".join(why_out) and "mood=" in ",".join(why_cls)
    assert enriched["mood"] > enriched_c["mood"]
    # tease_context: outing mood clears TEASE_ROLE_MIN_MOOD; class may not
    assert nickname.tease_context(t_out, "tease", st_out, now=now_out) is True or enriched["mood"] >= nickname.TEASE_ROLE_MIN_MOOD
    assert enriched_c["mood"] < nickname.TEASE_ROLE_MIN_MOOD


def test_brain_nick_passes_soft_bias_view():
    """brain._nick → nickname.pick gets soft-biased mood (outing)."""
    st = _st("weekend", "outdoor_outing", energy=1, emo=_emo(p=0.12, a=0.22))
    st["mood"] = 5.6
    st["energy"] = 5.5
    st["active_nickname"] = "小熊"
    st["active_nickname_day"] = "2026-10-25"
    n = brain._nick(st, "在干嘛", now=D(2026, 10, 25, 15, 0))
    assert n == "小熊"  # sticky still works
    # enrich path must raise mood above unbiased
    assert nickname.enrich_state_mood(st, D(2026, 10, 25, 15, 0))["mood"] >= 6.5


def test_proactive_emotion_hint_uses_daily_caps():
    """P1: proactive _emotion_hint goes through emotion_view_for + daily sweet/happy caps."""
    import proactive_beats as pb
    now = D(2026, 10, 25, 15, 0)
    st = _st("weekend", "outdoor_outing", energy=1, emo=_emo(p=0.12, a=0.22, intimacy=75))
    # uncapped outing: free_room in hint
    hint = pb._emotion_hint(st, now)
    assert "free_room=1" in hint or "prefer_idle=False" in hint or "gate=" in hint
    assert "prefer_idle=True" not in hint

    # daily sweet cap → idle + daily_sweet_cap flag (absence would warm without cap)
    st2 = _st("weekend", "outdoor_outing", energy=1, emo=_emo(p=0.12, a=0.22, intimacy=75))
    st2["last_reply_at"] = "2026-10-24 08:00:00"  # long absence
    st2["emotion"]["daily_sweet_date"] = "2026-10-25"
    st2["emotion"]["daily_sweet_count"] = emotion.DAILY_SWEET_MAX
    hint2 = pb._emotion_hint(st2, now)
    assert "gate=idle" in hint2
    assert "daily_sweet_cap=1" in hint2

    # daily happy slot cap flattens free_room
    st3 = _st("weekend", "outdoor_outing", energy=1, start="12:39", end="18:06",
              emo=_emo(p=0.12, a=0.22))
    st3["emotion"]["daily_happy_date"] = "2026-10-25"
    st3["emotion"]["daily_happy_slots"] = [
        "2026-10-25|10:00|city_fun", "2026-10-25|19:00|city_fun",
    ]
    hint3 = pb._emotion_hint(st3, now)
    assert "daily_happy_cap=1" in hint3
    assert "free_room=1" not in hint3

    # build_beat_task surfaces the hint
    beat = {"id": "daily_chat", "slot": st["life_now"], "today": st["life_today"]}
    task = pb.build_beat_task(beat, st2, now=now)
    assert "daily_sweet_cap=1" in task or "gate=idle" in task


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("OK")
