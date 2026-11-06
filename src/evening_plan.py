"""晚间日程：按周选择空闲晚，短活动为主，显式安排往返与回寝。

人设来源：data/persona_dossier.json。只生成计划，不操作聊天或记忆。
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import random
import json
from pathlib import Path


# Shared catalog drives schedule, outfit retrieval and reference collection.
CATALOG = json.loads((Path(__file__).resolve().parents[1] / "data/evening_activities.json").read_text())
ACTIVITIES = CATALOG["activities"]
SHORT_OUTINGS = tuple((a["id"], a["activity"], a["location"], a["duration_minutes"], a["transit_minutes"])
                      for a in ACTIVITIES if a["category"] != "night_work" and a["duration_minutes"][0] < 90)
LONG_OUTINGS = tuple((a["id"], a["activity"], a["location"], a["duration_minutes"], a["transit_minutes"])
                     for a in ACTIVITIES if a["category"] != "night_work" and a["duration_minutes"][0] >= 90)
QUIET = (
    ("宿舍整理照片/画两笔", "weekend_home"),
    ("宿舍看剧和室友闲聊", "weekend_home"),
    ("宿舍写作业/整理看片笔记", "school_library"),
    ("宿舍刷视频和男友聊天", "weekend_home"),
    ("宿舍收拾东西/听歌休息", "weekend_home"),
    ("宿舍翻书/和室友闲聊", "weekend_home"),
    ("给今天拍的照片调色，挑几张留着", "weekend_home"),
    ("和室友玩两局小游戏", "weekend_home"),
    ("试搭明天的衣服，整理小配饰", "weekend_home"),
    ("画一张小速写卡，听喜欢的歌", "weekend_home"),
    ("整理分镜作业和灵感本", "school_library"),
    ("和室友分零食，聊今天的趣事", "weekend_home"),
)


def rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))


def minutes(hm: str) -> int:
    h, m = map(int, hm.split(":"))
    return h * 60 + m


def hm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def slot(start: int, end: int, activity: str, bucket: str, **extra) -> dict:
    return dict(start=hm(start), end=hm(end), activity=activity, bucket=bucket,
                tone="放松、自然聊天", scene_hints=[activity], photo_ok=bucket != "sleep",
                energy=0, delay=[10, 120], source="evening_plan", **extra)


def protected(day: dict) -> bool:
    slots = day.get("slots") or []
    return (day.get("kind") not in ("workday", "weekend", "holiday", "winter_break", "summer_break")
            or bool(day.get("trip_id") or day.get("activity_id"))
            or day.get("source") == "user_override"
            or any(s.get("trip_id") or s.get("source") == "user_override" for s in slots))


def window(day: dict) -> tuple[int, int] | None:
    """仅替换晚饭后到洗漱前的自由段；晚实习/社团/约定仍优先。"""
    if protected(day):
        return None
    slots = day.get("slots") or []
    dinner = next((s for s in slots if s.get("activity") == "吃晚饭"), None)
    prep = next((s for s in slots if "洗漱" in s.get("activity", "")), None)
    if not dinner or not prep:
        return None
    start, end = minutes(dinner["end"]), minutes(prep["start"])
    for s in slots:
        if minutes(s["start"]) >= start and minutes(s["start"]) < end:
            if s.get("bucket") in ("work_internship", "school_class", "school_rehearsal"):
                start = max(start, minutes(s["end"]))
            elif any(w in s.get("activity", "") for w in ("例会", "话剧社", "影像社", "学生会")):
                start = max(start, minutes(s["end"]))
    return (start, end) if end > start else None


def fatigue(day: dict) -> float:
    slots = day.get("slots") or []
    busy = sum((minutes(s["end"]) - minutes(s["start"])) / 60
               for s in slots if s.get("bucket") in ("work_internship", "school_rehearsal"))
    return min(1.0, busy / 6)


def week_plan(date: dt.date, days: list[dict] | None = None) -> set[str]:
    """有可用空档时每周 1–2 晚；所有工作日可选，疲劳降低被选权重。"""
    import life_calendar as lc
    monday = date - dt.timedelta(days=date.weekday())
    r = rng(f"evening-week-{monday}")
    by = {d["date"]: d for d in days or []}
    internship = lc.internship_week_plan(monday)
    choices = {}
    for wd in lc._week_eligible_workdays(monday):
        key = (monday + dt.timedelta(days=wd)).isoformat()
        day = by.get(key)
        if day:
            free = window(day)
            if not free or free[1] - free[0] < 100:
                continue
            # 晚实习后留休息，不为了凑周次数挤出去玩。
            if any(s.get("bucket") == "work_internship" and s["start"] >= "18:00"
                   for s in day.get("slots", [])):
                continue
            weight = 1 - .65 * fatigue(day)
        else:
            if internship.get(wd) == "eve":
                continue
            weight = .55 if internship.get(wd) == "pm" else 1.0
        choices[key] = weight
    selected = set()
    for _ in range(min(r.choice((1, 2)), len(choices))):
        keys = sorted(choices)
        key = r.choices(keys, weights=[choices[k] for k in keys], k=1)[0]
        selected.add(key)
        choices.pop(key)
        for other in choices:
            if abs((dt.date.fromisoformat(other) - dt.date.fromisoformat(key)).days) == 1:
                choices[other] *= .2
    return selected


def complete_sleep(day: dict) -> dict:
    """已有入睡时间早于午夜时补齐真实睡眠槽。"""
    slots = day.get("slots") or []
    if slots and slots[-1]["end"] < "24:00" and "洗漱" in slots[-1].get("activity", ""):
        slots.append(slot(minutes(slots[-1]["end"]), 1440, "睡觉", "sleep"))
    return day


def weekend_plan(date: dt.date, days: list[dict] | None = None) -> set[str]:
    """周末通常选一晚，其余晚休息，避免两个独立抽签连续整月都不出门。"""
    import internship
    saturday = date - dt.timedelta(days=(date.weekday() - 5) % 7)
    r = rng(f"weekend-evening-{saturday}")
    if r.random() >= .8:
        return set()
    by = {d["date"]: d for d in days or []}
    choices = []
    for offset in (0, 1):
        d = saturday + dt.timedelta(days=offset)
        day = by.get(d.isoformat())
        if day:
            free = window(day)
            if not free or free[1] - free[0] < 100:
                continue
        elif internship.trip_due(d, "weekend"):
            continue
        choices.append(d.isoformat())
    return {r.choice(choices)} if choices else set()


def choose_activity(day, available, history=()):
    date = dt.date.fromisoformat(day["date"])
    recent = [d for d in history if 0 < (date - dt.date.fromisoformat(d["date"])).days <= 14]
    used = {d.get("evening_plan", {}).get("outing") for d in recent}
    previous_outings = sorted((d for d in recent if (d.get("evening_plan") or {}).get("category")), key=lambda d:d["date"])
    last_category = previous_outings[-1]["evening_plan"]["category"] if previous_outings else None
    groups = {}
    for a in ACTIVITIES:
        if a["category"] == "night_work" or a["duration_minutes"][0] + 2*a["transit_minutes"] + 25 > available:
            continue
        groups.setdefault(a["category"], []).append(a)
    if not groups:
        return None
    r = rng(f"evening-choice-v2-{date}")
    cats = sorted(groups)
    weights = []
    for cat in cats:
        count = sum((d.get("evening_plan") or {}).get("category") == cat for d in recent)
        weight = groups[cat][0]["category_weight"] / (1 + count * 2)
        if cat == last_category: weight *= .12
        if cat == "movie": weight *= max(.25, 1 - fatigue(day))
        weights.append(weight)
    if "campus_walk" in cats and len(cats) > 1:
        index = cats.index("campus_walk")
        # Novelty penalties must not accidentally promote walking to a major category.
        weights[index] = min(weights[index], sum(w for i,w in enumerate(weights) if i != index) * .02/.98)
    cat = r.choices(cats, weights=weights, k=1)[0]
    variants = groups[cat]
    # Stable date rotation also avoids the same variant on adjacent standalone days.
    index = date.toordinal() % len(variants)
    rotated = variants[index:] + variants[:index]
    return next((a for a in rotated if a["id"] not in used), rotated[0])


def enrich_night_work(day):
    """Use existing evening internship slots only; preserve start/end and work cadence."""
    if protected(day): return day
    date = dt.date.fromisoformat(day["date"])
    tasks = [a for a in ACTIVITIES if a["category"] == "night_work"]
    planned = []
    for old in day.get("slots", []):
        if (old.get("bucket") != "work_internship" or old["start"] < "18:00"
                or old.get("outing_id") or old.get('internship_mode') == 'indoor'):
            planned.append(old); continue
        start, end = minutes(old["start"]), minutes(old["end"])
        a = tasks[date.toordinal() % len(tasks)]
        transit = a["transit_minutes"]
        if end-start < 2*transit+a["duration_minutes"][0]:
            planned.append(old); continue
        common = dict(outing_id=f"night-work-{date}", outing_activity_id=a["id"], city="北京",
                      internship_mode='outdoor')
        planned.extend([
            slot(start, start+transit, "实习夜间外拍去程", "work_internship", leg="outbound", location="学校至海淀取景点途中", **common),
            slot(start+transit, end-transit, "电影公司实习·"+a["activity"], "work_internship", leg="activity", location=a["location"],
                 scene_id=a["scene_id"], photo_note=a["photo_note"], **common),
            slot(end-transit, end, "夜间实习收工返校", "work_internship", leg="return", location="返回学校宿舍途中", **common)])
        planned[-2]["scene_hints"] = a["scene_hints"]
    day["slots"] = planned
    return day


def enrich_day(day: dict, *, selected: set[str] | None = None,
               weekend_selected: set[str] | None = None, history=()) -> dict:
    day = enrich_night_work(copy.deepcopy(day))
    free = window(day)
    if not free:
        return day if protected(day) else detail_day(complete_sleep(day), history=history)
    start, end = free
    date = dt.date.fromisoformat(day["date"])
    r = rng(f"evening-{date}")
    tiring = fatigue(day)
    if day["kind"] == "workday":
        go_out = day["date"] in (selected if selected is not None else week_plan(date))
    elif day["kind"] == "weekend":
        go_out = day["date"] in (weekend_selected if weekend_selected is not None else weekend_plan(date))
    else:
        # 周末白天通常已外出，晚间也保留整晚休息。
        go_out = r.random() < .45
    planned = []
    quiet = QUIET[date.toordinal() % len(QUIET)]
    chosen = None
    if go_out and end - start >= 100:
        chosen = choose_activity(day, end-start, history)
        if chosen is None:
            return complete_sleep(day)
        aid, label, location = chosen["id"], chosen["activity"], chosen["location"]
        duration_range, transit = chosen["duration_minutes"], chosen["transit_minutes"]
        duration = min(r.randint(*duration_range), end - start - 2 * transit - 25)
        if duration < duration_range[0]:
            chosen = None
        else:
            rest = min(r.randint(20, 45), end - start - duration - 2 * transit)
            depart = start + rest
            finish = depart + transit + duration
            outing_id = f"local-{date}-{aid}"
            common = dict(outing_id=outing_id, city="北京", outing_activity_id=aid)
            planned.append(slot(start, depart, "回宿舍歇一会儿/收拾出门", "weekend_home"))
            planned.extend([
                slot(depart, depart + transit, f"从学校出发去{location}", "campus_life",
                     leg="outbound", location=f"学校至{location}途中", **common),
                slot(depart + transit, finish, label, "city_fun", leg="activity",
                     location=location, planned_duration_minutes=duration, scene_id=chosen["scene_id"],
                     photo_note=chosen["photo_note"], booking_status=chosen["booking_status"], **common),
                slot(finish, finish + transit, "返程回学校宿舍", "campus_life",
                     leg="return", location="回学校宿舍途中", **common),
            ])
            planned[-2]["scene_hints"] = chosen["scene_hints"]
            planned[-2]["photo_ok"] = chosen["category"] != "movie"
            start = finish + transit
    if start < end:
        # 整晚休息也分成自然的几件小事，避免一个标签占满四五小时。
        if end - start > 150:
            change = start + r.randint(65, 105)
            planned.append(slot(start, change, quiet[0], quiet[1], city="北京", location="学校宿舍"))
            start = change
            quiet = QUIET[(date.toordinal() + 3) % len(QUIET)]
        planned.append(slot(start, end, quiet[0], quiet[1], city="北京", location="学校宿舍"))
    a, b = free
    old = day["slots"]
    day["slots"] = ([s for s in old if minutes(s["end"]) <= a] + planned
                    + [s for s in old if minutes(s["start"]) >= b])
    day["evening_plan"] = {"version": 2, "outing": chosen["id"] if chosen else None,
                           "category": chosen["category"] if chosen else None,
                           "fatigue": round(tiring, 2)}
    day["photo_bias"] = "city_fun" if chosen else quiet[1]
    return detail_day(complete_sleep(day), history=history)


def detail_day(day: dict, *, history=()) -> dict:
    """Expand persisted plans without drawing a new outing or moving sleep/work.

    Details describe intentions, never ticket purchases, attendance or a companion's
    agreement. The three phases live inside the existing activity time budget.
    """
    day = copy.deepcopy(day)
    if protected(day):
        return day
    catalog = {a['id']: a for a in ACTIVITIES}
    for s in day.get('slots', []):
        a = catalog.get(s.get('outing_activity_id'))
        if not a or s.get('leg') != 'activity':
            continue
        start, end = minutes(s['start']), minutes(s['end'])
        if end - start < 3:
            continue
        # Budget actions in minutes, not fixed percentages of an arbitrary slot.
        budgets = {'shopping': (10,15,10), 'nightview': (10,20,10),
                   'singing': (10,40,10), 'light_fun': (10,35,10),
                   'campus_walk': (10,15,5), 'night_work': (15,60,15),
                   'movie': (10,max(1,end-start-20),10)}
        first, middle, last = budgets[a['category']]
        if first+middle+last > end-start:
            first = min(first, (end-start-1)//2)
            last = min(last, end-start-first-1)
            middle = max(1,end-start-first-last)
        cuts = [start, start+first, start+first+middle, start+first+middle+last]
        phases = [dict(start=hm(cuts[i]), end=hm(cuts[i+1]), activity=step,
                       status='计划', photo_ok=a['category'] != 'movie' or i != 1)
                  for i, step in enumerate(a['steps'])]
        if cuts[-1] < end:
            phases.append(dict(start=hm(cuts[-1]), end=hm(end),
                               activity='整理器材与工作记录' if a['category']=='night_work' else '自由慢逛或坐下歇脚，按时结束',
                               status='计划', photo_ok=True))
        s.update(plan_version=3, purpose=a['purpose'], companion_plan=a['companion_plan'],
                 destination_area=a['destination_area'], meeting_point=a['meeting_point'],
                 itinerary=phases, plan_status=a['plan_status'], fallback=a['fallback'],
                 scene_id=a['scene_id'], scene_hints=a['scene_hints'],
                 photo_subject=a['photo_subject'], photo_note=a['photo_note'],
                 followup_plan=a['followup'])
        # Retain a short conversational title; full steps remain available in
        # STATE and prompt_lines, not lost behind the truncated daily summary.
        group = [x for x in day['slots'] if x.get('outing_id') == s.get('outing_id')]
        status = s.get('execution_status')
        if status not in ('confirmed','cancelled'):
            status = 'pending' if a['category'] in ('movie','singing') else 'scheduled'
        for member in group:
            member['execution_status'] = status
        s['confirmation_required'] = a['category'] in ('movie','singing')
        out = next((x for x in group if x.get('leg') == 'outbound'), None)
        back = next((x for x in group if x.get('leg') == 'return'), None)
        s['route_plan'] = dict(origin='北舞校园', destination=a['destination_area'],
                               depart=out['start'] if out else s['start'],
                               return_by=back['end'] if back else s['end'],
                               transport='短途往返预算；出发前核实路线，超预算则取消')
    if any(s.get('plan_version') == 3 for s in day.get('slots', [])):
        day.setdefault('evening_plan', {})['details_version'] = 3
    elif history:
        yesterday = (dt.date.fromisoformat(day['date'])-dt.timedelta(days=1)).isoformat()
        prior = next((d for d in history if d['date'] == yesterday), {})
        previous = next((s for s in prior.get('slots', []) if s.get('followup_plan')), None)
        if previous and any(s.get('source') == 'evening_plan' and s.get('bucket') == 'weekend_home'
                            for s in day.get('slots', [])):
            day['evening_followup'] = dict(source_date=yesterday, activity_id=previous['outing_activity_id'],
                                          plan=previous['followup_plan'].replace('本次', '昨天的活动'))
    return day


def local_weekend_afternoon(day: dict) -> dict:
    """修复/生成本地周末白天：单一地点 + 双程交通，晚饭前回学校。"""
    day = copy.deepcopy(day)
    slots = day["slots"]
    brunch = next(s for s in slots if "早午饭" in s.get("activity", ""))
    dinner = next(s for s in slots if s.get("activity") == "吃晚饭")
    start, end = minutes(brunch["end"]), minutes(dinner["start"])
    options = (("公园散步拍照", "学校附近公园", 20),
               ("和室友逛书店/文创店", "海淀书店商店", 35),
               ("看展后在馆内休息", "北京城区展馆", 50),
               ("和同学做手工", "海淀手作店", 35))
    label, location, transit = options[dt.date.fromisoformat(day["date"]).toordinal() % len(options)]
    if end - start < 2 * transit + 60:
        return day
    common = dict(outing_id=f"local-{day['date']}-afternoon", city="北京",
                  outing_activity_id="afternoon")
    finish = min(end - transit, start + transit + 180)
    planned = [slot(start, start + transit, f"从学校去{location}", "campus_life",
                    leg="outbound", location=f"学校至{location}途中", **common),
               slot(start + transit, finish, label, "city_fun", leg="activity",
                    location=location, **common),
               slot(finish, finish + transit, "返程回学校", "campus_life", leg="return",
                    location="回学校途中", **common)]
    if finish + transit < end:
        planned.append(slot(finish + transit, end, "回宿舍歇脚/整理随身物品", "weekend_home"))
    day["slots"] = ([s for s in slots if minutes(s["end"]) <= start] + planned
                    + [s for s in slots if minutes(s["start"]) >= end])
    return day
