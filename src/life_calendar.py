"""小甜人设日程：3 个月落盘草稿 + 窗后自动生成；写入 STATE + mem0，供聊天/生图读取。

【活动设计根本来源 / 拟真优先】
  用户定调：让小甜更拟真。拟真 > 好玩堆砌。
  参照北舞创意学院编导系大三女生公开常识节奏（课、基训/排练、自习、轻度社团、
  平日也有轻量外出）+ 人设「小电影公司上班实习」。详见 docs/life-schedule-design.md。
  平日：school_* + 自习为主；实习按 ISO 周随机 2～4 个工作日投放（半天 pm 或 eve）；不实习日上课/学校活动或偶可出门。每周 1–2 个空闲工作日晚短时外出，按疲劳加权，保留休息与往返。起床/睡觉/准备睡为模糊区间非整点分钟；睡眠压力见 schedule.sleep_pressure。
  周末：娱乐可集中。偶尔一两天丰富可以，连续工作日每晚 city_fun = 假。

时区：Asia/Shanghai（东八区）——日程/作息/STATE 与 slot_at/now/生成一律此时区。
数据：data/life_schedule_2026q4.json（2026-10-24～约半年窗；寒暑假对齐北舞/市教委校历）
窗后：generate_range —— 周末/工作日规则 + 中国大陆法定假日表 + 寒暑假启发式
      + 人设 bucket 权重 + material_store（小红书等已采集素材）检索加权；不编造帖子。

记忆：
  - STATE.life_today / life_recent / life_plan_meta（结构化，state_block 必读）
  - mem0 kind=life_schedule：仅当日一行基础摘要（非全日多槽灌库；检索降权/最多一条）

闸门：活动须先有 activity_pool.rating，再经 src/reasonableness.py 审查通过，
      才可写入 data/life_schedule_*.json（见 tools/seed_winter_schedule.py）。
"""
from __future__ import annotations

import datetime as dt
import copy
import hashlib
import json
import pathlib
import random
import re
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
DRAFT_FILE = ROOT / "data" / "life_schedule_2026q4.json"
HOLIDAYS_FILE = ROOT / "data" / "cn_holidays.json"

# 日程/作息/STATE 墙钟一律 Asia/Shanghai（东八区）；slot_at/now/生成勿用其它时区
TZ_NAME = "Asia/Shanghai"
TZ = ZoneInfo(TZ_NAME)

def now_local() -> dt.datetime:
    """当前墙钟（naive）；默认 Asia/Shanghai，可由 config timezone/tz 覆盖（见 timeutil）。"""
    try:
        import timeutil as _tu
        return _tu.now_local()
    except Exception:
        return dt.datetime.now(TZ).replace(tzinfo=None)

def today_local() -> dt.date:
    return now_local().date()

# bucket → 生图/穿搭场景提示权重
BUCKET_SCENE = {
    "school_class": ["教室上课偷看窗外", "阶梯教室记笔记", "课间走廊"],
    "school_rehearsal": ["练功房把杆休息", "排练厅拉伸", "剧场侧台待命"],
    "school_library": ["图书馆窗边自习", "图书馆书架间找书", "自习室低头写编创阐述"],
    "work_internship": ["电影公司茶水间", "剪辑室屏幕反光", "片场场记板旁", "公司工位小憩"],
    "campus_life": ["食堂窗口打饭", "校园林荫道", "宿舍楼下咖啡"],
    "campus_fun": ["校园小剧场门口", "校园湖边夜景", "操场傍晚散步"],
    "city_fun": ["城市商场探店", "地铁站台随手拍", "电影院门口", "夜景街头"],
    "outdoor_outing": ["郊游登山歇脚", "公园草地野餐", "红叶林小径", "湖边栈道"],
    "weekend_home": ["宿舍窗边", "宿舍化妆镜自拍", "被窝里刷视频", "枕边和男友微信"],
    "travel": ["车站候车", "行李箱旁", "车窗风景", "机场登机口", "雪村木屋", "海边民宿阳台"],
    "sleep": [],
}

WEEKDAY_EVE_POOL = [
    ("校园小剧场看演出", "campus_fun", ["小剧场"]),
    ("学校附近探店", "city_fun", ["探店"]),
    ("五道口/合生汇逛街探店", "city_fun", ["逛街", "探店"]),
    ("Livehouse 小型演出", "city_fun", ["Livehouse", "演出"]),
    ("同学局·剧本杀/古堡逃脱", "city_fun", ["剧本杀", "密室"]),
    ("脱口秀/话剧小剧场", "city_fun", ["脱口秀", "话剧"]),
    ("电影院看新片", "city_fun", ["电影院"]),
    ("三里屯/工体夜逛拍照", "city_fun", ["三里屯", "夜景"]),
    ("798/美术馆夜场逛展", "city_fun", ["798", "展览"]),
    ("猫咖/手作DIY小课", "city_fun", ["猫咖", "手作"]),
    ("南锣/鼓楼 citywalk", "city_fun", ["citywalk", "鼓楼"]),
    ("影像社剪片小聚", "campus_fun", ["剪辑", "社团"]),
    ("书店+咖啡续摊", "city_fun", ["书店", "咖啡"]),
    ("电玩城/射拍馆体验", "city_fun", ["电玩城"]),
    ("韩式年糕火锅局", "city_fun", ["聚餐", "探店"]),
    ("胶片冲洗店+咖啡", "city_fun", ["胶片", "咖啡"]),
]
# 平日晚「拟真空档」：冲浪/自习/聊天 —— 多数工作日晚走这里，不抽娱乐池
WEEKDAY_EVE_QUIET_POOL = [
    ("宿舍刷小红书/抖音", "weekend_home", ["宿舍", "刷手机"], "dorm_surf"),
    ("回宿舍写编创阐述/作业", "school_library", ["宿舍", "作业"], "dorm_study"),
    ("宿舍和男友微信闲聊", "weekend_home", ["宿舍", "聊天"], "dorm_chat"),
    ("宿舍追剧摸鱼", "weekend_home", ["宿舍", "追剧"], "dorm_drama"),
    ("图书馆夜场自习", "school_library", ["图书馆", "自习"], "study_library"),
    ("影像社剪片小聚后回寝", "campus_fun", ["剪辑", "社团"], "campus_edit"),
]
# 实习任务/地点由 internship.py 从 data/internship_tree.json 读取。

