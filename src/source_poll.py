"""中心限速器：所有素材源轮询都必须经过这里。
用法：
    gate = source_poll.acquire("bda_site")       # 不允许时返回 None（含原因见 why()）
    if gate: ... gate.page(); gate.sleep_action(); ... gate.done(ok=True / captcha=True / error=True)
状态持久化在 data/source_poll_state.json（last/next/backoff/每日计数）。"""
import json, random, time, datetime, pathlib, fcntl, os, tempfile, threading, uuid
from contextlib import contextmanager

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "poll_limits.json"
STATE = ROOT / "data" / "source_poll_state.json"
CAPTCHA_MARKS = ("验证码", "安全验证", "滑块", "captcha", "verify", "访问异常", "请登录", "人机验证")
_MUTEX = threading.RLock()


@contextmanager
def _state_lock():
    with _MUTEX:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        with STATE.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

def cfg(platform):
    c = json.loads(CFG.read_text()); p = dict(c["defaults"]); p.update(c["platforms"].get(platform, {}))
    import os
    if os.environ.get("SCENE_REFS_BUILD") == "1":
        # temporary: allow poll_limits.json values as-is while building scene_refs (~10min floor)
        return p
    if p.get("login"): p["min_interval_h"] = max(p["min_interval_h"], 1.5)   # 登录平台硬下限 1.5h
    else: p["min_interval_h"] = max(p["min_interval_h"], 1)                # 公开源硬下限 1h
    return p

GLOBAL_KEY = "_global"

def global_cfg():
    g = json.loads(CFG.read_text()).get("global", {})
    return {"max_per_day": g.get("max_per_day", 20), "exclude": g.get("exclude", ["open_meteo"])}

def counts_global(platform): return platform not in global_cfg()["exclude"]

def global_count(now=None, s=None):
    now = now or datetime.datetime.now(); g = (s if s is not None else _load()).get(GLOBAL_KEY, {})
    return g.get("count", 0) if g.get("day") == now.date().isoformat() else 0

def _load():
    try: return json.loads(STATE.read_text())
    except Exception: return {}

def _save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE.parent, prefix='.source-poll-')
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(s, out, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def _hm(s): h, m = s.split(":"); return int(h) * 60 + int(m)

def in_quiet(p, now):
    a, b = (_hm(x) for x in p["quiet"]); t = now.hour * 60 + now.minute
    return a <= t < b if a <= b else (t >= a or t < b)

def looks_blocked(text):
    t = (text or "").lower(); return any(m.lower() in t for m in CAPTCHA_MARKS)

def check(platform, now=None, force=False, *, policy=None):
    """返回 (allowed, reason)。force=True（用户手动试跑）只跳过静默时段和最小间隔，日上限/全局上限照常。"""
    now = now or datetime.datetime.now(); p = policy if policy is not None else cfg(platform); s = _load(); st = s.get(platform, {})
    if (st.get('in_flight') or {}).get('until', 0) > now.timestamp(): return False, 'in-flight'
    if in_quiet(p, now) and not force: return False, "quiet-hours"
    if st.get("next") and now.timestamp() < st["next"] and not force: return False, "too-soon(next=%s)" % datetime.datetime.fromtimestamp(st["next"]).isoformat(timespec="minutes")
    if st.get("day") == now.date().isoformat() and st.get("count", 0) >= p["max_per_day"]: return False, "daily-cap"
    reserved = sum(1 for name, entry in s.items() if name != GLOBAL_KEY and counts_global(name)
                   and (entry.get('in_flight') or {}).get('until', 0) > now.timestamp())
    if counts_global(platform) and global_count(now, s) + reserved >= global_cfg()["max_per_day"]: return False, "global-daily-cap"
    return True, "ok"

class Gate:
    def __init__(self, platform, p, now, sleep=time.sleep, rng=random):
        self.platform, self.p, self.now, self._sleep, self.rng = platform, p, now, sleep, rng
        self.pages = self.scrolls = 0
        self.reservation = None
    def page(self):
        if self.pages >= self.p["max_pages"]: raise RuntimeError("max_pages reached")
        self.pages += 1
    def scroll(self):
        if self.scrolls >= self.p["max_scrolls"]: raise RuntimeError("max_scrolls reached")
        self.scrolls += 1
    def sleep_action(self):
        lo, hi = self.p["action_delay_s"]; d = self.rng.uniform(lo, hi)
        if d > 0: self._sleep(d)
        return d
    def done(self, ok=True, captcha=False, error=False):
        with _state_lock():
            return self._done(ok, captcha, error)
    def _done(self, ok, captcha, error):
        s = _load(); st = s.get(self.platform, {}); p = self.p; ts = self.now.timestamp()
        if (st.get('in_flight') or {}).get('id') == self.reservation:
            st.pop('in_flight', None)
        day = self.now.date().isoformat()
        st["count"] = (st.get("count", 0) if st.get("day") == day else 0) + 1; st["day"] = day; st["last"] = ts
        if captcha or error or not ok:
            st["backoff_h"] = min(max(st.get("backoff_h", p["min_interval_h"]), p["min_interval_h"]) * p["backoff_factor"], p["backoff_max_h"])
            gap = st["backoff_h"]; st["last_fail"] = "captcha" if captcha else "error"
        else:
            st.pop("backoff_h", None); st.pop("last_fail", None); gap = p["min_interval_h"]
        j = p["jitter"]; st["next"] = ts + gap * 3600 * self.rng.uniform(1 - j, 1 + j)
        if st["next"] < ts + p["min_interval_h"] * 3600 * (1 - j): st["next"] = ts + p["min_interval_h"] * 3600 * (1 - j)
        if counts_global(self.platform):
            g = s.get(GLOBAL_KEY, {}); g["count"] = global_count(self.now, s) + 1; g["day"] = day; s[GLOBAL_KEY] = g
        s[self.platform] = st; _save(s); return st

def acquire(platform, now=None, sleep=time.sleep, rng=random, force=False):
    now = now or datetime.datetime.now()
    with _state_lock():
        ok, why = check(platform, now, force)
        if not ok: return None
        gate = Gate(platform, cfg(platform), now, sleep, rng)
        gate.reservation = uuid.uuid4().hex
        state = _load()
        state.setdefault(platform, {})['in_flight'] = {'id': gate.reservation, 'until': now.timestamp() + 900}
        _save(state)
        return gate

if __name__ == "__main__":
    import sys
    for k in json.loads(CFG.read_text())["platforms"]: print(k, *check(k))
    print("global", global_count(), "/", global_cfg()["max_per_day"])
