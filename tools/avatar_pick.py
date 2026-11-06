"""小甜自己挑微信头像（不需要用户确认）。只读搜索小红书 + 本地候选池，硬过滤真人脸，按人设打分选一张。

来源：
  1) 小红书搜索列表（只读）：全局串行锁 + source_poll.acquire("xiaohongshu") →
     已登录 Chrome 的小红书标签页 osascript 跳搜索页 → collectors.xiaohongshu.LIST_JS 读封面 →
     tools/media_download.fetch 带 UA/Referer 下载（不带 cookie）→ 结束后把标签页导航回原 URL。
     **限额与串行锁共用主仓库** ~/xiaotian-wechat/data/{source_poll_state.json,collectors.lock}
     （本 worktree data/ 下是相对符号链接；代码里也强制 path override，避免双计数）。
     source_poll 不放行（静默时段/间隔/日上限）就跳过搜索，只用本地池；xhs_force=True 才跳过静默/间隔（日上限照常）。
  2) 本地候选池 data/avatar/candidates/*.png（sources.json 有标题/来源）。
硬规则：只要插画/宠物/风景/物件 —— 标题含真人类词直接丢；品牌/公司/广告发布者丢；
  macOS Vision 检出任何人脸或人体也丢（宁可错杀）。
打分：人设（甜、俏皮的大学女生）关键词 + 画面明亮/暖色/粉色 + 点赞（小权重）+ 分辨率（高清加分、缩略图降权）；
  排除最近用过的图；优先个人/插画号。
  3) 自己相册（config.avatar.album_dir）：`--album` / source=album 时
     random 抽图，不走小红书/source_poll/品牌号；**允许人像**（本库即小甜自己相册人像包，
     不做标题真人词/Vision 人脸硬筛；与 xhs 池不同）；排除近用约 6 张；heic 用 sips 转 jpg。
相册换头策略（见 album_allow / config.avatar.mood）：无 14 天硬约束；mood 门控 + 日概率 + 最短间隔小时。
小红书检索：无 14 天硬约束，必须守 **source_poll quiet（≈01:00–07:30，见 config/poll_limits.json）**，
  不是 proactive 的 quiet_hours / quiet_hours_weekend；配额满见 avatar_qq 队列（config.avatar.queue_path）。
用法：.venv/bin/python tools/avatar_pick.py [--no-search] [--xhs-force] [--query 关键词]  （只挑不换，打印 JSON）
      .venv/bin/python tools/avatar_pick.py --album   # 从自己相册随机抽一张
"""
import argparse, json, math, os, pathlib, random, re, sys, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tools"))
CAND = ROOT / "data" / "avatar" / "candidates"
AUTO = CAND / "auto"
HISTORY = ROOT / "data" / "avatar" / "history.json"
ALBUM_CACHE = ROOT / "data" / "avatar" / "album_cache"
ALBUM_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
CHANGE_LOCK = ROOT / "data" / "avatar" / "last_change.lock"  # QQ 换头互斥/最短间隔（JSON）
# 主仓库限额/锁（worktree 与 main 必须共享，否则会双计数或互相抢锁）
MAIN = pathlib.Path("~/xiaotian-wechat").expanduser()
MAIN_STATE = MAIN / "data" / "source_poll_state.json"
MAIN_LOCK = MAIN / "data" / "collectors.lock"
QUERIES = ["可爱头像 插画", "小猫头像 可爱", "小狗头像 治愈", "卡通头像 少女心", "治愈系 风景头像", "甜妹 头像 插画 小动物"]
# 真人/照片类词：命中直接丢
REAL_WORDS = re.compile(r"真人|自拍|本人|素颜|明星|爱豆|idol|写真|网红|小姐姐|帅哥|美女|男友|女友|情侣|证件照|妆|穿搭|ootd|"
                        r"颜值|生图|饭拍|街拍|cos|coser|模特|博主本人|我的脸|露脸", re.I)
# 品牌/广告/公司发布者：标题或作者命中直接丢（优先个人/插画号）
BRAND_WORDS = re.compile(
    r"公司|有限公司|旗舰店|官方店|专卖店|广告|招租|代购|批发|招商|代理|客服|售后|"
    r"品牌|官方旗舰|店铺|商城|淘宝|天猫|京东|拼多多|微商|引流|接单|定制头像商|"
    r"studio\s*official|official\s*store|旗舰", re.I)