# 不实习工作日下午：课/排练骨架外的学校活动（偶可轻出门，不用 city_fun 灌密度）
NON_INTERN_PM_POOL = [
    ("话剧社排练", "campus_fun", ["话剧社"], "club_drama"),
    ("影像社活动/剪片", "campus_fun", ["影像社"], "campus_edit"),
    ("学生会文艺部事务", "campus_life", ["学生会"], None),
    ("编创小组对戏", "school_rehearsal", ["排练"], None),
    ("校园湖边散步透气", "campus_fun", ["校园"], None),
    ("教室改阐述/看片笔记", "school_library", ["教室", "作业"], "dorm_study"),
]

# 实习周频：每 ISO 周在合格工作日中随机抽 2～4 天（可经 seed 复现）
INTERNSHIP_DAYS_PER_WEEK = (2, 4)

WEEKEND_OUT_POOL = [
    ("东四/西兴隆街 Citywalk 探店", "city_fun", ["citywalk", "文创"]),
    ("郊游徒步半日", "outdoor_outing", ["郊游", "徒步"]),
    ("公园野餐拍照", "outdoor_outing", ["野餐", "公园"]),
    ("逛园林景区", "outdoor_outing", ["园林"]),
    ("城际短途一日游", "outdoor_outing", ["短途"]),
    ("美术馆联票逛展", "city_fun", ["展览"]),
    ("环球影城半日打卡", "city_fun", ["环球影城"]),
    ("古着店+黑胶店血拼", "city_fun", ["古着", "黑胶"]),
    ("陶艺/插花手作体验", "city_fun", ["陶艺", "手作"]),
    ("攀岩馆初体验", "city_fun", ["攀岩"]),
    ("同学局·桌游剧本杀马拉松", "city_fun", ["桌游", "剧本杀"]),
    ("奥森飞盘/慢跑后喝咖啡", "outdoor_outing", ["奥森", "运动"]),
    ("古北水镇/长城冬日半日", "outdoor_outing", ["长城", "古北"]),
    ("博物馆日+南锣晚饭", "city_fun", ["博物馆", "南锣"]),
]
# 寒假旅行池：雪山/崇礼滑雪/新疆禾木/三亚 + 出国短途（须经 reasonableness 闸门）
# 每项可带可选第4元组 activity_pool id；generate 时只抽池，写入日程前走 may_enter_schedule
WINTER_TRAVEL_POOL = [
    ("崇礼雪场滑雪日", "outdoor_outing", ["滑雪", "崇礼", "雪山"], "ski_chongli"),
    ("雪场民宿泡脚复盘", "travel", ["民宿", "雪夜"], "ski_chongli"),
    ("新疆禾木雪村漫步", "travel", ["禾木", "雪村", "木屋"], "travel_hemu"),
    ("喀纳斯/禾木雪地徒步", "outdoor_outing", ["雪地徒步", "雪山"], "travel_hemu"),
    ("入门级雪山日照金山拍照", "outdoor_outing", ["雪山", "日照金山"], "travel_hemu"),
    ("三亚海边日落漫步", "outdoor_outing", ["海边日落", "椰林"], "travel_sanya"),
    ("三亚海岸线 citywalk+海鲜", "city_fun", ["海边", "海鲜"], "travel_sanya"),
    ("三亚浮浅/蜈支洲岛日", "outdoor_outing", ["浮浅", "海岛"], "travel_sanya"),
    ("东京冬季街头漫步", "travel", ["东京", "涩谷", "日本"], "travel_tokyo_winter"),
    ("首尔明洞/弘大探店", "city_fun", ["首尔", "明洞", "韩国"], "travel_seoul_winter"),
    ("清迈暖冬寺庙+夜市", "travel", ["清迈", "泰国"], "travel_chiangmai"),
    ("巴厘岛海边发呆日", "outdoor_outing", ["巴厘岛", "海边"], "travel_bali"),
    ("北海道粉雪初体验", "outdoor_outing", ["北海道", "滑雪"], "travel_hokkaido_ski"),
    ("新加坡滨海湾短途", "city_fun", ["新加坡", "滨海湾"], "travel_singapore"),
    ("大阪/京都冬季漫步", "travel", ["大阪", "京都", "日本"], "travel_osaka_winter"),
    ("港澳短途打卡", "city_fun", ["香港", "澳门"], "travel_hk_macau"),
    ("台北短途 citywalk", "city_fun", ["台北", "夜市"], "travel_taipei"),
    ("转机日·机场特种兵", "travel", ["机场", "行李箱"], "transit_day"),
]

# 合理度元数据快捷表（与 data/activity_pool.json 同步；generate 侧只读）
ACTIVITY_REASONABLENESS = {
    "ski_chongli": {"typical_days": 4, "min_stay_days": 3},
    "travel_hemu": {"typical_days": 7, "min_stay_days": 5},
    "travel_sanya": {"typical_days": 12, "min_stay_days": 7},
    "travel_tokyo_winter": {"typical_days": 6, "min_stay_days": 4},
    "travel_seoul_winter": {"typical_days": 5, "min_stay_days": 4},
    "travel_chiangmai": {"typical_days": 7, "min_stay_days": 5},
    "travel_bali": {"typical_days": 8, "min_stay_days": 6},
    "travel_hokkaido_ski": {"typical_days": 6, "min_stay_days": 5},
    "travel_singapore": {"typical_days": 5, "min_stay_days": 3},
    "travel_osaka_winter": {"typical_days": 6, "min_stay_days": 4},
    "travel_hk_macau": {"typical_days": 4, "min_stay_days": 3},
    "travel_taipei": {"typical_days": 5, "min_stay_days": 4},
}


def _load_draft():
    if not DRAFT_FILE.exists():
        return None
    return json.loads(DRAFT_FILE.read_text())


def _load_holidays():
    """data/cn_holidays.json 可覆盖；缺省用窗内已知 + 生成器内置。"""
    base = {
        "2027-01-01": "元旦",
        "2027-01-02": "元旦假期",
        "2027-01-03": "元旦假期",
        # 春节 2027-02-06 起（窗后生成会用）
        "2027-02-06": "春节",
        "2027-02-07": "春节",
        "2027-02-08": "春节",
        "2027-02-09": "春节",
        "2027-02-10": "春节",
        "2027-02-11": "春节",
        "2027-02-12": "春节",
    }
    if HOLIDAYS_FILE.exists():
        try:
            base.update(json.loads(HOLIDAYS_FILE.read_text()).get("dates") or {})
        except Exception:
            pass
    return base


def _rng(seed: str):
    return random.Random(int(hashlib.md5(seed.encode()).hexdigest()[:12], 16))


