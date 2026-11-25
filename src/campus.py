"""校园动态：低频（launchd 每天 2 次）抓北京舞蹈学院公开页面，提取演出/讲座/比赛等事件，
写入 data/campus_events.json；公开报道只作为「听说」写进 STATE 与日记，不作为亲身经历。
礼貌抓取：先读 robots.txt（404 视为无限制）、固定 UA、请求间隔 ≥3s、只抓首页级页面；
遇到 202/反爬 JS 挑战页直接跳过，不做任何绕过。只用标准库。
CLI：python campus.py --fetch [--no-state]   /   python campus.py --show"""
import json, re, sys, time, random, hashlib, datetime, pathlib, urllib.request, urllib.robotparser, urllib.parse, html

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "campus_events.json"
UA = "xiaotian-campus/1.0 (personal, low-frequency; 2 req/day)"
SOURCES = ["https://news.bda.edu.cn/", "https://www.bda.edu.cn/"]
KEYS = ["演出", "首演", "展演", "汇报", "比赛", "大赛", "桃李杯", "荷花奖", "讲座", "论坛", "工作坊", "晚会",
        "音乐会", "毕业", "开学", "招生", "考试", "排练", "剧目", "舞剧", "编导", "学生会", "社团", "志愿", "艺术节"]
MINE = ["编导", "创意学院", "学生会", "社团", "影像社", "话剧社", "艺术节", "迎新"]   # 只用于话题相关性，不证明参与。

def settings():
    from config import load as load_config
    from personalization_runtime import option
    cfg = load_config()
    if not cfg.get("persona_version"):
        return cfg, OUT, SOURCES, MINE
    sources = option("content.campus_sources", [], cfg=cfg) or []
    d = cfg.get("role_dossier") or {}
    school, orgs = d.get("school") or {}, d.get("orgs") or {}
    mine = [school.get(k) for k in ("college", "major", "institution")]
    mine += [row.get("name") for row in orgs.get("clubs") or []]
    mine += [orgs.get("student_union", {}).get("org")]
    key = hashlib.sha256(json.dumps([school.get("institution"), sources], ensure_ascii=False).encode()).hexdigest()[:16]
    scope = cfg.get("identity_runtime") or {}
    root = pathlib.Path(scope.get("runtime_root") or pathlib.Path(cfg["db_path"]).parent / "persona-content" / cfg["personalization"]["role_id"] / cfg["personalization"]["relation_id"])
    return cfg, root / "campus" / (key + ".json"), sources, [str(value) for value in mine if value]

def _robots_ok(url):
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    try:
        r = urllib.request.urlopen(urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA}), timeout=15)
        rp = urllib.robotparser.RobotFileParser(); rp.parse(r.read().decode("utf-8", "ignore").splitlines())
        return rp.can_fetch(UA, url)
    except urllib.error.HTTPError as e:
        return e.code in (404, 410)          # 无 robots.txt => 允许；401/403/5xx => 保守跳过
    except Exception:
        return False

def fetch(url):
    """返回 (status, text)；非 200 或挑战页返回 (status, '')。"""
    if not _robots_ok(url): return "robots-disallow", ""
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20)
        body = r.read().decode("utf-8", "ignore")
        if r.status != 200 or len(body) < 2000 or "<a" not in body: return r.status, ""
        return 200, body
    except urllib.error.HTTPError as e: return e.code, ""
    except Exception as e: return type(e).__name__, ""

def extract(page, base):
    evs = []
    mine = settings()[3]
    for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        t = html.unescape(re.sub(r"<[^>]+>|\s+", " ", inner)).strip()
        t = re.sub(r"\s*发布时间[:：]\S*", "", re.sub(r"^(头条|要闻|置顶)\s+", "", t)).strip()
        t = re.sub(r"\s{2,}", " ", t)
        if not (8 <= len(t) <= 80) or not any(k in t for k in KEYS): continue
        evs.append({"title": t, "url": urllib.parse.urljoin(base, href.strip()),
                    "tags": [k for k in KEYS if k in t], "mine": any(k in t for k in mine)})
    return evs

