"""采集器公共流程：限速门 + DOM(JS) 路径 + OCR 路径 + 入库。只读。"""
import json, os, re, sys, time, tempfile, pathlib, urllib.request, difflib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import source_poll
from collectors import chrome

ROOT = pathlib.Path(__file__).resolve().parents[2]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
MAX_ITEMS = 10

class Blocked(Exception): pass

def norm_item(d, platform):
    """把 JS 抽出的原始 dict 规整成 add_item 字段。"""
    clean = lambda s: re.sub(r"\s+", " ", (s or "")).strip()
    tags = [clean(t).lstrip("#").rstrip("#") for t in d.get("tags") or [] if clean(t)]
    text = clean(d.get("text"))
    tags += [t for t in re.findall(r"#([^#\s]{1,20})#?", text) if t not in tags]
    return {"url": d.get("url"), "title": clean(d.get("title"))[:200] or text[:60], "text": text[:4000],
            "author": clean(d.get("author")) or None, "tags": tags[:15],
            "comments": [clean(c)[:300] for c in (d.get("comments") or []) if clean(c)][:5],
            "images": [u for u in (d.get("images") or []) if u and u.startswith("http")][:6],
            "video": d.get("video") if (d.get("video") or "").startswith("http") else None,
            "extra": {k: d[k] for k in ("likes", "rank", "heat") if d.get(k)}}

def dedupe(items):
    seen, out = set(), []
    for it in items:
        if it["url"] and it["url"] not in seen: seen.add(it["url"]); out.append(it)
    return out

def download(url, referer, dest_dir, max_bytes=30_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=25) as r:
        ct = r.headers.get("Content-Type", ""); data = r.read(max_bytes + 1)
    if len(data) > max_bytes: return None
    ext = ".mp4" if "video" in ct else ".png" if "png" in ct else ".webp" if "webp" in ct or "webp" in url else ".jpg"
    fd, p = tempfile.mkstemp(suffix=ext, dir=dest_dir); os.write(fd, data); os.close(fd); return p

def save(items, platform, referer, store=None):
    import material_store
    st = store or material_store._s(); tmp = ROOT / "data" / "dl_tmp"; tmp.mkdir(parents=True, exist_ok=True)
    n_media = 0
    for it in items:
        media = []
        for u in ([it["video"]] if it.get("video") else []) + it["images"][:4]:
            try:
                p = download(u, referer, tmp)
                if p: media.append({"path": p, "source_url": u})
            except Exception: pass
        extra = dict(it["extra"], top_comments=it["comments"])
        st.add_item(platform, it["url"], author=it["author"], title=it["title"], text=it["text"],
                    tags=it["tags"], extra=extra, media=media, move=True)
        n_media += len(media)
    return n_media

def ocr_lines(boxes, min_len=4):
    """OCR 框 -> 按行合并的文本列表（y 相近的同行合并）。"""
    rows = []
    for b in sorted(boxes, key=lambda b: (round(b["y"] / 8), b["x"])):
        t = b["text"].strip()
        if not t: continue
        if rows and abs(rows[-1]["y"] - b["y"]) < 8 and b["x"] - rows[-1]["x2"] < 40:
            rows[-1]["text"] += " " + t; rows[-1]["x2"] = b["x"] + b["w"]
        else: rows.append({"text": t, "x": b["x"], "y": b["y"], "x2": b["x"] + b["w"]})
    return [r for r in rows if len(r["text"]) >= min_len]

UI_NOISE = re.compile(r"^(发现|发布|通知|我|首页|推荐|关注|热门|搜索|登录|更多|全部|综合|视频|图文|用户|直播|消息|私信|热搜|榜单|刷新|小红书|抖音|微博|创作|精选)$")

def ocr_items(boxes, min_len=6):
    """OCR 行里挑出像标题/正文的行（去 UI 词、纯数字），作为 OCR 路径的“条目”。"""
    out = []
    for r in ocr_lines(boxes, min_len):
        t = r["text"]
        if UI_NOISE.match(t) or re.fullmatch(r"[\d\s.,:万wW+-]+", t): continue
        out.append(t)
    return out