def draft_range():
    doc = _load_draft()
    if not doc:
        return None, None
    return dt.date.fromisoformat(doc["range"]["start"]), dt.date.fromisoformat(doc["range"]["end"])


_DAY_PROBES_BOUND: set[str] = set()


def _bind_day_scene_probes(day: dict | None) -> None:
    """日程已知时预绑 scene_ref 探针（同日同场景复用，防穿帮）。失败忽略。"""
    from preview_mode import is_preview
    if is_preview():
        return
    if not day or not day.get("date"):
        return
    dkey = str(day["date"])
    if dkey in _DAY_PROBES_BOUND:
        return
    try:
        import scene_refs as _sr
        _sr.ensure_day_probes(day, k=2)
        _DAY_PROBES_BOUND.add(dkey)
    except Exception as e:
        import sys
        print(f"[life_calendar] day scene probes skip: {e!r}", file=sys.stderr)



# Meal period bands stamped on get_day (breakfast|brunch|lunch|dinner).
_MEAL_SLOT_RE = re.compile(
    r"吃(早|午|晚)?饭|早午饭|早午餐|午饭|晚饭|早餐|午餐|晚餐|食堂|brunch|meal",
    re.I,
)
_MEAL_PERIOD_KW = {
    "brunch": ("早午饭", "早午餐", "brunch"),
    "breakfast": ("早饭", "早餐", "豆浆", "包子", "油条"),
    "lunch": ("午饭", "午餐", "中饭", "中午吃", "食堂午饭"),
    "dinner": ("晚饭", "晚餐", "夜宵", "宵夜", "晚饭吃"),
}
_MEAL_PERIODS = frozenset({"breakfast", "brunch", "lunch", "dinner"})


def compute_meal_period(slot: dict | None, now: dt.datetime | None = None) -> str | None:
    """Infer breakfast|brunch|lunch|dinner for a meal-like slot; else None."""
    slot = slot or {}
    act = str(slot.get("activity") or "")
    hints = " ".join(str(x) for x in (slot.get("scene_hints") or []))
    blob = f"{act} {hints}"
    if not _MEAL_SLOT_RE.search(blob):
        return None
    # keyword order: brunch before breakfast (早午饭)
    for period, kws in _MEAL_PERIOD_KW.items():
        if any(k in blob for k in kws):
            return period
    h = (now or now_local()).hour
    if 6 <= h <= 10:
        return "breakfast"
    if 11 <= h <= 14:
        return "lunch"
    if 17 <= h <= 21:
        return "dinner"
    return None


def stamp_meal_periods(day: dict | None, *, now: dt.datetime | None = None) -> dict | None:
    """Normalize meal_period on meal slots (idempotent). Non-meal slots untouched."""
    if not isinstance(day, dict):
        return day
    for s in day.get("slots") or []:
        if not isinstance(s, dict):
            continue
        cur = s.get("meal_period")
        if cur in _MEAL_PERIODS:
            continue
        period = compute_meal_period(s, now)
        if period:
            s["meal_period"] = period
        elif "meal_period" in s and cur not in _MEAL_PERIODS:
            s.pop("meal_period", None)
    return day


def get_day(date: dt.date | None = None) -> dict | None:
    """取某日日程：草稿窗内读 JSON；窗外自动 generate_day。

    Meal slots get meal_period normalized to breakfast|brunch|lunch|dinner
    (WANT_IMAGE / material pick read the same field).
    """
    date = date or dt.date.today()
    doc = _load_draft()
    if doc:
        by = {d["date"]: d for d in doc["days"]}
        if date.isoformat() in by:
            day = _project_sleep_carryover(by[date.isoformat()], doc, date)
            _bind_day_scene_probes(day)
            return stamp_meal_periods(day)
        start, end = draft_range()
        if start and end and start <= date <= end:
            day = by.get(date.isoformat())
            _bind_day_scene_probes(day)
            return stamp_meal_periods(day)
    day = generate_day(date)
    _bind_day_scene_probes(day)
    return stamp_meal_periods(day)


def _frozen_on(doc: dict, date: dt.date) -> bool:
    frozen = doc.get("frozen") or {}
    return bool(frozen.get("active")
                and (not frozen.get("from") or str(frozen["from"]) <= date.isoformat())
                and (not frozen.get("to") or date.isoformat() <= str(frozen["to"])))


def _protected_sleep_day(day: dict) -> bool:
    """Explicit arrangements and journeys take precedence over date-seed sleep."""
    return bool(day.get("source") == "user_override"
                or "user_override" in str(day.get("notes") or "")
                or day.get("frozen") or day.get("locked")
                or "travel" in str(day.get("kind") or "")
                or any(s.get("source") == "user_override" or s.get("trip_id")
                       or s.get("locked") or s.get("frozen")
                       for s in day.get("slots") or []))


