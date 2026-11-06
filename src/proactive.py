"""主动发起聊天（思路参考 WenXiaoWendy/cyberboss 的 system-checkin-poller：按忙闲唤醒模型决定 send|skip；
soul-agent 的关系记忆与睡眠判断；频率/话题规则见 data/chat_psychology.md。只借鉴思路，未拷贝 AGPL 代码）。
本模块**只生成草稿并打印**，没有发送路径（真发走 proactive_sender.py）；
真要发送需另行实现并经用户明确批准。内部提示走 brain.reply(task=...) 的独立段落，不伪装成他的消息。
python3 src/proactive.py [--force] [--now "2026-10-24 20:00"]"""
import argparse, datetime, random
from config import load
import memory, brain, schedule

BUSY = ("上课/自习", "学生会文艺部例会") + tuple(schedule.CLASS_ACTS | schedule.BUSY_ACTS)
TOPIC_TASKS = {
    "share_self": "你突然想起他了，想主动找他聊。话题：按状态里的今日人设日程分享你此刻在干嘛（工作日学校/晚上去玩，周末可郊游），像随手分享并抛话头。画面感强就另起一行 WANT_IMAGE:贴日程的中文短场景（主动节拍系统只发 1 张，勿写多图）；否则只文字。",
    "callback": "你突然想起他了，想主动找他聊。话题：从相关长期记忆/最近聊天里挑一件他提过的具体事，自然地问问后续；记忆里没有合适的就改成分享你自己的小事。",
    "context": "你突然想起他了，想主动找他聊。话题：结合真实的天气或时间（状态里给的，没有就别提天气）随口关心他一句。",
    "playful": "你突然想起他了，想主动找他聊。话题：轻松地撒个娇或调侃他一下，给他一个好回的梗。",
}
RULES = "别问「在吗」，别查岗，别抱怨他不理你，只发一句。必须带具体钩子，不要空招呼。"

def _ts(s): return datetime.datetime.fromisoformat(s.replace(" ", "T")) if s else None

def _min_gap_lo(cfg):
    if "min_gap_sec" in cfg:
        g = cfg["min_gap_sec"]
        return float(g[0] if isinstance(g, (list, tuple)) else g)
    return float(cfg.get("min_gap_hours", 3)) * 3600

def should_initiate(now, st, cfg, rng=random, unanswered=0):
    """硬门槛（与 scheduler.gates 对齐的精简版）。probability>0 时仍可作软过滤；默认 0 交给模型。"""
    if not cfg.get("enabled", False): return False, "proactive.enabled=false"
    import proactive_scheduler as ps
    ctx = {"pending_from_him": False, "unanswered_proactive_turns": unanswered}
    reason = ps.gates(now, st, cfg, ctx)
    if reason:
        return False, reason
    p = float(cfg.get("probability", 0) or 0)
    if p > 0:
        if schedule.current(now).get("activity") in BUSY: p *= cfg.get("busy_factor", 0.3)
        return rng.random() < p, f"软过滤 p={p:.2f}"
    return True, "硬门槛通过，交模型决定"

def pick_topic(cfg, rng=random):
    w = cfg.get("topics") or {"share_self": .35, "callback": .3, "context": .2, "playful": .15}
    return rng.choices(list(w), weights=list(w.values()))[0]

def build_task(topic): return TOPIC_TASKS[topic] + RULES

def draft(cid, now, topic):
    hist = memory.history(memory.db(), cid, 10)
    return brain.reply(cid, hist, dry=True, now=now, task=build_task(topic))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--force", action="store_true"); ap.add_argument("--now")
    a = ap.parse_args(); cfg = load(); cid = cfg["contact"]["id"]; pc = cfg.get("proactive", {})
    now = datetime.datetime.fromisoformat(a.now) if a.now else __import__("timeutil").now_local()
    ok, why = should_initiate(now, memory.read_state(cid), pc)
    print(f"[proactive] should_initiate={ok} ({why})")
    if ok or a.force:
        topic = pick_topic(pc); d = draft(cid, now, topic)
        print(f"[proactive] 话题={topic} 草稿（不会发送）:", d if d else "（模型选择沉默 [SKIP]）")

if __name__ == "__main__": main()
