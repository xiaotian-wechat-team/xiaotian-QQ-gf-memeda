"""离线：sleep_chat 晚安→已睡、睡前密度、主动压制。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import datetime as dt
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import sleep_chat as sc
import proactive_scheduler as ps


def test_goodnight_detect():
    assert sc.is_goodnight_text("晚安笨熊")
    assert sc.is_goodnight_text("我先睡了啊")
    assert sc.is_goodnight_text("快睡吧")
    assert not sc.is_goodnight_text("明天长城见")


def test_pre_sleep_tighten_less_dense():
    raw = "第一句比较长的话呀哼\n第二句也有点长哦\n第三句继续\n第四句还来\nWANT_IMAGE: dorm"
    out = sc.tighten_text(raw, "pre_sleep")
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) <= 3
    assert "WANT_IMAGE" not in out
    assert len(out) <= 40


def test_asleep_after_her_goodnight_unanswered():
    now = dt.datetime(2026, 10, 26, 0, 10, 0)
    st = {}
    mode, blob = sc.note_outbound("晚安笨熊", st, now)
    assert mode == "pre_sleep"
    assert blob["pending_goodnight"]["from"] == "me"
    # 4 min later — still pre_sleep
    m2, b2 = sc.peek_mode({sc.STATE_KEY: blob}, now + dt.timedelta(minutes=4))
    assert m2 == "pre_sleep"
    # 6 min, he never replied
    m3, b3 = sc.peek_mode({sc.STATE_KEY: blob}, now + dt.timedelta(minutes=11))
    assert m3 == "asleep"
    assert b3.get("asleep_since")
    why = sc.should_suppress_proactive({sc.STATE_KEY: b3}, now + dt.timedelta(minutes=11))
    assert why and "已睡" in why


def test_asleep_after_his_goodnight_she_silent():
    now = dt.datetime(2026, 10, 26, 0, 20, 0)
    mode, blob, style = sc.note_inbound("晚安", {}, now)
    assert mode == "pre_sleep" and style == "pre_sleep"
    m2, b2 = sc.peek_mode({sc.STATE_KEY: blob}, now + dt.timedelta(seconds=601))
    assert m2 == "asleep"


def test_she_replies_clears_his_pending():
    now = dt.datetime(2026, 10, 26, 0, 30, 0)
    _, blob, _ = sc.note_inbound("晚安呀", {}, now)
    mode2, blob2 = sc.note_outbound("嗯\n晚安", {sc.STATE_KEY: blob}, now + dt.timedelta(minutes=1))
    assert blob2.get("pending_goodnight", {}).get("from") == "me"  # her goodnight restarts (b)
    # he replies quickly → clear
    mode3, blob3, style3 = sc.note_inbound("嗯收到", {sc.STATE_KEY: blob2}, now + dt.timedelta(minutes=2))
    assert blob3.get("pending_goodnight") in (None, {})
    assert style3 in ("pre_sleep", "normal")


def test_wake_on_ping_keeps_asleep_style_once():
    now = dt.datetime(2026, 10, 26, 3, 0, 0)
    blob = {
        "mode": "asleep",
        "asleep_since": "2026-10-26 00:40:00",
        "last_me_at": "2026-10-26 00:35:00",
        "last_them_at": "2026-10-26 00:30:00",
    }
    mode, blob2, style = sc.note_inbound("还在吗", {sc.STATE_KEY: blob}, now)
    assert style == "asleep"  # first reply still minimal
    assert mode in ("normal", "pre_sleep")
    assert blob2.get("mode") != "asleep" or blob2.get("wake_reason") == "them_ping"


def test_gates_suppress_when_asleep():
    now = dt.datetime(2026, 10, 26, 1, 0, 0)
    st = {sc.STATE_KEY: {"mode": "asleep", "asleep_since": "2026-10-26 00:50:00"}}
    cfg = {"enabled": True, "no_double_text": True, "min_gap_sec": [1800, 3000], "max_per_day": 12,
           "beats": {"enabled": False}}
    ctx = {"pending_from_him": False, "unanswered_proactive_turns": 0}
    g = ps.gates(now, st, cfg, ctx, beat={"id": "daily_chat", "must": False})
    assert g and "已睡" in g


def test_gap_scale():
    from bubble_split import bubble_gap_sec
    g = bubble_gap_sec("嗯", scale=2.0, min_sec=1.5, max_sec=4.5, rng=__import__("random").Random(0))
    assert g >= 1.5


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            print("ok", k)
    print("ALL OK")
