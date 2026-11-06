import sys, pathlib, datetime, random, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import source_poll as sp

def test_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "STATE", tmp_path / "s.json")
    d = datetime.datetime(2026, 10, 26, 10, 0)
    assert sp.check("xiaohongshu", datetime.datetime(2026, 10, 26, 3, 0))[1] == "quiet-hours"
    assert sp.cfg("xiaohongshu")["min_interval_h"] == 2 and sp.cfg("open_meteo")["min_interval_h"] >= 1
    slept = []; g = sp.acquire("xiaohongshu", d, sleep=slept.append, rng=random.Random(0))
    assert 3 <= g.sleep_action() <= 12 and slept
    for _ in range(3): g.page()
    try: g.page(); assert False
    except RuntimeError: pass
    st = g.done(ok=True); gap = (st["next"] - d.timestamp()) / 3600
    assert 2 * 0.7 <= gap <= 2 * 1.3
    assert sp.check("xiaohongshu", d + datetime.timedelta(hours=1))[0] is False
    # backoff x2 up to 24h
    t = d
    for _ in range(6):
        t = datetime.datetime.fromtimestamp(sp._load()["xiaohongshu"]["next"]) + datetime.timedelta(minutes=1)
        if sp.in_quiet(sp.cfg("xiaohongshu"), t): t = t.replace(hour=8)
        st = sp.Gate("xiaohongshu", sp.cfg("xiaohongshu"), t, rng=random.Random(1)).done(captcha=True)
    assert st["backoff_h"] == 24
    assert sp.looks_blocked("请完成安全验证")

def test_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "STATE", tmp_path / "s.json")
    d = datetime.datetime(2026, 10, 26, 8, 0)
    sp._save({"bda_site": {"day": "2026-10-26", "count": 10}})
    assert sp.check("bda_site", d)[1] == "daily-cap"
    sp._save({"bda_site": {"day": "2026-10-26", "count": 9}})
    assert sp.check("bda_site", d)[0]

def test_per_platform_caps():
    for k in ["xiaohongshu", "douyin", "wechat_mp", "weibo", "bilibili", "bda_site"]:
        assert sp.cfg(k)["max_per_day"] == 10, k

def test_new_intervals():
    exp = {"xiaohongshu": 2, "douyin": 2, "wechat_mp": 2, "weibo": 1.5, "bilibili": 1.5, "bda_site": 3, "open_meteo": 1}
    for k, v in exp.items(): assert sp.cfg(k)["min_interval_h"] == v, k
    assert sp.global_cfg() == {"max_per_day": 20, "exclude": ["open_meteo", "open_meteo_bda", "open_meteo_yanyuan"]}

def test_global_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "STATE", tmp_path / "s.json")
    d = datetime.datetime(2026, 10, 26, 8, 0); rng = random.Random(0)
    plats = ["xiaohongshu", "douyin", "wechat_mp", "weibo", "bilibili", "bda_site"]
    n = 0
    for h in range(16):
        t = d + datetime.timedelta(hours=h, minutes=1)
        if t.hour >= 24 or t.date() != d.date(): break
        for p in plats:
            g = sp.acquire(p, t, sleep=lambda x: None, rng=rng)
            if g: g.done(ok=True); n += 1
    assert n == 20 and sp.global_count(d) == 20
    assert sp._load()["_global"] == {"count": 20, "day": "2026-10-26"}
    late = d.replace(hour=23, minute=30)
    assert any(sp.check(p, late)[1] == "global-daily-cap" for p in plats)
    # 天气不计入全局上限
    w = sp.acquire("open_meteo", late, sleep=lambda x: None, rng=rng); assert w; w.done(ok=True)
    assert sp.global_count(d) == 20
    # 次日重置
    nxt = d + datetime.timedelta(days=1)
    assert sp.global_count(nxt) == 0 and sp.check("bda_site", nxt)[0]


def test_force_skips_quiet_but_not_caps(tmp_path, monkeypatch):
    import datetime, json
    monkeypatch.setattr(sp, "STATE", tmp_path / "s.json")
    night = datetime.datetime(2026, 10, 24, 1, 5)
    assert sp.acquire("weibo", now=night) is None
    assert sp.acquire("weibo", now=night, force=True) is not None
    (tmp_path / "s.json").write_text(json.dumps({"weibo": {"day": "2026-10-24", "count": 10}}))
    assert sp.acquire("weibo", now=night, force=True) is None
