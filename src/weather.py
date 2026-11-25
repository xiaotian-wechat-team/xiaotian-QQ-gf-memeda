"""天气工具（免 key：Open-Meteo）。

两地：
  - bda / 小甜：北京海淀北舞（万寿寺路校区）
  - yanyuan / 用户：config.json weather.lat/lon（默认海淀燕园）

每天由 tools/weather_refresh.py 把当前实况 + 未来 3 天写到
data/weather_forecast.json（北京时间），并顺手更新 data/weather_cache.json。
失败只记日志，不覆盖上次有效预报。

不要把播报表塞进每条提示词或 life_schedule JSON。过期缓存不能当成现在的天气。
  weather.refresh()                          # 日刷 / 午后实况
  weather.lookup("yanyuan", "tomorrow")   # 读预报；过期再联网，否则 stale
  weather.call_tool({"place":"bda","date":"today"})
  weather.needs(user_text) / infer_place / infer_day
  weather.prompt_line(text)                  # 只挂新鲜的短句，不挂湿度风力全表

限速：source_poll open_meteo_<place>（各地独立 ≥1h）。定时刷新用 force 越过静默时段。
"""
from __future__ import annotations

import json, time, sys, os, re, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from config import load, ROOT
import source_poll

try:
    from zoneinfo import ZoneInfo
    BJ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    BJ = timezone(timedelta(hours=8))

_cache = {}  # (lat,lon) -> (ts, text)
CACHE_FILE = ROOT / "data" / "weather_cache.json"
BASELINE_FILE = ROOT / "data" / "weather_baseline.json"
FORECAST_FILE = ROOT / "data" / "weather_forecast.json"
TTL = 3600
FORECAST_TTL = 26 * 3600  # 早间全量撑到次日刷新，留一点余量
KEY_HOURS = (8, 14, 20)   # 北京时间早 / 午 / 晚

# 用户默认（MacBook 2026-10-23 定位）；config.weather.* 可覆盖
DEFAULT_USER = {"lat": 39.9925, "lon": 116.31, "city": "北京海淀燕园"}
# 北舞主校区：OSM amenity=university「北京舞蹈学院」万寿寺路 1 号
DEFAULT_BDA = {"lat": 39.9459, "lon": 116.3130, "city": "北京海淀北舞"}

PLACE_BDA = "bda"
PLACE_USER = "yanyuan"
PLACE_IDS = (PLACE_BDA, PLACE_USER)

_PLACE_ALIASES = {
    "bda": PLACE_BDA, "her": PLACE_BDA, "she": PLACE_BDA, "school": PLACE_BDA,
    "campus": PLACE_BDA, "北舞": PLACE_BDA, "小甜": PLACE_BDA, "海淀": PLACE_BDA,
    "万寿寺": PLACE_BDA, "beijing_dance_academy": PLACE_BDA,
    "yanyuan": PLACE_USER, "user": PLACE_USER, "him": PLACE_USER, "he": PLACE_USER,
    "燕园": PLACE_USER, "北京大学": PLACE_USER, "这边": PLACE_USER, "北大": PLACE_USER,
}

RAIN_SNOW = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
WMO = {0: "晴", 1: "大致晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇", 51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
       56: "冻毛毛雨", 57: "冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪",
       75: "大雪", 77: "雪粒", 80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪", 95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹"}

DEFAULT_ATTACH = {
    "temp_delta_c": 5.0,
    "day_swing_c": 12.0,
    "precip_prob": 50,
    "aqi_delta": 50,
    "aqi_high": 150,
    "baseline_hours": 48,
    "baseline_max": 24,
    "fetch_aqi": True,
    "auto_variance": True,  # brain：无 needs() 时是否仍按方差挂 BDA
}

# 聊天里提到才值得拉/挂天气。放宽到穿厚/冷吗/带伞，但不吃普通闲聊（冷幽默、在干嘛）。
_NEED_RE = re.compile(
    r"(天气|气温|下雨|下雪|降雨|雨雪|冷不冷|热不热|冷吗|热吗|凉吗|"
    r"多少度|几度|穿什么|穿啥|穿厚|穿暖|多穿|"
    r"外套|加衣服|带伞|伞|防晒|雾霾|AQI|空气质量|体感|降温|升温|刮风|"
    r"出门.*冷|冷.*出门|会不会下雨|会不会下雪|要不要带伞|有雨|有雪)",
    re.I,
)

TOOL_NAME = "get_weather"
TOOL_SPEC = {
    "name": TOOL_NAME,
    "description": "按地点和日期查小甜已刷新的预报（data/weather_forecast.json）。bda=海淀北舞，yanyuan=燕园。日期用今天/明天/后天。过期会再拉 Open-Meteo，拉不到就明确 stale，不要把旧雨雪当成现在。不要写进日程 JSON。",
    "parameters": {
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "enum": ["bda", "yanyuan"],
                "description": "bda=小甜海淀北舞；yanyuan=用户海淀燕园",
            },
            "date": {
                "type": "string",
                "description": "今天/明天/后天，或 today/tomorrow/day_after，或 YYYY-MM-DD（北京时间）。默认今天。",
            },
            "cache_only": {
                "type": "boolean",
                "description": "True 时只读预报和缓存、不发起网络请求",
            },
        },
        "required": ["place"],
    },
}


def attach_cfg(w=None):
    w = w if w is not None else load().get("weather", {})
    return {**DEFAULT_ATTACH, **(w.get("attach") or {})}


def _cfg(w=None):
    return w if w is not None else load().get("weather", {})


