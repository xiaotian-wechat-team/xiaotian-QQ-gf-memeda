#!/usr/bin/env python3
"""调研小红书+抖音 → 丰富 life_schedule / 打印池子建议。

用法（仓库根）:
  .venv/bin/python tools/enrich_life_from_research.py research [--force] [--topics ...]
  .venv/bin/python tools/enrich_life_from_research.py plan
  .venv/bin/python tools/enrich_life_from_research.py apply --write-schedule

不启动 wechat daemon。采集只读，走 collectors 锁与 source_poll。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_TOPICS = [
    ("xiaohongshu", "北京 海淀 学校附近 饭后散步 书店"),
    ("douyin", "大学生 室友 逛街 饮料 小店"),
    ("xiaohongshu", "北京 电影 小剧场 桌游 晚间"),
    ("douyin", "大学生 周末 手作 逛展"),
    ("xiaohongshu", "北京 周边 一日游 往返交通"),
    ("douyin", "假期 旅行 多日 行程 交通"),
]

OUT = ROOT / "data" / "collect_runs" / "life_enrich_research.json"
SCHEDULE = ROOT / "data" / "life_schedule_2026q4.json"


def cmd_research(args):
    from collectors.xiaohongshu import XHS
    from collectors.douyin import Douyin
    from collectors.runner import global_lock, Busy

    topics = []
    if args.topics:
        # alternate platforms
        plats = ["xiaohongshu", "douyin"]
        for i, t in enumerate(args.topics):
            topics.append((plats[i % 2], t))
    else:
        topics = list(DEFAULT_TOPICS)

    out = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": [], "ideas": []}
    try:
        with global_lock(wait_s=5):
            for i, (plat, topic) in enumerate(topics):
                if i:
                    time.sleep(args.gap)
                cls = XHS if plat == "xiaohongshu" else Douyin
                print(f"=== {plat} :: {topic} ===", flush=True)
                rep = cls(topic=topic).run(force=args.force, do_ocr=False, do_lcu=False)
                slim = {k: rep.get(k) for k in ("platform", "items", "status", "samples", "dom_list", "media")}
                print(json.dumps(slim, ensure_ascii=False), flush=True)
                out["runs"].append({"topic": topic, **slim})
                for t in rep.get("samples") or []:
                    if t:
                        out["ideas"].append({"platform": plat, "topic": topic, "title": t})
    except Busy as e:
        out["error"] = f"busy:{e}"
        print("BUSY", e, file=sys.stderr)
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print("WROTE", OUT, "ideas", len(out["ideas"]))
    # Step1→池：ideas upsert into activity_pool.json
    try:
        import activity_pool_upsert as apu
        st = apu.upsert_ideas(out.get("ideas") or [], write=True)
        print("pool_upsert matched", st.get("matched"), "created", st.get("created"),
              "touched", st.get("touched_ids"))
    except Exception as e:
        print("pool_upsert skip:", e)
    # Step2 钩子：时长待填清单（Cursor: reasonableness.py --pool-duration）
    try:
        import schedule_pipeline as sp
        todo = sp.fill_pool_duration_stub(out.get("ideas") or [])
        print("pool_duration_todo", todo.get("missing_typical_days"), "→ data/collect_runs/pool_duration_todo.json")
    except Exception as e:
        print("pool_duration_todo skip:", e)
    return 0


def cmd_plan(_args):
    import life_calendar as lc

    print("WEEKDAY_EVE_POOL", len(lc.WEEKDAY_EVE_POOL))
    for x in lc.WEEKDAY_EVE_POOL:
        print(" ", x[0], "→", x[1])
    print("WEEKEND_OUT_POOL", len(lc.WEEKEND_OUT_POOL))
    for x in lc.WEEKEND_OUT_POOL:
        print(" ", x[0], "→", x[1])
    print("WINTER_TRAVEL_POOL", len(getattr(lc, "WINTER_TRAVEL_POOL", [])))
    for x in getattr(lc, "WINTER_TRAVEL_POOL", []):
        print(" ", x[0], "→", x[1])
    if OUT.exists():
        doc = json.loads(OUT.read_text())
        print("research ideas", len(doc.get("ideas") or []))
        for it in (doc.get("ideas") or [])[:12]:
            print(" ", it.get("platform"), (it.get("title") or "")[:70])
    if SCHEDULE.exists():
        sch = json.loads(SCHEDULE.read_text())
        print("schedule winter sample:")
        for d in sch.get("days") or []:
            if d.get("kind") == "winter_break" or d.get("date", "") >= "2027-01-19":
                acts = " / ".join(s["activity"] for s in d["slots"] if s.get("bucket") != "sleep")
                print(" ", d["date"], acts)
    # generate a few post-horizon winter days
    import datetime as dt
    print("generate_day winter samples:")
    for ds in ("2027-01-25", "2027-01-28", "2027-02-01"):
        d = lc.generate_day(dt.date.fromisoformat(ds))
        acts = " / ".join(s["activity"] for s in d["slots"] if s.get("bucket") != "sleep")
        print(" ", ds, d["kind"], "|", acts)
    return 0


def cmd_apply(args):
    """apply 以 plan 为主；--write-schedule 时提醒草稿已由 enrich 流程维护。"""
    cmd_plan(args)
    if args.write_schedule:
        if not SCHEDULE.exists():
            print("missing", SCHEDULE, file=sys.stderr)
            return 1
        sch = json.loads(SCHEDULE.read_text())
        meta = sch.setdefault("enrichment", {})
        meta["last_apply"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta["tool"] = "tools/enrich_life_from_research.py"
        SCHEDULE.write_text(json.dumps(sch, ensure_ascii=False, indent=2) + "\n")
        print("updated enrichment.last_apply on", SCHEDULE)
    print("Pools live in src/life_calendar.py; draft days in data/life_schedule_2026q4.json")
    print("Re-run research then hand-edit / re-seed draft if you need a full rewrite.")
    return 0


def cmd_upsert_pool(args):
    import activity_pool_upsert as apu
    import json as _json
    st = apu.upsert_ideas(write=not args.dry_run)
    st.pop("doc", None)
    print(_json.dumps(st, ensure_ascii=False, indent=2))
    return 0



def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_r = sub.add_parser("research", help="XHS+Douyin topic collect")
    p_r.add_argument("--force", action="store_true")
    p_r.add_argument("--gap", type=float, default=8.0)
    p_r.add_argument("--topics", nargs="*", help="override topic strings (platforms alternate)")
    p_r.set_defaults(func=cmd_research)

    p_p = sub.add_parser("plan", help="print pools + winter samples")
    p_p.set_defaults(func=cmd_plan)

    p_a = sub.add_parser("apply", help="stamp enrichment meta; show plan")
    p_a.add_argument("--write-schedule", action="store_true")
    p_a.set_defaults(func=cmd_apply)

    p_u = sub.add_parser("upsert-pool", help="upsert research ideas into activity_pool.json")
    p_u.add_argument("--dry-run", action="store_true")
    p_u.set_defaults(func=cmd_upsert_pool)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