def repair_midnight_carryover(day: dict, previous_day: dict | None, *,
                              frozen: bool = False, allow_legacy_frozen: bool = False) -> dict:
    """Return a minimal copy with yesterday's untruncated bedtime carried over.

    Only a legacy 00:00 sleep placeholder is split. No daytime slots, explicit
    overrides, actual execution, or trips are changed. Frozen legacy clipping
    needs the narrowly authorized allow_legacy_frozen compatibility exception;
    it does not unfreeze the plan. Caller controls eligible dates; no writes.
    """
    if (not previous_day or day.get("sleep_rollover")
            or _protected_sleep_day(day) or _protected_sleep_day(previous_day)):
        return day
    previous_bounds = previous_day.get("sleep_bounds") or {}
    legacy_clipped = (previous_bounds.get("sleep_min") is None
                      and previous_bounds.get("prep_end") == "24:00")
    if frozen and not (allow_legacy_frozen and legacy_clipped):
        return day
    slots, previous = day.get("slots") or [], previous_day.get("slots") or []
    if not slots or not previous:
        return day
    first, last = slots[0], previous[-1]
    if (first.get("start") != "00:00" or first.get("bucket") != "sleep"
            or last.get("end") != "24:00" or last.get("bucket") != "weekend_home"
            or first.get("execution_status") or last.get("execution_status")
            or not re.search(r"宿舍|准备睡|洗漱|自由时间", str(last.get("activity") or ""))):
        return day
    try:
        date = dt.date.fromisoformat(day["date"])
        prev_date = dt.date.fromisoformat(previous_day["date"])
        if prev_date + dt.timedelta(days=1) != date:
            return day
        sleep_min = previous_bounds.get("sleep_min")
        if sleep_min is None:
            # Old drafts clipped prep_end at 24:00; reconstruct only that
            # lost carryover from the unchanged deterministic date seed.
            if (previous_day.get("sleep_bounds") or {}).get("prep_end") != "24:00":
                return day
            import schedule
            sleep_min = schedule.day_plan(prev_date)["sleep"]
        end = int(sleep_min) - 1440
        wake = _parse_hm(first["end"])
        if not (0 < end < wake and end <= 180):
            return day
    except (KeyError, TypeError, ValueError):
        return day
    result = copy.deepcopy(day)
    carried = []
    prep_start = max(0, end - 40)
    if prep_start:
        carried.append(_s("00:00", _fmt_min(prep_start), "宿舍夜间休息，还没睡",
                          "有点困但还醒着", "weekend_home", ["宿舍"], photo_ok=False, energy=-1))
    carried.append(_s(_fmt_min(prep_start), _fmt_min(end), "洗漱/准备睡",
                      "有点困", "weekend_home", ["宿舍"], photo_ok=False, energy=-1))
    for slot in carried:
        slot.update(source="sleep_midnight_carryover", carry_from=previous_day["date"])
    result["slots"] = carried + [{**result["slots"][0], "start": _fmt_min(end)}] + result["slots"][1:]
    result.setdefault("sleep_bounds", {})["sleep_start"] = _fmt_min(end)
    result["sleep_rollover"] = {"version": 1, "from": previous_day["date"],
        "sleep_min": int(sleep_min), "mode": "read_projection",
        "reason": "legacy_midnight_sleep_clipping" if legacy_clipped else "recorded_bedtime_carryover",
        "source": "schedule.day_plan/date_seed" if legacy_clipped else "previous_day.sleep_bounds.sleep_min",
        "frozen_compatibility": bool(frozen),
        "authorized_scope": "2026-10-29 midnight clipping repair"}
    return result


def _project_sleep_carryover(day: dict, doc: dict | None, date: dt.date) -> dict:
    # Historical diary/calendar facts are immutable. Future/current legacy
    # placeholders can be interpreted without overwriting the draft on disk.
    if date < today_local() or not doc:
        return day
    prev_date = date - dt.timedelta(days=1)
    previous = next((d for d in doc.get("days") or [] if d.get("date") == prev_date.isoformat()), None)
    return repair_midnight_carryover(day, previous,
        frozen=_frozen_on(doc, date) or _frozen_on(doc, prev_date), allow_legacy_frozen=True)


def slot_at(now: dt.datetime | None = None) -> dict | None:
    now = now or now_local()
    if getattr(now, "tzinfo", None) is not None:
        now = now.astimezone(TZ).replace(tzinfo=None)
    day = get_day(now.date())
    if not day:
        return None
    t = f"{now.hour:02d}:{now.minute:02d}"
    for s in day.get("slots") or []:
        a, b = s.get("start") or "00:00", s.get("end") or "24:00"
        # 支持 end=24:00
        if a <= t < b or (b == "24:00" and a <= t):
            from schedule_execution import effective
            return effective({**s, "date": day["date"], "kind": day.get("kind"), "photo_bias": day.get("photo_bias")})
    return None


def resolve_life_now(st: dict | None, now: dt.datetime | None = None) -> dict | None:
    """此刻槽：优先按 STATE.life_today.slots 用 now 重算；缺 today 再 slot_at(now)。

    STATE.life_now 仅当与算出的 live 同日且同 start/end 时作缓存，否则丢弃（防跨槽粘住）。
    live 为 None 时绝不回退 cached（跨槽/跨日粘住 outing 等）。
    soft-bias / state_lines / photo_context / reviewed_schedule_feed 共用。
    墙钟：Asia/Shanghai（见 now_local）。
    """
    now = now or now_local()
    if getattr(now, "tzinfo", None) is not None:
        now = now.astimezone(TZ).replace(tzinfo=None)
    st = st or {}
    today = st.get("life_today") if isinstance(st.get("life_today"), dict) else None
    live = None
    if today and str(today.get("date") or "")[:10] == now.date().isoformat():
        today = _project_sleep_carryover(today, _load_draft(), now.date())
        t = f"{now.hour:02d}:{now.minute:02d}"
        for s in today.get("slots") or []:
            a, b = s.get("start") or "00:00", s.get("end") or "24:00"
            if a <= t < b or (b == "24:00" and a <= t):
                live = {**s, "date": today.get("date"), "kind": today.get("kind"),
                        "photo_bias": today.get("photo_bias")}
                break
    if live is None:
        live = slot_at(now)
    cached = st.get("life_now") if isinstance(st.get("life_now"), dict) else None
    if live and cached and (
        cached.get("date") == live.get("date")
        and cached.get("start") == live.get("start")
        and cached.get("end") == live.get("end")
    ):
        from schedule_execution import effective
        return effective(live) if (live.get("execution_status") or live.get("original_plan_status")
                                   or live.get("source") == "sleep_midnight_carryover") else effective(cached)
    # live 缺失时丢弃 cached（与 docstring 一致）
    from schedule_execution import effective
    return effective(live)


def recent_days(n=3, today: dt.date | None = None) -> list[dict]:
    today = today or today_local()
    return [get_day(today - dt.timedelta(days=i)) for i in range(n) if get_day(today - dt.timedelta(days=i))]


def _winter_kind(date: dt.date) -> str | None:
    """寒暑假：优先北舞/市教委对齐窗，缺省再回退春节启发式。

    2026–2027：参照京教办〔2026〕4号非义务教育寒假 2027-01-25～02-21、
    春季开学 02-22；北舞官网校历页存在但抓取被 WAF 拦。期末考安排在放假前，
    人设寒假旅弧自 01-19 起（考完即走）。暑假约 7/1–8/31。
    """
    # Fixed windows (Asia/Shanghai academic year)
    if dt.date(2027, 1, 19) <= date <= dt.date(2027, 2, 21):
        return "winter_break"
    if dt.date(2027, 1, 12) <= date <= dt.date(2027, 1, 16) and date.weekday() < 5:
        return "exam_week"
    if dt.date(date.year, 7, 1) <= date <= dt.date(date.year, 8, 31):
        return "summer_break"
    y = date.year
    spring = {
        2028: dt.date(2028, 1, 26),
    }.get(y)
    if spring:
        brk = spring - dt.timedelta(days=18)
        review = brk - dt.timedelta(days=7)
        if date >= brk and date < spring + dt.timedelta(days=14):
            return "winter_break"
        if review <= date < brk and date.weekday() < 5:
            return "exam_week"
    return None



