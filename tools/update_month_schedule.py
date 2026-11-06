#!/usr/bin/env python3
"""每周：更新/审查下月日程 → FREEZE → sync_to_memory 喂小甜。

也覆盖用户 override 后门与学业优先检查（见 src/schedule_pipeline.py）。

用法（仓库根，不启 daemon）:
  .venv/bin/python tools/update_month_schedule.py plan --month 2026-11
  .venv/bin/python tools/update_month_schedule.py apply --month 2026-11
  .venv/bin/python tools/update_month_schedule.py freeze --month 2026-11
  .venv/bin/python tools/update_month_schedule.py sync
  .venv/bin/python tools/update_month_schedule.py override-begin --text '下周陪我出门'
  .venv/bin/python tools/update_month_schedule.py override-confirm --reply '周六下午三点三里屯' \\
      --date 2026-11-01 --start 15:00 --end 19:00 --activity '和他三里屯逛街' --route 三里屯
  .venv/bin/python tools/update_month_schedule.py override-apply
  .venv/bin/python tools/update_month_schedule.py study-check --date 2027-01-15

不启动 wechat daemon / launchd。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _parse_month(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


def _next_month(today: dt.date | None = None) -> tuple[int, int]:
    today = today or dt.date.today()
    if today.month == 12:
        return today.year + 1, 1
    return today.year, today.month + 1


def cmd_plan(args):
    import schedule_pipeline as sp
    import reasonableness as rz

    y, m = _parse_month(args.month) if args.month else _next_month()
    doc = sp.load_schedule()
    yld = sp.review_agents_must_yield(doc)
    print("month", f"{y:04d}-{m:02d}")
    print("frozen", doc.get("frozen"))
    print("agents_yield", yld)
    res = sp.ensure_month_days(y, m, doc=doc, write=False)
    print("would_add", len(res.get("added") or []), res.get("added")[:8], "..." if len(res.get("added") or []) > 8 else "")
    # dry reasonableness on that month slice
    days = [d for d in (res.get("doc") or doc).get("days") or []
            if d["date"].startswith(f"{y:04d}-{m:02d}")]
    ok, rev = rz.may_enter_schedule(days, use_agent=False, log=False)
    print("reasonableness ok=", ok, "score=", rev.get("score"), "n=", len(days))
    for iss in (rev.get("issues") or [])[:8]:
        print(" ", iss.get("severity"), iss.get("code"), iss.get("msg"))
    return 0


def cmd_apply(args):
    import schedule_pipeline as sp
    import reasonableness as rz

    y, m = _parse_month(args.month) if args.month else _next_month()
    doc = sp.load_schedule()
    yld = sp.review_agents_must_yield(doc)
    if yld["yield"] and "user_override_pending" in yld["reasons"]:
        print("REFUSED: user override pending — agents must yield", file=sys.stderr)
        return 3
    if sp.is_frozen(doc, on=dt.date(y, m, 1)) and not args.force:
        print("REFUSED: month frozen; pass --force to rewrite or unfreeze first", file=sys.stderr)
        print(json.dumps(doc.get("frozen"), ensure_ascii=False))
        return 2

    fill = sp.ensure_month_days(
        y, m, doc=doc, write=True,
        inject_research=bool(args.inject_research),
        max_inject=int(args.max_inject),
    )
    if not fill.get("ok"):
        print("REFUSED fill", fill, file=sys.stderr)
        return 2
    print("filled", fill["month"], "added", len(fill["added"]))

    doc = sp.load_schedule()
    days = [d for d in doc.get("days") or [] if d["date"].startswith(f"{y:04d}-{m:02d}")]
    yld2 = sp.review_agents_must_yield(doc)
    ok, rev = rz.may_enter_schedule(days, use_agent=False, log=True)
    print("reasonableness ok=", ok, "score=", rev.get("score"), "engine=", rev.get("engine"))
    enr = doc.setdefault("enrichment", {})
    enr["last_month_review"] = {
        "month": f"{y:04d}-{m:02d}",
        "ok": ok,
        "score": rev.get("score"),
        "engine": rev.get("engine"),
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "agents_yielded": yld2,
    }
    sp.save_schedule(doc)
    if args.freeze_after and ok:
        fr = sp.freeze_month(y, m, note="weekly next-month update+freeze")
        print("FROZEN", fr)
    return 0 if ok else 1


def cmd_freeze(args):
    import schedule_pipeline as sp

    y, m = _parse_month(args.month) if args.month else _next_month()
    fr = sp.freeze_month(y, m, note=args.note or "weekly next-month update")
    print(json.dumps(fr, ensure_ascii=False, indent=2))
    return 0


def cmd_unfreeze(_args):
    import schedule_pipeline as sp

    print(json.dumps(sp.unfreeze(), ensure_ascii=False, indent=2))
    return 0


def cmd_sync(args):
    """FEED：把当前/冻结日程写入 STATE + mem0（不启 daemon）。"""
    import json as _json
    from pathlib import Path as _P

    cfg = _json.loads((_P(ROOT) / "config.json").read_text())
    cid = args.cid or cfg["wechat"]["target"]
    import life_calendar as lc

    payload = lc.sync_to_memory(cid, write_mem0=not args.no_mem0)
    meta = payload.get("life_plan_meta") or {}
    import schedule_pipeline as sp

    fr = sp.load_schedule().get("frozen")
    print("synced", cid, "meta", meta, "frozen", fr)
    return 0


def cmd_override_begin(args):
    import schedule_pipeline as sp

    pending = sp.begin_override_confirm(args.text, cid=args.cid)
    print(json.dumps(pending, ensure_ascii=False, indent=2))
    print("--- wechat_ask ---")
    print(pending["wechat_ask"])
    return 0


def cmd_override_confirm(args):
    import schedule_pipeline as sp

    pending = sp.confirm_override_from_user_reply(
        args.reply,
        date=args.date,
        start=args.start,
        end=args.end,
        activity=args.activity,
        route=args.route,
    )
    print(json.dumps(pending, ensure_ascii=False, indent=2))
    return 0


def cmd_override_apply(_args):
    import schedule_pipeline as sp

    res = sp.apply_override_slot()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_study_check(args):
    import schedule_pipeline as sp

    r = sp.study_priority_block(args.date, user_ask_outing=True)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if not r.get("block") else 1


def cmd_status(_args):
    import schedule_pipeline as sp

    doc = sp.load_schedule()
    print(json.dumps({
        "frozen": doc.get("frozen"),
        "overrides_n": len(doc.get("overrides") or []),
        "pending": sp.load_override_pending(),
        "agents_yield": sp.review_agents_must_yield(doc),
        "range": doc.get("range"),
        "duration_todo": sp.fill_pool_duration_stub(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_pool_upsert(args):
    import activity_pool_upsert as apu
    st = apu.upsert_ideas(write=not args.dry_run)
    st.pop("doc", None)
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0


def cmd_pool_duration(args):
    import reasonableness as rz
    if args.agent:
        os.environ["REASONABLENESS_AGENT"] = "1"
    r = rz.fill_pool_durations_with_agent(args.ids or None)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok") else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--month", help="YYYY-MM; default=next month")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply")
    p.add_argument("--month", help="YYYY-MM; default=next month")
    p.add_argument("--agent", action="store_true", help="legacy ignored flag; schedule checks are local")
    p.add_argument("--freeze-after", action="store_true")
    p.add_argument("--force", action="store_true", help="write even if frozen")
    p.add_argument("--inject-research", action="store_true",
                   help="inject XHS/Douyin research ideas into soft month slots")
    p.add_argument("--max-inject", type=int, default=6)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("freeze")
    p.add_argument("--month", help="YYYY-MM; default=next month")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_freeze)

    sub.add_parser("unfreeze").set_defaults(func=cmd_unfreeze)

    p = sub.add_parser("sync")
    p.add_argument("--cid")
    p.add_argument("--no-mem0", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("override-begin")
    p.add_argument("--text", required=True)
    p.add_argument("--cid")
    p.set_defaults(func=cmd_override_begin)

    p = sub.add_parser("override-confirm")
    p.add_argument("--reply", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--activity", required=True)
    p.add_argument("--route")
    p.set_defaults(func=cmd_override_confirm)

    sub.add_parser("override-apply").set_defaults(func=cmd_override_apply)

    p = sub.add_parser("study-check")
    p.add_argument("--date", required=True)
    p.set_defaults(func=cmd_study_check)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("pool-upsert", help="research ideas → activity_pool upsert")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_pool_upsert)

    p = sub.add_parser("pool-duration", help="duration-only fill on activity_pool (Cursor if AGENT=1)")
    p.add_argument("--agent", action="store_true")
    p.add_argument("--ids", nargs="*")
    p.set_defaults(func=cmd_pool_duration)


    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