def match_rate(dom_titles, ocr_texts):
    """DOM 标题里有多少能在 OCR 文本中找到（相似度≥0.6）。"""
    if not dom_titles: return None
    blob = ocr_texts; hit = 0
    for t in dom_titles:
        t = re.sub(r"\s+", "", t)[:20]
        if not t: continue
        best = max((difflib.SequenceMatcher(None, t, re.sub(r"\s+", "", o)[:len(t) + 4]).ratio() for o in blob), default=0)
        hit += best >= 0.6
    return round(hit / len(dom_titles), 2)

def ocr_pass(tab, gate):
    """OCR 路径：切到标签页截图识别；会抢焦点，结束后恢复原前台应用。
    抢焦点期间持微信 UI 锁（proactive 角色＝非阻塞、回复优先），避免 daemon 的 ⌘V/回车落进 Chrome。
    30s 内拿不到锁就跳过本次 OCR（DOM 结果照常）。"""
    import ui_lock as ul
    t0 = time.time()
    while True:
        try: lk = ul.ui_lock("proactive"); lk.__enter__(); break
        except ul.LockBusy:
            if time.time() - t0 > 30:
                return {"texts": [], "boxes": 0, "secs": round(time.time() - t0, 2), "focus": False, "blocked": False, "skipped": "wechat ui busy"}
            time.sleep(2)
    prev = chrome.frontmost()
    try:
        chrome.show_tab(tab); time.sleep(1.5)
        path = chrome.capture(); boxes = chrome.ocr(path)
        try: os.unlink(path)
        except OSError: pass
    finally:
        try: chrome.restore(prev)
        finally: lk.__exit__(None, None, None)
    texts = ocr_items(boxes)
    return {"texts": texts, "boxes": len(boxes), "secs": round(time.time() - t0, 2), "focus": prev != "com.google.Chrome",
            "blocked": source_poll.looks_blocked(" ".join(b["text"] for b in boxes[:80]))}

def lcu_allowed():
    import subprocess
    lcu = os.path.expanduser("~/.local/share/lcu/current/bin/lcu")
    try: out = subprocess.run([lcu, "apps", "list", "--json"], capture_output=True, text=True, timeout=10).stdout
    except Exception: return False
    return "com.google.Chrome" in out

def ax_texts(ax):
    """LCU AX 树文本 -> 候选条目文本（link/文本 节点的名字，去 URL/UI 词）。"""
    out = []
    for line in (ax or "").splitlines():
        m = re.match(r"\s*\d+ (?:link|文本|text|标题|heading|图像)\s+(.*)", line)
        if not m: continue
        t = re.split(r", (?:Value|Help|URL|Secondary Actions):", m.group(1))[0].strip()
        if len(t) >= 6 and not UI_NOISE.match(t) and not re.fullmatch(r"[A-Za-z0-9_.\-/:%]+", t): out.append(t)
    return list(dict.fromkeys(out))

def lcu_pass(tab=None, dom_titles=()):
    """LCU 路径（可选）：Chrome 必须已 `lcu apps allow`；不绕过授权。
    LCU 只能读前窗活动标签页的 AX 树（Browser API 关闭），所以临时切 Chrome 内的活动标签页（不 activate 应用），读完切回。"""
    if not lcu_allowed(): return {"available": False, "why": "Chrome 未授权"}
    prev_app = chrome.frontmost(); prev_idx = None; t0 = time.time()
    sys.path.insert(0, str(ROOT / "tools")); from lcu_client import LCU
    try:
        if tab:
            prev_idx = int(chrome.osa('on run argv\ntell application "Google Chrome" to return active tab index of window (item 1 of argv as integer)\nend run', tab[0]))
            chrome.osa('on run argv\ntell application "Google Chrome" to set active tab index of window (item 1 of argv as integer) to (item 2 of argv as integer)\nend run', tab[0], tab[1])
        c = LCU()
        try:
            c.js('let app = await cua.getApp("Google Chrome");', timeout=60)
            r = c.js('nodeRepl.write(await app.getAXState({disableDiffing: true}))', timeout=60)
        finally: c.close()
        ax = "\n".join(x.get("text", "") for x in r.get("result", {}).get("content", []))
    except Exception as e:
        return {"available": True, "error": str(e)[:200], "secs": round(time.time() - t0, 2)}
    finally:
        if tab and prev_idx and prev_idx != tab[1]:
            try: chrome.osa('on run argv\ntell application "Google Chrome" to set active tab index of window (item 1 of argv as integer) to (item 2 of argv as integer)\nend run', tab[0], prev_idx)
            except Exception: pass
    texts = ax_texts(ax)
    return {"available": True, "secs": round(time.time() - t0, 2), "ax_len": len(ax), "n_texts": len(texts),
            "match": match_rate(list(dom_titles), texts), "sample": texts[:4], "texts": texts,
            "focus_stolen": prev_app != chrome.frontmost(), "blocked": source_poll.looks_blocked(" ".join(texts[:80]))}