def _thesis_defense_kind(date: dt.date) -> str | None:
    """学业块日（答辩/期末/复习）：读 data/study_blocks.json。"""
    pth = ROOT / "data" / "study_blocks.json"
    if not pth.exists():
        return None
    try:
        doc = json.loads(pth.read_text())
    except Exception:
        return None
    key = date.isoformat()
    blocks = doc.get("blocks") or doc.get("dates") or {}
    item = blocks.get(key) if isinstance(blocks, dict) else None
    if not item:
        return None
    allowed = ("thesis_defense", "exam_week", "finals", "exam_review")
    if isinstance(item, str):
        return item if item in allowed else "thesis_defense"
    kind = (item or {}).get("kind") or "thesis_defense"
    # 早期模糊复习周不改写整天 kind（仍上课），仅近考/考试日抬 kind
    if kind == "exam_review":
        strength = float((item or {}).get("refuse_strength") or 0)
        if strength < 0.7:
            return None
        return "exam_week"
    return kind if kind in allowed else "thesis_defense"



def _fmt_min(m: int) -> str:
    """分钟→HH:MM（当日墙钟，允许显示到 23:xx；m 可≥1440 时取模）。"""
    m = int(m) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _fmt_end_min(m: int) -> str:
    """槽 end：≥23:59 或 ≥1440 → 24:00，使 a<=t<b 盖满当日。"""
    m = int(m)
    if m >= 24 * 60 - 1:
        return "24:00"
    return _fmt_min(m)


def _parse_hm(hm: str) -> int:
    h, m = map(int, hm.split(":"))
    return h * 60 + m


def _rand_min(r: random.Random, lo: int, hi: int) -> int:
    lo, hi = int(lo), int(hi)
    if hi < lo:
        lo, hi = hi, lo
    return r.randint(lo, hi)