def places(w=None):
    """返回 {place_id: {lat,lon,city}}；yanyuan 跟顶层 lat/lon/city（兼容旧配置）。"""
    w = _cfg(w)
    custom = w.get("places") or {}
    user = {
        "lat": float(w.get("lat", DEFAULT_USER["lat"])),
        "lon": float(w.get("lon", DEFAULT_USER["lon"])),
        "city": w.get("city") or DEFAULT_USER["city"],
    }
    bda_c = custom.get("bda") or {}
    bda = {
        "lat": float(bda_c.get("lat", DEFAULT_BDA["lat"])),
        "lon": float(bda_c.get("lon", DEFAULT_BDA["lon"])),
        "city": bda_c.get("city") or DEFAULT_BDA["city"],
    }
    lx_c = custom.get("yanyuan") or {}
    if lx_c:
        user = {
            "lat": float(lx_c.get("lat", user["lat"])),
            "lon": float(lx_c.get("lon", user["lon"])),
            "city": lx_c.get("city") or user["city"],
        }
    return {PLACE_BDA: bda, PLACE_USER: user}


def resolve_place(place=None, w=None):
    """place → (place_id, loc)。默认 yanyuan（兼容 now_weather 旧行为）。"""
    w = _cfg(w)
    if not w.get("enabled", False):
        return None, None, "weather.enabled=false"
    pid = PLACE_USER if place is None or place == "" else _PLACE_ALIASES.get(str(place).strip().lower(), str(place).strip().lower())
    # 中文别名在 lower 后丢失：再试原始
    if pid not in PLACE_IDS:
        pid = _PLACE_ALIASES.get(str(place).strip(), pid)
    if pid not in PLACE_IDS:
        return None, None, f"unknown-place:{place}"
    loc = places(w)[pid]
    return pid, loc, "ok"


def status(w=None):
    """兼容旧 API：返回用户地点 loc 或 (None, reason)。"""
    pid, loc, why = resolve_place(PLACE_USER, w)
    if not loc:
        return None, why
    return loc, why


def tool_spec():
    return dict(TOOL_SPEC)


def needs(text: str | None) -> bool:
    """聊天原文是否在谈天气/穿衣/雨雪等（按需挂载门闩）。"""
    if not text:
        return False
    return bool(_NEED_RE.search(text))


def infer_place(text: str | None, default: str = PLACE_BDA) -> str:
    """从聊天推断地点：提燕园/这边→用户；提北舞/海淀→BDA；默认小甜侧。"""
    t = text or ""
    # 用户侧关键词优先（「你们那边」仍偏她；「我这边/燕园」偏用户）
    if re.search(r"(燕园|北京大学|北大|我这边|我这|我们这边)", t):
        return PLACE_USER
    if re.search(r"(北舞|海淀|万寿寺|学校|宿舍|校园|你那边|你那)", t):
        return PLACE_BDA
    return default if default in PLACE_IDS else PLACE_BDA


def infer_day(text: str | None) -> str:
    """今天 / 明天 / 后天。大后天超出 3 日预报，交给 lookup 返回 bad-date。"""
    t = text or ""
    if re.search(r"大后天", t):
        return "out_of_range"
    if re.search(r"后天|后儿", t):
        return "day_after"
    if re.search(r"明天|明日|明早|明晚", t):
        return "tomorrow"
    return "today"


def url(loc):
    q = urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "Asia/Shanghai",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max", "forecast_days": 1,
    })
    return f"https://api.open-meteo.com/v1/forecast?{q}"


def aqi_url(loc):
    q = urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "Asia/Shanghai",
        "current": "us_aqi,pm2_5",
    })
    return f"https://air-quality-api.open-meteo.com/v1/air-quality?{q}"


def snap_from_data(d, aqi=None):
    c, dl = d["current"], d.get("daily", {}) or {}
    tmin = (dl.get("temperature_2m_min") or [None])[0]
    tmax = (dl.get("temperature_2m_max") or [None])[0]
    precip = (dl.get("precipitation_probability_max") or [None])[0]
    return {
        "temp": float(c["temperature_2m"]),
        "code": int(c.get("weather_code") or 0),
        "tmin": None if tmin is None else float(tmin),
        "tmax": None if tmax is None else float(tmax),
        "precip_prob": None if precip is None else float(precip),
        "aqi": None if aqi is None else float(aqi),
    }


def fmt(d, city, aqi=None):
    c, dl = d["current"], d.get("daily", {})
    s = (f"{city} {WMO.get(c.get('weather_code'), '未知')} {c['temperature_2m']:.0f}°C（体感{c['apparent_temperature']:.0f}°C）"
         f" 湿度{c['relative_humidity_2m']}% 风{c['wind_speed_10m']:.0f}km/h")
    if dl.get("temperature_2m_max"):
        s += f"，今天 {dl['temperature_2m_min'][0]:.0f}~{dl['temperature_2m_max'][0]:.0f}°C"
        p = (dl.get("precipitation_probability_max") or [None])[0]
        if p is not None:
            s += f" 降水概率{p}%"
    if aqi is not None:
        s += f" AQI{int(aqi)}"
    return s


