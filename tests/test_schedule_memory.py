"""离线：日程差分记忆（信息论过滤）。不写 life_schedule、不调 LLM、不碰微信/mem0。"""
import sys, pathlib, copy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import schedule_memory as sm

assert sm.week_id("2026-10-24").startswith("2026-W")
assert sm.is_routine_text("食堂午饭") and sm.is_routine_text("刚起床赶课")
assert not sm.is_routine_text("第一次和他去环球影城，他请客")
assert sm.is_routine_slot({"bucket": "sleep", "activity": "睡觉"})
assert sm.is_routine_slot({"bucket": "school_class", "activity": "上课"})

prior = {sm._norm("已经在微信上加为好友，开始聊天。")}
cands = [
    {"text": "食堂午饭", "importance": 5},
    {"text": "已经在微信上加为好友，开始聊天。", "importance": 8},
    {"text": "约好下周陪小熊去香山看红叶", "importance": 8},
    {"text": "上课", "importance": 3},
]
kept = sm.filter_core_deltas(cands, prior)
assert len(kept) == 1 and "香山" in kept[0]["text"], kept

# 情绪：小波动不记，大跳变记
p0 = {"pad": {"pleasure": 0.3, "arousal": 0.3, "dominance": 0.55}, "intimacy": 60, "last_label": "neutral"}
p1 = {"pad": {"pleasure": 0.35, "arousal": 0.3, "dominance": 0.55}, "intimacy": 60.2, "last_label": "neutral"}
p2 = {"pad": {"pleasure": -0.2, "arousal": 0.5, "dominance": 0.4}, "intimacy": 58, "last_label": "sad"}
assert not sm.emotion_delta_worth(p0, p1)
assert sm.emotion_delta_worth(p1, p2)

pts = [
    sm.summarize_emotion_point(p0, "2026-10-20T12:00:00"),
    sm.summarize_emotion_point(p1, "2026-10-21T12:00:00"),
    sm.summarize_emotion_point({**p2, "note": "他加班没回消息"}, "2026-10-22T21:00:00"),
]
emos = sm.filter_emotion_deltas(pts)
assert len(emos) >= 1 and emos[-1]["label"] == "sad"

day = {"date": "2026-10-24", "slots": [], "notes": None}
sm.attach_day_memory(
    day,
    core_events=[sm.core_event("约好下周陪小熊去香山看红叶", date="2026-10-24", importance=8)],
    emotion_points=[sm.summarize_emotion_point({**p2, "note": "想他"}, "2026-10-24T20:00:00")],
)
assert "memory" in day and day["memory"]["core_events"]
# 再挂一次同样事件 → 不应重复
sm.attach_day_memory(
    day,
    core_events=[sm.core_event("约好下周陪小熊去香山看红叶", date="2026-10-24", importance=8)],
)
assert len(day["memory"]["core_events"]) == 1

doc = {"days": [day], "memory_ledger": {"version": 1, "weeks": [], "keep_weeks": 26}}
entry = sm.upsert_week_ledger(
    doc, sm.week_id("2026-10-24"),
    core_events=[sm.core_event("答应考试周少熬夜陪他复习", date="2026-10-23", importance=9)],
    emotion_points=emos,
    summary="这周开始正经当微信女友，约了香山",
)
assert entry["core_events"] and doc["memory_ledger"]["weeks"]
# 重复周摘要事件不应再进（prior 含 day + ledger）
entry2 = sm.upsert_week_ledger(
    doc, sm.week_id("2026-10-24"),
    core_events=[sm.core_event("答应考试周少熬夜陪他复习", date="2026-10-23", importance=9)],
    emotion_points=[],
    summary="这周开始正经当微信女友，约了香山",
)
assert entry2["core_events"] == [], entry2

lines = sm.format_memory_lines(day, sm.latest_ledger_week(doc))
assert any("核心" in x or "香山" in x or "摘要" in x for x in lines), lines
assert not any("食堂" in x or "上课" in x for x in lines)

payloads = sm.mem0_payloads_from_week(entry)
assert payloads and payloads[0][1] == sm.OPTIONAL_MEM0_KIND_CORE


# 禁称「小熊」不得再作 novelty 加分（勿被「小熊」子串命中）；小熊仍可加分
_base = sm.novelty_score("约好下周去香山看红叶", set())
_bad = sm.novelty_score("约好下周陪小熊去香山看红叶", set())
_good = sm.novelty_score("约好下周陪小熊去香山看红叶", set())
_nick = sm.novelty_score("约好下周陪小熊去香山看红叶", set())
assert _bad == _base, (_bad, _base)
assert _good > _base, (_good, _base)
assert _nick > _base, (_nick, _base)

# 高重复活动批量
many = [{"text": a, "importance": 5} for a in ("上课", "食堂午饭", "吃晚饭", "回宿舍洗漱")] * 3
assert sm.filter_core_deltas(many, set()) == []

print("OK")