BRAND_HANDLES = {  # 已知品牌/机构号（小写比对）
    "北京云创讯通科技有限公司", "mcdonald", "麦当劳", "肯德基", "星巴克", "迪士尼", "泡泡玛特",
}
PERSONA = {  # 甜、俏皮的大学女生
    "可爱": 3, "甜": 3, "软萌": 3, "俏皮": 3, "少女": 2, "粉": 2, "草莓": 2, "奶油": 1, "蛋糕": 1,
    "猫": 3, "咪": 3, "狗": 3, "柯基": 3, "兔": 3, "小熊": 3, "仓鼠": 2, "小动物": 2,
    "插画": 2, "卡通": 2, "手绘": 2, "治愈": 2, "温柔": 1, "小众": 1, "花": 1, "云": 1, "星星": 1,
}
NEGATIVE = {"暗黑": -4, "丧": -3, "emo": -3, "酷": -2, "黑白": -2, "恐怖": -5, "男生": -3, "情头": -2, "伤感": -3}
# 分辨率：原图最短边（square_png 前）。≥600 加分；<300 强降权（仍可进池，但几乎选不上）
HIRES_BONUS, TINY_PENALTY, TINY_PX, GOOD_PX = 2.0, -6.0, 300, 600


# ---------------- 打分 ----------------
def text_score(text):
    t = (text or "").lower(); s, why = 0, []
    for k, w in list(PERSONA.items()) + list(NEGATIVE.items()):
        if k.lower() in t: s += w; why.append(f"{k}{w:+d}")
    return s, why


def image_score(im):
    """明亮、暖色/粉色、有饱和度 → 更像「甜」。返回 (分, 说明)。"""
    hsv = im.convert("RGB").resize((64, 64)).convert("HSV")
    h, s_, v = (list(c.tobytes()) for c in hsv.split())
    n = len(h); bright = sum(v) / n / 255; sat = sum(s_) / n / 255
    warm = sum(1 for hh, ss in zip(h, s_) if ss > 40 and (hh < 30 or hh > 200)) / n   # 红/粉/橙/品红
    sc = 3 * min(bright, 0.85) + 2 * min(sat, 0.5) + 3 * warm
    return round(sc, 2), f"明亮{bright:.2f} 饱和{sat:.2f} 暖粉{warm:.2f}"


def likes_score(likes):
    m = re.match(r"([\d.]+)\s*([万w])?", str(likes or "").strip(), re.I)
    if not m: return 0.0
    n = float(m.group(1)) * (10000 if m.group(2) else 1)
    return round(min(math.log10(n + 1), 5) * 0.4, 2)


def res_score(px):
    """分辨率加分/降权。px = 原图最短边；None 表示未知。"""
    if px is None: return 0.0, "分辨率未知"
    if px < TINY_PX: return TINY_PENALTY, f"缩略图{px}px{TINY_PENALTY:+.0f}"
    if px >= GOOD_PX: return HIRES_BONUS, f"高清{px}px+{HIRES_BONUS:.0f}"
    # 300–599：线性从 0 到 HIRES_BONUS
    b = round(HIRES_BONUS * (px - TINY_PX) / (GOOD_PX - TINY_PX), 2)
    return b, f"中等{px}px+{b}"


# ---------------- 真人脸过滤（macOS Vision）----------------
def detect_people(path):
    """返回检测到的人脸 + 人体数量；Vision 不可用时返回 None（调用方按“不安全”处理）。"""
    try:
        import Vision, Quartz
        from Foundation import NSURL
    except Exception:
        return None
    url = NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    reqs = [Vision.VNDetectFaceRectanglesRequest.alloc().init(), Vision.VNDetectHumanRectanglesRequest.alloc().init()]
    ok, err = handler.performRequests_error_(reqs, None)
    if not ok: return None
    n = 0
    for r in reqs:
        for o in (r.results() or []):
            if o.confidence() >= 0.3: n += 1
    return n


def brand_reason(title, author):
    """None = 通过；否则返回拒绝原因（公司/广告/旗舰店/已知品牌号）。"""
    blob = f"{title or ''} {author or ''}"
    if author and author.strip().lower() in {h.lower() for h in BRAND_HANDLES}:
        return f"已知品牌号「{author}」"
    for h in BRAND_HANDLES:
        if h.lower() in blob.lower(): return f"含品牌词「{h}」"
    m = BRAND_WORDS.search(blob)
    if m: return f"品牌/广告发布者「{m.group(0)}」"
    return None