def parse_snap_text(text):
    if not text:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)°C", text)
    temp = float(m.group(1)) if m else None
    rng = re.search(r"今天\s*(-?\d+(?:\.\d+)?)~(-?\d+(?:\.\d+)?)°C", text)
    tmin = float(rng.group(1)) if rng else None
    tmax = float(rng.group(2)) if rng else None
    p = re.search(r"降水概率(\d+(?:\.\d+)?)%", text)
    precip = float(p.group(1)) if p else None
    aq = re.search(r"AQI(\d+)", text)
    aqi = float(aq.group(1)) if aq else None
    code = None
    for k, name in WMO.items():
        if name and name in text:
            code = k
            break
    if temp is None:
        return None
    return {"temp": temp, "code": code if code is not None else 0, "tmin": tmin, "tmax": tmax,
            "precip_prob": precip, "aqi": aqi}


def _key(loc):
    return "%.4f,%.4f" % (loc["lat"], loc["lon"])


def _read_disk():
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _write_disk(k, ts, text, snap=None):
    from preview_mode import is_preview
    if is_preview():
        return
    d = _read_disk()
    ent = {"ts": ts, "text": text}
    if snap is not None:
        ent["snap"] = snap
    d[k] = ent
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, CACHE_FILE)


def _read_baseline_raw():
    try:
        return json.loads(BASELINE_FILE.read_text())
    except Exception:
        return {}


def _write_baseline_raw(data):
    from preview_mode import is_preview
    if is_preview():
        return
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BASELINE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, BASELINE_FILE)


def _baseline_samples(place_id, data=None):
    """兼容旧格式 {samples:[...]} = yanyuan；新格式 {places:{id:{samples}}}。"""
    data = data if data is not None else _read_baseline_raw()
    if "places" in data:
        return list((data.get("places") or {}).get(place_id, {}).get("samples") or [])
    if place_id == PLACE_USER:
        return list(data.get("samples") or [])
    return []


def _write_baseline_samples(place_id, samples):
    data = _read_baseline_raw()
    if "places" not in data:
        old = list(data.get("samples") or [])
        data = {"places": {PLACE_USER: {"samples": old}}}
        if "samples" in data:
            pass
    places_b = data.setdefault("places", {})
    places_b[place_id] = {"samples": samples}
    # 清掉顶层旧 samples，避免双源
    data.pop("samples", None)
    _write_baseline_raw(data)


def record_baseline(snap, cfg=None, now=None, place=PLACE_USER):
    if not snap:
        return
    cfg = attach_cfg(cfg)
    now = time.time() if now is None else now
    pid, _, why = resolve_place(place, cfg if isinstance(cfg, dict) and "lat" in cfg else None)
    if not pid:
        pid = PLACE_USER
    samples = _baseline_samples(pid)
    samples.append({"ts": now, **{k: snap.get(k) for k in ("temp", "code", "tmin", "tmax", "precip_prob", "aqi")}})
    cut = now - float(cfg["baseline_hours"]) * 3600
    samples = [s for s in samples if s.get("ts", 0) >= cut][-int(cfg["baseline_max"]):]
    _write_baseline_samples(pid, samples)


def baseline_means(cfg=None, now=None, exclude_latest=True, place=PLACE_USER):
    cfg = attach_cfg(cfg)
    now = time.time() if now is None else now
    pid, _, _ = resolve_place(place)
    if not pid:
        pid = PLACE_USER
    cut = now - float(cfg["baseline_hours"]) * 3600
    samples = [s for s in _baseline_samples(pid) if s.get("ts", 0) >= cut]
    if exclude_latest and len(samples) >= 2:
        samples = samples[:-1]
    temps = [s["temp"] for s in samples if s.get("temp") is not None]
    aqis = [s["aqi"] for s in samples if s.get("aqi") is not None]
    return {
        "temp": (sum(temps) / len(temps)) if temps else None,
        "aqi": (sum(aqis) / len(aqis)) if aqis else None,
        "n": len(samples),
        "place": pid,
    }


def variance_reasons(snap, baseline=None, cfg=None):
    if not snap:
        return []
    cfg = attach_cfg(cfg)
    base = baseline if baseline is not None else baseline_means(cfg)
    reasons = []
    code = snap.get("code")
    if code is not None and int(code) in RAIN_SNOW:
        reasons.append(f"rain_snow:{WMO.get(int(code), code)}")
    pp = snap.get("precip_prob")
    if pp is not None and pp >= float(cfg["precip_prob"]):
        reasons.append(f"precip_prob:{pp:.0f}>={cfg['precip_prob']}")
    tmin, tmax = snap.get("tmin"), snap.get("tmax")
    if tmin is not None and tmax is not None and (tmax - tmin) >= float(cfg["day_swing_c"]):
        reasons.append(f"day_swing:{tmax - tmin:.0f}>={cfg['day_swing_c']}")
    bt = base.get("temp") if base else None
    if bt is not None and snap.get("temp") is not None:
        d = abs(snap["temp"] - bt)
        if d >= float(cfg["temp_delta_c"]):
            reasons.append(f"temp_delta:{d:.1f}>={cfg['temp_delta_c']}(vs {bt:.1f})")
    aqi = snap.get("aqi")
    if aqi is not None:
        if aqi >= float(cfg["aqi_high"]):
            reasons.append(f"aqi_high:{aqi:.0f}>={cfg['aqi_high']}")
        ba = base.get("aqi") if base else None
        if ba is not None and abs(aqi - ba) >= float(cfg["aqi_delta"]):
            reasons.append(f"aqi_delta:{abs(aqi - ba):.0f}>={cfg['aqi_delta']}(vs {ba:.0f})")
    return reasons


def _now_ts(now=None):
    if now is None:
        return time.time()
    if isinstance(now, (int, float)):
        return float(now)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=BJ).timestamp()
        return now.timestamp()
    raise TypeError(type(now))


