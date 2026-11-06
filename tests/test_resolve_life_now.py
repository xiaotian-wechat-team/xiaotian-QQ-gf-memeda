"""resolve_life_now: discard stale cache; end=24:00 covers late evening; TZ Shanghai."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import life_calendar as lc


def test_discard_cached_outing_at_2359():
    """@23:59 with afternoon outing in life_now cache → must NOT return outing."""
    today = {
        "date": "2026-10-25",
        "kind": "weekend",
        "photo_bias": "outdoor_outing",
        "slots": [
            {"start": "00:00", "end": "10:56", "activity": "睡觉", "bucket": "sleep",
             "tone": "", "energy": -4, "photo_ok": False},
            {"start": "10:56", "end": "12:39", "activity": "赖床", "bucket": "weekend_home",
             "tone": "", "energy": 0, "photo_ok": True},
            {"start": "12:39", "end": "18:06", "activity": "古北水镇/长城冬日半日",
             "bucket": "outdoor_outing", "tone": "出游", "energy": 1, "photo_ok": True},
            {"start": "18:06", "end": "19:53", "activity": "吃晚饭", "bucket": "campus_life",
             "tone": "", "energy": 1, "photo_ok": True},
            {"start": "19:53", "end": "22:43", "activity": "学校附近探店", "bucket": "city_fun",
             "tone": "", "energy": 1, "photo_ok": True},
            {"start": "22:43", "end": "24:00", "activity": "回宿舍洗漱/准备睡",
             "bucket": "weekend_home", "tone": "犯困", "energy": -1, "photo_ok": False},
        ],
    }
    cached_outing = {
        "date": "2026-10-25", "start": "12:39", "end": "18:06",
        "activity": "古北水镇/长城冬日半日", "bucket": "outdoor_outing",
        "kind": "weekend", "photo_bias": "outdoor_outing",
    }
    st = {"life_today": today, "life_now": cached_outing}
    now = dt.datetime(2026, 10, 25, 23, 59)
    cur = lc.resolve_life_now(st, now)
    assert cur is not None, "live prep-sleep slot should resolve at 23:59 with end=24:00"
    assert cur.get("bucket") != "outdoor_outing", cur
    assert "准备睡" in (cur.get("activity") or "") or cur.get("bucket") == "weekend_home", cur
    assert cur.get("start") == "22:43" and cur.get("end") == "24:00", cur


def test_live_none_does_not_return_cache():
    """When no slot matches and slot_at also None, return None — never stale cache."""
    st = {
        "life_today": {"date": "2026-10-25", "kind": "weekend", "slots": []},
        "life_now": {"date": "2026-10-25", "start": "12:00", "end": "18:00",
                     "activity": "outing", "bucket": "outdoor_outing"},
    }
    # Empty slots + monkeypatch slot_at → None via empty get_day is hard; use mismatched date today
    st["life_today"]["date"] = "2026-10-24"  # not now's date → skip today walk
    # Force slot_at miss by using a date outside draft with empty generate? Use patch.
    real_slot = lc.slot_at
    try:
        lc.slot_at = lambda now=None: None
        cur = lc.resolve_life_now(st, dt.datetime(2026, 10, 25, 23, 59))
        assert cur is None, cur
    finally:
        lc.slot_at = real_slot


def test_tz_constant_shanghai():
    assert lc.TZ_NAME == "Asia/Shanghai"
    n = lc.now_local()
    assert n.tzinfo is None
    assert isinstance(n, dt.datetime)


def test_generate_day_last_end_not_2359():
    for i in range(14):
        day = lc.generate_day(dt.date(2026, 11, 2) + dt.timedelta(days=i))
        ends = [s.get("end") for s in day.get("slots") or []]
        assert "23:59" not in ends, (day["date"], ends)
        sb = day.get("sleep_bounds") or {}
        assert sb.get("prep_end") != "23:59", sb
        # last slot should be 24:00 when overnight / day-covering prep
        if ends:
            assert ends[-1] != "23:59"


if __name__ == "__main__":
    test_discard_cached_outing_at_2359()
    print("ok discard outing @23:59")
    test_live_none_does_not_return_cache()
    print("ok live None no cache")
    test_tz_constant_shanghai()
    print("ok tz")
    test_generate_day_last_end_not_2359()
    print("ok generate no 23:59")
    print("OK")