def safe_reason(title, path, people=detect_people, author=None):
    """None = 通过；否则返回拒绝原因。硬规则：标题像真人 / 品牌广告 / 检出人脸人体 / 无法检测。"""
    m = REAL_WORDS.search(title or "")
    if m: return f"标题含真人类词「{m.group(0)}」"
    br = brand_reason(title, author)
    if br: return br
    n = people(path)
    if n is None: return "无法做人脸检测（安全起见不用）"
    if n > 0: return f"检出 {n} 个人脸/人体"
    return None


# ---------------- 候选来源 ----------------
def local_pool(cand_dir=CAND):
    meta = {}
    try: meta = {x["file"]: x for x in json.loads((cand_dir / "sources.json").read_text())}
    except Exception: pass
    try:
        am = {x["file"]: x for x in json.loads((cand_dir / "auto" / "sources.json").read_text())}
        meta = {**meta, **am}
    except Exception: pass
    out = []
    for p in sorted(cand_dir.glob("*.png")) + sorted((cand_dir / "auto").glob("*.png")):
        m = meta.get(p.name, {})
        from PIL import Image
        try: px = min(Image.open(p).size)
        except Exception: px = None
        out.append(dict(path=str(p), title=m.get("title", ""), query=m.get("query", ""), author=m.get("author"),
                        source_url=m.get("note_url") or m.get("source_url"), likes=m.get("likes"),
                        origin="local", px=m.get("px") or px))
    return out


def square_png(src, dest, size=640):
    from PIL import Image
    im = Image.open(src).convert("RGB"); s = min(im.size); l, t = (im.width - s) // 2, (im.height - s) // 2
    dest = pathlib.Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    im.crop((l, t, l + s, t + s)).resize((size, size), Image.LANCZOS).save(dest); return str(dest), s


def use_main_poll():
    """把 source_poll.STATE / runner 锁切到主仓库，避免 worktree 另开一份计数。"""
    import source_poll
    from collectors import runner
    if MAIN_STATE.exists(): source_poll.STATE = MAIN_STATE
    return MAIN_LOCK if MAIN_LOCK.exists() else runner.LOCK


def search_xhs(query, xhs_force=False, max_n=8, log=print):
    """只读搜索一页；返回 (status, [候选])。遵守主仓库全局锁 + source_poll（含日上限/全局上限）。"""
    import source_poll
    from collectors import chrome
    from collectors.runner import global_lock, Busy
    from collectors.xiaohongshu import LIST_JS
    import media_download as md
    lock = use_main_poll()
    try:
        with global_lock(path=lock, wait_s=0):
            gate = source_poll.acquire("xiaohongshu", force=xhs_force)
            if not gate: return "skipped:" + source_poll.check("xiaohongshu", force=xhs_force)[1], []
            tab = chrome.find_tab("xiaohongshu.com")
            if not tab: gate.done(error=True); return "error:no-tab", []
            orig = next((u for w, t, u in chrome.tabs() if (w, t) == tuple(tab)), None)
            out = []
            try:
                url = ("https://www.xiaohongshu.com/search_result?keyword=%s&source=web_search_result_notes"
                       % urllib.parse.quote(query))
                gate.page(); chrome.goto(tab, url); chrome.wait_ready(tab); gate.sleep_action()
                raw = chrome.js_json(tab, LIST_JS) or {}
                if raw.get("blocked"): gate.done(captcha=True); return "captcha:%s" % raw["blocked"], []
                tmp = AUTO / "raw"; tmp.mkdir(parents=True, exist_ok=True)
                for it in (raw.get("items") or [])[:max_n]:
                    if not it.get("images"): continue
                    # 品牌号直接跳过（省一次下载）
                    if brand_reason(it.get("title"), it.get("author")): continue
                    got, used_url = None, None
                    # hires() 已按高清→原图排序；取第一张能下到的（优先高清）
                    for u in md.hires(it["images"][0], "xiaohongshu"):
                        try: got = md.fetch(u, "https://www.xiaohongshu.com/", str(tmp))
                        except Exception as e: log("下载失败:", str(e)[:80]); got = None
                        if got: used_url = u; break
                    if not got: continue
                    p, sha = got
                    try:
                        from PIL import Image
                        px0 = min(Image.open(p).size)
                    except Exception:
                        os.unlink(p); continue
                    dest, px = square_png(p, AUTO / f"xhs_{sha[:12]}.png"); os.unlink(p)
                    out.append(dict(path=dest, title=it.get("title", ""), author=it.get("author"), query=query,
                                    source_url=it.get("url"), likes=it.get("likes"), origin="xhs",
                                    px=px, source_img=used_url))
                gate.done(ok=bool(out))
            except Exception as e:
                gate.done(error=True); return "error:%s" % str(e)[:200], out
            finally:
                if orig:
                    try: chrome.goto(tab, orig)
                    except Exception: pass
            _append_sources(out)
            return "ok", out
    except Busy:
        return "skipped:collector-busy", []


