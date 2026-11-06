"""小甜的作息：北京舞蹈学院创意学院编导系（舞蹈编导，偏导演/编创）大三，校学生会文艺部副部长，影像社、话剧社成员，工作日（周一~周五）上课/基训/自习、偶有电影公司实习；休息日睡懒觉/出门玩。落盘细节以 life_calendar 为准（拟真优先，见 docs/life-schedule-design.md）。
思路参考 DiveWind/soul-agent 的 life_profiles/heartbeat，自行实现。
墙钟 Asia/Shanghai。每天的起床/睡觉时间按「日期种子」在模糊区间内随机抽取（同一天多次调用结果一致，禁止整点刻板）；每晚约 LATE_P 概率熬夜到 2~3 点。睡眠压力 sleep_pressure(now)：越晚指数增强，压晚聊意愿/拉长 reply_delay（仍可回消息）。
（≈每周 1~2 次）；周五、周六晚睡得更晚，休息日起得更晚。熬夜后第二天精力偏低。
config.json "schedule": 非空列表时改用固定时间表（旧格式 [{"start","end","activity","tone","delay","energy"}]）；
"life_schedule": 可覆盖下面 PARAMS 的任意键。接口：current(now) / reply_delay(now) / is_sleeping(now) / day_plan(date) / sleep_pressure(now)。"""
import datetime, random, hashlib
from zoneinfo import ZoneInfo
from config import load

TZ_NAME = "Asia/Shanghai"
TZ = ZoneInfo(TZ_NAME)

def now_local():
    """作息墙钟：默认 Asia/Shanghai naive；可由 config timezone/tz 覆盖（见 timeutil）。"""
    try:
        import timeutil as _tu
        return _tu.now_local()
    except Exception:
        return datetime.datetime.now(TZ).replace(tzinfo=None)

PARAMS = {
    "seed": "xiaotian",
    "workday_wake": [7 * 60 + 35, 20, 7 * 60, 8 * 60 + 15],     # 均值, 标准差, 下限, 上限（分钟）
    "weekend_wake": [10 * 60, 45, 8 * 60 + 30, 12 * 60],
    "sleep_school_night": [23 * 60 + 50, 25, 23 * 60, 24 * 60 + 50],  # 第二天要上课（周日~周四晚）
    "sleep_free_night": [24 * 60 + 40, 30, 23 * 60 + 40, 25 * 60 + 40],  # 周五、周六晚
    "late_p": 0.2, "late_range": [26 * 60, 27 * 60],                # 熬夜到 2:00~3:00
    # 睡眠压力：p = 1 - base ** (-elapsed / scale)，elapsed=距 anchor 的分钟（过午夜累加）
    # 可经 config.json life_schedule 覆盖。用于压晚聊主动意愿、拉长回复延迟。
    "sleep_pressure_anchor_min": 21 * 60 + 40,   # 约 21:40 起涨
    "sleep_pressure_base": 1.15,                # 指数底 (>1)
    "sleep_pressure_scale_min": 30,             # 尺度（分钟）
    "sleep_pressure_delay_factor": 2.5,          # reply_delay 最大再乘 (1 + factor*p)
}
WEEKEND_PLANS = [("出门逛街/探店", "边走边回、发照片的心情"), ("和室友出去玩", "玩嗨了回得慢"), ("宅在宿舍追剧", "懒洋洋、话多"),
                 ("去图书馆假装学习", "无聊、想找人聊")]

# 工作日课表（weekday -> 上午 / 下午 / 晚上(19:00~21:00) 活动；None 表示自由）
WEEK = {
    0: (("编导技法课", "偷偷回、很短"), ("现代舞基训", "练功中、回不了"), None),
    1: (("导演基础/镜头语言课", "偷偷回、很短"), ("剧目排练", "排练间隙才回，累但兴奋"), ("学生会文艺部例会", "开会摸鱼回、吐槽")),
    2: (("中外舞蹈史", "上课犯困、偷偷回"), ("编创作业排练", "排练间隙才回"), ("影像社拍片/剪片", "专注、回得慢")),
    3: (("编导技法课", "偷偷回、很短"), ("学生会活动筹备", "忙、话短"), None),
    4: (("剧目排练", "排练间隙才回"), ("自习/写编创阐述", "偶尔摸鱼回"), ("话剧社排练", "排练中、回得慢")),
}
CLASS_ACTS = {"上课", "编导技法课", "导演基础/镜头语言课", "中外舞蹈史"}   # 上课类
BUSY_ACTS = {"现代舞基训", "剧目排练", "编创作业排练", "话剧社排练", "影像社拍片/剪片", "学生会活动筹备"}

def _p():
    p = dict(PARAMS); p.update(load().get("life_schedule") or {}); return p

def _rng(date, p): return random.Random(int(hashlib.md5(f"{p['seed']}-{date.isoformat()}".encode()).hexdigest()[:12], 16))

def _draw(rng, spec):
    mu, sd, lo, hi = spec; return int(min(hi, max(lo, rng.gauss(mu, sd))))

def is_weekend(date): return date.weekday() >= 5

def day_plan(date, p=None):
    """当天计划：wake（当天分钟）、sleep（可 >1440，表示过了零点）、late（今晚熬夜）、weekend 活动。"""
    p = p or _p(); r = _rng(date, p)
    wake = _draw(r, p["weekend_wake" if is_weekend(date) else "workday_wake"])
    free_night = date.weekday() in (4, 5)                 # 周五、周六晚，第二天不上课
    sleep = _draw(r, p["sleep_free_night" if free_night else "sleep_school_night"])
    late = r.random() < p["late_p"]
    if late: sleep = max(sleep, r.randint(*p["late_range"]))
    return {"date": date, "wake": wake, "sleep": sleep, "late": late, "weekend": is_weekend(date),
            "outing": WEEKEND_PLANS[r.randrange(len(WEEKEND_PLANS))]}