def bj_now(now=None):
    """北京时间。naive datetime 按北京墙钟理解，不按机器本地时区重解释。"""
    return datetime.fromtimestamp(_now_ts(now), BJ)


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _code(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ts_fresh(ts, now=None, ttl=TTL):
    if ts is None:
        return False
    try:
        age = _now_ts(now) - float(ts)
    except (TypeError, ValueError):
        return False
    # 允许两分钟钟差，避免「刚写完、now 略早」被当成过期。
    return -120 <= age < float(ttl)


def _entry_fresh(ent, now=None, ttl=TTL):
    return bool(ent) and _ts_fresh((ent or {}).get("ts"), now, ttl)


def _atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def _refresh_log(msg, log_path=None):
    line = bj_now().strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, file=sys.stderr)
    if not log_path:
        return
    try:
        p = log_path if hasattr(log_path, "open") else __import__("pathlib").Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"weather log failed: {e}", file=sys.stderr)


def forecast_url(loc, days=3):
    q = urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "Asia/Shanghai",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "forecast_days": int(days),
    })
    return f"https://api.open-meteo.com/v1/forecast?{q}"


def load_forecast():
    try:
        return json.loads(FORECAST_FILE.read_text())
    except Exception:
        return None


def _forecast_fresh(doc, now=None, place=None):
    if not isinstance(doc, dict):
        return False
    block = (doc.get("places") or {}).get(place) if place else doc
    if not isinstance(block, dict):
        return False
    ts = block.get("forecast_updated_ts")
    if ts is None:
        ts = doc.get("forecast_updated_ts", doc.get("updated_ts"))
    if ts is None:
        return False
    try:
        age = _now_ts(now) - float(ts)
    except (TypeError, ValueError):
        return False
    return -120 <= age <= FORECAST_TTL


def resolve_day(date, now=None):
    """返回 {offset,date,label}。只覆盖今天起 3 天（北京时间）。"""
    today = bj_now(now).date()
    if date is None or str(date).strip() == "":
        off = 0
    else:
        raw = str(date).strip()
        key = raw.lower()
        aliases = {
            "today": 0, "今天": 0, "今日": 0, "现在": 0,
            "tomorrow": 1, "明天": 1, "明日": 1,
            "day_after": 2, "后天": 2,
        }
        if key in aliases:
            off = aliases[key]
        elif raw in aliases:
            off = aliases[raw]
        else:
            try:
                off = (datetime.strptime(raw[:10], "%Y-%m-%d").date() - today).days
            except Exception:
                return None
    if off < 0 or off > 2:
        return None
    day = today + timedelta(days=off)
    return {"offset": off, "date": day.isoformat(), "label": ("今天", "明天", "后天")[off]}


def _short_place(pid, city=None):
    if pid == PLACE_BDA:
        return "海淀"
    if pid == PLACE_USER:
        return "燕园"
    return (city or "那边").replace("北京", "") or "那边"


def warmth_word(temp):
    if temp is None:
        return None
    t = float(temp)
    if t <= 0:
        return "冷"
    if t <= 8:
        return "凉"
    if t <= 15:
        return "有点凉"
    if t <= 22:
        return "舒服"
    if t <= 28:
        return "暖"
    return "热"


def wet_phrase(code, precip):
    c = _code(code)
    if c in {71, 73, 75, 77, 85, 86}:
        return "有雪"
    if c in RAIN_SNOW:
        return "有雨"
    if precip is not None and float(precip) >= 50:
        return "可能下雨"
    if (precip is not None and float(precip) < 20) or c in (0, 1, 2, 3):
        return "干、不下雨"
    return None


def brief_from_snap(pid, snap, label, city=None):
    """给人看的短句，不带温度/体感/湿度/风/AQI。"""
    if not snap:
        return None
    temp = snap.get("temp")
    if label in ("今天", "明天", "后天") and snap.get("tmin") is not None and snap.get("tmax") is not None:
        temp = (float(snap["tmin"]) + float(snap["tmax"])) / 2
    bits = [b for b in (warmth_word(temp), wet_phrase(snap.get("code"), snap.get("precip_prob"))) if b]
    if not bits:
        return None
    return f"{_short_place(pid, city)}{label}{'、'.join(bits)}"


def _facts_from(snap):
    if not snap:
        return {}
    cond = WMO.get(int(snap["code"])) if snap.get("code") is not None else None
    facts = {
        "condition": cond,
        "temp": snap.get("temp"),
        "tmin": snap.get("tmin"),
        "tmax": snap.get("tmax"),
        "precip_prob": snap.get("precip_prob"),
        "aqi": snap.get("aqi"),
    }
    return {k: v for k, v in facts.items() if v is not None}


def _row_for(doc, pid, date_iso):
    block = ((doc or {}).get("places") or {}).get(pid) or {}
    for d in block.get("days") or []:
        if d.get("date") == date_iso:
            return d
    return None


def _snap_from_day(day, current=None):
    current = current or {}
    temp = current.get("temp")
    if temp is None and day.get("tmin") is not None and day.get("tmax") is not None:
        temp = (float(day["tmin"]) + float(day["tmax"])) / 2
    code = day.get("code")
    if code is None:
        code = current.get("code")
    precip = day.get("precip_prob")
    if precip is None:
        precip = current.get("precip_prob")
    return {
        "temp": temp, "code": code,
        "tmin": day.get("tmin"), "tmax": day.get("tmax"),
        "precip_prob": precip, "aqi": current.get("aqi"),
    }