def _append_sources(items, cand_dir=CAND):
    p = cand_dir / "auto" / "sources.json"
    try: old = json.loads(p.read_text())
    except Exception: old = []
    seen = {x["file"] for x in old}
    for it in items:
        f = pathlib.Path(it["path"]).name
        if f not in seen:
            old.append(dict(file=f, px=it.get("px"),
                            **{k: it.get(k) for k in ("title", "author", "query", "source_url", "likes")}))
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(old, ensure_ascii=False, indent=1))


def recently_used(history=HISTORY, n=6):
    """近 n 条非 test 记录里出现过的 basename（image + source_image），供排除。"""
    try: h = json.loads(pathlib.Path(history).read_text())
    except Exception: return set()
    out = set()
    for e in h[-n:]:
        if e.get("test"): continue
        for k in ("image", "source_image"):
            if e.get(k): out.add(pathlib.Path(e[k]).name)
    return out



# ---------------- 自己相册 ----------------
def load_avatar_cfg(cfg_path=None):
    """读 config.json 的 avatar 段；缺省 album_dir=appearance zhuzhuxiaoye/raw。"""
    cfg_path = pathlib.Path(cfg_path or (ROOT / "config.json"))
    try: cfg = json.loads(cfg_path.read_text())
    except Exception: cfg = {}
    av = dict(cfg.get("avatar") or {})
    if not av.get("album_dir"):
        assets = (cfg.get("appearance") or {}).get("assets_dir") or str(pathlib.Path("~/xiaotian-assets/appearance").expanduser())
        av["album_dir"] = str(pathlib.Path(assets) / "zhuzhuxiaoye" / "raw")
    av.setdefault("album_min_days", 0)
    av.setdefault("qq_change_min_h", 24)
    av.setdefault("cross_change_min_h", 24)
    # xhs：取消 14 天硬约束（默认 0）；机器人检测仍走 source_poll
    av.setdefault("xhs_min_days", 0)
    av.setdefault("queue_path", str(ROOT / "data" / "avatar" / "queue.json"))
    mood = dict(av.get("mood") or {})
    mood.setdefault("daily_p", 0.28)
    mood.setdefault("min_interval_h", 6)
    mood.setdefault("mood_min", 6.0)
    mood.setdefault("high_mood", 8.0)
    mood.setdefault("high_mood_bonus_p", 0.22)
    mood.setdefault("sweet_pulse_bonus_p", 0.15)
    mood.setdefault("roll_state_path", str(ROOT / "data" / "avatar" / "album_gate.json"))
    av["mood"] = mood
    return av


def album_pool(album_dir=None):
    """递归收集 album_dir 下图片；返回 [{path, origin, title}, ...]。"""
    d = pathlib.Path(album_dir or load_avatar_cfg()["album_dir"]).expanduser()
    if not d.is_dir(): return []
    out = []
    for p in sorted(d.rglob("*")):
        if not p.is_file(): continue
        if p.suffix.lower() not in ALBUM_EXTS: continue
        # 跳过隐藏/缓存
        if any(part.startswith(".") for part in p.parts): continue
        out.append(dict(path=str(p), title=p.stem, origin="album", source="album"))
    return out


def ensure_readable(path, cache_dir=ALBUM_CACHE):
    """heic → sips 转 jpg 到 cache；其它格式原样返回。"""
    path = pathlib.Path(path)
    if path.suffix.lower() != ".heic":
        return path
    import subprocess
    cache_dir = pathlib.Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cache_dir / (path.stem + ".jpg")
    if not dst.exists() or dst.stat().st_mtime < path.stat().st_mtime:
        r = subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(dst)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            raise RuntimeError(f"sips heic→jpg 失败: {path.name}: {(r.stderr or r.stdout)[:200]}")
    return dst


def album_face_reason(title, path, people=detect_people):
    """相册不再做人脸/真人词硬筛（人像包合法来源）。保留函数供兼容，恒返回 None=通过。
    小红书/本地池仍走 safe_reason。"""
    return None


