"""天气工具：两地 places + get/call_tool + source_poll 限速 + 缓存（全部 mock，无网络）。"""
import sys, os, json, time, datetime, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import pytest
import weather, config, source_poll

D = {"current": {"temperature_2m": 9.6, "apparent_temperature": 7.2, "relative_humidity_2m": 80, "weather_code": 61, "wind_speed_10m": 11.2},
     "daily": {"temperature_2m_max": [15.1], "temperature_2m_min": [5.9], "precipitation_probability_max": [70]}}
W = {"enabled": True, "lat": 39.9925, "lon": 116.31, "city": "北京海淀燕园",
     "places": {"bda": {"lat": 39.9459, "lon": 116.3130, "city": "北京海淀北舞"},
                "yanyuan": {"lat": 39.9925, "lon": 116.31, "city": "北京海淀燕园"}}}

@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(source_poll, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(weather, "CACHE_FILE", tmp_path / "weather_cache.json")
    monkeypatch.setattr(weather, "BASELINE_FILE", tmp_path / "weather_baseline.json")
    monkeypatch.setattr(weather, "_cache", {})
    monkeypatch.setattr(weather, "_fetch", lambda u: pytest.fail("network!"))
    monkeypatch.setattr(source_poll, "in_quiet", lambda p, now: False)

def test_places_and_fmt():
    w = config.load()["weather"]
    assert w["enabled"] and w["provider"] == "open-meteo"
    assert "bda" in (w.get("places") or {})
    pls = weather.places(W)
    assert abs(pls["bda"]["lat"] - 39.9459) < 1e-4
    assert abs(pls["yanyuan"]["lat"] - 39.9925) < 1e-4
    assert weather.now_weather({"enabled": False})[0] is None
    s = weather.fmt(D, "北京海淀燕园"); assert "小雨" in s and "10°C" in s and "6~15°C" in s and "70%" in s
    assert "latitude=39.9459" in weather.url(weather.resolve_place("bda", W)[1])

def test_get_two_places_separate_cache():
    calls = []
    def f(u):
        calls.append(u)
        return D
    r1 = weather.get("yanyuan", fetch=f, w=W)
    assert r1["ok"] and r1["place"] == "yanyuan" and "小雨" in r1["text"] and len(calls) == 1
    r2 = weather.get("bda", fetch=f, w=W)
    assert r2["ok"] and r2["place"] == "bda" and "北舞" in r2["text"] and len(calls) == 2
    disk = json.loads(weather.CACHE_FILE.read_text())
    assert "39.9925,116.3100" in disk and "39.9459,116.3130" in disk
    # cache hit, no new fetch
    r3 = weather.get("bda", fetch=f, w=W)
    assert r3["why"] == "cache" and len(calls) == 2
    r4 = weather.get("bda", cache_only=True, w=W)
    assert r4["ok"] and r4["why"] in ("cache", "cache-stale")
    r5 = weather.call_tool({"place": "bda", "cache_only": True})
    # call_tool 走真实 config places；只要不炸且 shape 对
    assert "place" in r5 and "why" in r5

def test_one_request_per_hour_across_processes():
    calls = []
    f = lambda u: calls.append(u) or D
    s, why = weather.now_weather(W, fetch=f); assert why == "ok" and "小雨" in s and len(calls) == 1
    assert json.loads(weather.CACHE_FILE.read_text())
    weather._cache.clear()
    s2, why2 = weather.now_weather(W, fetch=f); assert why2 == "cache" and s2 == s and len(calls) == 1
    d = json.loads(weather.CACHE_FILE.read_text()); k = next(iter(d)); d[k]["ts"] -= 4000
    weather.CACHE_FILE.write_text(json.dumps(d))
    s3, why3 = weather.now_weather(W, fetch=f); assert why3 == "cache-stale" and len(calls) == 1

def test_gate_allows_after_interval_and_error_keeps_cache():
    weather.now_weather(W, fetch=lambda u: D)
    st = json.loads(source_poll.STATE.read_text())
    key = "open_meteo_yanyuan" if "open_meteo_yanyuan" in st else "open_meteo"
    st[key]["next"] = time.time() - 1
    source_poll.STATE.write_text(json.dumps(st))
    d = json.loads(weather.CACHE_FILE.read_text()); k = next(iter(d)); d[k]["ts"] -= 4000
    weather.CACHE_FILE.write_text(json.dumps(d))
    def boom(u): raise OSError("down")
    s, why = weather.now_weather(W, fetch=boom); assert why == "cache-stale" and s
    st = json.loads(source_poll.STATE.read_text())
    key = "open_meteo_yanyuan" if "open_meteo_yanyuan" in st else "open_meteo"
    assert st[key]["count"] == 2

def test_no_cache_rate_limited():
    st = {"open_meteo_yanyuan": {"next": time.time() + 3000}}; source_poll.STATE.write_text(json.dumps(st))
    s, why = weather.now_weather(W, fetch=lambda u: pytest.fail("x")); assert s is None and why.startswith("rate-limited")

def test_needs_and_infer_place():
    assert weather.needs("今天穿什么出门")
    assert weather.needs("会不会下雨")
    assert not weather.needs("在干嘛")
    assert weather.infer_place("燕园冷不冷") == "yanyuan"
    assert weather.infer_place("你们学校冷吗") == "bda"
    assert weather.infer_place("在干嘛") == "bda"
    assert weather.tool_spec()["name"] == "get_weather"

def test_prompt_line_on_demand(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "CACHE_FILE", tmp_path / "weather_cache.json")
    monkeypatch.setattr(weather, "BASELINE_FILE", tmp_path / "weather_baseline.json")
    monkeypatch.setattr(weather, "_cache", {})
    wcfg = dict(W, attach={**weather.DEFAULT_ATTACH, "auto_variance": True})
    # chitchat + no cache → no line
    assert weather.prompt_line("在干嘛", w=wcfg) is None
    # seed BDA cache
    weather.get("bda", fetch=lambda u: D, w=wcfg)
    # needs → attach
    line = weather.prompt_line("今天穿什么", w=wcfg)
    assert line and line.startswith("天气：") and "北舞" in line
    # variance rain on cache → attach without needs
    line2 = weather.prompt_line("哈哈", w=wcfg)
    assert line2 and "天气：" in line2

def test_should_attach_variance(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "CACHE_FILE", tmp_path / "weather_cache.json")
    monkeypatch.setattr(weather, "BASELINE_FILE", tmp_path / "weather_baseline.json")
    monkeypatch.setattr(weather, "_cache", {})
    cfg = {"temp_delta_c": 5, "day_swing_c": 12, "precip_prob": 50, "aqi_delta": 50, "aqi_high": 150,
           "baseline_hours": 48, "baseline_max": 24, "fetch_aqi": False}
    wcfg = {"enabled": True, "lat": 39.9925, "lon": 116.31, "city": "燕园", "attach": cfg,
            "places": W["places"]}
    calm = {"temp": 17, "code": 2, "tmin": 10, "tmax": 18, "precip_prob": 10, "aqi": 40}
    ok, why = weather.should_attach(wcfg, snap=calm, baseline={"temp": 16, "aqi": 35, "n": 3}, place="yanyuan")
    assert ok is False and why == "stable", (ok, why)
    rain = dict(calm, code=61)
    ok, why = weather.should_attach(wcfg, snap=rain, baseline={"temp": 16, "aqi": 35, "n": 3})
    assert ok and "rain_snow" in why
    swing = dict(calm, tmin=2, tmax=20)
    ok, why = weather.should_attach(wcfg, snap=swing, baseline={"temp": 16, "aqi": 35, "n": 3})
    assert ok and "day_swing" in why
    cold = dict(calm, temp=8)
    ok, why = weather.should_attach(wcfg, snap=cold, baseline={"temp": 16, "aqi": 35, "n": 3})
    assert ok and "temp_delta" in why
    haze = dict(calm, aqi=180)
    ok, why = weather.should_attach(wcfg, snap=haze, baseline={"temp": 16, "aqi": 40, "n": 3})
    assert ok and ("aqi_high" in why or "aqi_delta" in why)
    assert weather.snap_from_data(D)["code"] == 61
    text = weather.fmt(D, "燕园"); assert weather.parse_snap_text(text)["temp"] == 10

def test_baseline_per_place(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "BASELINE_FILE", tmp_path / "weather_baseline.json")
    snap = {"temp": 10, "code": 2, "tmin": 5, "tmax": 12, "precip_prob": 10, "aqi": 40}
    weather.record_baseline(snap, place="bda", now=1000.0)
    weather.record_baseline(dict(snap, temp=20), place="yanyuan", now=1000.0)
    raw = json.loads(weather.BASELINE_FILE.read_text())
    assert "places" in raw
    assert raw["places"]["bda"]["samples"][0]["temp"] == 10
    assert raw["places"]["yanyuan"]["samples"][0]["temp"] == 20
