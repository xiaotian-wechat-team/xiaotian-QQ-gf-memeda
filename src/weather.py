"""天气工具（免 key：Open-Meteo，按经纬度 + 磁盘缓存）。

两地：
  - bda / 小甜：北京海淀北舞（万寿寺路校区）
  - yanyuan / 用户：config.json weather.lat/lon（默认海淀燕园）

不要把天气塞进每条提示词或 life_schedule JSON。调用方按需：
  weather.get("bda")           # 工具主入口 → {place,text,snap,why,...}
  weather.call_tool({"place":"bda"})
  weather.needs(user_text)     # 聊天是否谈天气/穿衣/出门
  weather.infer_place(text)    # bda | yanyuan

限速：source_poll open_meteo_<place>（各地独立 ≥1h）；缓存 data/weather_cache.json（按 lat,lon）。
方差挂载 should_attach(place=...) 仍可用，但 brain 优先 needs() 再挂。
"""
from __future__ import annotations

import json, time, sys, os, re, urllib.request, urllib.parse
from config import load, ROOT
import source_poll

_cache = {}  # (lat,lon) -> (ts, text)
CACHE_FILE = ROOT / "data" / "weather_cache.json"
BASELINE_FILE = ROOT / "data" / "weather_baseline.json"
TTL = 3600

# 用户默认（北京大学燕园）；config.weather.* 可覆盖
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
    "燕园": PLACE_USER, "这边": PLACE_USER, "北大": PLACE_USER,
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

# 聊天里提到才值得拉/挂天气（穿衣/雨雪/冷暖/出门）
_NEED_RE = re.compile(
    r"(天气|气温|下雨|下雪|降雨|雨雪|冷不冷|热不热|多少度|几度|穿什么|穿啥|"
    r"外套|加衣服|伞|防晒|雾霾|AQI|空气质量|体感|降温|升温|刮风|"
    r"出门.*冷|冷.*出门|会不会下雨|要不要带伞)",
    re.I,
)

TOOL_NAME = "get_weather"
TOOL_SPEC = {
    "name": TOOL_NAME,
    "description": "按地点查当前天气（Open-Meteo + 磁盘缓存）。小甜/北舞用 bda；用户燕园用 yanyuan。不要写进日程 JSON。",
    "parameters": {
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "enum": ["bda", "yanyuan"],
                "description": "bda=小甜海淀北舞；yanyuan=用户海淀燕园",
            },
            "cache_only": {
                "type": "boolean",
                "description": "True 时只读缓存、不发起网络请求",
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
    if re.search(r"(燕园|北京大学|我这边|我这|我们这边)", t):
        return PLACE_USER
    if re.search(r"(北舞|海淀|万寿寺|学校|宿舍|校园|你那边|你那)", t):
        return PLACE_BDA
    return default if default in PLACE_IDS else PLACE_BDA


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
    snap = snap if snap is not None else current_snap(w, place=place)
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
    """LLM/脚本工具入口。args: {place, cache_only?}。"""
    a = dict(args or {})
    a.update(kwargs)
    place = a.get("place") or PLACE_BDA
    cache_only = bool(a.get("cache_only") or False)
    return get(place, cache_only=cache_only)


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


def prompt_line(user_text=None, *, place=None, w=None):
    """brain 用：按需生成「天气：…」行；不需要则 None。

    优先级：needs(user_text) → get(place)；否则 auto_variance + should_attach → cache_only get。
    默认 place：infer_place(user_text)（偏 bda）。
    """
    w = _cfg(w)
    if not w.get("enabled", False):
        return None
    pid = place or infer_place(user_text, default=PLACE_BDA)
    if needs(user_text):
        r = get(pid, cache_only=False, w=w)
        if r.get("text"):
            return f"天气：{r['text']}"
        if r.get("why") and "enabled=false" not in (r.get("why") or ""):
            return "天气：未知（别编天气）"
        return None
    if not attach_cfg(w).get("auto_variance", True):
        return None
    # 方差门：只读缓存，不主动刷网
    att, _ = should_attach(w, place=pid)
    if not att:
        return None
    r = get(pid, cache_only=True, w=w)
    if r.get("text"):
        return f"天气：{r['text']}"
    return None


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