def _week_monday(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


def _week_eligible_workdays(monday: dt.date) -> list[int]:
    """本周一～五中可投放实习的 weekday 下标（排除假日/寒暑假/考试硬块）。"""
    eligible = []
    for i in range(5):
        d = monday + dt.timedelta(days=i)
        if _load_holidays().get(d.isoformat()):
            continue
        wk = _winter_kind(d)
        if wk in ("winter_break", "summer_break", "exam_week"):
            continue
        if _thesis_defense_kind(d):
            continue
        eligible.append(i)
    return eligible


def internship_week_plan(date: dt.date) -> dict[int, str]:
    """ISO 周实习计划：{weekday: 'pm'|'eve'}，目标每周 2～4 天（合格日不足则全取）。"""
    iso = date.isocalendar()
    monday = _week_monday(date)
    r = _rng(f"intern-week-{iso[0]}-W{iso[1]:02d}")
    eligible = _week_eligible_workdays(monday)
    if not eligible:
        return {}
    lo, hi = INTERNSHIP_DAYS_PER_WEEK
    n = min(r.randint(lo, hi), len(eligible))
    pick = list(eligible)
    r.shuffle(pick)
    plan = {}
    for i, wd in enumerate(pick[:n]):
        # One evening in each eligible week; remaining periods stay varied.
        plan[wd] = "eve" if i == 0 else ("pm" if r.random() < .75 else "eve")
    return plan


def _day_sleep_bounds(date: dt.date, r: random.Random) -> dict:
    """模糊起床/准备睡/入睡：优先 schedule.day_plan，再微抖动禁止整点刻板。"""
    try:
        import schedule as sched
        plan = sched.day_plan(date)
        wake = int(plan["wake"])
        sleep = int(plan["sleep"])  # 可 >1440
    except Exception:
        wake = _rand_min(r, 7 * 60 + 5, 8 * 60 + 10) if date.weekday() < 5 else _rand_min(r, 9 * 60 + 10, 11 * 60 + 40)
        sleep = _rand_min(r, 23 * 60 + 5, 24 * 60 + 40)
    # 准备睡起点：入睡前 35～85 分钟，且不早于 21:20
    lead = _rand_min(r, 35, 85)
    # 过午夜入睡：当日末槽 end 用 24:00（不用 23:59），半开区间 a<=t<b 盖满当日
    sleep_disp = sleep if sleep < 24 * 60 else 24 * 60
    prep_start = max(21 * 60 + 20, min(sleep_disp, 24 * 60) - lead)
    prep_start = min(prep_start, 23 * 60 + 25)
    if prep_start >= sleep_disp:
        sleep_disp = min(24 * 60, prep_start + _rand_min(r, 40, 70))
    return {
        "wake": wake,
        "wake_hm": _fmt_min(wake),
        "prep_start": prep_start,
        "prep_start_hm": _fmt_min(prep_start),
        "sleep": sleep,
        "prep_end_hm": _fmt_end_min(sleep_disp),
        "late": bool(sleep >= 26 * 60) if sleep else False,
    }



def generate_day(date: dt.date, *, materials_query: str | None = None) -> dict:
    """窗后/缺省日：规则生成一档。materials 只检索已入库素材标题作 hints，不造假帖。

    实习：ISO 周随机 2～4 个合格工作日（internship_week_plan）。
    作息：起床/准备睡/槽边界取自模糊区间（非整点分钟），见 _day_sleep_bounds。
    """
    r = _rng(f"gen-{date.isoformat()}")
    hol = _load_holidays().get(date.isoformat())
    wd = date.weekday()
    wk = _winter_kind(date)
    defense = _thesis_defense_kind(date)
    if defense:
        kind = defense
    elif hol:
        kind = "holiday"
    elif wk:
        kind = wk
    elif wd >= 5:
        kind = "weekend"
    else:
        kind = "workday"

    import internship
    if internship.trip_due(date, kind):
        return internship.trip_day(date)

    mat_hints = _material_hints(materials_query or _default_mat_query(kind, date), k=3)
    bounds = _day_sleep_bounds(date, r)
    wake_hm = bounds["wake_hm"]
    prep_hm = bounds["prep_start_hm"]
    prep_end_hm = bounds["prep_end_hm"]
    slots = []

    def _append_prep_sleep(eve_end_hm: str):
        # eve_end 不得晚于 prep；能量随睡眠压力略降
        start = eve_end_hm if eve_end_hm <= prep_hm else prep_hm
        if start >= prep_end_hm:
            start = _fmt_min(max(21 * 60 + 20, _parse_hm(prep_end_hm) - 45))
        slots.append(_s(start, prep_end_hm, "回宿舍洗漱/准备睡", "犯困、想道晚安", "weekend_home",
                        ["宿舍", "洗漱"], photo_ok=False, energy=-1))

    def _hm_jitter(base_min: int, lo_off: int, hi_off: int) -> str:
        return _fmt_min(_rand_min(r, base_min + lo_off, base_min + hi_off))

    if kind in ("weekend", "holiday", "winter_break", "summer_break"):
        brunch_end = _hm_jitter(13 * 60, -25, 35)  # ~12:35–13:35
        slots.append(_s("00:00", wake_hm, "睡觉", "迷迷糊糊", "sleep", photo_ok=False, energy=-4))
        slots.append(_s(wake_hm, brunch_end, "赖床/吃早午饭", "懒洋洋", "weekend_home", ["宿舍"], energy=0))
        lunch_end = _hm_jitter(18 * 60 + 20, -20, 25)
        dinner_start = lunch_end
        dinner_end = _hm_jitter(19 * 60 + 25, -15, 30)
        if dinner_end <= dinner_start:
            dinner_end = _fmt_min(_parse_hm(dinner_start) + _rand_min(r, 40, 70))
        # 单日生成器只安排本地活动；跨城旅行须由补充 Agent 生成完整多日弧。
        slots.append(_s(brunch_end, lunch_end, "本地周末活动", "边走边回", "city_fun", ["北京"], energy=1))
        bias = "city_fun"
        slots.append(_s(dinner_start, dinner_end, "吃晚饭", "放松", "campus_life", ["晚饭"], energy=1))
        slots.append(_s(dinner_end, prep_hm, "回宿舍休息", "放松", "weekend_home", ["宿舍"]))
        _append_prep_sleep(prep_hm)
    else:
        # ---- 工作日：课/基训/自习 + 周级 2～4 天实习；晚间娱乐稀疏 ----
        lib = kind in ("exam_week", "thesis_defense", "finals", "exam_review")
        intern_slot = None if lib else internship_week_plan(date).get(wd)  # 'pm'|'eve'|None
        class_start = _hm_jitter(9 * 60, -12, 18)   # ~08:48–09:18
        lunch_start = _hm_jitter(12 * 60, -10, 20)
        lunch_end = _hm_jitter(13 * 60 + 25, -15, 20)
        pm_end = _hm_jitter(17 * 60 + 25, -20, 25)
        dinner_end = _hm_jitter(18 * 60 + 25, -15, 25)
        if dinner_end <= pm_end:
            dinner_end = _fmt_min(_parse_hm(pm_end) + _rand_min(r, 35, 55))
        if lunch_end <= lunch_start:
            lunch_end = _fmt_min(_parse_hm(lunch_start) + _rand_min(r, 50, 80))
        if class_start <= wake_hm:
            class_start = _fmt_min(_parse_hm(wake_hm) + _rand_min(r, 40, 70))

        slots.append(_s("00:00", wake_hm, "睡觉", "迷迷糊糊", "sleep", photo_ok=False, energy=-4))
        slots.append(_s(wake_hm, class_start, "刚起床赶课", "刚醒、慢半拍", "campus_life", energy=-1))
        am_by_wd = {
            0: ("编导技法课", "school_class", ["教室"]),
            1: ("导演基础/镜头语言课", "school_class", ["教室"]),
            2: ("中外舞蹈史", "school_class", ["教室"]),
            3: ("编导技法课", "school_class", ["教室"]),
            4: ("剧目排练", "school_rehearsal", ["练功房"]),
        }
        if lib:
            am_act = "毕业答辩/候场" if kind == "thesis_defense" else "期末复习·图书馆"
            am_bucket, am_hints = "school_library", (["答辩"] if kind == "thesis_defense" else ["图书馆"])
        else:
            am_act, am_bucket, am_hints = am_by_wd.get(wd, ("上课", "school_class", ["教室"]))
        slots.append(_s(class_start, lunch_start, am_act,
                        "偷偷回、很短" if kind != "thesis_defense" else "紧张、回得很少",
                        am_bucket, mat_hints or am_hints, energy=0))
        slots.append(_s(lunch_start, lunch_end, "食堂午饭", "边吃边聊", "campus_life", ["食堂"], energy=1))

        pm_by_wd = {
            0: ("现代舞基训", "school_rehearsal", ["练功房"]),
            1: ("剧目排练", "school_rehearsal", ["练功房"]),
            2: ("编创作业排练", "school_rehearsal", ["练功房"]),
            3: ("学生会活动筹备", "campus_life", ["学生会"]),
            4: ("自习/写编创阐述", "school_library", ["图书馆"]),
        }
        intern_pm = intern_slot == "pm"
        if lib:
            pm_act = "答辩复盘/材料" if kind == "thesis_defense" else "图书馆自习"
            pm_bucket, pm_hints = "school_library", (["答辩"] if kind == "thesis_defense" else ["图书馆"])
            pm_tone = "紧绷、少回" if kind == "thesis_defense" else "偶尔摸鱼回"
        elif intern_pm:
            intern = internship.activity(date, 'pm')
            pm_act, pm_bucket, pm_hints = intern[0], intern[1], list(intern[2]) + mat_hints
            pm_tone = "实习中、回得慢"
        else:
            # 不实习：上课骨架或学校活动（约 28% 抽活动池）
            if r.random() < 0.28:
                alt = NON_INTERN_PM_POOL[r.randrange(len(NON_INTERN_PM_POOL))]
                pm_act, pm_bucket, pm_hints = alt[0], alt[1], list(alt[2]) + mat_hints
            else:
                pm_act, pm_bucket, pm_hints = pm_by_wd.get(wd, ("排练/课", "school_rehearsal", ["练功房"]))
            pm_tone = "偶尔摸鱼回"
        slots.append(_s(lunch_end, pm_end, pm_act, pm_tone, pm_bucket, pm_hints or mat_hints, energy=0))
        slots.append(_s(pm_end, dinner_end, "吃晚饭", "放松、话多一点", "campus_life", ["晚饭"], energy=1))

        if kind in ("exam_week", "thesis_defense", "finals", "exam_review"):
            eve_act = "答辩材料准备" if kind == "thesis_defense" else "期末复习·图书馆夜场"
            slots.append(_s(dinner_end, prep_hm, eve_act, "专注、回得慢", "school_library",
                            (mat_hints or ["图书馆", "复习"]), photo_ok=False, energy=-1))
            bias = "school_library"
        else:
            # 保留晚实习；空闲晚由 evening_plan 按整周与疲劳选择，不限制星期。
            if intern_slot == "eve":
                intern = internship.activity(date, 'eve')
                eve_end = _fmt_min(min(_parse_hm(prep_hm), _parse_hm(dinner_end) + _rand_min(r, 150, 200)))
                if eve_end <= dinner_end:
                    eve_end = prep_hm
                slots.append(_s(dinner_end, eve_end, intern[0], "实习收工、有点累", intern[1],
                                list(intern[2]) + mat_hints, energy=0))
                bias = intern[1]
                if eve_end < prep_hm:
                    # 收工到准备睡之间补一点宿舍空档
                    slots.append(_s(eve_end, prep_hm, "宿舍刷小红书/抖音", "想冲浪、想跟他聊", "weekend_home",
                                    ["宿舍", "刷手机"] + mat_hints, energy=1))
                    bias = "weekend_home"
            else:
                slots.append(_s(dinner_end, prep_hm, "回宿舍休息", "放松、自然聊天",
                                "weekend_home", ["宿舍"]))
                bias = "weekend_home"
        _append_prep_sleep(slots[-1]["end"] if slots else prep_hm)

    for slot in slots:
        if slot.get('bucket') == 'work_internship':
            slot.update(internship.activity_details(date, 'eve' if slot['start'] >= '18:00' else 'pm'))

    day = {
        "date": date.isoformat(),
        "weekday": wd,
        "weekday_zh": "一二三四五六日"[wd],
        "kind": kind,
        "holiday": hol,
        "photo_bias": bias,
        "slots": slots,
        "notes": "auto-generated" if not (DRAFT_FILE.exists() and draft_range()[0] and draft_range()[0] <= date <= draft_range()[1]) else None,
        "material_hints": mat_hints,
        "sleep_bounds": {"wake": wake_hm, "prep_start": prep_hm, "prep_end": prep_end_hm,
                         "sleep_min": bounds["sleep"], "late": bounds["late"]},
    }

    import evening_plan
    if kind in ("weekend", "holiday", "winter_break", "summer_break"):
        day = evening_plan.local_weekend_afternoon(day)
    day = evening_plan.complete_sleep(evening_plan.enrich_day(day))
    # New generated days also cover the previous night's post-midnight wake
    # interval. Prefer a recorded previous day whenever one exists.
    prev_date = date - dt.timedelta(days=1)
    doc = _load_draft() or {}
    previous = next((d for d in doc.get("days") or [] if d.get("date") == prev_date.isoformat()), None)
    if previous is None:
        import schedule
        previous = {"date": prev_date.isoformat(), "sleep_bounds": {
            "sleep_min": schedule.day_plan(prev_date)["sleep"]}, "slots": [
                {"start": "23:00", "end": "24:00", "bucket": "weekend_home",
                 "activity": "准备睡"}]}
    return repair_midnight_carryover(day, previous,
        frozen=_frozen_on(doc, date) or _frozen_on(doc, prev_date), allow_legacy_frozen=True)



def generate_range(start: dt.date, end: dt.date) -> list[dict]:
    out = []
    d = start
    while d <= end:
        import evening_plan
        out.append(evening_plan.enrich_day(generate_day(d), history=out))
        d += dt.timedelta(days=1)
    return out


def _s(start, end, activity, tone, bucket, hints=None, photo_ok=True, energy=0):
    return {
        "start": start, "end": end, "activity": activity, "tone": tone,
        "bucket": bucket, "scene_hints": hints or [], "photo_ok": photo_ok,
        "energy": energy, "delay": [10, 120] if photo_ok else [20, 180],
    }


def _default_mat_query(kind, date: dt.date) -> str:
    if kind in ("weekend", "holiday") or "outing" in kind:
        return "郊游 出游 公园 拍照" if date.month in (10, 11) else "citywalk 探店 剧本杀 Livehouse 逛展"
    if kind == "winter_break":
        return "寒假 旅行 滑雪 雪山 禾木 三亚 崇礼"
    if kind == "summer_break":
        return "暑假 旅行 海边 徒步"
    if kind == "exam_week":
        return "图书馆 自习"
    return "校园 上课 排练 自习 宿舍"


def _material_hints(query: str, k=3) -> list[str]:
    """只读已采集素材；失败/空库 → []。绝不编造小红书帖。"""
    try:
        import material_store
        hits = material_store.search(query, platform="xiaohongshu", limit=k) or []
        if len(hits) < k:
            hits = hits + (material_store.search(query, platform="douyin", limit=k) or [])
        if not hits:
            hits = material_store.search(query, limit=k) or []
        out = []
        for h in hits:
            t = (h.get("title") or h.get("text") or "").strip()
            if not t:
                continue
            t = re.sub(r"\s+", " ", t)[:24]
            if t and t not in out:
                out.append(t)
        return out[:k]
    except Exception:
        return []



def format_day_brief(day: dict | None) -> str:
    """当日要点一行：日期+kind+最多 3 个非睡活动名（无起止时刻堆叠）。STATE「今日」仍是完整真相。"""
    if not day:
        return ""
    acts = []
    for s in day.get("slots") or []:
        if s.get("bucket") == "sleep":
            continue
        a = (s.get("activity") or "").strip()
        if a and a not in acts:
            acts.append(a)
        if len(acts) >= 3:
            break
    head = f"小甜{day.get('date','')}（{day.get('kind','')}）"
    if day.get("holiday"):
        head += f"·{day['holiday']}"
    if not acts:
        return head + "：有安排见状态「今日」"
    return head + "：" + "、".join(acts)


def format_day_line(day: dict | None) -> str:
    if not day:
        return ""
    bits = []
    for s in day.get("slots") or []:
        if s.get("bucket") == "sleep":
            continue
        bits.append(f"{s['start']}-{s['end']} {s['activity']}")
    head = f"小甜{day['date']}（周{day.get('weekday_zh','')}·{day.get('kind')}）"
    if day.get("holiday"):
        head += f"·{day['holiday']}"
    return head + "：" + "；".join(bits[:6])


def sync_to_memory(cid: str, now: dt.datetime | None = None, *, write_mem0: bool = True) -> dict:
    """写入 STATE.life_*（merge，不丢 emotion/proactive_beats 等非 life 键），并（可选）mem0 当日一行摘要。

    运行时 ensure：若 emotion / proactive_beats 缺失则种子默认值（非一次性手工灌数）。
    """
    now = now or now_local()
    if getattr(now, "tzinfo", None) is not None:
        now = now.astimezone(TZ).replace(tzinfo=None)
    today = get_day(now.date())
    recent = [d for d in recent_days(3, now.date()) if d]
    cur = slot_at(now)
    meta = {
        "source": "draft" if (today and not (today.get("notes") or "").startswith("auto")) else "auto",
        "draft_file": str(DRAFT_FILE.name),
        "synced_at": now.isoformat(timespec="seconds"),
        "tz": TZ_NAME,
    }
    payload = {
        "life_today": today,
        "life_recent": recent,
        "life_now": cur,
        "life_plan_meta": meta,
    }
    try:
        import memory
        # 缺 emotion / proactive_beats 时运行时种子，避免只靠一次性灌 STATE
        memory.ensure_defaults(cid, now=now)
        # update_state 是 read-modify-write merge；禁止整文件覆盖，以免丢掉 emotion 等
        memory.update_state(cid, **payload)
    except Exception as e:
        print(f"[life_calendar] STATE 写入失败：{e}")
    # mem0：只留当日一行基础摘要（非全日多版本）。完整槽位以 STATE「今日」为准。
    if write_mem0:
        brief = format_day_brief(today)
        if brief:
            try:
                import mem
                day_ts = (today or {}).get("date") or now.date().isoformat()
                # 同日只保留一条：清掉旧 life_schedule 再写入要点
                for h in mem.all_for(cid) or []:
                    meta = h.get("metadata") or {}
                    if meta.get("kind") != "life_schedule":
                        continue
                    if str(meta.get("ts") or "") == str(day_ts) or str(day_ts) in (h.get("memory") or ""):
                        mid = h.get("id")
                        if mid:
                            try:
                                mem.client().delete(mid)
                            except Exception:
                                pass
                mem.add(cid, brief, importance=5, kind="life_schedule", source="life_calendar",
                        ts=str(day_ts))
            except Exception as e:
                print(f"[life_calendar] mem0 摘要写入失败：{e}")
    return payload


def state_lines(st: dict | None, now: dt.datetime | None = None, *,
                lean: bool = True, attach_yesterday: bool = False,
                attach_memory: bool = False) -> list[str]:
    """给 brain.state_block 用的中文行。

    lean 默认：只给一行今日摘要（此刻活动由 schedule.current 承担，避免重复）。
    yesterday / schedule_memory 仅在 attach_* 为 True 时挂上。
    """
    now = now or now_local()
    st = st or {}
    today = st.get("life_today") or get_day(now.date())
    cur = resolve_life_now(st, now)
    lines = []
    if not lean and cur:
        lines.append(f"小甜此刻日程：{cur.get('activity')}（{cur.get('start')}-{cur.get('end')}，{cur.get('bucket')}）"
                     f"{'，适合随手拍给他' if cur.get('photo_ok') else ''}")
    # Keep the current evening detail outside the 120-character daily summary.
    # Planned companions and reservations must not turn into claimed facts.
    detailed = cur if (cur or {}).get('itinerary') else next(
        (s for s in (today or {}).get('slots', [])
         if s.get('itinerary') and s['start'] >= now.strftime('%H:%M')), None)
    if detailed:
        route = detailed.get('route_plan') or {}
        phases = '；'.join(f"{p['start']}-{p['end']} {p['activity']}" for p in detailed['itinerary'])
        lines.append(f"晚间计划（非已成行事实）：{detailed.get('destination_area')}；"
                     f"拟同行：{detailed.get('companion_plan')}；{detailed.get('purpose')}。"
                     f"{route.get('depart')}出发，{route.get('return_by')}前回寝。{phases}。"
                     "门店/场次与同行未确认，不说已约好/已购票；不能按时往返则取消。")
    elif today and today.get('evening_followup'):
        lines.append('今晚空闲时的可选延续（只在昨日实际成行时）：' + today['evening_followup']['plan'])
    if today:
        summary = " → ".join(
            f"{s['activity']}" for s in today.get("slots") or [] if s.get("bucket") != "sleep"
        )[:120]
        if summary:
            lines.append(("今日：" if lean else "小甜今日行程摘要：") + summary)
    attach_y = attach_yesterday or bool(st.get("attach_yesterday"))
    attach_m = attach_memory or bool(st.get("attach_schedule_memory"))
    if attach_y:
        recent = st.get("life_recent") or []
        if len(recent) >= 2 and recent[1]:
            slots = recent[1].get("slots") or [{}]
            lines.append("昨日：" + slots[min(2, len(slots) - 1)].get("activity", ""))
    if attach_m or not lean:
        try:
            import schedule_memory as sm
            doc = _load_draft() or {}
            week = sm.latest_ledger_week(doc)
            budget = 120 if lean else 280
            for ln in sm.format_memory_lines(today if isinstance(today, dict) else None, week, budget=budget):
                if ln:
                    lines.append(ln)
        except Exception:
            pass
    return lines


def photo_context(st: dict | None = None, now: dt.datetime | None = None) -> dict:
    """生图用：bucket、hints、activity。"""
    now = now or now_local()
    st = st or {}
    cur = resolve_life_now(st, now)
    today = st.get("life_today") or get_day(now.date())
    bucket = (cur or {}).get("bucket") or (today or {}).get("photo_bias") or "campus_life"
    hints = list((cur or {}).get("scene_hints") or [])
    if not hints:
        hints = list(BUCKET_SCENE.get(bucket, [])[:3])
    return {
        "bucket": bucket,
        "activity": (cur or {}).get("activity") or (today or {}).get("kind"),
        "hints": hints,
        "kind": (today or {}).get("kind"),
        "photo_ok": True if not cur else bool(cur.get("photo_ok", True)),
        # 槽位起止：生图/bridge 据此推断夜光（勿只靠 caption「晚上」）
        "start": (cur or {}).get("start"),
        "end": (cur or {}).get("end"),
    }


def pool_activity_id(entry) -> str | None:
    """WINTER_TRAVEL_POOL 等条目的可选 activity_pool id（第4元）。"""
    if isinstance(entry, (list, tuple)) and len(entry) >= 4:
        return entry[3]
    return None


def reasonableness_meta(activity_id: str | None) -> dict:
    if not activity_id:
        return {}
    meta = dict(ACTIVITY_REASONABLENESS.get(activity_id) or {})
    try:
        import reasonableness as rz
        act = rz.activities_by_id().get(activity_id)
        if act:
            meta.update(act.get("reasonableness") or {})
            meta["rating"] = rz.activity_rating(act)
    except Exception:
        pass
    return meta