def _today_row(doc, pid, now=None):
    return _row_for(doc, pid, bj_now(now).date().isoformat())


def ambient_observation(place=PLACE_BDA, w=None, now=None):
    """只返回新鲜实况或当天预报。过期雪景不会被当成现在。"""
    w = _cfg(w)
    pid, loc, _why = resolve_place(place, w)
    if not loc:
        return None
    doc = load_forecast() or {}
    block = (doc.get("places") or {}).get(pid) or {}
    cur = block.get("current") or {}
    if _ts_fresh(cur.get("updated_ts"), now):
        snap = {
            "temp": cur.get("temp"), "code": cur.get("code"),
            "tmin": None, "tmax": None, "precip_prob": cur.get("precip_prob"),
            "aqi": cur.get("aqi"),
        }
        row = _today_row(doc, pid, now) if _forecast_fresh(doc, now, pid) else None
        if row:
            snap["tmin"] = row.get("tmin")
            snap["tmax"] = row.get("tmax")
            if snap.get("precip_prob") is None:
                snap["precip_prob"] = row.get("precip_prob")
        if snap.get("temp") is not None or snap.get("code") is not None:
            return {"snap": snap, "label": "这会儿", "source": "current", "place": pid, "city": loc["city"]}
    ent = _read_disk().get(_key(loc))
    if _entry_fresh(ent, now):
        snap = ent.get("snap") or parse_snap_text(ent.get("text") or "")
        if snap:
            return {"snap": snap, "label": "这会儿", "source": "cache", "place": pid, "city": loc["city"]}
    row = _today_row(doc, pid, now) if _forecast_fresh(doc, now, pid) else None
    if row:
        return {"snap": _snap_from_day(row, None), "label": "今天", "source": "daily",
                "place": pid, "city": loc["city"]}
    return None


def _block_from_raw(raw, loc, aqi, now):
    c = raw.get("current") or {}
    daily = raw.get("daily") or {}
    hourly = raw.get("hourly") or {}
    times = list(hourly.get("time") or [])
    h_code = list(hourly.get("weather_code") or [])
    h_temp = list(hourly.get("temperature_2m") or [])
    h_pop = list(hourly.get("precipitation_probability") or [])
    dates = list(daily.get("time") or [])[:3]
    d_code = list(daily.get("weather_code") or [])
    d_min = list(daily.get("temperature_2m_min") or [])
    d_max = list(daily.get("temperature_2m_max") or [])
    d_pop = list(daily.get("precipitation_probability_max") or [])
    days = []
    for i, date in enumerate(dates):
        code = _code(d_code[i] if i < len(d_code) else None)
        slots = []
        for hour in KEY_HOURS:
            prefix = f"{date}T{hour:02d}"
            idx = next((j for j, t in enumerate(times) if str(t).startswith(prefix)), None)
            if idx is None:
                continue
            sc = _code(h_code[idx] if idx < len(h_code) else None)
            slots.append({
                "hour": hour,
                "temp": _num(h_temp[idx] if idx < len(h_temp) else None),
                "code": sc,
                "condition": WMO.get(sc) if sc is not None else None,
                "precip_prob": _num(h_pop[idx] if idx < len(h_pop) else None),
            })
        days.append({
            "date": date,
            "tmin": _num(d_min[i] if i < len(d_min) else None),
            "tmax": _num(d_max[i] if i < len(d_max) else None),
            "precip_prob": _num(d_pop[i] if i < len(d_pop) else None),
            "code": code,
            "condition": WMO.get(code) if code is not None else None,
            "slots": slots,
        })
    cc = _code(c.get("weather_code"))
    current = {
        "updated": bj_now(now).isoformat(timespec="seconds"),
        "updated_ts": _now_ts(now),
        "temp": _num(c.get("temperature_2m")),
        "apparent": _num(c.get("apparent_temperature")),
        "humidity": _num(c.get("relative_humidity_2m")),
        "wind": _num(c.get("wind_speed_10m")),
        "code": cc if cc is not None else 0,
        "condition": WMO.get(cc if cc is not None else 0, "未知"),
        "aqi": aqi,
    }
    return {"city": loc["city"], "lat": loc["lat"], "lon": loc["lon"], "current": current, "days": days,
            "forecast_updated_ts": _now_ts(now), "forecast_updated": current["updated"]}


def _snap_for_cache(block):
    current = block.get("current") or {}
    day = (block.get("days") or [{}])[0] or {}
    return {
        "temp": current.get("temp"),
        "code": current.get("code") if current.get("code") is not None else day.get("code"),
        "tmin": day.get("tmin"), "tmax": day.get("tmax"),
        "precip_prob": day.get("precip_prob"),
        "aqi": current.get("aqi"),
    }


