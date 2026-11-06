#!/usr/bin/env python3
"""为 579 个 outfit 场景预抓真实场景参考图 → ~/xiaotian-assets/scene_refs/

优先：Bing 图片搜索（公开、礼貌限速）+ Wikimedia Commons。
可选：--collectors 走小红书/抖音/微博只读采集（串行锁 + source_poll；需 SCENE_REFS_BUILD=1 临时放宽）。

目标约 2000 张；sha256 + pHash 去重；Apple Vision 人脸数→people_free；bge-small-zh 存场景查询向量。

用法：
  SCENE_REFS_BUILD=1 .venv/bin/python tools/build_scene_refs.py [--target 2000] [--per-scene 4] [--resume]
  SCENE_REFS_BUILD=1 .venv/bin/python tools/build_scene_refs.py --status
  SCENE_REFS_BUILD=1 .venv/bin/python tools/build_scene_refs.py --restore-limits   # 恢复 poll_limits

可中断；状态在 scene_refs/progress.json 与 SQLite。
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, pathlib, random, re, sqlite3, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ASSETS = pathlib.Path(os.path.expanduser(os.environ.get("SCENE_REFS_DIR", "~/xiaotian-assets/scene_refs")))
IMG_DIR = ASSETS / "images"
LOG_DIR = ASSETS / "logs"
DB = ASSETS / "scene_refs.sqlite"
PROGRESS = ASSETS / "progress.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# Prefer empty / no-people queries
EMPTY_SUFFIX_ZH = ["空景", "无人", "空无一人", "环境", "背景"]
EMPTY_SUFFIX_EN = ["empty", "no people", "interior", "exterior", "background only"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
  id INTEGER PRIMARY KEY,
  scene_id TEXT NOT NULL,
  scene_name TEXT,
  source TEXT NOT NULL,
  source_url TEXT,
  page_url TEXT,
  relpath TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  phash TEXT,
  width INTEGER,
  height INTEGER,
  bytes INTEGER,
  people_free INTEGER DEFAULT 0,
  faces INTEGER DEFAULT -1,
  quality REAL DEFAULT 0.5,
  query TEXT,
  embedding BLOB,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_scene ON images(scene_id);
CREATE INDEX IF NOT EXISTS idx_sha ON images(sha256);
CREATE INDEX IF NOT EXISTS idx_phash ON images(phash);
CREATE TABLE IF NOT EXISTS skips (
  url TEXT PRIMARY KEY,
  reason TEXT,
  ts REAL
);
"""

def ensure():
    ASSETS.mkdir(parents=True, exist_ok=True); IMG_DIR.mkdir(exist_ok=True); LOG_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB); con.executescript(SCHEMA); con.commit(); return con

def load_scenes():
    d = json.loads((ROOT / "data" / "outfit_scenes.json").read_text())
    return d["scenes"]

def load_progress():
    try: return json.loads(PROGRESS.read_text())
    except Exception: return {"done_scenes": {}, "updated": None}

