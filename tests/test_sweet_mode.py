"""emotion.sweet_mode + nickname coquet cues + brain intimacy gate."""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import emotion, nickname, brain

D = datetime.datetime

def test_coquet_cues_and_not_ganma():
    assert nickname.emotion("想你了")[0] == "coquet"
    assert nickname.emotion("想我了吗")[0] == "coquet"
    assert nickname.emotion("在干嘛")[0] == "neutral"
    assert nickname.emotion("午饭吃什么")[0] == "neutral"

def test_sweet_mode_idle_warm_care():
    emo = emotion.default()
    idle = emotion.sweet_mode(emo, "吃了吗", last_reply_at="2026-10-25 12:00:00", now=D(2026, 10, 25, 12, 10))
    assert idle["mode"] == "idle" and "日常" in idle["tip"]
    warm = emotion.sweet_mode(emo, "好想你", last_reply_at="2026-10-25 12:00:00", now=D(2026, 10, 25, 12, 10))
    assert warm["mode"] == "warm"
    absen = emotion.sweet_mode(emo, "在干嘛", last_reply_at="2026-10-24 08:00:00", now=D(2026, 10, 25, 12, 10))
    assert absen["mode"] == "warm" and "隔了" in absen["tip"]
    care = emotion.sweet_mode(emo, "我好难过", last_reply_at="2026-10-25 12:00:00", now=D(2026, 10, 25, 12, 10))
    assert care["mode"] == "care"

def test_prompt_injects_gate():
    st = {"emotion": emotion.default(), "last_reply_at": "2026-10-25 12:00:00", "mood": 5.6, "energy": 5.5}
    p = brain.build_prompt("demo_contact", [("them", "今天吃啥")], nick="小熊", st=st, now=D(2026, 10, 25, 12, 5))
    assert "亲密度门控" in p and "日常" in p
    assert "禁止称呼" not in p
    assert "这次称呼对方为：小熊" in p
    p2 = brain.build_prompt("demo_contact", [("them", "想你了")], nick="小熊", st=st, now=D(2026, 10, 25, 12, 5))
    assert "可略甜" in p2
    assert "禁止称呼" not in p2
    assert "这次称呼对方为：小熊" in p2

def test_nickname_base_less_sweet():
    assert nickname.BASE[0] <= 0.4 and nickname.BASE[2] <= 0.45
    t_idle, _, _ = nickname.target("今天吃啥", {"mood": 5.6, "energy": 5.5})
    t_miss, _, _ = nickname.target("想你了", {"mood": 5.6, "energy": 5.5})
    assert t_miss[0] > t_idle[0] and t_miss[2] > t_idle[2]


def test_daily_sweet_hard_cap_in_sweet_mode():
    emo = emotion.default()
    emo["daily_sweet_date"] = "2026-10-25"
    emo["daily_sweet_count"] = emotion.DAILY_SWEET_MAX
    sm = emotion.sweet_mode(emo, "想你了", last_reply_at="2026-10-25 12:00:00", now=D(2026, 10, 25, 12, 10))
    assert sm["mode"] == "idle" and sm.get("daily_sweet_forced")

if __name__ == "__main__":
    test_coquet_cues_and_not_ganma()
    test_sweet_mode_idle_warm_care()
    test_prompt_injects_gate()
    test_nickname_base_less_sweet()
    print("OK")