class Collector:
    platform = domain = referer = None
    def plan(self): """返回 [(url, extract_js, kind)]"""; raise NotImplementedError
    def parse(self, raw, kind): return [norm_item(d, self.platform) for d in (raw or {}).get("items", [])]

    def run(self, force=False, do_ocr=True, store=None, log=print, do_lcu=False):
        rep = {"platform": self.platform, "items": 0, "media": 0, "status": "ok"}
        gate = source_poll.acquire(self.platform, force=force)
        if not gate:
            rep["status"] = "skipped:" + source_poll.check(self.platform, force=force)[1]; return rep
        tab = chrome.find_tab(self.domain)
        if not tab: gate.done(error=True); rep["status"] = "error:no-tab"; return rep
        orig = chrome.js(tab, "location.href"); items = []; t0 = time.time()
        try:
            for url, code, kind in self.plan(items):
                if len(items) >= MAX_ITEMS and kind != "detail": break
                gate.page(); chrome.goto(tab, url); chrome.wait_ready(tab); gate.sleep_action()
                raw = chrome.js_json(tab, code) or {}
                if raw.get("blocked"): raise Blocked(raw.get("blocked"))
                for _ in range(raw.get("want_scroll", 0) and 2 or 0):
                    if len(items) + len(raw.get("items", [])) >= MAX_ITEMS: break
                    gate.scroll(); chrome.js(tab, "window.scrollBy(0, innerHeight*0.9); 'ok'"); gate.sleep_action()
                    raw = chrome.js_json(tab, code) or raw
                got = self.parse(raw, kind)
                if kind == "detail": self.merge_detail(items, got, url)
                else: items[:] = dedupe(items + got)[:MAX_ITEMS]
                if kind == "list" and do_ocr and "ocr" not in rep:
                    o = ocr_pass(tab, gate); rep["ocr"] = {k: v for k, v in o.items() if k != "texts"}
                    rep["ocr"]["n_items"] = len(o["texts"]); rep["ocr"]["match"] = match_rate([i["title"] for i, r in zip(got, raw.get("items", [])) if r.get("vis")] or [i["title"] for i in got], o["texts"])
                    rep["ocr"]["sample"] = o["texts"][:4]; rep["dom_list"] = {"n": len(got), "titles": [i["title"] for i in got][:4]}
                    if o["blocked"]: raise Blocked("ocr saw verification text")
                if kind == "list" and do_lcu and "lcu" not in rep:
                    l = lcu_pass(tab, [i["title"] for i in got if i["title"]]); rep["lcu"] = {k: v for k, v in l.items() if k != "texts"}
                    rep.setdefault("dom_list", {"n": len(got), "titles": [i["title"] for i in got][:4]})
                    if l.get("blocked"): raise Blocked("lcu saw verification text")
            rep["dom_secs"] = round(time.time() - t0, 2)
            rep["media"] = save(items, self.platform, self.referer, store); rep["items"] = len(items)
            rep["samples"] = [i["title"] for i in items[:3]]
            gate.done(ok=bool(items)); 
            if not items: rep["status"] = "empty"
        except Blocked as e:
            gate.done(captcha=True); rep["status"] = "captcha:%s" % e
        except Exception as e:
            gate.done(error=True); rep["status"] = "error:%s" % str(e)[:200]
        finally:
            try: chrome.goto(tab, orig)
            except Exception: pass
        rep["pages"], rep["scrolls"] = gate.pages, gate.scrolls
        return rep
    def merge_detail(self, items, got, url):
        if not got: return
        d = got[0]
        for it in items:
            if it["url"].split("?")[0] == url.split("?")[0]:
                for k in ("text", "author"):
                    if d[k] and len(d[k]) > len(it[k] or ""): it[k] = d[k]
                it["tags"] = list(dict.fromkeys(it["tags"] + d["tags"]))[:15]
                it["comments"] = d["comments"] or it["comments"]; it["images"] = d["images"] or it["images"]
