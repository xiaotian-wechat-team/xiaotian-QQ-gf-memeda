"""相册 mood 最短间隔 + 小红书换头队列。"""
import datetime as dt
import json
import pathlib
import random
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
import avatar_pick as P
import avatar_qq as Q


def test_album_min_interval_blocks(tmp_path):
    h = tmp_path / "h.json"
    now = dt.datetime(2026, 10, 25, 12, 0)
    h.write_text(json.dumps([
        {"ts": "2026-10-25T08:00:00", "status": "changed", "channel": "qq", "source": "album", "test": False},
    ]))
    mood_cfg = {"daily_p": 1.0, "min_interval_h": 6, "mood_min": 0, "high_mood": 9,
                "high_mood_bonus_p": 0, "sweet_pulse_bonus_p": 0,
                "roll_state_path": str(tmp_path / "roll.json")}
    ok, reason, detail = P.album_allow(
        now=now, history=h, mood_snap={"mood": 8, "sweet_pulse": True},
        mood_cfg=mood_cfg, rng=random.Random(0),
    )
    assert ok is False and reason == "min-interval"
    assert detail["hours_since"] == 4.0

    ok2, reason2, _ = P.album_allow(
        now=now + dt.timedelta(hours=3), history=h,
        mood_snap={"mood": 8, "sweet_pulse": True},
        mood_cfg=mood_cfg, rng=random.Random(0),
    )
    assert ok2 is True and reason2 == "ok"


def test_album_daily_roll_reused_same_day(tmp_path):
    h = tmp_path / "h.json"
    h.write_text("[]")
    roll_path = tmp_path / "roll.json"
    mood_cfg = {"daily_p": 0.5, "min_interval_h": 0, "mood_min": 0, "high_mood": 99,
                "high_mood_bonus_p": 0, "sweet_pulse_bonus_p": 0,
                "roll_state_path": str(roll_path)}
    now = dt.datetime(2026, 10, 25, 10, 0)
    # 固定 rng：第一次 roll=0.1 < 0.5 → ok；同日复用同一 roll
    class R1:
        def random(self):
            return 0.1
    ok, reason, d1 = P.album_allow(
        now=now, history=h, mood_snap={"mood": 7}, mood_cfg=mood_cfg, rng=R1(),
    )
    assert ok and reason == "ok" and d1["roll"] == 0.1
    class R2:
        def random(self):
            raise AssertionError("should not re-roll same day")
    ok2, _, d2 = P.album_allow(
        now=now + dt.timedelta(hours=2), history=h, mood_snap={"mood": 7},
        mood_cfg=mood_cfg, rng=R2(),
    )
    assert ok2 and d2["roll"] == 0.1


def test_album_mood_low_blocks(tmp_path):
    h = tmp_path / "h.json"
    h.write_text("[]")
    mood_cfg = {"daily_p": 1.0, "min_interval_h": 0, "mood_min": 6.0, "high_mood": 9,
                "high_mood_bonus_p": 0, "sweet_pulse_bonus_p": 0,
                "roll_state_path": str(tmp_path / "r.json")}
    ok, reason, _ = P.album_allow(
        now=dt.datetime(2026, 10, 25, 12), history=h,
        mood_snap={"mood": 4.0, "sweet_pulse": False},
        mood_cfg=mood_cfg, rng=random.Random(1),
    )
    assert ok is False and reason == "mood-low"
    ok2, reason2, _ = P.album_allow(
        now=dt.datetime(2026, 10, 25, 12), history=h,
        mood_snap={"mood": 4.0, "sweet_pulse": True},
        mood_cfg=mood_cfg, rng=random.Random(1),
    )
    assert ok2 and reason2 == "ok"


def test_enqueue_daily_cap_tomorrow_first(tmp_path, monkeypatch):
    q = tmp_path / "queue.json"
    monkeypatch.setattr(P, "queue_path", lambda av=None: q)
    now = dt.datetime(2026, 10, 25, 15, 0)
    # 先塞一条普通任务
    P.enqueue_avatar("quiet-hours", now=now - dt.timedelta(hours=1), path=q, front=False)
    item = P.enqueue_avatar("daily-cap", now=now, path=q, front=True)
    data = P.load_queue(q)
    assert data["items"][0]["id"] == item["id"]
    assert data["items"][0]["earliest"] == "2026-10-26T07:30"
    assert data["items"][0]["priority"] == 0


def test_peek_ready_respects_earliest_and_quiet(tmp_path, monkeypatch):
    q = tmp_path / "queue.json"
    monkeypatch.setattr(P, "queue_path", lambda av=None: q)
    now = dt.datetime(2026, 10, 25, 8, 0)  # after quiet 07:30
    P.enqueue_avatar("daily-cap", now=dt.datetime(2026, 10, 24, 20, 0), path=q, front=True)
    # daily-cap from 24th → earliest 25th 07:30 → ready at 08:00
    ready = P.peek_ready(now=now, path=q)
    assert ready is not None
    # still in quiet → None
    quiet_now = dt.datetime(2026, 10, 25, 2, 0)
    assert P.peek_ready(now=quiet_now, path=q) is None


def test_xhs_min_days_zero_not_rate_limited(tmp_path):
    h = tmp_path / "h.json"
    now = dt.datetime(2026, 10, 25, 12, 0)
    h.write_text(json.dumps([
        {"ts": "2026-10-25T11:00:00", "status": "changed", "channel": "qq", "source": "xhs", "test": False},
    ]))
    assert Q.rate_limited(now, h, days=0, for_source="xhs") is None
    assert Q.rate_limited(now, h, days=14, for_source="xhs") is not None


def test_config_defaults_xhs_zero_and_mood(monkeypatch, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"avatar": {"album_dir": str(tmp_path)}}))
    av = P.load_avatar_cfg(cfg)
    assert av["xhs_min_days"] == 0
    assert av["mood"]["min_interval_h"] == 6
    assert "queue.json" in av["queue_path"] or av["queue_path"].endswith("queue.json")


def test_change_interval_lock(tmp_path):
    lock = tmp_path / "last_change.lock"
    now = dt.datetime(2026, 10, 25, 12, 0)
    ok, reason, _ = P.change_interval_allow(now=now, min_h=24, path=lock)
    assert ok and reason == "ok"
    P.mark_avatar_changed(channel="qq", source="xhs", now=now - dt.timedelta(hours=3), path=lock)
    ok2, reason2, d2 = P.change_interval_allow(now=now, min_h=24, path=lock)
    assert ok2 is False and reason2 == "change-interval"
    assert d2["hours_since"] == 3.0
    ok3, _, _ = P.change_interval_allow(now=now, min_h=24, path=lock, force=True)
    assert ok3


def test_config_qq_change_min_h_default(monkeypatch, tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"avatar": {"album_dir": str(tmp_path)}}))
    av = P.load_avatar_cfg(cfg)
    assert av["qq_change_min_h"] == 24


def test_album_allows_portrait_xhs_rejects(tmp_path):
    """策略分叉：album_face_reason 恒放行；safe_reason（xhs）仍拒人脸。"""
    import avatar_pick as P
    from PIL import Image
    img = tmp_path / "p.jpg"
    Image.new("RGB", (64, 64), (200, 180, 190)).save(img)
    assert P.album_face_reason("真人自拍", img, people=lambda _: 3) is None
    assert P.safe_reason("真人自拍", img, people=lambda _: 0) is not None
    assert "人脸" in P.safe_reason("风景", img, people=lambda _: 2)