def save_progress(p):
    p["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = PROGRESS.with_suffix(".tmp"); tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1)); tmp.replace(PROGRESS)

def phash_hex(im):
    import imagehash
    return str(imagehash.phash(im))

def sha256_bytes(b): return hashlib.sha256(b).hexdigest()

def vision_faces(path):
    """Apple Vision via existing swift helper; returns faces count or -1."""
    swift = ROOT / "tools" / "vision" / "refscan.swift"
    bin_path = ASSETS / "refscan.bin"
    try:
        if not bin_path.exists() or bin_path.stat().st_mtime < swift.stat().st_mtime:
            import subprocess
            subprocess.run(["swiftc", "-O", "-o", str(bin_path), str(swift),
                            "-framework", "Vision", "-framework", "AppKit"],
                           check=True, capture_output=True, timeout=120)
        import subprocess
        r = subprocess.run([str(bin_path), str(path)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0: return -1, 0.0
        o = json.loads(r.stdout.strip().splitlines()[-1])
        return int(o.get("faces", -1)), float(o.get("quality") or 0)
    except Exception:
        return -1, 0.0

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")

# ---------- Bing ----------
def bing_search(query, first=1, count=35):
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/images/search?q={q}&form=HDRSC2&first={first}&qft=+filterui:photo-photo"
    raw, _ = http_get(url)
    text = raw.decode("utf-8", "ignore")
    urls = [html.unescape(u) for u in re.findall(r"&quot;murl&quot;:&quot;(.*?)&quot;", text)]
    # filter obvious junk
    out = []
    for u in urls:
        if not u.startswith("http"): continue
        low = u.lower()
        if any(x in low for x in (".svg", "logo", "sprite", "favicon", "1x1", "pixel.gif")): continue
        out.append(u)
    # dedupe preserve order
    seen, uniq = set(), []
    for u in out:
        if u in seen: continue
        seen.add(u); uniq.append(u)
    return uniq[:count]

# ---------- Wikimedia ----------
def wiki_search(query, limit=8):
    api = ("https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch="
           + urllib.parse.quote(query) + f"&gsrnamespace=6&gsrlimit={limit}&prop=imageinfo"
           "&iiprop=url|size|mime&iiurlwidth=1280&format=json")
    raw, _ = http_get(api)
    d = json.loads(raw.decode())
    out = []
    for p in (d.get("query") or {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        mime = ii.get("mime") or ""
        if not mime.startswith("image/") or "svg" in mime: continue
        u = ii.get("thumburl") or ii.get("url")
        if u: out.append(u.split("?")[0] if "upload.wikimedia.org" in u else u)
    return out

def queries_for(scene):
    if scene.get('scene_queries'):
        return list(dict.fromkeys(scene['scene_queries']))
    name, aliases = scene["name"], scene.get("aliases") or []
    cat = scene.get("category") or ""
    qs = []
    # Chinese empty-scene biased
    qs.append(f"{name} {' '.join(EMPTY_SUFFIX_ZH[:2])}")
    if aliases:
        qs.append(f"{aliases[0]} 空景 环境")
    qs.append(f"{cat} {name} 场所 照片")
    # light English for wiki
    # crude: use name as-is; wiki handles some CN
    qs.append(f"{name} empty no people")
    # dedupe
    seen, out = set(), []
    for q in qs:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q); out.append(q)
    return out

def already_have(con, sha=None, ph=None, url=None):
    if url and con.execute("SELECT 1 FROM skips WHERE url=?", (url,)).fetchone(): return "skip"
    if sha and con.execute("SELECT 1 FROM images WHERE sha256=?", (sha,)).fetchone(): return "sha"
    if ph:
        # hamming distance <= 6 ≈ near-dup; store exact match first; crude exact
        if con.execute("SELECT 1 FROM images WHERE phash=?", (ph,)).fetchone(): return "phash"
    return None

def mark_skip(con, url, reason):
    con.execute("INSERT OR REPLACE INTO skips(url,reason,ts) VALUES(?,?,?)", (url, reason, time.time()))
    con.commit()

def save_image(con, scene, source, url, data, query, embed_blob):
    from PIL import Image
    from io import BytesIO
    sha = sha256_bytes(data)
    if already_have(con, sha=sha): return None, "sha"
    try:
        im = Image.open(BytesIO(data)); im.load()
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        w, h = im.size
        if min(w, h) < 400: return None, "too-small"
        if max(w, h) > 4000:
            im.thumbnail((1600, 1600), Image.LANCZOS); w, h = im.size
        ph = phash_hex(im.convert("RGB"))
    except Exception as e:
        return None, f"decode:{e}"
    if already_have(con, ph=ph): return None, "phash"
    # write file
    rel = f"{scene['id']}/{sha[:16]}.jpg"
    dest = IMG_DIR / rel; dest.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(dest, "JPEG", quality=88, optimize=True)
    faces, fq, face_area = -1, 0.0, 0.0
    if os.environ.get("SCENE_REFS_VISION", "0") == "1":
        faces, fq = vision_faces(dest)
        # re-read face_area via a second cheap call is wasteful; approximate people_free below
    # people small/far: faces==0 OR (deferred) unknown→0 until rescore
    people_free = 1 if faces == 0 else 0
    area = w * h
    quality = min(1.0, area / (1600 * 1200)) * 0.7 + (0.25 if people_free else 0.0) + 0.05 * min(1.0, fq)
    con.execute(
        "INSERT INTO images(scene_id,scene_name,source,source_url,relpath,path,sha256,phash,width,height,bytes,"
        "people_free,faces,quality,query,embedding,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (scene["id"], scene["name"], source, url, rel, str(dest), sha, ph, w, h, dest.stat().st_size,
         people_free, faces, round(quality, 4), query, embed_blob, time.time()),
    )
    con.commit()
    return dest, "ok"

def embed_query(model, text):
    if model is None: return None
    v = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
    return np_to_blob(v)

def np_to_blob(v):
    import numpy as np
    return np.asarray(v, dtype=np.float32).tobytes()

def count_images(con):
    return con.execute("SELECT COUNT(*) FROM images").fetchone()[0]

def status(con):
    n = count_images(con)
    pf = con.execute("SELECT COUNT(*) FROM images WHERE people_free=1").fetchone()[0]
    sc = con.execute("SELECT COUNT(DISTINCT scene_id) FROM images").fetchone()[0]
    by = dict(con.execute("SELECT source, COUNT(*) FROM images GROUP BY source"))
    return {"images": n, "people_free": pf, "scenes_covered": sc, "by_source": by, "db": str(DB)}

def restore_limits():
    bak = ROOT / "config" / "poll_limits.json.bak-pre-scene-refs"
    dst = ROOT / "config" / "poll_limits.json"
    if not bak.exists():
        print("backup missing:", bak); return False
    dst.write_text(bak.read_text())
    print("restored", dst, "from", bak)
    return True

def download_one(url, referer=None):
    try:
        headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        if referer: headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            data = r.read(); ct = r.headers.get("Content-Type", "")
        if len(data) < 5000 or len(data) > 15_000_000: return None, "size"
        if "html" in (ct or "").lower(): return None, "html"
        return data, "ok"
    except Exception as e:
        return None, str(e)[:80]

def process_scene(con, scene, model, per_scene, delay_range, progress):
    sid = scene["id"]
    have = con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (sid,)).fetchone()[0]
    if have >= per_scene:
        progress["done_scenes"][sid] = have
        return have
    need = per_scene - have
    got = 0
    for q in queries_for(scene):
        if got >= need: break
        emb = embed_query(model, q)
        # Bing
        try:
            urls = bing_search(q, count=20)
        except Exception as e:
            print(f"  [bing fail] {sid} {e}", flush=True); urls = []
        time.sleep(random.uniform(*delay_range))
        # Wikimedia (english-ish)
        try:
            wq = f"{scene['name']} empty"
            urls += wiki_search(wq, limit=5)
        except Exception:
            pass
        time.sleep(random.uniform(0.5, 1.2))
        for u in urls:
            if got >= need: break
            if already_have(con, url=u): continue
            data, why = download_one(u)
            if not data:
                mark_skip(con, u, why); continue
            src = "wikimedia" if "wikimedia.org" in u else "bing"
            path, why = save_image(con, scene, src, u, data, q, emb)
            if why != "ok":
                mark_skip(con, u, why); continue
            got += 1
            print(f"  + {sid} {got+have}/{per_scene} {src} faces=? {path.name}", flush=True)
            time.sleep(random.uniform(0.3, 0.8))
    total = con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (sid,)).fetchone()[0]
    progress["done_scenes"][sid] = total
    return total


# ---------- 小红书 / 微博只读空景（串行锁 + source_poll；无点赞/评论/关注）----------
MAIN = pathlib.Path("~/xiaotian-wechat").expanduser()
MAIN_LOCK = MAIN / "data" / "collectors.lock"

def use_main_poll():
    """Worktree 与主仓库共用限额/锁。"""
    import source_poll
    from collectors import runner
    ms = MAIN / "data" / "source_poll_state.json"
    if ms.exists():
        source_poll.STATE = ms
    return MAIN_LOCK if MAIN_LOCK.exists() else runner.LOCK

def cn_topic(scene, platform="xiaohongshu"):
    """Prefer Chinese empty-scene queries; weibo s.weibo.com often 418s on niche terms — keep broader."""
    name = scene.get("name") or ""
    alias = (scene.get("aliases") or [""])[0]
    cat = scene.get("category") or ""
    if platform == "weibo":
        # broader, more common Chinese photo queries
        return (f"{name} 实拍", f"{alias or name} 风景 照片", f"{cat} {name} 打卡")
    return (f"{name} 空景 无人", f"{alias or name} 空景 环境", f"{name} 场所 实拍")

def _search_urls(platform, topic, gate, tab, max_items=8):
    """在已持有的 gate/tab 上搜一轮，返回 image urls。不点赞/评论/关注。"""
    from collectors import chrome
    from collectors.xiaohongshu import LIST_JS as XHS_LIST, DETAIL_JS as XHS_DETAIL
    from collectors.weibo import SEARCH_JS as WB_SEARCH
    import urllib.parse
    urls = []
    if platform == "xiaohongshu":
        search = "https://www.xiaohongshu.com/search_result?keyword=%s&source=web_search_result_notes" % urllib.parse.quote(topic)
        gate.page(); chrome.goto(tab, search); chrome.wait_ready(tab); gate.sleep_action()
        raw = chrome.js_json(tab, XHS_LIST) or {}
        if raw.get("blocked"):
            raise RuntimeError("blocked:" + str(raw.get("blocked")))
        items = (raw.get("items") or [])[:max_items]
        for it in items:
            for u in (it.get("images") or []):
                if u and u.startswith("http"): urls.append(u)
        for it in items[:2]:
            u = it.get("url")
            if not u: continue
            try:
                gate.page(); chrome.goto(tab, u); chrome.wait_ready(tab); gate.sleep_action()
                d = chrome.js_json(tab, XHS_DETAIL) or {}
                if d.get("blocked"): break
                for di in (d.get("items") or []):
                    for iu in (di.get("images") or []):
                        if iu and iu.startswith("http"): urls.append(iu)
            except Exception:
                break
    else:
        search = "https://s.weibo.com/weibo?q=%s" % urllib.parse.quote(topic)
        gate.page(); chrome.goto(tab, search); chrome.wait_ready(tab); gate.sleep_action()
        raw = chrome.js_json(tab, WB_SEARCH) or {}
        if raw.get("blocked"):
            raise RuntimeError("blocked:" + str(raw.get("blocked")))
        if not (raw.get("items") or []):
            body = (chrome.js(tab, "document.body.innerText.slice(0,200)") or "")
            if "418" in body or "无法正常运作" in body:
                raise RuntimeError("blocked:weibo-418")
        for it in (raw.get("items") or [])[:max_items]:
            for u in (it.get("images") or []):
                if u and u.startswith("http"): urls.append(u)
    seen, out = set(), []
    for u in urls:
        if u in seen: continue
        seen.add(u); out.append(u)
    return out

def collect_cn_pass(con, scenes, model, per_scene, progress, platforms=("xiaohongshu", "weibo"), per_platform_scenes=4):
    """串行锁内：每平台只 acquire 一次，搜若干缺图场景的中文空景，写入 scene_refs。可 resume。"""
    import source_poll
    from collectors import runner, chrome
    lock = use_main_poll()
    added = 0
    try:
        with runner.global_lock(lock, wait_s=5):
            need = [s for s in scenes if con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (s["id"],)).fetchone()[0] < per_scene]
            random.shuffle(need)
            for plat in platforms:
                batch = need[:per_platform_scenes]
                if not batch: break
                domain = {"xiaohongshu": "xiaohongshu.com", "weibo": "weibo.com"}[plat]
                tab = chrome.find_tab(domain)
                if not tab:
                    print(f"  [cn/{plat}] no-tab — skip", flush=True); continue
                gate = source_poll.acquire(plat, force=False)
                if not gate:
                    why = source_poll.check(plat)[1]
                    print(f"  [cn/{plat}] skipped:{why}", flush=True); continue
                orig = chrome.js(tab, "location.href")
                got_any = False
                try:
                    for sc in batch:
                        if gate.pages >= gate.p["max_pages"]:
                            print(f"  [cn/{plat}] max_pages reached", flush=True); break
                        have = con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (sc["id"],)).fetchone()[0]
                        if have >= per_scene: continue
                        topics = cn_topic(sc, plat)
                        got = 0
                        for topic in topics:
                            if got + have >= per_scene: break
                            if gate.pages >= gate.p["max_pages"]: break
                            print(f"  [cn/{plat}] {sc['id']} q={topic!r}", flush=True)
                            try:
                                urls = _search_urls(plat, topic, gate, tab, max_items=8)
                            except RuntimeError as e:
                                print(f"  [cn/{plat}] {e}", flush=True)
                                gate.done(captcha=True); gate = None; got_any = got_any
                                raise
                            print(f"  [cn/{plat}] urls={len(urls)}", flush=True)
                            emb = embed_query(model, topic)
                            ref = "https://www.xiaohongshu.com/" if plat == "xiaohongshu" else "https://weibo.com/"
                            for u in urls:
                                if got + have >= per_scene: break
                                if already_have(con, url=u): continue
                                data, why = download_one(u, referer=ref)
                                if not data:
                                    mark_skip(con, u, why); continue
                                path, why = save_image(con, sc, plat, u, data, topic, emb)
                                if why != "ok":
                                    mark_skip(con, u, why); continue
                                got += 1; added += 1; got_any = True
                                print(f"  + {sc['id']} {got+have}/{per_scene} {plat} {path.name}", flush=True)
                                time.sleep(random.uniform(0.4, 1.0))
                        total = con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (sc["id"],)).fetchone()[0]
                        progress["done_scenes"][sc["id"]] = total
                    if gate is not None:
                        gate.done(ok=True)  # poll itself succeeded even if some topics empty
                except RuntimeError:
                    pass
                except Exception as e:
                    print(f"  [cn/{plat}] error:{e}", flush=True)
                    try:
                        if gate is not None: gate.done(error=True)
                    except Exception: pass
                finally:
                    try: chrome.goto(tab, orig)
                    except Exception: pass
                done_ids = {s["id"] for s in batch}
                need = [s for s in need if s["id"] not in done_ids]
    except runner.Busy:
        print("  [cn] collectors.lock busy — skip this pass", flush=True)
    return added


