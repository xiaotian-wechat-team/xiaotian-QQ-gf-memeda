"""日程表上的「差分记忆」账本（信息论：只记惊讶/差异，不重复记高重复日常）。

主面：挂在 data/life_schedule_*.json 的 day.memory / 文档级 memory_ledger，
随周更/反思写入；brain.state_lines 可读。向量（mem0 kind=core/emotion_core）为可选二级索引。

不删 chat.db / diary / MEMORY.md；也不做 mem0 全量 wipe（旧方案已弃）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

# 高重复日常：出现即默认「零信息」，除非带明确异常标记
ROUTINE_SUBSTR = (
    "睡觉", "赖床", "刚起床赶课", "上课", "排练/课", "食堂午饭", "吃晚饭",
    "回宿舍洗漱", "自由时间/准备睡", "图书馆自习", "编导技法课", "剧目排练",
    "现代舞基训", "导演基础", "中外舞蹈史", "编创作业排练", "学生会活动筹备",
    "早午饭", "通勤", "赶课", "洗漱", "准备睡",
)
ROUTINE_BUCKETS = frozenset({
    "sleep", "school_class", "school_rehearsal", "school_library", "campus_life",
})
# 情绪标签：相对 baseline 的「值得记」阈值（PAD 任一轴 |Δ| 或亲密度周变化）
EMO_PAD_DELTA = 0.25
EMO_INTIMACY_DELTA = 3.0
# 每周每联系人最多挂多少条核心事件 / 情绪点（超出按 novelty 截断）
MAX_CORE_PER_WEEK = 8
MAX_EMO_PER_WEEK = 5
# 文本近重复：归一化后相同或包含关系
_WS = re.compile(r"\s+")


def week_id(d: dt.date | str | None = None) -> str:
    """ISO 周标签，如 2026-W43（周一为一周之始）。"""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    d = d or dt.date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _norm(t: str) -> str:
    return _WS.sub("", (t or "").strip().lower())


def is_routine_text(text: str) -> bool:
    t = text or ""
    if not t.strip():
        return True
    return any(s in t for s in ROUTINE_SUBSTR)


def is_routine_slot(slot: dict | None) -> bool:
    if not slot:
        return True
    if slot.get("bucket") in ROUTINE_BUCKETS and not slot.get("surprise"):
        # 常规 bucket 但仍可能是特色活动文案（探店/逛展等）→ 再看文本
        act = slot.get("activity") or ""
        if is_routine_text(act):
            return True
        # campus_life 里「吃晚饭」类 routine；特色探店不算
        if slot.get("bucket") == "campus_life" and is_routine_text(act):
            return True
    if is_routine_text(slot.get("activity") or ""):
        return True
    return False


def novelty_score(text: str, prior_norms: set[str]) -> float:
    """0=无信息（例行或已记），1=全新。prior_norms 为已归一化的历史核心事件。"""
    n = _norm(text)
    if not n or is_routine_text(text):
        return 0.0
    if n in prior_norms:
        return 0.0
    for p in prior_norms:
        if n in p or p in n:
            return 0.15  # 近重复，几乎不记
    # 长度与「专有」启发：含日期/人名/地名数字略加分
    score = 0.55
    if re.search(r"\d{4}-\d{2}-\d{2}|\d+月|\d+号", text):
        score += 0.15
    if re.search(r"小熊|(?<!王)小熊|维尼|约定|吵架|和好|第一次|答应|拒绝|重要", text):
        score += 0.2
    return min(1.0, score)


def emotion_delta_worth(prev: dict | None, cur: dict | None) -> bool:
    """相对上一快照，PAD/亲密度变化是否够「惊讶」该上账本。"""
    prev = prev or {}
    cur = cur or {}
    pp, cp = prev.get("pad") or {}, cur.get("pad") or {}
    for k in ("pleasure", "arousal", "dominance"):
        if abs(float(cp.get(k, 0)) - float(pp.get(k, 0))) >= EMO_PAD_DELTA:
            return True
    if abs(float(cur.get("intimacy", 60)) - float(prev.get("intimacy", 60))) >= EMO_INTIMACY_DELTA:
        return True
    # 强烈标签本身可记一次（angry/sad/coquet），neutral/happy 小波动不记
    lab = cur.get("last_label") or "neutral"
    if lab in ("angry", "sad", "coquet") and lab != (prev.get("last_label") or ""):
        return True
    return False


def summarize_emotion_point(emo: dict | None, when: str | None = None) -> dict:
    """结构化情绪点（主存储）；embedding 可选二级。"""
    emo = emo or {}
    pad = emo.get("pad") or {}
    return {
        "ts": when or (emo.get("updated") or ""),
        "pad": {
            "pleasure": round(float(pad.get("pleasure", 0.3)), 3),
            "arousal": round(float(pad.get("arousal", 0.3)), 3),
            "dominance": round(float(pad.get("dominance", 0.55)), 3),
        },
        "intimacy": round(float(emo.get("intimacy", 60)), 2),
        "label": emo.get("last_label") or "neutral",
        "note": emo.get("note") or "",
    }


def core_event(text: str, *, date: str = "", source: str = "chat",
               importance: int = 6, kind: str = "event") -> dict:
    t = (text or "").strip()
    return {
        "id": hashlib.sha1(f"{date}|{_norm(t)}".encode()).hexdigest()[:12],
        "date": date,
        "text": t,
        "kind": kind,  # event|agreement|preference|landmine|milestone
        "source": source,
        "importance": int(importance),
    }


def collect_prior_norms(doc: dict | None, days: list[dict] | None = None) -> set[str]:
    """已落盘核心事件的归一化集合，用于去重。"""
    out: set[str] = set()
    days = days if days is not None else list((doc or {}).get("days") or [])
    for day in days:
        mem = day.get("memory") or {}
        for ev in mem.get("core_events") or []:
            out.add(_norm(ev.get("text") or ""))
    ledger = (doc or {}).get("memory_ledger") or {}
    for week in ledger.get("weeks") or []:
        for ev in week.get("core_events") or []:
            out.add(_norm(ev.get("text") or ""))
    return {x for x in out if x}


def filter_core_deltas(candidates: list[dict], prior_norms: set[str],
                       *, max_n: int = MAX_CORE_PER_WEEK) -> list[dict]:
    """信息论过滤：例行丢弃、近重复丢弃，按 novelty×importance 取 top。"""
    scored = []
    seen = set(prior_norms)
    for c in candidates:
        text = (c.get("text") or "").strip()
        if not text or is_routine_text(text):
            continue
        nov = novelty_score(text, seen)
        if nov < 0.4:
            continue
        imp = int(c.get("importance") or 5)
        scored.append((nov * (0.5 + imp / 10), c))
        seen.add(_norm(text))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:max_n]]


def filter_emotion_deltas(points: list[dict], *, max_n: int = MAX_EMO_PER_WEEK) -> list[dict]:
    """相邻点只保留「差分值得记」的；第一条若有强标签也保留。"""
    if not points:
        return []
    keep = []
    prev = None
    for p in points:
        emo_like = {
            "pad": p.get("pad"),
            "intimacy": p.get("intimacy"),
            "last_label": p.get("label"),
            "updated": p.get("ts"),
            "note": p.get("note"),
        }
        if prev is None:
            if (p.get("label") or "neutral") not in ("neutral",) or p.get("note"):
                keep.append(p)
            prev = emo_like
            continue
        if emotion_delta_worth(prev, emo_like):
            keep.append(p)
            prev = emo_like
    return keep[-max_n:]


def attach_day_memory(day: dict, *, core_events: list[dict] | None = None,
                      emotion_points: list[dict] | None = None,
                      week: str | None = None) -> dict:
    """写入/合并 day.memory（就地改 day，并返回 memory 块）。"""
    mem = dict(day.get("memory") or {})
    mem["week"] = week or week_id(day.get("date"))
    prior = {_norm(e.get("text") or "") for e in (mem.get("core_events") or [])}
    new_cores = filter_core_deltas(core_events or [], prior)
    if new_cores:
        mem["core_events"] = list(mem.get("core_events") or []) + new_cores
    emos = filter_emotion_deltas(
        list(mem.get("emotion") or []) + list(emotion_points or [])
    )
    if emos:
        mem["emotion"] = emos
    # 空块不挂，保持日程干净
    if mem.get("core_events") or mem.get("emotion"):
        day["memory"] = mem
    return mem


def upsert_week_ledger(doc: dict, week: str, *, core_events: list[dict],
                       emotion_points: list[dict], summary: str = "") -> dict:
    """文档级 memory_ledger.weeks[]：每周一条汇总（人读主账本）。"""
    ledger = dict(doc.get("memory_ledger") or {"version": 1, "weeks": []})
    weeks = list(ledger.get("weeks") or [])
    prior = collect_prior_norms(doc)
    cores = filter_core_deltas(core_events, prior)
    emos = filter_emotion_deltas(emotion_points)
    entry = {
        "week": week,
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
        "summary": (summary or "").strip(),
        "core_events": cores,
        "emotion": emos,
    }
    # 替换同 week
    weeks = [w for w in weeks if w.get("week") != week] + [entry]
    weeks.sort(key=lambda w: w.get("week") or "")
    # 默认保留最近 26 周
    keep_n = int((ledger.get("keep_weeks") or 26))
    ledger["weeks"] = weeks[-keep_n:]
    ledger["version"] = int(ledger.get("version") or 1)
    doc["memory_ledger"] = ledger
    return entry


def format_memory_lines(day: dict | None = None, ledger_week: dict | None = None,
                        *, budget: int = 280) -> list[str]:
    """给 brain.state_block：只吐差分，不复述上课通勤。"""
    lines = []
    cores, emos = [], []
    if day and day.get("memory"):
        cores.extend(day["memory"].get("core_events") or [])
        emos.extend(day["memory"].get("emotion") or [])
    if ledger_week:
        cores.extend(ledger_week.get("core_events") or [])
        emos.extend(ledger_week.get("emotion") or [])
        if ledger_week.get("summary"):
            lines.append("本周关系摘要：" + ledger_week["summary"][:120])
    # 去重文本
    seen = set()
    core_bits = []
    for e in cores:
        t = (e.get("text") or "").strip()
        n = _norm(t)
        if not t or n in seen or is_routine_text(t):
            continue
        seen.add(n)
        core_bits.append(t)
    if core_bits:
        lines.append("过往核心（差分）：" + "；".join(core_bits[:6]))
    if emos:
        last = emos[-1]
        pad = last.get("pad") or {}
        bit = (f"情绪账本近点：{last.get('label', 'neutral')} "
               f"P{pad.get('pleasure', 0):+.2f}/A{pad.get('arousal', 0):+.2f} "
               f"亲密度{last.get('intimacy', '?')}")
        if last.get("note"):
            bit += f"（{last['note'][:40]}）"
        lines.append(bit)
    # budget
    out, used = [], 0
    for ln in lines:
        if used + len(ln) > budget:
            break
        out.append(ln)
        used += len(ln)
    return out


def latest_ledger_week(doc: dict | None, week: str | None = None) -> dict | None:
    weeks = ((doc or {}).get("memory_ledger") or {}).get("weeks") or []
    if not weeks:
        return None
    if week:
        for w in weeks:
            if w.get("week") == week:
                return w
    return weeks[-1]


# --- 可选二级：把差分摘要嵌进 mem0（不替代日程账本） ---
OPTIONAL_MEM0_KIND_CORE = "schedule_core"
OPTIONAL_MEM0_KIND_EMO = "schedule_emotion"


def mem0_payloads_from_week(entry: dict) -> list[tuple[str, str, int]]:
    """返回 [(text, kind, importance), ...] 供调用方 mem.add；本模块不碰 Qdrant。"""
    out = []
    for e in entry.get("core_events") or []:
        t = (e.get("text") or "").strip()
        if t:
            out.append((f"[核心·{entry.get('week')}] {t}", OPTIONAL_MEM0_KIND_CORE,
                        int(e.get("importance") or 7)))
    emos = entry.get("emotion") or []
    if emos:
        last = emos[-1]
        pad = last.get("pad") or {}
        text = (f"[情绪·{entry.get('week')}] {last.get('label')} "
                f"P{pad.get('pleasure', 0):+.2f} A{pad.get('arousal', 0):+.2f} "
                f"亲密度{last.get('intimacy')}"
                + (f" {last['note']}" if last.get("note") else ""))
        out.append((text, OPTIONAL_MEM0_KIND_EMO, 6))
    if entry.get("summary"):
        out.append((f"[周摘要·{entry.get('week')}] {entry['summary']}", OPTIONAL_MEM0_KIND_CORE, 8))
    return out