def refresh(places=None, *, fetch=None, w=None, current_only=False, now=None, log_path=None, force=False):
    """刷新两地预报。失败保留上次有效文件。current_only 只换实况、不动已有逐日。"""
    from preview_mode import is_preview
    if is_preview():
        _refresh_log("preview, skip write", log_path)
        return {"ok": False, "why": "preview", "kept": True, "errors": []}
    w = _cfg(w)
    ids = list(places or PLACE_IDS)
    old = load_forecast() or {}
    # Migrate legacy global timestamps before retaining any unrefreshed place.
    old_places = {pid: {"forecast_updated_ts": old.get("forecast_updated_ts", old.get("updated_ts", 0)),
                        "forecast_updated": old.get("forecast_updated", old.get("updated")), **block}
                  for pid, block in (old.get("places") or {}).items()}
    new_places = dict(old_places)
    errors = []
    fetched = []
    for place in ids:
        pid, loc, why = resolve_place(place, w)
        if not loc:
            errors.append(str(why))
            _refresh_log(str(why), log_path)
            continue
        gate = source_poll.acquire(f"open_meteo_{pid}", force=force)
        if not gate:
            msg = f"{pid}: rate-limited"
            errors.append(msg)
            _refresh_log(msg + "；保留上次有效预报", log_path)
            continue
        try:
            raw = (fetch or _fetch)(forecast_url(loc))
            aqi = None
            if fetch is None and attach_cfg(w).get("fetch_aqi", True):
                aqi = _fetch_aqi(loc, None)
            block = _block_from_raw(raw, loc, aqi, now)
            if current_only and (old_places.get(pid) or {}).get("days"):
                kept = dict(old_places[pid])
                kept["current"] = block["current"]
                kept["city"] = block["city"]
                kept["lat"] = block["lat"]
                kept["lon"] = block["lon"]
                block = kept
            elif len(block.get("days") or []) < 3:
                raise RuntimeError("forecast days < 3")
            if block.get("current", {}).get("temp") is None:
                raise RuntimeError("missing current temp")
            text = fmt(raw, loc["city"], aqi=aqi)
            snap = _snap_for_cache(block)
            gate.done(ok=True)
            fetched.append((pid, loc, block, snap, text))
        except Exception as e:
            try:
                gate.done(error=True)
            except Exception:
                pass
            msg = f"{pid}: {e}"
            errors.append(msg)
            _refresh_log(msg + "；保留上次有效预报", log_path)
    if not fetched:
        _refresh_log("refresh failed, keep previous forecast", log_path)
        return {"ok": False, "why": "refresh-failed", "errors": errors, "kept": True}
    for pid, _loc, block, _snap, _text in fetched:
        new_places[pid] = block
    ts = _now_ts(now)
    iso = bj_now(now).isoformat(timespec="seconds")
    doc = {
        "updated": iso,
        "updated_ts": ts,
        "tz": "Asia/Shanghai",
        "forecast_days": 3,
        "places": new_places,
    }
    oldest = min(new_places.values(), key=lambda b: b.get("forecast_updated_ts") or 0)
    doc["forecast_updated"] = oldest.get("forecast_updated")
    doc["forecast_updated_ts"] = oldest.get("forecast_updated_ts") or 0
    _atomic_json(FORECAST_FILE, doc)
    for pid, loc, _block, snap, text in fetched:
        _write_disk(_key(loc), ts, text, snap=snap)
        try:
            record_baseline(snap, w, now=ts, place=pid)
        except Exception as e:
            _refresh_log(f"{pid}: baseline {e}", log_path)
    why_out = "ok" if not errors else "partial"
    _refresh_log(
        f"refresh {why_out} current_only={current_only} places={','.join(p for p, *_ in fetched)}",
        log_path,
    )
    return {"ok": not errors, "why": why_out, "errors": errors, "updated": iso, "kept": False}


def refresh_kind(now=None, doc=None):
    """上午全量 3 日。午后默认只刷实况；预报本身过期时仍全量，避免错过早晨那次后整天没有 3 日文件。"""
    doc = load_forecast() if doc is None else doc
    if not _forecast_fresh(doc, now):
        return "full"
    return "current" if bj_now(now).hour >= 12 else "full"


def peek(place, date=None, *, w=None, now=None):
    """只读预报文件或未过期缓存。不联网。过期则 text 为空。"""
    w = _cfg(w)
    pid, loc, why = resolve_place(place, w)
    base = {"ok": False, "place": pid or place, "city": (loc or {}).get("city"),
            "lat": (loc or {}).get("lat"), "lon": (loc or {}).get("lon"),
            "text": None, "brief": None, "snap": None, "facts": None}
    if not loc:
        return {**base, "why": why}
    day = resolve_day(date, now)
    if not day:
        return {**base, "why": "bad-date"}
    doc = load_forecast()
    row = _row_for(doc, pid, day["date"]) if _forecast_fresh(doc, now, pid) else None
    if row:
        block = (doc.get("places") or {}).get(pid) or {}
        current = block.get("current") if day["offset"] == 0 and _ts_fresh((block.get("current") or {}).get("updated_ts"), now) else None
        snap = _snap_from_day(row, current)
        # 今天且实况仍新鲜：现象以实况为准，避免早晨的「有雪」盖住午后已放晴。
        if day["offset"] == 0 and current and current.get("code") is not None:
            snap = dict(snap)
            snap["code"] = current.get("code")
            if current.get("temp") is not None:
                snap["temp"] = current.get("temp")
            if int(current["code"]) in (0, 1, 2, 3):
                snap["precip_prob"] = 0
        brief = brief_from_snap(pid, snap, day["label"], loc["city"])
        return {**base, "ok": True, "why": "forecast", "text": brief, "brief": brief, "snap": snap,
                "facts": _facts_from(snap), "day": day["date"], "label": day["label"],
                "updated": doc.get("updated")}
    ent = _read_disk().get(_key(loc)) if day["offset"] == 0 else None
    if _entry_fresh(ent, now):
        snap = ent.get("snap") or parse_snap_text(ent.get("text") or "")
        brief = brief_from_snap(pid, snap or {}, "今天", loc["city"])
        if brief:
            return {**base, "ok": True, "why": "cache", "text": brief, "brief": brief, "snap": snap,
                    "facts": _facts_from(snap), "day": day["date"], "label": "今天"}
    why_out = "stale" if (doc or ent) else "no-forecast"
    return {**base, "why": why_out, "day": day["date"], "label": day["label"]}


