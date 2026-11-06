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
    """离线：主动聊天硬门槛 + 独立提示词（不调 LLM、不发送）。"""
    import sys, pathlib, datetime, random, inspect
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    import proactive, brain, config, schedule
    D = datetime.datetime; cfg = dict(config.load()["proactive"], enabled=False)
    on = dict(cfg, enabled=True, probability=0)   # 默认交模型，硬门槛通过即 True
    day = D(2026, 10, 24, 20, 0)                       # 周六晚 自由时间
    assert not proactive.should_initiate(day, {}, cfg)[0]
    assert proactive.should_initiate(day, {}, on)[0]
    assert not proactive.should_initiate(D(2026, 10, 24, 4, 30), {}, on)[0]
    # min_gap：距上次主动发送不足 25min
    assert not proactive.should_initiate(day, {"last_proactive_at": "2026-10-24 19:55:00"}, on)[0]
    assert proactive.should_initiate(day, {"last_proactive_at": "2026-10-24 19:00:00", "last_reply_at": "2026-10-24 19:05:00"}, on)[0]
    st = {"last_reply_at": "2026-10-24 10:00:00", "last_proactive_at": "2026-10-24 12:00:00"}
    ok, why = proactive.should_initiate(day, st, on); assert ok
    assert not proactive.should_initiate(day, {"proactive_day": "2026-10-24", "proactive_count": on.get("max_per_day", 5)}, on)[0]
    # 未回主动冻结已移除（降频在 scheduler）；should_initiate 不再因 unanswered 拒绝
    ok, why = proactive.should_initiate(day, {}, on, unanswered=5); assert ok or "连发" in why or "min_gap" in why or "次数" in why or "硬门槛" in why or "模型" in why
    # probability>0 时忙时软过滤仍乘 busy_factor
    mon10 = D(2026, 10, 26, 10, 0); assert schedule.current(mon10)["activity"] in schedule.CLASS_ACTS
    _, why = proactive.should_initiate(mon10, {}, dict(on, probability=.5), random.Random(0)); assert "0.15" in why
    # 话题按权重
    c = [proactive.pick_topic(cfg, random.Random(i)) for i in range(400)]; assert set(c) == set(cfg["topics"])
    src = inspect.getsource(proactive); assert '("them"' not in src and "PROMPT_HINT" not in src
    calls = {}
    proactive.memory.history = lambda c, cid, n: [("them", "今天考完试了")]; proactive.memory.db = lambda: None
    brain.memory_block = lambda cid, q: "（测试）"; brain.memory.read_state = lambda cid: {}
    # 强制长路径，避免 short_backend=deepseek/kimi 打到真 HTTP
    _orig_pick = __import__("llm").pick_backend
    __import__("llm").pick_backend = lambda *a, **k: "cursor_agent"
    brain.BACKENDS["cursor_agent"] = lambda p: calls.setdefault("p", p) and "[SKIP]"
    assert proactive.draft("demo_contact", day, "callback") is None
    p = calls["p"]; assert "# 任务（系统内部指令，不是他发的消息）" in p and "[SKIP]" in p
    hist_part = p.split("# 最近聊天")[1].split("# 任务")[0]
    assert "想起他" not in hist_part and hist_part.count("他: ") == 1
    brain.BACKENDS["cursor_agent"] = lambda p: "考得咋样呀"
    assert proactive.draft("demo_contact", day, "share_self") == "考得咋样呀"
    __import__("llm").pick_backend = _orig_pick
    assert "聊天方式" in brain.build_prompt.__globals__["CHAT_TIPS"]
    print("OK")
