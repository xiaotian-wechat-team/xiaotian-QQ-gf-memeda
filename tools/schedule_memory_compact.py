#!/usr/bin/env python3
"""把本周聊天/情绪差分挂到 life_schedule 账本（信息论：只记差异）。

默认 --dry-run，不写盘、不碰 mem0、不启 wechat。
真正写入：去掉 --dry-run；可选 --mem0 把摘要同步为二级向量。

  .venv/bin/python tools/schedule_memory_compact.py              # dry-run 本周
  .venv/bin/python tools/schedule_memory_compact.py --apply      # 写入日程 JSON
  .venv/bin/python tools/schedule_memory_compact.py --apply --mem0
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import schedule_memory as sm
import life_calendar as lc
from config import load

# 例行事实抽取候选的廉价启发（无 LLM 时）；有 LLM 再走 mem.extract_facts
def _chat_lines(cid: str, start: dt.date, end: dt.date) -> list[str]:
    import memory
    c = memory.db()
    rows = c.execute(
        "SELECT sender,text,substr(ts,1,10) FROM messages WHERE contact=? AND substr(ts,1,10)>=? AND substr(ts,1,10)<=? ORDER BY id",
        (cid, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [f"{'小甜' if s == 'me' else '他'}({d}): {t}" for s, t, d in rows]


def _week_bounds(d: dt.date) -> tuple[dt.date, dt.date]:
    # ISO: Monday=1 … Sunday=7
    start = d - dt.timedelta(days=d.isoweekday() - 1)
    end = start + dt.timedelta(days=6)
    return start, end


def _candidate_facts(chat: list[str], who: str) -> list[dict]:
    """优先 LLM 抽取；失败则空（绝不把例行日程当事实灌进账本）。"""
    if not chat:
        return []
    try:
        import mem
        facts = mem.extract_facts(chat[-200:], who=who)
        mem.release()
        return [sm.core_event(f["fact"], importance=int(f.get("importance") or 5),
                              source="compact", date=dt.date.today().isoformat())
                for f in facts if f.get("fact")]
    except Exception as e:
        print(f"[compact] extract_facts skip: {e}", file=sys.stderr)
        return []


def _emotion_snapshot(cid: str) -> list[dict]:
    import memory, emotion
    st = memory.read_state(cid)
    emo = st.get("emotion") or emotion.default()
    return [sm.summarize_emotion_point(emo)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(dt.date.today()), help="落在该 ISO 周内的任意一天")
    ap.add_argument("--apply", action="store_true", help="写入 life_schedule JSON（默认只预览）")
    ap.add_argument("--mem0", action="store_true", help="额外写入 mem0 二级索引（需 --apply）")
    ap.add_argument("--summary", default="", help="可选本周一句话摘要")
    args = ap.parse_args()
    cfg = load()
    cid = cfg["contact"]["id"]
    who = cfg["contact"].get("name") or "小熊"
    day0 = dt.date.fromisoformat(args.date[:10])
    start, end = _week_bounds(day0)
    wid = sm.week_id(day0)
    chat = _chat_lines(cid, start, end)
    print(f"[compact] week={wid} {start}..{end} chat_lines={len(chat)} apply={args.apply}")

    cores = _candidate_facts(chat, who) if chat else []
    emos = _emotion_snapshot(cid)
    # 无聊天时仍可记情绪差分（相对 ledger 上一点）
    doc = lc._load_draft() or {"days": [], "memory_ledger": {"version": 1, "weeks": []}}
    prior = sm.collect_prior_norms(doc)
    cores_f = sm.filter_core_deltas(cores, prior)
    print("[compact] core candidates:", json.dumps(cores_f, ensure_ascii=False, indent=2))
    print("[compact] emotion points:", json.dumps(emos, ensure_ascii=False, indent=2))

    if not args.apply:
        preview = sm.upsert_week_ledger(
            {"days": doc.get("days"), "memory_ledger": json.loads(json.dumps(doc.get("memory_ledger") or {"version": 1, "weeks": []}))},
            wid, core_events=cores_f, emotion_points=emos, summary=args.summary,
        )
        print("[compact] dry-run week entry:", json.dumps(preview, ensure_ascii=False, indent=2))
        print("[compact] prompt lines:", sm.format_memory_lines(None, preview))
        return

    # 挂到本周每天（有聊天日）+ 周 ledger
    by_date = {d.get("date"): d for d in doc.get("days") or []}
    # 把核心事件挂在 end（周日）那天，便于人读
    anchor = by_date.get(end.isoformat()) or by_date.get(day0.isoformat())
    if anchor is not None:
        sm.attach_day_memory(anchor, core_events=cores_f, emotion_points=emos, week=wid)
    entry = sm.upsert_week_ledger(doc, wid, core_events=cores_f, emotion_points=emos, summary=args.summary)
    path = lc.DRAFT_FILE
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"[compact] wrote {path} week={wid} cores={len(entry.get('core_events') or [])}")

    if args.mem0:
        import mem
        for text, kind, imp in sm.mem0_payloads_from_week(entry):
            mem.add(cid, text, importance=imp, kind=kind, source=f"schedule_memory:{wid}", ts=end.isoformat())
        mem.release()
        print("[compact] mem0 secondary index updated")


if __name__ == "__main__":
    main()