def pick_album(history=HISTORY, album_dir=None, exclude_n=6, size=640, log=print, people=detect_people, max_tries=48):
    """自己相册随机一张：不走 xhs/source_poll/品牌；**允许人像**（不做标题/Vision 人脸硬筛）；排除近 exclude_n 次用过的 basename。"""
    pool = album_pool(album_dir)
    if not pool:
        log("相册为空:", album_dir or load_avatar_cfg().get("album_dir"))
        return None
    excl = recently_used(history, n=exclude_n)
    cur = current_avatar(history)
    if cur: excl = set(excl) | {pathlib.Path(cur).name}
    cands = [c for c in pool if pathlib.Path(c["path"]).name not in excl]
    if not cands:
        cands = list(pool)
        log(f"相册近 {exclude_n} 次全覆盖，回退整池 {len(pool)}")
    order = list(cands)
    random.shuffle(order)
    rejected = []
    import hashlib
    dest_dir = ALBUM_CACHE; dest_dir.mkdir(parents=True, exist_ok=True)
    for c in order[: max(1, int(max_tries))]:
        src = pathlib.Path(c["path"])
        try:
            readable = ensure_readable(src)
        except Exception as e:
            rejected.append({"file": src.name, "why": f"不可读:{e}"[:80]})
            continue
        # album 允许人像：跳过标题真人词 + Vision 硬筛（xhs 仍走 safe_reason）
        _ = album_face_reason(c.get("title") or src.stem, readable, people=people)
        digest = hashlib.md5(str(src).encode()).hexdigest()[:10]
        dest = dest_dir / f"album_{src.stem[:48]}_{digest}.png"
        squared, px = square_png(readable, dest, size=size)
        reason = (f"相册随机「{src.name}」：origin=album，跳过小红书/品牌；"
                  f"允许人像（未做人脸硬筛）；池 {len(pool)}，近用排除 {len(excl)}，"
                  f"试 {len(rejected)+1} 张后命中，淘汰 {len(rejected)}")
        return dict(image=squared, source_image=str(src), title=src.stem, author=None, source_url=None,
                    query=None, origin="album", source="album", score=None, reason=reason, px=px,
                    rejected=rejected[:20], runner_up=[], search_status="album")
    log(f"相册无可读图：试 {len(rejected)} 张均失败")
    return dict(image=None, source_image=None, title=None, author=None, source_url=None,
                query=None, origin="album", source="album", score=None,
                reason="相册候选均不可读或处理失败", px=None,
                rejected=rejected[:40], runner_up=[], search_status="album-no-safe")



# ---------------- 心情门控（相册）----------------
def load_mood_snapshot(cid=None):
    """伪实现：读联系人 STATE 的 mood/energy/sweet；失败则中性偏甜默认。"""
    try:
        import emotion as E
        import memory as M
        from config import load as load_cfg
        cfg = load_cfg()
        cid = cid or (cfg.get("contact") or {}).get("id")
        st = M.read_state(cid) if cid else {}
        emo = st.get("emotion") or {}
        d = E.derived(emo) if emo else {"mood": 6.5, "energy": 5.0, "intimacy": 50.0}
        sweet = False
        try:
            sm = E.sweet_mode(emo or E.default(), "", last_reply_at=st.get("last_reply_at"))
            sweet = bool(sm.get("want_warm") or sm.get("mode") in ("warm", "miss", "care"))
        except Exception:
            sweet = float(d.get("intimacy") or 0) >= 60 and float(d.get("mood") or 0) >= 6.5
        return {"mood": float(d.get("mood", 6.5)), "energy": float(d.get("energy", 5.0)),
                "intimacy": float(d.get("intimacy", 50.0)), "sweet_pulse": sweet, "source": "state"}
    except Exception:
        return {"mood": 6.5, "energy": 5.0, "intimacy": 50.0, "sweet_pulse": False, "source": "default"}


def _last_album_changed_at(history=HISTORY, channel="qq"):
    try:
        h = json.loads(pathlib.Path(history).read_text())
    except Exception:
        return None
    for e in reversed(h):
        if e.get("test") or e.get("status") != "changed":
            continue
        if e.get("channel", channel) != channel:
            continue
        if (e.get("source") or "") != "album":
            continue
        try:
            return __import__("datetime").datetime.fromisoformat(e["ts"])
        except Exception:
            return None
    return None


