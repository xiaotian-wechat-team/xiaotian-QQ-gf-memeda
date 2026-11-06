"""端到端采集流水线（全 mock：无 Chrome、无网络）。"""
import sys, os, json, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from collectors import pipeline, runner

class FakeStore:
    def __init__(self): self.purged = 0
    def purge_expired(self): self.purged += 1; return {"items": 1, "files": 2}

def mk(log, status):
    class C:
        def __init__(self): pass
        def run(self, force, do_ocr, store=None):
            log.append(("collect", self.p, force)); return {"platform": self.p, "status": status[self.p], "items": 3, "media": 1}
    return C

def factory(log, status):
    reg = {}
    for n in pipeline.ORDER:
        reg[n] = type(n, (mk(log, status),), {"p": n})
    return reg

def test_pipeline_order_media_purge_summary(tmp_path):
    log, sleeps = [], []; st = FakeStore()
    status = {"xiaohongshu": "ok", "douyin": "skipped:too-soon", "weibo": "ok"}
    def media(p, store=None, in_session=False, log=None):
        assert in_session; log_ = None; logs.append(p); return {"status": "ok", "media": 2}
    logs = []
    s = pipeline.run(factory=factory(log, status), media_fn=media, store=st, sleep=sleeps.append,
                     lock_path=tmp_path / "l", runs_dir=tmp_path / "runs")
    assert [x[1] for x in log] == ["xiaohongshu", "douyin", "weibo"] and not any(x[2] for x in log)  # 默认不 force
    assert logs == ["xiaohongshu", "weibo"] and len(sleeps) == 4 and st.purged == 1
    assert s["items"] == 9 and s["media_files"] == 3 + 4 and s["status"] == "ok"
    assert json.loads((tmp_path / "runs" / "last.json").read_text())["purge"] == {"items": 1, "files": 2}

def test_pipeline_busy_lock(tmp_path):
    import fcntl
    p = tmp_path / "l"; f = open(p, "a+"); fcntl.flock(f, fcntl.LOCK_EX)
    import multiprocessing
    # 另一进程持锁：在子进程里持锁更真实；同进程不同 fd 的 flock 也互斥
    s = pipeline.run(factory=factory([], {n: "ok" for n in pipeline.ORDER}), media_fn=lambda *a, **k: {}, store=FakeStore(),
                     sleep=lambda x: None, lock_path=p, runs_dir=tmp_path / "r")
    assert s["status"].startswith("busy") and s["platforms"] == [] and s["purge"]
    f.close()

def test_collector_error_isolated(tmp_path):
    class Bad:
        def run(self, **k): raise RuntimeError("x")
    reg = factory([], {n: "ok" for n in pipeline.ORDER}); reg["douyin"] = Bad
    s = pipeline.run(factory=reg, media=False, store=FakeStore(), sleep=lambda x: None, lock_path=tmp_path / "l", runs_dir=tmp_path / "r")
    assert [r["status"] for r in s["platforms"]] == ["ok", "error:x", "ok"]

def test_media_download_in_session_skips_poll_and_lock(monkeypatch, tmp_path):
    sys.path.insert(0, str(pipeline.ROOT / "tools")); import media_download as md, source_poll
    from collectors import chrome
    monkeypatch.setattr(source_poll, "acquire", lambda *a, **k: (_ for _ in ()).throw(AssertionError("poll")))
    monkeypatch.setattr(runner, "global_lock", lambda **k: (_ for _ in ()).throw(AssertionError("lock")))
    monkeypatch.setattr(chrome, "find_tab", lambda d: None)
    assert md.run("weibo", in_session=True)["status"] == "error:no-tab"
