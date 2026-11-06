"""Day/night hour-band emotion soft bias (view + sweet gate lean)."""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import emotion, nickname, brain

D = datetime.datetime


def test_hour_bands_cover_day():
    assert emotion.hour_band_for(D(2026, 10, 26, 3, 0)) == "late_night"
    assert emotion.hour_band_for(D(2026, 10, 26, 8, 30)) == "morning"
    assert emotion.hour_band_for(D(2026, 10, 26, 14, 0)) == "day"
    assert emotion.hour_band_for(D(2026, 10, 26, 18, 30)) == "dusk"
    assert emotion.hour_band_for(D(2026, 10, 26, 20, 30)) == "evening"
    assert emotion.hour_band_for(D(2026, 10, 26, 23, 10)) == "pre_bed"


def test_day_tease_vs_evening_sweet():
    day = emotion.hour_soft_bias(D(2026, 10, 26, 14, 0))
    eve = emotion.hour_soft_bias(D(2026, 10, 26, 20, 30))
    bed = emotion.hour_soft_bias(D(2026, 10, 26, 23, 10))
    assert day["hour_band"] == "day"
    assert day["tease_lean"] > eve["tease_lean"]
    assert day["playful_lean"] > eve["playful_lean"]
    assert day["sweet_lean"] < eve["sweet_lean"] < bed["sweet_lean"]
    assert day["intimacy_delta"] < eve["intimacy_delta"] <= bed["intimacy_delta"]
    assert "hour_day" in day["reason_codes"]
    assert "hour_pre_bed" in bed["reason_codes"]


def test_progressive_sweet_dusk_evening_prebed():
    dusk = emotion.hour_soft_bias(D(2026, 10, 26, 18, 0))
    eve = emotion.hour_soft_bias(D(2026, 10, 26, 21, 0))
    bed = emotion.hour_soft_bias(D(2026, 10, 26, 22, 30))
    assert dusk["sweet_lean"] < eve["sweet_lean"] < bed["sweet_lean"]
    assert dusk["intimacy_delta"] < eve["intimacy_delta"] < bed["intimacy_delta"]


def test_view_merges_schedule_and_hour():
    # busy class at 14:00 — schedule fatigue + day tease lean both present
    now = D(2026, 10, 26, 14, 0)
    slot = {
        "start": "00:00", "end": "24:00", "activity": "UNITTEST",
        "tone": "X", "bucket": "school_class", "energy": -1,
        "scene_hints": [], "photo_ok": True, "delay": [10, 120],
    }
    st = {
        "emotion": emotion.default(),
        "life_today": {
            "date": "2026-10-26", "kind": "workday",
            "photo_bias": "school_class", "slots": [slot],
        },
        "life_now": {**slot, "kind": "workday", "photo_bias": "school_class", "date": "2026-10-26"},
        "life_plan_meta": {"source": "draft"},
    }
    view, bias = emotion.emotion_view_for(st, now)
    assert bias.get("prefer_idle")  # class busy
    assert bias.get("hour_band") == "day"
    assert bias.get("tease_lean", 0) >= emotion.HOUR_TEASE_TIP_MIN
    assert "busy_schedule" in bias["reason_codes"]
    assert "hour_day" in bias["reason_codes"]
    # PAD still fatigued from class (hour nudge soft, doesn't flip to smile)
    assert view["pad"]["pleasure"] < emotion.BASELINE["pleasure"] + 0.05


def test_sweet_mode_day_tip_tease_no_announce():
    emo = emotion.default()
    now = D(2026, 10, 26, 14, 0)
    bias = emotion.hour_soft_bias(now)
    sm = emotion.sweet_mode(
        emotion.apply_soft_pad(emo, bias), "吃了吗",
        last_reply_at="2026-10-26 13:50:00", now=now, schedule_bias=bias,
    )
    assert sm["mode"] == "idle"
    assert "tease" in sm["tip"] and "嘴损心软" in sm["tip"]
    assert "白天" not in sm["tip"] and "晚上" not in sm["tip"] and "时段" not in sm["tip"]
    assert sm.get("hour_band") == "day"


def test_sweet_mode_evening_tip_sweet_no_announce():
    emo = emotion.default()
    now = D(2026, 10, 26, 22, 40)
    bias = emotion.hour_soft_bias(now)
    sm = emotion.sweet_mode(
        emotion.apply_soft_pad(emo, bias), "还在吗",
        last_reply_at="2026-10-26 22:30:00", now=now, schedule_bias=bias,
    )
    assert sm["mode"] == "idle"
    assert "略黏" in sm["tip"] or "亲密" in sm["tip"]
    assert "白天" not in sm["tip"] and "晚上" not in sm["tip"]
    assert sm.get("sweet_lean", 0) >= emotion.HOUR_SWEET_TIP_MIN


def test_busy_still_suppresses_absence_warm_evening():
    """Schedule prefer_idle wins over evening sweet_lean for absence-warm."""
    emo = emotion.default()
    now = D(2026, 10, 26, 21, 0)
    bias = emotion.merge_soft_bias(
        {"prefer_idle": True, "free_room": False, "pleasure_delta": -0.2,
         "arousal_delta": -0.2, "intimacy_delta": -1, "reason_codes": ["busy_schedule"],
         "fields_used": {}, "tease_lean": 0, "playful_lean": 0, "sweet_lean": 0, "hour_band": None},
        emotion.hour_soft_bias(now),
    )
    assert bias["prefer_idle"] and bias["sweet_lean"] >= 0.5
    sm = emotion.sweet_mode(
        emo, "在干嘛",
        last_reply_at="2026-10-25 10:00:00",  # absence
        now=now, schedule_bias=bias,
    )
    assert sm["mode"] == "idle"  # busy suppresses absence warm
    assert sm.get("schedule_prefer_idle")


def test_nickname_day_more_tease_than_evening():
    st = {"mood": 5.6, "energy": 5.5}
    t_day, _, why_d = nickname.target("吃了吗", st, now=D(2026, 10, 26, 14, 0))
    t_eve, _, why_e = nickname.target("吃了吗", st, now=D(2026, 10, 26, 22, 40))
    # dims: sweet tease intimacy serious playful
    assert t_day[1] > t_eve[1]  # tease
    assert t_day[4] > t_eve[4]  # playful
    assert t_eve[0] > t_day[0] or t_eve[2] > t_day[2]  # sweet or intimacy
    assert any("hour=" in w for w in why_d)


def test_prompt_gate_no_clock_announce():
    st = {"emotion": emotion.default(), "last_reply_at": "2026-10-26 13:50:00",
          "mood": 5.6, "energy": 5.5}
    p = brain.build_prompt("demo_contact", [("them", "吃了吗")], nick="小熊",
                           st=st, now=D(2026, 10, 26, 14, 5))
    assert "亲密度门控" in p
    assert "白天所以" not in p and "晚上所以" not in p


if __name__ == "__main__":
    for fn in [test_hour_bands_cover_day, test_day_tease_vs_evening_sweet,
               test_progressive_sweet_dusk_evening_prebed, test_view_merges_schedule_and_hour,
               test_sweet_mode_day_tip_tease_no_announce, test_sweet_mode_evening_tip_sweet_no_announce,
               test_busy_still_suppresses_absence_warm_evening, test_nickname_day_more_tease_than_evening,
               test_prompt_gate_no_clock_announce]:
        fn()
        print("OK", fn.__name__)
    print("ALL OK")
