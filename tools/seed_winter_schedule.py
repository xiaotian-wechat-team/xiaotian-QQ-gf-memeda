#!/usr/bin/env python3
"""按合理度重建寒假行程弧，闸门通过后写入 life_schedule。

弧线默认（可改）：
  崇礼滑雪 ~4d → 交通日 → 禾木/喀纳斯 ~7d → 交通日 → 三亚 ~12d
  （窗末可接出国短途备选，不默认硬塞）

用法（仓库根，不启 daemon）:
  .venv/bin/python tools/seed_winter_schedule.py plan
  .venv/bin/python tools/seed_winter_schedule.py apply          # 需通过 reasonableness
  .venv/bin/python tools/seed_winter_schedule.py apply --force  # 仍写入但记 fail
  .venv/bin/python tools/seed_winter_schedule.py apply  # local deterministic checks
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SCHEDULE = ROOT / "data" / "life_schedule_2026q4.json"


def _s(start, end, activity, tone, bucket, hints=None, photo_ok=True, energy=0, **extra):
    d = {
        "start": start, "end": end, "activity": activity, "tone": tone,
        "bucket": bucket, "scene_hints": hints or [], "photo_ok": photo_ok,
        "energy": energy, "delay": [10, 120] if photo_ok else [20, 180],
    }
    d.update(extra)
    return d


def _day(date: dt.date, kind: str, photo_bias: str, slots: list, notes: str = "",
         activity_id: str | None = None, reasonableness: dict | None = None):
    wd = date.weekday()
    d = {
        "date": date.isoformat(),
        "weekday": wd,
        "weekday_zh": "一二三四五六日"[wd],
        "kind": kind,
        "holiday": None,
        "photo_bias": photo_bias,
        "slots": slots,
        "notes": notes or None,
    }
    if activity_id:
        d["activity_id"] = activity_id
    if reasonableness:
        d["reasonableness"] = reasonableness
    return d


def build_arc(start: dt.date = dt.date(2027, 1, 19)) -> list[dict]:
    """崇礼4 + 交通1 + 禾木7 + 交通1 + 三亚12 = 25 天 → 至 2/12。"""
    import reasonableness as rz
    by = rz.activities_by_id()
    days: list[dict] = []
    d = start

    def rz_meta(aid: str) -> dict:
        act = by.get(aid) or {}
        meta = dict(act.get("reasonableness") or {})
        meta["activity_id"] = aid
        meta["rating"] = rz.activity_rating(act)
        return meta

    # --- 崇礼 4 天 ---
    ski = [
        ("高铁/大巴去崇礼雪场", "兴奋、发定位", "travel", ["高铁", "行李箱"],
         "崇礼太舞/富龙初体验滑雪", "摔了好几跤还笑", "outdoor_outing", ["滑雪", "崇礼"]),
        ("崇礼云顶/翠云山整日滑", "终于能转弯了", "outdoor_outing", ["滑雪", "雪道"],
         "雪场小镇泡脚+夜滑体验", "腿在抖但开心", "city_fun", ["夜滑", "小镇"]),
        ("万龙/太舞进阶绿蓝道", "敢上一点坡了", "outdoor_outing", ["滑雪", "雪道"],
         "雪具厅复盘+热奶茶", "晒伤了哈哈", "travel", ["民宿", "雪夜"]),
        ("上午半日补滑+收拾", "意犹未尽", "outdoor_outing", ["滑雪"],
         "返京休整、洗装备", "特种兵前夜", "travel", ["行李箱"]),
    ]
    for i, row in enumerate(ski):
        a1, t1, b1, h1, a2, t2, b2, h2 = row
        days.append(_day(
            d, "winter_break", "outdoor_outing" if i < 3 else "travel",
            [
                _s("00:00", "09:00", "睡觉", "迷迷糊糊", "sleep", photo_ok=False, energy=-4),
                _s("09:00", "13:00", a1, t1, b1, h1, energy=1),
                _s("13:00", "18:30", a2, t2, b2, h2, energy=2 if "滑" in a2 else 1),
                _s("18:30", "22:30", "雪场/宿舍晚饭休整", "想发照片", "weekend_home", ["晚饭"], energy=0),
                _s("22:30", "24:00", "准备睡", "困", "sleep", photo_ok=False, energy=-2),
            ],
            notes=f"reasonableness:ski_chongli day {i+1}/4",
            activity_id="ski_chongli",
            reasonableness=rz_meta("ski_chongli"),
        ))
        d += dt.timedelta(days=1)

    # --- 交通日：京 → 乌市/阿勒泰 ---
    days.append(_day(
        d, "winter_break", "travel",
        [
            _s("00:00", "06:30", "睡觉", "赶早班机", "sleep", photo_ok=False, energy=-4),
            _s("06:30", "14:00", "北京→乌鲁木齐飞行日", "特种兵但兴奋", "travel", ["机场", "转机"], energy=0),
            _s("14:00", "20:00", "乌市休整/转阿勒泰准备", "窗外开始变白", "travel", ["机场", "行李箱"], energy=0),
            _s("20:00", "24:00", "过夜、早睡", "养体力", "sleep", photo_ok=False, energy=-3),
        ],
        notes="transit after ski before hemu",
        activity_id="transit_day",
        reasonableness=rz_meta("transit_day"),
    ))
    d += dt.timedelta(days=1)

    # --- 禾木 7 天 ---
    hemu = [
        ("乌市/阿勒泰进山路上", "窗外全是白", "travel", ["雪原", "车窗"],
         "抵达禾木村·木屋安顿", "美到说不出话", "travel", ["禾木", "木屋"]),
        ("哈登观景台晨雾摄影", "手冻红了", "outdoor_outing", ["禾木", "晨雾", "雪山"],
         "禾木桥+白桦林慢逛", "像童话", "outdoor_outing", ["禾木桥", "白桦林"]),
        ("吉克普林/雪地项目体验", "喘但风景值了", "outdoor_outing", ["滑雪", "雪地摩托"],
         "木屋火锅夜", "暖和回来了", "travel", ["木屋", "火锅"]),
        ("喀纳斯方向雪地徒步", "镜头都起雾", "outdoor_outing", ["喀纳斯", "雪地徒步"],
         "老村民俗喝茶", "听故事", "travel", ["图瓦", "奶茶"]),
        ("白哈巴/边境村落半日", "风好大", "outdoor_outing", ["白哈巴", "雪村"],
         "回禾木拍蓝调夜景", "灯暖雪冷", "travel", ["夜景", "木屋"]),
        ("洪巴斯/美丽峰方向", "骑马看雪山", "outdoor_outing", ["雪山", "草原"],
         "村里小店淘伴手礼", "奶疙瘩", "city_fun", ["伴手礼"]),
        ("最后晨雾+收拾出村", "依依不舍", "outdoor_outing", ["晨雾", "禾木"],
         "出山到阿勒泰休整", "腿酸幸福", "travel", ["车窗"]),
    ]
    for i, row in enumerate(hemu):
        a1, t1, b1, h1, a2, t2, b2, h2 = row
        days.append(_day(
            d, "winter_break", "outdoor_outing" if i < 6 else "travel",
            [
                _s("00:00", "08:30", "睡觉", "被窝才是真理", "sleep", photo_ok=False, energy=-4),
                _s("08:30", "13:00", a1, t1, b1, h1, energy=2),
                _s("13:00", "18:30", a2, t2, b2, h2, energy=1),
                _s("18:30", "22:00", "木屋晚饭休整", "想发照片", "travel", ["木屋"], energy=0),
                _s("22:00", "24:00", "早睡养体力", "困", "sleep", photo_ok=False, energy=-2),
            ],
            notes=f"reasonableness:travel_hemu day {i+1}/7",
            activity_id="travel_hemu",
            reasonableness=rz_meta("travel_hemu"),
        ))
        d += dt.timedelta(days=1)

    # --- 交通日：阿勒泰 → 三亚 ---
    days.append(_day(
        d, "winter_break", "travel",
        [
            _s("00:00", "05:30", "睡觉", "赶早班机", "sleep", photo_ok=False, energy=-4),
            _s("05:30", "16:00", "阿勒泰→乌市→三亚转机日", "外套一层层脱", "travel", ["机场", "转机"], energy=0),
            _s("16:00", "20:00", "抵达三亚·海棠湾方向入住", "外套瞬间多余", "travel", ["海边", "民宿"], energy=1),
            _s("20:00", "24:00", "海鲜晚饭+早睡", "咸湿空气", "city_fun", ["海鲜"], energy=1),
        ],
        notes="transit hemu → sanya",
        activity_id="transit_day",
        reasonableness=rz_meta("transit_day"),
    ))
    d += dt.timedelta(days=1)

    # --- 三亚 12 天 ---
    sanya = [
        ("海边浮浅/沙滩漫步", "咸湿空气好爽", "outdoor_outing", ["沙滩", "海岛"]),
        ("蜈支洲/西岛方向", "玻璃海本人", "outdoor_outing", ["海岛", "浮浅"]),
        ("亚龙湾躺平日", "晒黑一点点", "outdoor_outing", ["亚龙湾", "沙滩"]),
        ("呀诺达雨林半日", "终于不是海了", "outdoor_outing", ["雨林"]),
        ("海岸线 citywalk+海鲜", "吃到停不下来", "city_fun", ["海鲜", "夜市"]),
        ("民宿阳台发呆拍照", "懒得移动", "travel", ["民宿", "阳台"]),
        ("天涯/镇海方向慢游", "毕业感照片", "outdoor_outing", ["天涯", "海岸"]),
        ("冲浪课初体验/看浪", "摔进海里也笑", "outdoor_outing", ["冲浪", "后海"]),
        ("免税店浅逛+咖啡", "只看不买（大概）", "city_fun", ["免税城"]),
        ("椰梦长廊日落专场", "橘子海", "outdoor_outing", ["日落", "椰林"]),
        ("和同学视频安利行程", "想再待一周", "weekend_home", ["宿舍感", "视频"]),
        ("收尾打包+海边最后一逛", "皮肤盐粒感", "travel", ["行李箱", "海边"]),
    ]
    for i, (act, tone, bucket, hints) in enumerate(sanya):
        days.append(_day(
            d, "winter_break", bucket if bucket != "weekend_home" else "outdoor_outing",
            [
                _s("00:00", "09:30", "睡觉", "海风助眠", "sleep", photo_ok=False, energy=-4),
                _s("09:30", "13:00", "早午饭+防晒磨蹭", "懒洋洋", "weekend_home", ["民宿"], energy=0),
                _s("13:00", "18:30", act, tone, bucket, hints, energy=2 if "海" in act or "浪" in act else 1),
                _s("18:30", "22:30", "夜宵/夜市或回民宿", "想发照片", "city_fun", ["夜市", "海鲜"], energy=1),
                _s("22:30", "24:00", "准备睡", "困", "sleep", photo_ok=False, energy=-2),
            ],
            notes=f"reasonableness:travel_sanya day {i+1}/12",
            activity_id="travel_sanya",
            reasonableness=rz_meta("travel_sanya"),
        ))
        d += dt.timedelta(days=1)

    return days


def merge_into_schedule(arc: list[dict], *, extend_end: bool = True) -> dict:
    sch = json.loads(SCHEDULE.read_text())
    by = {d["date"]: d for d in sch.get("days") or []}
    for d in arc:
        by[d["date"]] = d
    days = [by[k] for k in sorted(by)]
    sch["days"] = days
    if extend_end and arc:
        end = arc[-1]["date"]
        sch.setdefault("range", {})
        if end > sch["range"].get("end", ""):
            sch["range"]["end"] = end
        start = sch["range"].get("start")
        if start:
            s = dt.date.fromisoformat(start)
            e = dt.date.fromisoformat(sch["range"]["end"])
            sch["range"]["days"] = (e - s).days + 1
    enr = sch.setdefault("enrichment", {})
    enr["reasonableness_seed"] = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    enr["winter_arc"] = {
        "ski_chongli": 4, "transit": 2, "travel_hemu": 7, "travel_sanya": 12,
        "tool": "tools/seed_winter_schedule.py",
    }
    return sch


def cmd_plan(_a):
    import reasonableness as rz
    arc = build_arc()
    print(f"arc {arc[0]['date']} → {arc[-1]['date']} ({len(arc)} days)")
    for d in arc:
        acts = " / ".join(s["activity"] for s in d["slots"] if s.get("bucket") != "sleep")
        print(f"  {d['date']} [{d.get('activity_id')}] {acts[:100]}")
    ok, res = rz.may_enter_schedule(arc, log=False)
    print("reasonableness ok=", ok, "score=", res.get("score"), "engine=", res.get("engine"))
    for iss in res.get("issues") or []:
        print(" ", iss.get("severity"), iss.get("code"), iss.get("msg"))
    print("segments:")
    for s in res.get("segments") or []:
        print(f"  {s['start']}..{s['end']} ({s['n_days']}d) {s['name']}")
    hook = res.get("agent_hook") or {}
    print("agent_hook available=", hook.get("available"), "bin=", hook.get("bin"),
          "schedule checks are local; no review Agent")
    return 0 if ok else 1


def cmd_apply(args):
    import reasonableness as rz

    arc = build_arc()
    ok, res = rz.may_enter_schedule(arc, use_agent=None, log=True)
    print("reasonableness ok=", ok, "score=", res.get("score"))
    for iss in res.get("issues") or []:
        print(" ", iss.get("severity"), iss.get("code"), iss.get("msg"))
    if not ok and not args.force:
        print("REFUSED write: fix issues or pass --force", file=sys.stderr)
        return 2
    sch = merge_into_schedule(arc)
    sch.setdefault("enrichment", {})["reasonableness_review"] = {
        "ok": ok, "score": res.get("score"), "engine": res.get("engine"),
        "issues": res.get("issues"), "forced": bool(args.force and not ok),
    }
    SCHEDULE.write_text(json.dumps(sch, ensure_ascii=False, indent=2) + "\n")
    print("WROTE", SCHEDULE, "days", len(sch["days"]), "end", sch["range"]["end"])

    # queue scene refs for travel activities (4×)
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from queue_scene_refs import enqueue_for_activities
        q = enqueue_for_activities(
            ["ski_chongli", "travel_hemu", "travel_sanya",
             "travel_tokyo_winter", "travel_seoul_winter",
             "travel_chiangmai", "travel_bali", "travel_hokkaido_ski",
             "travel_singapore"]
        )
        print("scene_refs queued:", q)
    except Exception as e:
        print("scene_refs queue skip:", e)
    return 0 if ok else 3


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(func=cmd_plan)
    p = sub.add_parser("apply")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_apply)
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
