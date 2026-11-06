"""端到端采集作业：全局锁内串行 xiaohongshu -> douyin -> weibo（平台间随机间隔）-> 对采集成功的平台
tools/media_download.py 补充下载当前页媒体 -> 入 material_store -> purge 过期 -> 写运行摘要 data/collect_runs/*.json。
默认不 force：source_poll 不放行的平台直接 skipped（静默时段 01:00-07:30、最小间隔、日上限）。
用法（src/ 下）: python -m collectors.pipeline [--force] [--no-ocr] [--no-media] [--dry-plan]"""
import datetime, json, os, random, sys, time, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import runner

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "data" / "collect_runs"
ORDER = ["xiaohongshu", "douyin", "weibo"]

def _media_mod():
    sys.path.insert(0, str(ROOT / "tools")); import media_download; return media_download

def plan(force=False, now=None):
    import source_poll
    return {n: source_poll.check(n, now=now, force=force) for n in ORDER}

def run(force=False, do_ocr=True, media=True, gap=(20, 60), sleep=time.sleep, factory=None, media_fn=None,
        store=None, lock_path=runner.LOCK, runs_dir=RUNS, now=None):
    t0 = time.time(); started = datetime.datetime.now().isoformat(timespec="seconds")
    summ = {"started": started, "force": force, "platforms": [], "media": [], "purge": None, "status": "ok"}
    reg = factory or runner.collectors()
    try:
        with runner.global_lock(lock_path, wait_s=0):
            for i, n in enumerate(ORDER):
                if i: sleep(random.uniform(*gap))
                try: summ["platforms"].append(reg[n]().run(force=force, do_ocr=do_ocr, store=store))
                except Exception as e: summ["platforms"].append({"platform": n, "status": "error:%s" % str(e)[:200]})
            if media:
                fn = media_fn or _media_mod().run
                for r in summ["platforms"]:
                    if r.get("status") != "ok": continue
                    sleep(random.uniform(3, 10))
                    try: m = fn(r["platform"], store=store, in_session=True, log=lambda *a: None)
                    except Exception as e: m = {"status": "error:%s" % str(e)[:200]}
                    summ["media"].append(dict(m, platform=r["platform"]))
    except runner.Busy as e:
        summ["status"] = "busy:%s" % e
    try:
        import material_store
        st = store or material_store._s(); summ["purge"] = st.purge_expired()
    except Exception as e: summ["purge"] = {"error": str(e)[:200]}
    summ["items"] = sum(r.get("items", 0) for r in summ["platforms"])
    summ["media_files"] = sum(r.get("media", 0) for r in summ["platforms"]) + sum(m.get("media", 0) for m in summ["media"])
    summ["secs"] = round(time.time() - t0, 1)
    runs_dir = pathlib.Path(runs_dir); runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / ("%s.json" % started.replace(":", ""))
    out.write_text(json.dumps(summ, ensure_ascii=False, indent=1)); (runs_dir / "last.json").write_text(out.read_text())
    summ["summary_file"] = str(out)
    return summ

if __name__ == "__main__":
    a = sys.argv[1:]
    if "--dry-plan" in a: print(json.dumps(plan("--force" in a), ensure_ascii=False)); sys.exit(0)
    print(json.dumps(run(force="--force" in a, do_ocr="--no-ocr" not in a, media="--no-media" not in a), ensure_ascii=False, indent=1))