def lookup(place, date=None, *, cache_only=False, fetch=None, w=None, now=None):
    """查预报。新鲜就直接用；过期且允许联网就刷新。失败明确 stale，不回带旧雨雪文案。"""
    from preview_mode import is_preview
    hit = peek(place, date, w=w, now=now)
    if hit.get("ok") and hit.get("text"):
        return hit
    if hit.get("why") == "bad-date" or cache_only or is_preview():
        hit["ok"] = False
        hit["text"] = None
        hit["brief"] = None
        hit["facts"] = None
        hit["snap"] = None
        if hit.get("why") not in ("bad-date", "stale", "no-forecast"):
            hit["why"] = "stale"
        return hit
    pid = hit.get("place") or place
    refreshed = refresh(places=[pid], fetch=fetch, w=w, now=now, force=False)
    hit2 = peek(place, date, w=w, now=now)
    if hit2.get("ok") and hit2.get("text"):
        return hit2
    hit2["ok"] = False
    hit2["text"] = None
    hit2["brief"] = None
    hit2["facts"] = None
    hit2["snap"] = None
    hit2["why"] = "stale"
    hit2["refresh"] = refreshed.get("why")
    return hit2


def model_view(result):
    """给模型看的天气。过期快照不带温度和雨雪现象。"""
    if not isinstance(result, dict):
        return {"ok": False, "why": "unavailable", "text": None,
                "say": "没拿到新鲜天气。不要描述现在的雨雪，也不要报温度。"}
    why = result.get("why")
    status = result.get("status")
    if why in ("cache-stale", "stale") or status == "stale":
        return {
            "ok": False, "place": result.get("place"), "why": "stale", "status": "stale",
            "text": None,
            "say": "没拿到新鲜天气。不要说现在在下雨或下雪，也不要报温度。",
        }
    facts = result.get("facts") if isinstance(result.get("facts"), dict) else None
    view = {
        "ok": result.get("ok"), "place": result.get("place"), "city": result.get("city"),
        "why": why, "day": result.get("day"), "label": result.get("label"),
        "text": result.get("brief") or result.get("text"),
    }
    if facts:
        slim = {k: facts[k] for k in ("condition", "temp", "tmin", "tmax", "precip_prob", "aqi") if facts.get(k) is not None}
        if slim:
            view["facts"] = slim
    if status:
        view["status"] = status
    return {k: v for k, v in view.items() if v is not None}


def _guard_prompt_line(brief):
    if not brief:
        return None
    if any(tok in brief for tok in ("湿度", "体感", "AQI", "km/h", "°C", "℃")):
        return None
    return "天气：" + brief


def current_snap(w=None, place=PLACE_USER):
    pid, loc, why = resolve_place(place, w)
    if not loc:
        return None
    ent = _read_disk().get(_key(loc)) or {}
    snap = ent.get("snap")
    if snap:
        return snap
    return parse_snap_text(ent.get("text") or "")


def should_attach(w=None, snap=None, baseline=None, cfg=None, place=PLACE_USER):
    w = _cfg(w)
    if not w.get("enabled", False):
        return False, "weather.enabled=false"
    if snap is None:
        obs = ambient_observation(place, w=w)
        if not obs:
            _pid, loc, _why = resolve_place(place, w)
            ent = _read_disk().get(_key(loc)) if loc else None
            if ent and not _entry_fresh(ent):
                return False, "stale"
            return False, "no-snap"
        snap = obs["snap"]
    if not snap:
        return False, "no-snap"
    base = baseline if baseline is not None else baseline_means(w, place=place)
    reasons = variance_reasons(snap, baseline=base, cfg=w)
    if reasons:
        return True, ",".join(reasons)
    return False, "stable"


def _fetch(u):
    with urllib.request.urlopen(u, timeout=6) as r:
        return json.loads(r.read())


def _fetch_aqi(loc, fetch=None):
    try:
        d = (fetch or _fetch)(aqi_url(loc))
        cur = d.get("current") or {}
        if cur.get("us_aqi") is not None:
            return float(cur["us_aqi"])
    except Exception:
        return None
    return None