def load():
    try:
        db = json.loads(settings()[1].read_text())
        # Older caches promoted relevant news to personal participation randomly.
        # Downgrade at read time, including before the next scheduled fetch.
        db["life"] = [life_event(e) for e in db.get("life", []) if e.get("title")]
        return db
    except (OSError, ValueError, TypeError, AttributeError):
        return {"events": [], "last_fetch": None, "log": []}

def merge(db, evs, now):
    seen = {e["id"] for e in db["events"]}; new = []
    for e in evs:
        e["id"] = hashlib.sha1(e["title"].encode()).hexdigest()[:12]
        if e["id"] in seen: continue
        seen.add(e["id"]); e["first_seen"] = now; db["events"].append(e); new.append(e)
    db["events"] = db["events"][-300:]
    return new

def life_event(e, rng=random):
    """A public headline proves publication, not attendance or current activity."""
    return {"kind": "听说", "text": f"学校公开新闻提到「{e['title']}」",
            "title": e["title"], "url": e.get("url", ""), "id": e.get("id", ""),
            "source": "public_news", "personal_participation": False}

def topics(n=3):
    """给 brain/proactive 用的话题钩子：最近的生活事件。"""
    return (load().get("life") or [])[-n:]

def prompt_line():
    t = topics(2)
    return ("校园新闻（只说明报道过，不证明本人参与、在忙或活动正在进行）：" + "；".join(x["text"] for x in t)) if t else ""

def run(write_state=True, rng=random, force=False):
    from config import load as load_config, snapshot
    with snapshot(load_config()) as cfg:
        from runtime_policy import allowed
        if cfg.get("persona_version") and write_state and not allowed("proactive", cfg):
            return {"skipped": "当前人物未授权校园动态写入"}
        result = _run(write_state, rng, force)
        from personalization_runtime import report_loaded
        report_loaded("content", cfg)
        return result


def _run(write_state=True, rng=random, force=False):
    cfg, out, sources, _ = settings()
    if not sources:
        return {"skipped": "尚未配置学校公开来源"}
    sys.path.insert(0, str(ROOT / "src")); import source_poll   # 中心限速（北舞官网 >=6h±30%、2 次/天、01:00-07:30 静默）
    key = "bda_site"
    if cfg.get("persona_version"):
        key = "campus:" + cfg["personalization"]["role_id"] + ":" + cfg["personalization"]["relation_id"] + ":" + out.stem
    limits = source_poll.cfg("bda_site")
    ok, why = source_poll.check(key, force=force, policy=limits)
    if not ok: return {"skipped": why}
    gate = source_poll.Gate(key, limits, datetime.datetime.now())
    now = datetime.datetime.now().isoformat(timespec="seconds"); db = load(); log = []; evs = []; bad = False
    for i, u in enumerate(sources):
        if i: gate.sleep_action()
        gate.page(); st, body = fetch(u); log.append({"url": u, "status": st, "ts": now})
        if body and source_poll.looks_blocked(body[:3000]) and len(re.findall(r"<a", body)) < 20: bad = True; body = ""
        if st != 200 and st != "robots-disallow": bad = True
        if body: evs += extract(body, u)
    gate.done(ok=not bad, captcha=False, error=bad)
    new = merge(db, evs, now)
    life = [life_event(e, rng) for e in new if e["mine"]][:2] + [life_event(e, rng) for e in new if not e["mine"]][:1]
    db.setdefault("life", []).extend(life); db["life"] = db["life"][-30:]
    db["last_fetch"] = now; db["log"] = (db.get("log", []) + log)[-40:]
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(db, ensure_ascii=False, indent=1))
    if write_state and db["life"]:
        sys.path.insert(0, str(ROOT / "src")); import memory
        for cid in (str((cfg.get("contact") or {}).get("id") or "demo_contact"),):
            memory.update_state(cid, campus_life=[x["text"] for x in db["life"][-3:]])
            for x in life: memory.append_diary(cid, f"[校园·{x['kind']}] {x['text']}")
    return {"log": log, "found": len(evs), "new": len(new), "life": life}

if __name__ == "__main__":
    if "--show" in sys.argv: print(json.dumps(load(), ensure_ascii=False, indent=1)[:4000])
    else: print(json.dumps(run(write_state="--no-state" not in sys.argv, force="--force" in sys.argv), ensure_ascii=False, indent=1))