def album_effective_p(mood_snap, mood_cfg=None):
    """日概率：基线 + 高心情加成 + 小甜心波动加成，封顶 0.95。"""
    m = mood_cfg or load_avatar_cfg().get("mood") or {}
    p = float(m.get("daily_p", 0.28))
    mood = float((mood_snap or {}).get("mood", 6.5))
    if mood >= float(m.get("high_mood", 8.0)):
        p += float(m.get("high_mood_bonus_p", 0.22))
    if (mood_snap or {}).get("sweet_pulse"):
        p += float(m.get("sweet_pulse_bonus_p", 0.15))
    return max(0.0, min(0.95, p))


def _album_daily_roll(now, mood_cfg, rng, state_path=None):
    """同一自然日只掷一次骰；结果落盘 roll_state_path。返回 (roll, path)。"""
    import datetime as _dt
    state_path = pathlib.Path(state_path or mood_cfg.get("roll_state_path") or (ROOT / "data" / "avatar" / "album_gate.json"))
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    day = (now.date() if hasattr(now, "date") else _dt.date.today()).isoformat()
    try:
        st = json.loads(state_path.read_text())
    except Exception:
        st = {}
    if st.get("date") == day and "roll" in st:
        return float(st["roll"]), state_path
    roll = float(rng.random())
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"date": day, "roll": roll}, ensure_ascii=False, indent=1))
    return roll, state_path


def album_allow(now=None, history=HISTORY, mood_snap=None, mood_cfg=None, rng=None, force=False, channel="qq"):
    """相册换头是否放行。返回 (ok, reason, detail)。

    规则（无 14 天）：
      1) min_interval_h：距上次 album changed 不足则拒
      2) mood < mood_min 且无 sweet_pulse → 拒（低落不折腾头像）
      3) 日概率：effective_p = daily_p (+高心情/小甜心加成)；同日掷一次，roll>=p 则拒
    force=True 跳过全部门控。
    """
    import datetime as _dt
    now = now or _dt.datetime.now()
    av = load_avatar_cfg()
    m = dict(mood_cfg or av.get("mood") or {})
    detail = {"mood_cfg": {k: m.get(k) for k in ("daily_p", "min_interval_h", "mood_min", "high_mood")}}
    if force:
        return True, "forced", detail
    # 1) 最短间隔
    last = _last_album_changed_at(history, channel=channel)
    min_h = float(m.get("min_interval_h", 6))
    if last is not None and min_h > 0:
        hours = (now - last).total_seconds() / 3600.0
        detail["last_album_at"] = last.isoformat(timespec="seconds")
        detail["hours_since"] = round(hours, 2)
        if hours < min_h:
            return False, "min-interval", detail
    # 2) 心情门
    snap = mood_snap if mood_snap is not None else load_mood_snapshot()
    detail["mood"] = snap
    mood_v = float(snap.get("mood", 6.5))
    if mood_v < float(m.get("mood_min", 6.0)) and not snap.get("sweet_pulse"):
        return False, "mood-low", detail
    # 3) 日概率
    rng = rng or random.Random()
    p = album_effective_p(snap, m)
    roll, sp = _album_daily_roll(now, m, rng, state_path=m.get("roll_state_path"))
    detail.update({"effective_p": p, "roll": roll, "roll_state": str(sp)})
    if roll >= p:
        return False, "daily-miss", detail
    return True, "ok", detail



# ---------------- QQ 换头最短间隔（process-queue / album 互斥）----------------
def load_change_lock(path=None):
    p = pathlib.Path(path or CHANGE_LOCK)
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def last_avatar_change_at(path=None):
    """最近一次真实换头时间（ISO → datetime）；无则 None。"""
    import datetime as _dt
    st = load_change_lock(path)
    ts = st.get("last_avatar_change_ts") or st.get("ts")
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts)
    except Exception:
        return None


def change_interval_allow(now=None, min_h=None, path=None, force=False):
    """距上次任意 QQ 换头是否已过最短间隔。返回 (ok, reason, detail)。"""
    import datetime as _dt
    now = now or _dt.datetime.now()
    av = load_avatar_cfg()
    if min_h is None:
        min_h = float((av.get("cross_change_min_h") if av.get("cross_change_min_h") is not None
                       else av.get("qq_change_min_h", 24)))
    detail = {"min_h": min_h, "lock": str(path or CHANGE_LOCK)}
    if force or min_h <= 0:
        return True, "ok", detail
    last = last_avatar_change_at(path)
    if last is None:
        return True, "ok", detail
    hours = (now - last).total_seconds() / 3600.0
    detail["last_avatar_change_ts"] = last.isoformat(timespec="seconds")
    detail["hours_since"] = round(hours, 2)
    if hours < min_h:
        return False, "change-interval", detail
    return True, "ok", detail