def _b(act, tone, delay, energy): return {"activity": act, "tone": tone, "delay": delay, "energy": energy}

def _awake_block(plan, t, prev_late):
    """plan 当天 t 分钟（可 >1440）时的活动；t ∈ [wake, sleep)。"""
    w, s = plan["wake"], plan["sleep"]; tired = -1 if prev_late else 0
    if s - t <= 40: return _b("准备睡觉", "犯困、黏人、催他睡", [5, 40], -2)
    if t >= 24 * 60 + 30 or (plan["late"] and t >= 24 * 60): return _b("熬夜刷手机", "夜猫子模式、有点亢奋又困", [3, 30], -1)
    if t - w < 45: return _b("刚起床", "刚醒、慢半拍、带点起床气" if prev_late else "刚醒、慢半拍", [10, 90], -1 + tired)
    if plan["weekend"]:
        if t < 13 * 60: return _b("赖床/吃早午饭", "懒洋洋", [5, 60], 0 + tired)
        if t < 18 * 60: act, tone = plan["outing"]; return _b(act, tone, [10, 120], 1 + tired)
        if t < 19 * 60 + 30: return _b("吃晚饭", "放松、话多一点", [5, 40], 1)
        return _b("自由时间", "最放松、最爱闹", [3, 20], 1)
    am, pm, ev = WEEK.get(plan["date"].weekday(), (("上课", "偷偷回、很短"), ("上课/自习", "偶尔摸鱼回"), None))
    if t < 12 * 60: return _b(am[0], am[1], [30, 180], 0 + tired)
    if t < 13 * 60 + 30: return _b("吃午饭", "边吃边聊、轻松", [5, 40], 1)
    if t < 17 * 60 + 30: return _b(pm[0], pm[1], [40, 180] if pm[0] in BUSY_ACTS else [20, 120], 0 + tired)
    if t < 19 * 60: return _b("吃晚饭", "放松、话多一点", [5, 40], 1)
    if ev and t < 21 * 60: return _b(ev[0], ev[1], [30, 120], 0)
    return _b("自由时间", "最放松、最爱闹", [3, 20], 1)

def _m(hhmm): h, m = map(int, hhmm.split(":")); return h * 60 + m

def _static(now, blocks):
    t = now.hour * 60 + now.minute
    for b in blocks:
        s, e = _m(b["start"]), _m(b["end"])
        if (s <= t < e) if s < e else (t >= s or t < e): return b
    return _b("发呆", "", [5, 30], 0)

def current(now=None):
    now = now or now_local(); fixed = load().get("schedule")
    if fixed: return _static(now, fixed)
    # 人设落盘/自动日程优先（life_calendar）
    try:
        import life_calendar as lc
        slot = lc.slot_at(now)
        if slot and slot.get("activity"):
            return _b(slot["activity"], slot.get("tone") or "", slot.get("delay") or [5, 40],
                      slot.get("energy") or 0)
    except Exception:
        pass
    p = _p(); d = now.date(); t = now.hour * 60 + now.minute
    today, yday = day_plan(d, p), day_plan(d - datetime.timedelta(days=1), p)
    if t + 1440 < yday["sleep"]:                       # 昨晚还没睡
        return _awake_block(yday, t + 1440, day_plan(d - datetime.timedelta(days=2), p)["late"])
    if t < today["wake"]: return _b("睡觉", "迷迷糊糊、很短、带点起床气", [60, 300], -4)
    if t >= today["sleep"]: return _b("睡觉", "迷迷糊糊、很短", [60, 300], -4)
    return _awake_block(today, t, yday["late"])

def sleep_pressure(now=None, *, base=None, scale=None, anchor=None):
    """越晚越高的睡眠约束 ∈[0,1]。公式：1 - base^(-(elapsed)/scale)。

    elapsed：从当日 anchor（默认约 21:40）起算的分钟；过午夜、尚未 wake 时用 t+1440。
    仍可与用户聊天；主动意愿/回复延迟消费此值。详见 docs/life-schedule-design.md。
    """
    now = now or now_local()
    p = _p()
    base = float(base if base is not None else p.get("sleep_pressure_base", 1.15))
    scale = float(scale if scale is not None else p.get("sleep_pressure_scale_min", 30))
    anchor = int(anchor if anchor is not None else p.get("sleep_pressure_anchor_min", 21 * 60 + 40))
    if base <= 1.0 or scale <= 0:
        return 0.0
    t = now.hour * 60 + now.minute
    wake = day_plan(now.date(), p)["wake"]
    if t < wake:
        elapsed = (t + 1440) - anchor
    else:
        elapsed = t - anchor
    if elapsed <= 0:
        return 0.0
    return min(1.0, 1.0 - (base ** (-elapsed / scale)))


def reply_delay(now=None, rng=random):
    now = now or now_local()
    lo, hi = current(now).get("delay", [5, 30])
    d = rng.uniform(lo, hi)
    sp = sleep_pressure(now)
    fac = float(_p().get("sleep_pressure_delay_factor", 2.5))
    return d * (1.0 + fac * sp)


def is_sleeping(now=None):
    return current(now).get("activity") == "睡觉"

if __name__ == "__main__":
    d = datetime.date.today()
    for i in range(7):
        pl = day_plan(d + datetime.timedelta(days=i))
        print(pl["date"], "周" + "一二三四五六日"[pl["date"].weekday()], f"起{pl['wake']//60}:{pl['wake']%60:02d}",
              f"睡{pl['sleep']//60%24}:{pl['sleep']%60:02d}", "熬夜" if pl["late"] else "")