def get(place=PLACE_BDA, *, cache_only=False, fetch=None, w=None):
    """按需天气工具。返回 dict：place/city/lat/lon/text/snap/why/ok。

    cache_only=True：只读磁盘/进程缓存，不走 source_poll、不联网。
    """
    from preview_mode import is_preview
    cache_only = cache_only or is_preview()
    w = _cfg(w)
    pid, loc, why = resolve_place(place, w)
    if not loc:
        return {"ok": False, "place": place, "city": None, "lat": None, "lon": None,
                "text": None, "snap": None, "why": why}
    k = (loc["lat"], loc["lon"])
    dk = _key(loc)
    c = _read_disk().get(dk)
    if c:
        _cache[k] = (c["ts"], c["text"])
    if c and time.time() - c["ts"] < TTL:
        return {"ok": True, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                "text": c["text"], "snap": c.get("snap") or parse_snap_text(c.get("text") or ""),
                "why": "cache"}
    if cache_only:
        if c:
            return {"ok": True, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                    "text": c["text"], "snap": c.get("snap") or parse_snap_text(c.get("text") or ""),
                    "why": "cache-stale"}
        return {"ok": False, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                "text": None, "snap": None, "why": "no-cache"}
    poll_key = f"open_meteo_{pid}"
    gate = source_poll.acquire(poll_key)
    if not gate:
        if c:
            return {"ok": True, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                    "text": c["text"], "snap": c.get("snap") or parse_snap_text(c.get("text") or ""),
                    "why": "cache-stale"}
        return {"ok": False, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                "text": None, "snap": None,
                "why": "rate-limited:" + source_poll.check(poll_key)[1]}
    try:
        raw = (fetch or _fetch)(url(loc))
        aqi = None
        if fetch is None and attach_cfg(w).get("fetch_aqi", True):
            aqi = _fetch_aqi(loc, None)
        snap = snap_from_data(raw, aqi=aqi)
        s = fmt(raw, loc["city"], aqi=aqi)
        ts = time.time()
        _cache[k] = (ts, s)
        _write_disk(dk, ts, s, snap=snap)
        record_baseline(snap, w, now=ts, place=pid)
        gate.done(ok=True)
        return {"ok": True, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                "text": s, "snap": snap, "why": "ok"}
    except Exception as e:
        gate.done(error=True)
        if c:
            return {"ok": True, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                    "text": c["text"], "snap": c.get("snap") or parse_snap_text(c.get("text") or ""),
                    "why": "cache-stale"}
        return {"ok": False, "place": pid, "city": loc["city"], "lat": loc["lat"], "lon": loc["lon"],
                "text": None, "snap": None, "why": f"open-meteo error: {e}"}


def call_tool(args=None, **kwargs):
    """LLM/脚本工具入口。args: {place, date?, cache_only?}。带日期就查预报。"""
    a = dict(args or {})
    a.update(kwargs)
    place = a.get("place") or PLACE_BDA
    cache_only = bool(a.get("cache_only") or False)
    fetch = a.get("fetch")
    w = a.get("w")
    now = a.get("now")
    if a.get("date") is not None or a.get("day") is not None:
        day = a.get("date") if a.get("date") is not None else a.get("day")
        return lookup(place, day, cache_only=cache_only, fetch=fetch, w=w, now=now)
    return get(place, cache_only=cache_only, fetch=fetch, w=w)


def now_weather(w=None, fetch=None, place=None):
    """兼容旧 (text, why)。默认地点=用户燕园。"""
    r = get(place if place is not None else PLACE_USER, cache_only=False, fetch=fetch, w=w)
    return r.get("text"), r.get("why") or "ok"


def set_location(lat, lon, city, place=PLACE_USER):
    """写入 config：yanyuan 改顶层 lat/lon/city；bda 写 weather.places.bda。"""
    p = ROOT / "config.json"
    c = json.loads(p.read_text())
    w = c.setdefault("weather", {})
    pid = _PLACE_ALIASES.get(str(place).strip().lower(), str(place).strip().lower())
    if pid == PLACE_BDA:
        places_c = w.setdefault("places", {})
        places_c["bda"] = {"lat": float(lat), "lon": float(lon), "city": city}
    else:
        w.update(lat=float(lat), lon=float(lon), city=city)
        places_c = w.setdefault("places", {})
        places_c["yanyuan"] = {"lat": float(lat), "lon": float(lon), "city": city}
    p.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n")


def prompt_line(user_text=None, *, place=None, w=None, proactive=False, now=None):
    """短句挂载。过期数据直接不挂。主动消息只在新鲜且有明显变化时提。

    用户正在问天气时读预报/新鲜缓存（不在这里联网；联网在 lookup）。
    闲聊不挂播报表，只在方差门通过时挂「海淀今天凉、干、不下雨」这种短句。
    """
    w = _cfg(w)
    if not w.get("enabled", False):
        return None
    pid = place or infer_place(user_text, default=PLACE_BDA)
    if needs(user_text) and not proactive:
        return _guard_prompt_line(peek(pid, infer_day(user_text), w=w, now=now).get("brief"))
    if not attach_cfg(w).get("auto_variance", True):
        return None
    obs = ambient_observation(pid, w=w, now=now)
    if not obs:
        return None
    att, _why = should_attach(w, snap=obs["snap"], place=pid)
    if not att:
        return None
    return _guard_prompt_line(brief_from_snap(pid, obs["snap"], obs["label"], obs.get("city")))


# 旧名：部分脚本/注释仍写 DEFAULT
DEFAULT = DEFAULT_USER

if __name__ == "__main__":
    args = sys.argv[1:]
    place = PLACE_USER
    if "--place" in args:
        i = args.index("--place")
        place = args[i + 1]
        del args[i:i + 2]
    if len(args) >= 4 and args[0] == "--set":
        # --set LAT LON CITY   ；地点用 --place（默认 yanyuan）
        set_location(args[1], args[2], args[3], place=place)
        print("set", place, args[1], args[2], args[3])
        sys.exit(0)
    if args and args[0] == "--tool":
        raw = args[1] if len(args) > 1 else "{}"
        print(json.dumps(call_tool(json.loads(raw)), ensure_ascii=False))
        sys.exit(0)
    if args and args[0] == "--attach":
        ok, why = should_attach(place=place)
        print(("ATTACH" if ok else "SKIP"), why)
        sys.exit(0 if ok else 1)
    if args and args[0] == "--needs":
        t = args[1] if len(args) > 1 else ""
        print("YES" if needs(t) else "NO", infer_place(t))
        sys.exit(0)
    print(json.dumps(get(place), ensure_ascii=False) if "--json" in args else now_weather(place=place))
