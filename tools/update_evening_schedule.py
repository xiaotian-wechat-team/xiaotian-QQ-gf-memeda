#!/usr/bin/env python3
"""按用户要求更新未来晚间计划，修复旧调研注入；不发消息、不写 STATE。

默认预览；--apply 写日程。冻结月份需显式 --include-frozen，保留原冻结记录。
"""
from __future__ import annotations
import argparse
import copy
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import evening_plan as ep
import life_calendar as lc
import reasonableness as rz
import schedule_pipeline as sp


def update(doc: dict, start: dt.date, *, include_frozen: bool = False,
           details_only: bool = False) -> tuple[dict, dict]:
    result = copy.deepcopy(doc)
    original = {d["date"]: d for d in doc["days"]}
    repaired = list((doc.get("enrichment") or {}).get("evening_update", {}).get("repaired_research_days", []))
    eligible = []
    for index, day in enumerate(result["days"]):
        date = dt.date.fromisoformat(day["date"])
        if date < start or (sp.is_frozen(doc, on=date) and not include_frozen):
            continue
        if (day.get("source") == "user_override" or day.get("trip_id")
                or any(s.get("source") == "user_override" for s in day["slots"])):
            continue
        if details_only:
            result['days'][index] = ep.detail_day(day, history=result['days'][:index])
            continue
        # 旧注入覆盖了饭点和睡前槽，恢复本地一天的完整骨架。
        if any(s.get("source") == "research_inject" for s in day["slots"]):
            day = lc.generate_day(date)
            result["days"][index] = day
            if day["date"] not in repaired:
                repaired.append(day["date"])
        if day["date"] in repaired:
            day = ep.local_weekend_afternoon(day)
            result["days"][index] = day
        if not ep.protected(day):
            eligible.append(index)
        # 学业日只补已入睡后到午夜的缺口；完整旅行弧保持原样。
        elif not day.get("activity_id"):
            ep.complete_sleep(day)
    plans = {}
    for index in eligible:
        day = result["days"][index]
        date = dt.date.fromisoformat(day["date"])
        monday = date - dt.timedelta(days=date.weekday())
        if monday not in plans:
            plans[monday] = ep.week_plan(date, result["days"])
        weekend = ep.weekend_plan(date, result["days"]) if day["kind"] == "weekend" else None
        result["days"][index] = ep.enrich_day(day, selected=plans[monday], weekend_selected=weekend,
                                                    history=[d for d in result["days"] if d["date"] < day["date"]])
    changed = [d["date"] for d in result["days"] if d != original[d["date"]]]
    ok, review = rz.may_enter_schedule(result["days"], use_agent=False, log=False)
    if not ok:
        raise ValueError(json.dumps(review["issues"], ensure_ascii=False))
    rules = result.setdefault("rules", {})
    rules["workday"] = rules.get("workday", "").replace(
        "平日晚娱乐稀疏（约≤35%工作日有一晚 city_fun），多数晚宿舍冲浪/自习/聊天",
        "平日按空闲与疲劳每周选1–2晚短时外出（不固定星期），其余晚保留休息；独立往返与回寝槽")
    rules["after_horizon"] = "life_calendar.generate_range 生成学业/实习骨架与本地活动；完整跨城旅行由日程补充 Agent 连同多日停留和交通安排"
    rules["evening"] = (
        "工作日有空档时每周1–2晚外出，按疲劳降权、不固定星期；多数活动30–90分钟，"
        "逛街/夜景/唱歌/电影/轻娱乐分组采样，校园散步基础权重2/100；近14天重复降权；"
        "已有晚实习可选夜间外拍；独立往返与回寝槽，保留休息晚。"
        "调研仅提供灵感，跨城必须完整多日交通与停留计划；无独立review Agent。")
    result.setdefault("enrichment", {})["evening_update"] = {
        "from_date": start.isoformat(), "changed_days": changed, "repaired_research_days": repaired,
        "frozen_change_authorized": include_frozen, "local_checks_ok": ok,
        "details_only": details_only,
    }
    return result, dict(changed_days=len(changed), repaired_research_days=repaired,
                        local_checks_ok=ok, issues=review["issues"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--include-frozen", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--details-only", action="store_true", help="细化原计划；保留日期、活动和全部时刻，不重新抽签")
    args = parser.parse_args()
    doc = sp.load_schedule()
    result, report = update(doc, dt.date.fromisoformat(args.from_date), include_frozen=args.include_frozen,
                            details_only=args.details_only)
    if args.apply:
        sp.save_schedule(result)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
