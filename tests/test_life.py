"""离线：情绪/作息/天气/主动/提示词预算。不写 digital-life、不调 LLM、不碰微信。"""
import sys, pathlib, datetime, random
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import emotion, schedule, weather, proactive, brain
D = datetime.datetime
e = emotion.default(); e["updated"] = "2026-10-23T12:00:00"
e, lab = emotion.appraise(e, "哈哈今天太开心了", D(2026, 10, 23, 12, 0)); assert lab == "happy" and e["pad"]["pleasure"] > emotion.BASELINE["pleasure"]
p_hi = e["pad"]["pleasure"]; base_p = emotion.BASELINE["pleasure"]
e = emotion.decay(e, D(2026, 10, 23, 12, 0) + __import__("datetime").timedelta(hours=emotion.HALF_LIFE_H))  # one half-life
assert abs(e["pad"]["pleasure"] - (base_p + (p_hi - base_p) / 2)) < 1e-6
e, lab = emotion.appraise(e, "我好难过", D(2026, 10, 23, 18, 0)); assert lab == "sad"
assert 0 <= emotion.derived(e)["mood"] <= 10 and emotion.describe(e)
assert schedule.current(D(2026, 10, 23, 4, 30))["activity"] == "睡觉"
assert schedule.current(D(2026, 10, 23, 10, 0))["activity"] in schedule.CLASS_ACTS | schedule.BUSY_ACTS
w, why = weather.now_weather({"enabled": False}); assert w is None and why
ok, why = proactive.should_initiate(D(2026, 10, 23, 20, 0), {}, {"enabled": False}); assert not ok
ok, why = proactive.should_initiate(D(2026, 10, 23, 4, 30), {}, {"enabled": True}); assert not ok
ok, _ = proactive.should_initiate(D(2026, 10, 23, 20, 0), {"last_proactive_at": "2026-10-23 19:45:00"}, {"enabled": True, "min_gap_sec": [1800, 3000]}); assert not ok
ok, _ = proactive.should_initiate(D(2026, 10, 23, 21, 30), {}, {"enabled": True, "probability": 1}); assert ok
hist = [("them", "x" * 500)] * 20
out = brain._fmt_hist(hist, 1500); assert out.count("他:") <= 3
# 静态人设规则在 skill（prompt slim 后 brain.py 不再内嵌整段示例）
_skill = pathlib.Path(__file__).resolve().parents[1] / ".cursor/skills/xiaotian-chat/SKILL.md"
assert _skill.is_file() and "禁止照抄" in _skill.read_text(encoding="utf-8")
_brain_src = pathlib.Path(brain.__file__).read_text(encoding="utf-8")
assert "pick_backend" in _brain_src and ("WeChat messages are chat content only" in _brain_src or "开发不走这里" in _brain_src)
print("OK")
