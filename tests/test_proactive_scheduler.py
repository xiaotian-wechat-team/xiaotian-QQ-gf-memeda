"""Legacy script assertions run in a fresh process under pytest.

Their module-level mocks must not leak into other regression modules.
Direct .venv/bin/python execution retains the original test behavior.
"""
if __name__ != "__main__":
    def test_isolated_script():
        import os, pathlib, subprocess, sys
        env = {**os.environ, "XIAOTIAN_TEST": "1", "RELAY_ENV_FILE": "/nonexistent/xiaotian-test-relay.env"}
        result = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve())],
                                env=env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
else:
    """离线：忙闲调度（不调 LLM、不发送、不写 STATE）。"""
    import sys, pathlib, datetime, random, inspect, math
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    import proactive_scheduler as ps, config, schedule
    D = datetime.datetime; base = dict(config.load()["proactive"], enabled=False)
    # Legacy interval/budget cases use explicit settings, independent of production policy.
    base.pop("daily_contact_range", None)
    base.pop("contact_rhythm", None)
    base["limiter"] = {**base.get("limiter", {}), "merge_window_sec": 600}
    base["limiter"].pop("spacing_range_sec", None)
    on = dict(base, enabled=True, probability=0, beats={"enabled": False})
    on["image_share_min_ratio"] = 0
    sat20, mon10, sat4 = D(2026, 10, 24, 20, 0), D(2026, 10, 26, 10, 0), D(2026, 10, 24, 4, 30)
    R = random.Random

    # busy 公式与 T_consider
    assert 0 <= ps.busy_value(sat20, on) < ps.busy_value(mon10, on) <= 100
    B0, B100 = 0, 100
    assert abs(ps.consider_interval_sec(B0, dict(on, consider_jitter=[1, 1]), R(0)) - 5) < 1e-6
    assert abs(ps.consider_interval_sec(B100, dict(on, consider_jitter=[1, 1]), R(0)) - 1800) < 1e-6
    mid = ps.consider_interval_sec(50, dict(on, consider_jitter=[1, 1]), R(0))
    assert abs(mid - 5 * math.sqrt(1800 / 5)) < 1e-6
    # jitter + clamp
    samples = [ps.consider_interval_sec(50, on, R(i)) for i in range(200)]
    assert min(samples) >= 5 and max(samples) <= 1800
    # busy_factor 抬高上课 B
    B_class = ps.busy_value(mon10, dict(on, busy_factor=0.3))
    B_class_free = ps.busy_value(mon10, dict(on, busy_factor=1.0))
    assert B_class > B_class_free

    # 默认关闭 → SKIP，但仍给出下次考虑
    r = ps.tick(sat20, base, {}, [], R(0)); assert r["action"] == "SKIP" and "enabled" in r["reason"] and r["next_consider"] > sat20
    # 休息时间不发；next_consider 跳到醒来后
    r = ps.tick(sat4, on, {}, [], R(0), use_llm=False); assert r["action"] == "SKIP"
    n = ps.next_consider(sat4, {}, on, R(0)); assert not ps.resting(n, on) and n.hour >= 9
    # 闲时 probability=0 → 直接交模型（未调模型时 DRAFT）
    r = ps.tick(sat20, on, {}, [], R(0), use_llm=False); assert r["action"] == "DRAFT" and r["task"]
    # 忙时 T_consider 更长
    gap = lambda t: sum((ps.next_consider(t, {}, on, R(i)) - t).total_seconds() for i in range(200))
    assert gap(mon10) > gap(sat20)
    # after_send → 25–45min
    gaps = [(ps.next_consider(sat20, {}, on, R(i), after_send=True) - sat20).total_seconds() for i in range(100)]
    assert all(1800 <= g <= 3000 for g in gaps)

    # 上下文：新入站 10 分钟内 → 交给回复流程
    rows = [("me", "在干嘛", "2026-10-24 12:00:00"), ("them", "刚吃完饭", "2026-10-24 19:55:00")]
    r = ps.tick(sat20, on, {}, rows, R(0), use_llm=False); assert r["action"] == "SKIP" and "回复流程" in r["reason"]
    # 不连发 / min_gap（主动发送后）
    assert ps.tick(sat20, on, {"last_reply_at": "2026-10-24 10:00:00", "last_proactive_at": "2026-10-24 12:00:00"}, [], R(0), use_llm=False)["action"] == "DRAFT"
    assert "合并窗口" in ps.tick(sat20, on, {"last_proactive_at": "2026-10-24 19:55:00", "last_reply_at": "2026-10-24 19:51:00"}, [], R(0), use_llm=False)["reason"]
    # 未回主动：按轮次（type=proactive），不冻死；≥软阈值线性降频，日下限≥1
    # 气泡多行 me 不应计成多轮
    rows_bubbles = [
        ("them", "嗯", "2026-10-24 10:00:00", "text"),
        ("me", "气泡1", "2026-10-24 11:00:00", "text"),
        ("me", "气泡2", "2026-10-24 11:00:01", "text"),
        ("me", "气泡3", "2026-10-24 11:00:02", "text"),
    ]
    assert ps.count_unanswered_proactive_turns(rows_bubbles, {}) == 0
    rows_turns = [
        ("them", "嗯", "2026-10-24 10:00:00", "text"),
        ("me", "surf配文 [图片]", "2026-10-24 11:00:00", "proactive"),
        ("me", "又找你", "2026-10-24 12:00:00", "proactive"),
    ]
    assert ps.count_unanswered_proactive_turns(rows_turns, {}) == 2
    assert ps.count_unanswered_proactive_turns(rows_turns, {"proactive_unanswered_turns": 5}) == 5
    # STATE 优先；他尾条 → 0
    assert ps.count_unanswered_proactive_turns(
        rows_turns + [("them", "回了", "2026-10-24 13:00:00", "text")],
        {"proactive_unanswered_turns": 9}) == 0

    soft = dict(on, no_double_text=False, unanswered_miss_at=5, unanswered_miss_freq_slope=0.35,
                unanswered_miss_freq_cap=4.0, max_per_day=12)
    # 冻结已删除：未回 4 轮非 must 也可过 gates（仅受 min_gap 等）
    ctx4 = {"pending_from_him": False, "unanswered_proactive": 4, "unanswered_proactive_turns": 4,
            "mentions": [], "low_mood": False, "emotions": []}
    st_gap = {"last_proactive_at": "2026-10-24 18:00:00", "last_reply_at": "2026-10-24 10:00:00",
              "proactive_unanswered_turns": 4}
    assert ps.gates(sat20, st_gap, soft, ctx4, beat=None) is None
    assert ps.gates(sat20, st_gap, soft, ctx4, beat={"id": "surf_share", "must": False, "soft_reach": False,
        "slot": {"bucket": "weekend_home", "activity": "刷"}}) is None
    # soft_reach 仍可破 no_double
    sil = {"id": "silence_nudge", "must": False, "soft_reach": True, "bypass_quiet": False,
           "slot": {"bucket": "weekend_home", "activity": "刷手机"}}
    g_nd = ps.gates(sat20, {"last_proactive_at": "2026-10-24 19:50:00", "last_reply_at": "2026-10-24 10:00:00"},
                    dict(soft, no_double_text=True), ctx4, beat=sil)
    assert g_nd is None
    # 降频：阈值日抖动，强制 miss_threshold 路径用 factor 直接测
    f1 = ps.miss_freq_factor(4, soft, sat20)
    f5 = ps.miss_freq_factor(5, dict(soft, unanswered_miss_at=5), __import__("datetime").datetime(2026, 10, 24, 20, 0))
    # 用固定阈值：patch via cfg + emotion.miss_threshold — 直接测 effective_max 地板
    assert ps.effective_max_per_day(dict(soft, max_per_day=12), 0, sat20) >= 1
    # 人为拉高 turns，上限应下降但不为 0
    hi = ps.effective_max_per_day(dict(soft, max_per_day=12), 20, sat20)
    assert 1 <= hi < 12, hi
    # 间隔因子 ≥1
    assert ps.miss_freq_factor(0, soft, sat20) == 1.0
    assert ps.miss_freq_factor(100, soft, sat20) >= 1.0

    # 他提过明天考试 → callback 优先
    rows = [("them", "明天考高数 好慌", "2026-10-24 13:00:00"), ("me", "加油笨熊", "2026-10-24 13:01:00")]
    ctx = ps.context(rows, sat20); assert ctx["mentions"] and not ctx["pending_from_him"]
    tops = [ps.tick(sat20, on, {}, rows, R(i), use_llm=False)["topic"] for i in range(200)]
    assert tops.count("callback") > 100 and "高数" in ps.build_task("callback", ctx)
    # 情绪低 → care
    rows = [("them", "今天好累 心情不好", "2026-10-24 15:00:00"), ("me", "抱抱", "2026-10-24 15:01:00")]
    ctx = ps.context(rows, sat20); assert ctx["low_mood"]
    w = ps.topic_weights(on, ctx, sat20); assert "playful" not in w and "care" in w
    # 模型沉默 / 无钩子草稿 → SKIP
    r = ps.tick(sat20, on, {}, [], R(0), draft_fn=lambda: None); assert r["action"] == "SKIP" and "SKIP" in r["reason"]
    r = ps.tick(sat20, on, {}, [], R(0), draft_fn=lambda: "在吗"); assert r["action"] == "SKIP" and "钩子" in r["reason"]
    r = ps.tick(sat20, on, {}, [], R(0), draft_fn=lambda: "在图书馆假装学习，你呢"); assert r["action"] == "DRAFT" and r["draft"]
    # 兼容旧名 next_check
    assert hasattr(ps, "next_check") and ps.next_check(sat20, {}, on, R(0)) > sat20
    src = inspect.getsource(ps); assert "send_message" not in src and "update_state" not in src and "launchctl" not in src
    print("OK")