def mark_avatar_changed(channel="qq", source=None, now=None, path=None, image=None):
    """真实换头成功后写入 last_change.lock（供 process-queue / album 互斥）。"""
    import datetime as _dt
    now = now or _dt.datetime.now()
    p = pathlib.Path(path or CHANGE_LOCK)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_avatar_change_ts": now.isoformat(timespec="seconds"),
        "channel": channel,
        "source": source,
        "image": image,
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


# ---------------- 换头队列（小红书等受 source_poll 约束时）----------------
def queue_path(av=None):
    av = av or load_avatar_cfg()
    p = pathlib.Path(av.get("queue_path") or (ROOT / "data" / "avatar" / "queue.json"))
    if not p.is_absolute():
        p = ROOT / p
    return p


def load_queue(path=None):
    p = pathlib.Path(path or queue_path())
    try:
        data = json.loads(p.read_text())
    except Exception:
        data = {"items": []}
    if not isinstance(data, dict):
        data = {"items": []}
    data.setdefault("items", [])
    return data


def save_queue(data, path=None):
    p = pathlib.Path(path or queue_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return p


def quiet_window(platform="xiaohongshu"):
    """返回 (quiet_start_hm, quiet_end_hm) 分钟数。

    读 **source_poll**（config/poll_limits.json defaults.quiet，默认 01:00–07:30）。
    与 proactive 的 quiet_hours / quiet_hours_weekend（约 00:30–09:00/10:00）无关。
    """
    import source_poll
    p = source_poll.cfg(platform)
    a, b = p.get("quiet") or ["01:00", "07:30"]
    return source_poll._hm(a), source_poll._hm(b)


def after_quiet_datetime(day, platform="xiaohongshu"):
    """某日 quiet 结束后的第一刻（本地）。"""
    import datetime as _dt
    _, end_m = quiet_window(platform)
    return _dt.datetime.combine(day, _dt.time(end_m // 60, end_m % 60))


def earliest_for_poll_block(why, now=None, platform="xiaohongshu"):
    """根据 source_poll 拒绝原因算 earliest。daily-cap → 明天 quiet 结束后（第一号）。"""
    import datetime as _dt
    import re as _re
    now = now or _dt.datetime.now()
    why = why or ""
    if why in ("daily-cap", "global-daily-cap") or why.startswith("daily"):
        return after_quiet_datetime(now.date() + _dt.timedelta(days=1), platform)
    if why == "quiet-hours":
        end = after_quiet_datetime(now.date(), platform)
        if now < end:
            return end
        return after_quiet_datetime(now.date() + _dt.timedelta(days=1), platform)
    m = _re.search(r"too-soon\(next=([^)]+)\)", why)
    if m:
        try:
            t = _dt.datetime.fromisoformat(m.group(1))
            # 若 next 仍落在静默内，推到 quiet 结束
            import source_poll
            if source_poll.in_quiet(source_poll.cfg(platform), t):
                return after_quiet_datetime(t.date(), platform)
            return t
        except Exception:
            pass
    # captcha/backoff 等：保守推到明天第一号
    return after_quiet_datetime(now.date() + _dt.timedelta(days=1), platform)


def enqueue_avatar(reason, now=None, source="xhs", query=None, path=None, front=None):
    """入队。配额满（daily-cap*）默认插到队首（明天第一号）；返回 item。"""
    import datetime as _dt
    import uuid
    now = now or _dt.datetime.now()
    why = reason or "unknown"
    if front is None:
        front = why in ("daily-cap", "global-daily-cap") or why.startswith("daily")
    earliest = earliest_for_poll_block(why, now=now)
    item = {
        "id": uuid.uuid4().hex[:12],
        "enqueued_at": now.isoformat(timespec="seconds"),
        "earliest": earliest.isoformat(timespec="minutes"),
        "source": source,
        "query": query,
        "reason": why,
        "priority": 0 if front else 10,
    }
    data = load_queue(path)
    items = list(data.get("items") or [])
    if front:
        items.insert(0, item)
    else:
        items.append(item)
    # 稳定：priority 升序，同优先按 earliest
    items.sort(key=lambda x: (int(x.get("priority", 10)), x.get("earliest") or "", x.get("enqueued_at") or ""))
    data["items"] = items
    save_queue(data, path)
    return item


def peek_ready(now=None, path=None, platform="xiaohongshu"):
    """队首若 earliest<=now 且不在 source_poll quiet（≈01:00–07:30），返回 item；否则 None。不弹出。"""
    import datetime as _dt
    import source_poll
    now = now or _dt.datetime.now()
    if source_poll.in_quiet(source_poll.cfg(platform), now):
        return None
    data = load_queue(path)
    for it in data.get("items") or []:
        try:
            t = _dt.datetime.fromisoformat(it["earliest"])
        except Exception:
            continue
        if t <= now:
            return it
    return None


def dequeue(item_id, path=None):
    data = load_queue(path)
    items = [x for x in (data.get("items") or []) if x.get("id") != item_id]
    data["items"] = items
    save_queue(data, path)
    return data

# ---------------- 选择 ----------------
def score(c, people=detect_people):
    from PIL import Image
    bad = safe_reason(c.get("title"), c["path"], people, author=c.get("author"))
    if bad: return None, bad
    ts, why = text_score(f'{c.get("title", "")} {c.get("query", "")}')
    isc, idesc = image_score(Image.open(c["path"]))
    ls = likes_score(c.get("likes"))
    rs, rdesc = res_score(c.get("px"))
    total = round(ts + isc + ls + rs, 2)
    return total, f"人设词[{' '.join(why) or '无'}]={ts} 画面[{idesc}]={isc} 点赞={ls} {rdesc}"


def choose(cands, people=detect_people, exclude=(), current=None):
    scored, rejected = [], []
    for c in cands:
        name = pathlib.Path(c["path"]).name
        if name in exclude or (current and name == pathlib.Path(current).name):
            rejected.append((name, "最近用过/当前头像")); continue
        s, why = score(c, people)
        if s is None: rejected.append((name, why)); continue
        scored.append((s, why, c))
    if not scored: return None, rejected, []
    scored.sort(key=lambda x: -x[0])
    s, why, c = scored[0]
    reason = (f"选「{c.get('title') or pathlib.Path(c['path']).name}」"
              f"{' @' + c['author'] if c.get('author') else ''}：总分 {s}（{why}）；"
              f"无真人脸（Vision 0 人脸/人体）、非品牌广告号；在 {len(scored)} 张合格候选里最高，淘汰 {len(rejected)} 张")
    pick = dict(image=c["path"], title=c.get("title"), author=c.get("author"), source_url=c.get("source_url"),
                query=c.get("query"), origin=c.get("origin"), score=s, reason=reason, px=c.get("px"),
                rejected=[f"{n}: {w}" for n, w in rejected][:20],
                runner_up=[dict(image=pathlib.Path(x[2]["path"]).name, score=x[0], px=x[2].get("px"))
                           for x in scored[1:4]])
    return pick, rejected, scored


def current_avatar(history=HISTORY):
    try: h = [e for e in json.loads(pathlib.Path(history).read_text()) if e.get("status") in ("changed", "already")]
    except Exception: return None
    return h[-1]["image"] if h else None


def pick(force=False, search=True, xhs_force=False, query=None, people=detect_people, log=print, history=HISTORY, source=None, album_dir=None):
    """返回 pick dict（含 image 路径与 reason）或 None。
    source="album"：走自己相册 random.choice（见 pick_album）；其它：小红书+本地池打分。
    force 只是透传给日志，不影响采集限速。"""
    if source == "album":
        return pick_album(history=history, album_dir=album_dir, log=log)
    status, fresh = ("not-searched", [])
    if search:
        q = query or random.choice(QUERIES)
        status, fresh = search_xhs(q, xhs_force=xhs_force, log=log)
        log(f"小红书搜索「{q}」: {status}，新候选 {len(fresh)}")
    pool = {c["path"]: c for c in local_pool()}
    for c in fresh: pool[c["path"]] = c
    p, rejected, scored = choose(list(pool.values()), people, exclude=recently_used(history), current=current_avatar(history))
    if p:
        p["search_status"] = status
        p.setdefault("source", "xhs")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-search", action="store_true"); ap.add_argument("--xhs-force", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--album", action="store_true", help="从自己相册(config.avatar.album_dir)随机抽一张")
    a = ap.parse_args()
    print(json.dumps(
        pick(search=not a.no_search, xhs_force=a.xhs_force, query=a.query,
             source="album" if a.album else None),
        ensure_ascii=False, indent=1))
