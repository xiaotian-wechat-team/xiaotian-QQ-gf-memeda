"""未回主动轮次：情绪轻沉 + 降频地板（不冻结）。"""
import sys, pathlib, datetime as D
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import emotion, proactive_scheduler as ps

def test_apply_miss_once_per_day():
    now = D.datetime(2026, 10, 25, 16, 0)
    emo = emotion.default()
    emo2, m = emotion.apply_unanswered_miss(emo, turns=5, threshold=5, now=now)
    assert m["applied"]
    assert emo2["pad"]["pleasure"] < emotion.BASELINE["pleasure"]
    emo3, m2 = emotion.apply_unanswered_miss(emo2, turns=6, threshold=5, now=now,
                                             applied_day=emo2.get("unanswered_miss_day"))
    assert not m2["applied"] and m2["skipped"] == "already_today"
    tip = emotion.miss_language_tip(turns=5, threshold=5)
    assert "催读" in tip and "5" not in tip  # 不报数字

def test_turn_count_not_bubbles():
    rows = [
        ("them", "hi", "t0", "text"),
        ("me", "a", "t1", "text"),
        ("me", "b", "t2", "text"),
        ("me", "surf [图片]", "t3", "proactive"),
    ]
    assert ps.count_unanswered_proactive_turns(rows, {}) == 1

def test_freq_floor():
    cfg = {"unanswered_miss_at": 5, "unanswered_miss_freq_slope": 0.5,
           "unanswered_miss_freq_cap": 4.0, "max_per_day": 10}
    now = D.datetime(2026, 10, 25, 12, 0)
    assert ps.effective_max_per_day(cfg, 0, now) >= 1
    assert ps.effective_max_per_day(cfg, 99, now) >= 1
    assert ps.miss_freq_factor(0, cfg, now) == 1.0

if __name__ == "__main__":
    test_apply_miss_once_per_day()
    test_turn_count_not_bubbles()
    test_freq_floor()
    print("OK")