def main():
    os.environ.setdefault("SCENE_REFS_BUILD", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=2000)
    ap.add_argument("--per-scene", type=int, default=4)
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--restore-limits", action="store_true")
    ap.add_argument("--rescore-faces", action="store_true", help="run Apple Vision on all images, set people_free")
    ap.add_argument("--scene-id", help="only one scene id")
    ap.add_argument("--limit-scenes", type=int, default=0, help="process at most N scenes this run")
    ap.add_argument("--delay", default="1.5,3.5", help="bing query delay lo,hi seconds")
    ap.add_argument("--collectors", action="store_true",
                    help="also pull Chinese empty-scene photos via read-only 小红书/微博 (serial lock)")
    ap.add_argument("--collector-every", type=int, default=15,
                    help="run a CN collector pass every N bing scenes (default 15)")
    ap.add_argument("--max-hours", type=float, default=0,
                    help="stop after N hours (0=no limit); then caller should --restore-limits")
    a = ap.parse_args()
    if a.restore_limits:
        sys.exit(0 if restore_limits() else 1)
    con = ensure()
    if a.rescore_faces:
        rows=list(con.execute("SELECT id,path,relpath FROM images")); n=0
        for i,path,rel in rows:
            pth=pathlib.Path(path)
            if not pth.is_file(): pth=IMG_DIR/rel
            faces,fq=vision_faces(pth)
            # small faces (far away) count as people_free: face_area from swift not returned; use faces<=0 or quality low+faces<=2 heuristic via labels people conf — keep strict faces==0 OR faces>0 with tiny bbox via re-parse
            pf=1 if faces==0 else 0
            # if faces>0, check face_area from a quick re-run of JSON
            if faces>0:
                try:
                    import subprocess
                    r=subprocess.run([str(ASSETS/"refscan.bin"), str(pth)], capture_output=True, text=True, timeout=30)
                    o=json.loads(r.stdout.strip().splitlines()[-1]); fa=float(o.get("face_area") or 0)
                    if fa < 0.02: pf=1  # small/far faces OK
                except Exception: pass
            q=con.execute("SELECT width,height FROM images WHERE id=?",(i,)).fetchone()
            area=(q[0] or 1)*(q[1] or 1)
            quality=min(1.0, area/(1600*1200))*0.7+(0.25 if pf else 0.0)+0.05*min(1.0,fq)
            con.execute("UPDATE images SET faces=?, people_free=?, quality=? WHERE id=?",(faces,pf,round(quality,4),i)); n+=1
            if n%20==0: con.commit(); print("rescored",n,flush=True)
        con.commit(); print(json.dumps(status(con),ensure_ascii=False,indent=1)); return
    if a.status:
        print(json.dumps(status(con), ensure_ascii=False, indent=1)); return
    delay = tuple(float(x) for x in a.delay.split(","))
    scenes = load_scenes()
    if a.scene_id:
        scenes = [s for s in scenes if s["id"] == a.scene_id]
    progress = load_progress()
    # load embed model once
    model = None
    try:
        import outfit
        model = outfit._load_model()
        print("embed model:", "ok" if model else "unavailable")
    except Exception as e:
        print("embed model fail:", e)
    # compile vision helper early
    print("vision faces smoke…", flush=True)
    # prioritize scenes with fewest images
    random.shuffle(scenes)  # diversify overnight
    scenes.sort(key=lambda s: progress.get("done_scenes", {}).get(s["id"], 0))
    n_proc = 0
    t0 = time.time()
    try:
        # initial CN pass if requested (resume-safe; respects source_poll 10-min gate)
        if getattr(a, "collectors", False) and count_images(con) < a.target:
            print("CN collector pass (startup)…", flush=True)
            collect_cn_pass(con, scenes, model, a.per_scene, progress)
            save_progress(progress)
            print("status", status(con), flush=True)
        for sc in scenes:
            if count_images(con) >= a.target:
                print("hit target", a.target); break
            if a.max_hours and (time.time() - t0) / 3600.0 >= a.max_hours:
                print("hit max-hours", a.max_hours); break
            if a.limit_scenes and n_proc >= a.limit_scenes: break
            print(f"[{count_images(con)}/{a.target}] scene {sc['id']} {sc['name']}", flush=True)
            process_scene(con, sc, model, a.per_scene, delay, progress)
            n_proc += 1
            if a.collectors and n_proc % max(1, a.collector_every) == 0 and count_images(con) < a.target:
                print("CN collector pass…", flush=True)
                collect_cn_pass(con, scenes, model, a.per_scene, progress)
            if n_proc % 5 == 0:
                save_progress(progress)
                print("status", status(con), flush=True)
    finally:
        save_progress(progress)
        print("FINAL", json.dumps(status(con), ensure_ascii=False), flush=True)
        # re-score people_free for rows with faces==-1 skipped; already set
    con.close()

if __name__ == "__main__":
    main()
