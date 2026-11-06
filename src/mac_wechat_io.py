"""WeChat I/O on Mac WeChat (/Applications/WeChat.app) via screenshot + Apple Vision OCR.
思路复用 vendor/wechat-mac-rpa: Quartz 找窗口 -> `screencapture -l <wid>` -> VNRecognizeTextRequest(zh-Hans)
-> 按 x 位置分左右（左=对方, 右=自己）-> cliclick 点击 + pbcopy/⌘V 粘贴 + 回车发送。
接口与 wechat_io.py 一致: open_chat / read_messages / send_message / norm；另增 send_image(path)（文件入剪贴板+粘贴发送，供 chat_media 桥接）。"""
import os, re, sys, subprocess, time, unicodedata, pathlib, difflib
import Quartz, Vision
from Foundation import NSURL
from config import load
CFG = load()
MAC = CFG.get("mac", {})
CLICLICK = MAC.get("cliclick", "/opt/homebrew/bin/cliclick")
SHOT = pathlib.Path(MAC.get("shot_path", os.path.expanduser("~/xiaotian-wechat/data/mac_shot.png")))
SIDEBAR_W = MAC.get("sidebar_w", 300)   # 左侧图标栏+会话列表宽度(pt)
HEADER_H = MAC.get("header_h", 60)     # 聊天标题栏高度
INPUT_H = MAC.get("input_h", 150)      # 底部输入区高度

def norm(t):
    t = unicodedata.normalize("NFC", t or "")
    t = "".join(c for c in t if unicodedata.category(c) not in ("Mn", "Me", "Cf"))
    return re.sub(r"\s+", "", t)

def _osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    if r.returncode: raise PermissionError(f"osascript failed (辅助功能/自动化权限?): {r.stderr.strip()}")
    return r.stdout.strip()

def window():
    """Return (wid, x, y, w, h) of the largest on-screen WeChat window."""
    best = None
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, 0) or []:
        if w.get("kCGWindowOwnerName") not in ("WeChat", "微信") or w.get("kCGWindowLayer") != 0: continue
        b = w.get("kCGWindowBounds"); area = b["Width"] * b["Height"]
        # 图片查看器/设置等子窗口也是 layer 0，可能比主窗口大（实测查看器 825x866）；主窗口标题为「微信」/「WeChat」优先
        key = (w.get("kCGWindowName") in ("微信", "WeChat", "Weixin"), area)
        if b["Width"] > 500 and (best is None or key > best[0]):
            best = (key, int(w["kCGWindowNumber"]), int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]))
    if not best: raise RuntimeError("WeChat main window not on screen")
    return best[1:]

def activate():
    """强制把微信拉到前台。已在前台时请用 activate_if_needed()，免去这段睡眠。"""
    subprocess.run(["open", "-a", "WeChat"], check=True); time.sleep(0.35)

def capture():
    wid, x, y, w, h = window()
    SHOT.parent.mkdir(exist_ok=True)
    r = subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), str(SHOT)], capture_output=True, text=True)
    if r.returncode or not SHOT.exists(): raise PermissionError(f"screencapture failed (屏幕录制权限?): {r.stderr}")
    return (x, y, w, h)

def ocr(path=None):
    """Apple Vision OCR -> [{'text','x','y','w','h'}] in window points (top-left origin)."""
    path = str(path or SHOT)
    up = MAC.get("ocr_upscale", 2)
    if up and up > 1:   # 1x 屏截图字太小，Vision 会把「干嘛呢」读成「干噰呢」；放大 2x 后正确（2026-10-24 实测）
        from PIL import Image
        im = Image.open(path)
        if im.width < 1500:
            tmp = str(pathlib.Path(path).with_suffix(".up.png")); im.resize((im.width * up, im.height * up), Image.LANCZOS).save(tmp); path = tmp
    _, _, _, ww, wh = window()
    return _vision(path, ww, wh)

def _vision(path, ww, wh):
    """OCR 图片 -> 以 (ww, wh) 点坐标系表示的框（左上原点）。"""
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W, H = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
    sx, sy = ww / W, wh / H   # pixels -> points (Retina)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(bool(MAC.get("ocr_language_correction", True)))  # 实测关掉会把「干嘛呢」读成「干噰呢」
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, err = h.performRequests_error_([req], None)
    if not ok: raise RuntimeError(err)
    out = []
    for o in req.results() or []:
        c = o.topCandidates_(1)
        if not c: continue
        bb = o.boundingBox()
        out.append({"text": c[0].string(), "x": bb.origin.x * W * sx, "y": (1 - bb.origin.y - bb.size.height) * H * sy,
                    "w": bb.size.width * W * sx, "h": bb.size.height * H * sy})
    return sorted(out, key=lambda e: (e["y"], e["x"]))

def click(wx, wy):
    """Click at window-relative point."""
    _, x, y, _, _ = window()
    r = subprocess.run([CLICLICK, f"c:{int(x + wx)},{int(y + wy)}"], capture_output=True, text=True)
    if r.returncode: raise PermissionError(f"cliclick failed (辅助功能权限?): {r.stderr}")

def _paste(text):
    subprocess.run(["pbcopy"], input=text, text=True, check=True)
    _osa('tell application "System Events" to keystroke "v" using command down')

def _key(code):
    _osa(f'tell application "System Events" to key code {code}')

def _bear_ys(x0, x1, y0, y1, img=None):
    """昵称汉字带 U+20D5 组合符，Mac 微信渲染不出来、OCR 读不到；只有 🐻 可见。
    用 🐻 的棕色像素 (≈132,90,48) 在区域内定位，返回命中的 y（窗口点坐标）。"""
    from PIL import Image
    im = Image.open(img or SHOT).convert("RGB"); W, H = im.size
    _, _, _, ww, wh = window(); kx, ky = W / ww, H / wh
    tr, tg, tb = BEAR_RGB; ys = []
    for y in range(int(y0 * ky), int(min(y1, wh) * ky), 2):
        hits = 0
        for x in range(int(x0 * kx), int(min(x1, ww) * kx), 2):
            r, g, b = im.getpixel((x, y))
            if abs(r - tr) < 18 and abs(g - tg) < 18 and abs(b - tb) < 18: hits += 1
        if hits >= 2: ys.append(y / ky)
    return ys

BEAR_RGB = tuple(MAC.get("bear_rgb", (132, 90, 48)))

def title():
    capture()
    return " ".join(e["text"] for e in ocr() if e["x"] > SIDEBAR_W and e["y"] < HEADER_H)

REMARK = CFG["contact"].get("remark", "")
SEARCH_KEY = CFG["contact"].get("search_key") or REMARK

def in_chat_with(nick=None) -> bool:
    """优先：标题栏 OCR 含备注「小熊」；后备：标题栏左半有 🐻 像素。"""
    capture()
    head = norm("".join(e["text"] for e in ocr() if e["x"] > SIDEBAR_W and e["y"] < HEADER_H))
    if REMARK and norm(REMARK) in head: return True
    _, _, _, w, _ = window()
    return bool(_bear_ys(SIDEBAR_W, SIDEBAR_W + (w - SIDEBAR_W) * 0.5, 5, HEADER_H))

def _open_by_search():
    """搜索框粘贴 search_key；结果是独立弹层，`-l` 截不到，改用 `-R` 截窗口区域，
    OCR 找含完整备注的「联系人」结果行并点击（不点「搜索网络结果」）。"""
    _key(53); time.sleep(0.3)
    click(160, 28); time.sleep(0.8)
    _paste(SEARCH_KEY); time.sleep(2.5)
    _, x, y, w, h = window()
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(SHOT)], check=True)
    hit = [e for e in ocr() if e["x"] < SIDEBAR_W + 40 and e["y"] > 45 and norm(REMARK) in norm(e["text"])]
    if not hit: _key(53); return False
    click(180, hit[0]["y"] + hit[0]["h"] / 2); time.sleep(1.5)
    return True

def open_chat(nick=None):
    """顺序：已在聊天 → 会话列表 OCR 找备注 → 搜索框搜备注 → 🐻 像素后备。"""
    nick = nick or CFG["contact"]["nickname"]
    activate()
    if in_chat_with(nick): return True
    _key(53); time.sleep(0.3)                # Esc 退出可能的搜索态
    click(30, 122); time.sleep(0.8)          # 左栏「微信」会话图标
    capture()
    if REMARK:
        rows = [e for e in ocr() if 60 < e["x"] < SIDEBAR_W and e["y"] > 45 and norm(REMARK) in norm(e["text"])]
        if rows:
            click(180, rows[0]["y"] + rows[0]["h"] / 2); time.sleep(1.5)
            if in_chat_with(nick): return True
        if _open_by_search() and in_chat_with(nick): return True
        click(30, 122); time.sleep(0.8); capture()
    ys = _bear_ys(70, SIDEBAR_W - 5, 50, 640)  # 后备：会话列表里 🐻 所在行
    if not ys: raise RuntimeError(f"chat with {nick} not found (remark/search/🐻 all failed)")
    click(180, ys[0] + 10); time.sleep(1.5)
    if not in_chat_with(nick): raise RuntimeError("opened row but title check failed")
    return True

_SYS = re.compile(r"^((凌晨|早上|上午|中午|下午|傍晚|晚上)\s*\d{1,2}:\d{2}|\d{1,2}:\d{2}|昨天.*|前天.*|星期.*|\d{1,2}月\d{1,2}日.*|\d{4}年.*|以上是打招呼的消息|.*撤回了一条消息.*)$")

FP_SHOT = SHOT.with_name("mac_fp.png")

def chat_fingerprint():
    """廉价变化检测：`screencapture -l` 截窗口（不激活、不点击），裁出聊天区+标题栏，缩成 48x48 灰度后取哈希。
    不跑 OCR；每次约几十 ms。窗口不在屏幕上时抛异常（daemon 退避后走完整检查）。"""
    import hashlib
    from PIL import Image
    wid, _, _, w, h = window()
    r = subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), str(FP_SHOT)], capture_output=True, text=True)
    if r.returncode or not FP_SHOT.exists(): raise PermissionError(f"screencapture failed: {r.stderr}")
    im = Image.open(FP_SHOT); W, H = im.size; kx, ky = W / w, H / h
    im = im.crop((int(SIDEBAR_W * kx), 0, W, int((h - INPUT_H) * ky))).convert("L").resize((48, 48))
    return hashlib.blake2b(bytes(v >> 3 for v in im.tobytes()), digest_size=8).hexdigest()  # 低 3 位丢掉，抗抖动

def read_chat():
    """一次截图+一次 OCR：返回 (是否在目标聊天, 可见消息)。不点击。"""
    _, _, w, h = capture(); els = ocr()
    head = norm("".join(e["text"] for e in els if e["x"] > SIDEBAR_W and e["y"] < HEADER_H))
    ok = bool(REMARK and norm(REMARK) in head) or bool(_bear_ys(SIDEBAR_W, SIDEBAR_W + (w - SIDEBAR_W) * 0.5, 5, HEADER_H))
    msgs = read_messages(_els=(els, w, h))
    if CLS_CFG.get("enabled", True):
        try: msgs = classify_merge(msgs, els, w, h, menu_ok=ok)
        except Exception as ex: print(f"[classify] disabled this round: {ex!r}", file=sys.stderr)
    if ok and MAC.get("copy_read", True):
        try: copy_refine(msgs)
        except Exception as ex: print(f"[copy_read] fallback to OCR: {ex!r}", file=sys.stderr)
    for m in msgs: m.pop("_box", None)
    return ok, msgs

# ---------- 消息类型识别（msg_classifier）：先于复制读取；非文字气泡换成类型占位符 ----------
CLS_CFG = CFG.get("msg_classifier", {})
CLS_LAST = {}   # 最近一轮的耗时/结果（调试与 devtest 用）

def classify_merge(msgs, els, w, h, menu_ok=False, shot=None):
    """OCR 气泡列表 + 像素分类 → 合并：
    - 分类为 text 的块：保留 OCR 消息并标 type=text；
    - image/sticker/voice/other：删掉落在块内的 OCR 行（语音时长、图里的字），插入占位消息
      {'from','text':'[图片]'…,'type','media'?,'duration'?,'confidence','signals','_box'}；
    - partial（被边缘裁掉）丢弃；system 由 read_messages 原样保留。"""
    import msg_classifier as mc
    t0 = time.time()
    blobs = mc.classify_screen(shot or SHOT, els, w, h, SIDEBAR_W, HEADER_H, INPUT_H, cfg=CLS_CFG,
                               media_dir=str(SHOT.parent / "media"))
    if menu_ok and CLS_CFG.get("menu_confirm", True):
        low = [b for b in blobs if b["from"] == "them" and b["type"] in ("image", "sticker", "voice", "other")
               and b["confidence"] < CLS_CFG.get("menu_confirm_below", mc.DEFAULTS["menu_confirm_below"])]
        for b in low[-CLS_CFG.get("menu_confirm_cap", 2):]:
            key = ("menu", b.get("image_crop_path") or b["box"])
            labs = _MENU_CACHE.get(key)
            if labs is None:
                try: labs = _MENU_CACHE[key] = menu_labels(b["box"])
                except Exception as ex: print(f"[classify] menu confirm failed: {ex!r}", file=sys.stderr); labs = None
            if labs: mc.apply_menu(b, labs)
    out = [dict(m) for m in msgs]
    for m in out:
        if m["from"] != "system": m.setdefault("type", "text")
    for b in blobs:
        if b["type"] in ("text", "system", "partial"): continue
        inside = lambda m: m.get("_box") and b["box"][0] - 2 <= m["_box"][0] + m["_box"][2] / 2 <= b["box"][0] + b["box"][2] + 2 \
            and b["box"][1] - 2 <= m["_box"][1] + m["_box"][3] / 2 <= b["box"][1] + b["box"][3] + 2
        # 防分类误伤：块内已有 OCR 聊天正文且块高像文字条时，保留文字，不换成[图片]/[表情包]
        hit = [m for m in out if inside(m) and m.get("from") in ("them", "me")]
        ocr_txt = "".join((m.get("text") or "") for m in hit)
        if b["type"] in ("image", "sticker") and b["box"][3] <= 52 and re.search("[\u4e00-\u9fffA-Za-z]", ocr_txt or ""):
            for m in hit:
                m["type"] = "text"
                m.setdefault("signals", {})["merge_keep_text"] = True
            continue
        out = [m for m in out if not inside(m)]
        nm = {"from": b["from"], "text": mc.placeholder(b), "y": int(b["box"][1]), "type": b["type"],
              "confidence": b["confidence"], "signals": b["signals"], "_box": b["box"]}
        for k in ("duration", "subtype"):
            if b.get(k) is not None: nm[k] = b[k]
        if b.get("image_crop_path"): nm["media"] = b["image_crop_path"]
        out.append(nm)
    out.sort(key=lambda m: m["y"])
    CLS_LAST.update(ms=round((time.time() - t0) * 1000), blobs=blobs)
    return out

_MENU_CACHE = {}

def menu_labels(box):
    """只读确认：右键块 → OCR 菜单全部项 → Esc 关闭。不点任何菜单项。须在 ui_lock 内、已核对聊天标题后调用。"""
    import ui_lock as ul
    _, wx, wy, ww, wh = window()
    bx, by, bw, bh = box
    if not (SIDEBAR_W < bx and by > HEADER_H and by + bh < wh - INPUT_H - 5): return None
    with ul.ui_lock("reply"):
        activate_if_needed()
        if not in_chat_with(): return None
        sx, sy = wx + bx + min(bw / 2, 30), wy + by + min(bh / 2, 30)
        _cli(f"rc:{int(sx)},{int(sy)}"); time.sleep(0.3)
        rx, ry, rw, rh = int(sx - 40), max(0, int(sy - 420)), 300, 840
        subprocess.run(["screencapture", "-x", "-R", f"{rx},{ry},{rw},{rh}", str(MENU_SHOT)], check=True)
        _key(53)
    return [e["text"].strip() for e in _vision(MENU_SHOT, rw, rh)]

def read_messages(nick=None, _els=None):
    """Visible messages [{'from':'me'|'them'|'system','text','y'}]. 左侧气泡=对方，右侧=自己，居中=系统/时间。"""
    if _els: allx, w, h = _els
    else: _, _, w, h = capture(); allx = ocr()
    els = [e for e in allx if e["x"] > SIDEBAR_W + 5 and HEADER_H < e["y"] < h - INPUT_H]
    pane_l, pane_r = SIDEBAR_W, w; mid = (pane_l + pane_r) / 2
    lines = []
    for e in els:
        cx = e["x"] + e["w"] / 2
        if _SYS.match(e["text"]) or (abs(cx - mid) < 40 and e["x"] - pane_l > 120 and pane_r - (e["x"] + e["w"]) > 120):
            frm = "system"
        elif cx < mid: frm = "them"
        else: frm = "me"
        lines.append({"from": frm, "text": e["text"], "y": int(e["y"]), "h": e["h"], "x": e["x"], "w": e["w"]})
    # 合并同一气泡内的多行（同发送方、行距小）
    out = []
    for l in lines:
        if out and out[-1]["from"] == l["from"] != "system" and l["y"] - out[-1]["_ye"] < l["h"] * 0.9:
            out[-1]["text"] += l["text"]; out[-1]["_ye"] = l["y"] + l["h"]
        else:
            out.append({**l, "_ye": l["y"] + l["h"]})
    return [{"from": m["from"], "text": m["text"], "y": m["y"], "_box": (m["x"], m["y"], m["w"], m["h"])} for m in out]

# ---------- 复制读取：OCR 只负责定位，正文取自微信「复制」后的剪贴板 ----------
COPY_CAP = MAC.get("copy_cap", 3)          # 每轮最多复制几条（只取最新的对方文字气泡）
COPY_MIN_SIM = MAC.get("copy_min_sim", 0.5) # 剪贴板与 OCR 相似度下限；低于它视为非文字气泡/复制错，退回 OCR
_COPIED = {}   # norm(OCR 文本) -> 复制得到的原文（进程内缓存；已处理过的气泡不再右键）
MENU_SHOT = SHOT.with_name("mac_menu.png")

def _pb():
    from AppKit import NSPasteboard
    return NSPasteboard.generalPasteboard()

def pb_save():
    """保存剪贴板全部条目、全部类型（原始 data）。"""
    items = []
    for it in _pb().pasteboardItems() or []:
        items.append([(str(t), it.dataForType_(t)) for t in it.types() if it.dataForType_(t) is not None])
    return items

def pb_restore(items):
    from AppKit import NSPasteboardItem
    pb = _pb(); pb.clearContents()
    objs = []
    for kv in items:
        it = NSPasteboardItem.alloc().init()
        for t, d in kv: it.setData_forType_(d, t)
        objs.append(it)
    if objs: pb.writeObjects_(objs)

def pb_string():
    return _pb().stringForType_("public.utf8-plain-text")

def pb_count():
    return int(_pb().changeCount())

def _cli(*args):
    r = subprocess.run([CLICLICK, *args], capture_output=True, text=True)
    if r.returncode: raise PermissionError(f"cliclick failed: {r.stderr}")

def _menu_find(sx, sy, label="复制"):
    """截右键位置周围的屏幕区域（菜单是独立弹层，-l 截不到），OCR 找菜单项，返回屏幕坐标。"""
    rx, ry, rw, rh = int(sx - 40), int(sy - 420), 300, 840
    if ry < 0: rh += ry; ry = 0
    subprocess.run(["screencapture", "-x", "-R", f"{rx},{ry},{rw},{rh}", str(MENU_SHOT)], check=True)
    for e in _vision(MENU_SHOT, rw, rh):
        if e["text"].strip() == label:
            return rx + e["x"] + e["w"] / 2, ry + e["y"] + e["h"] / 2
    return None

def _sim(a, b):
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

def copy_bubble(box, ocr_text):
    """右键气泡 → 点菜单「复制」→ 读剪贴板。失败返回 None。调用方负责剪贴板保存/恢复与锁。"""
    _, wx, wy, ww, wh = window()
    bx, by, bw, bh = box
    if not (SIDEBAR_W < bx and by > HEADER_H and by + bh < wh - INPUT_H - 5): return None  # 绝不碰输入/发送区
    sx, sy = wx + bx + min(bw / 2, 20), wy + by + bh / 2
    n0 = pb_count()
    _cli(f"rc:{int(sx)},{int(sy)}"); time.sleep(0.25)
    pt = _menu_find(sx, sy)
    if not pt: _key(53); return None
    _cli(f"c:{int(pt[0])},{int(pt[1])}")
    for _ in range(10):
        time.sleep(0.05)
        if pb_count() != n0: break
    else: return None
    t = pb_string()
    if not t or _sim(t, ocr_text) < COPY_MIN_SIM: return None   # 链接卡片/图片等复制出的不是这段字
    return t

def copy_refine(msgs, cap=None, lock=None):
    """把最新的、未处理过的对方文字气泡（最多 cap 条）替换为复制得到的原文。
    title 已由调用方核对；这里在 UI 锁内再查一次前台窗口标题，保存/恢复剪贴板。"""
    cap = COPY_CAP if cap is None else cap
    todo = []
    for m in reversed(msgs):
        if len(todo) >= cap: break
        if m["from"] != "them" or not m.get("_box") or m.get("type", "text") != "text": continue
        k = norm(m["text"])
        if k in _COPIED: m["text"] = _COPIED[k]; continue
        if len(todo) < cap: todo.append(m)
    for m in msgs:    # 更早的也用缓存
        k = norm(m["text"])
        if m["from"] == "them" and k in _COPIED: m["text"] = _COPIED[k]
    if not todo: return 0
    import ui_lock as ul
    with (lock or (lambda: ul.ui_lock("reply")))():
        activate_if_needed()
        if not in_chat_with(): return 0
        saved = pb_save(); n = 0
        try:
            for m in todo:
                k = norm(m["text"]); t = copy_bubble(m["_box"], m["text"])
                if t: _COPIED[k] = t; m["text"] = t; m["src"] = "copy"; n += 1
        finally:
            pb_restore(saved)
    return n

def activate_if_needed():
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if str(app.localizedName()) not in ("WeChat", "微信", "Weixin"): activate()

def fill_input(text):
    _, _, _, w, h = window()
    click((SIDEBAR_W + w) / 2, h - INPUT_H / 2); time.sleep(0.12)
    _paste(text); time.sleep(0.2)

def clear_input():
    _osa('tell application "System Events" to keystroke "a" using command down'); _key(51)

def send_message(text):
    """键盘事件发给前台应用：已在前台则 activate_if_needed（跳过强制 activate 的睡眠），再复核标题。"""
    activate_if_needed()
    if not in_chat_with():
        activate()  # 标题不对时再强制拉前台
        if not in_chat_with(): raise RuntimeError("send aborted: title check failed after activate")
    fill_input(text)
    _key(36)  # 回车发送
    time.sleep(0.35)  # 等气泡落盘；原 1s 偏长

if __name__ == "__main__":
    open_chat()
    print("title:", title())
    for m in read_messages(): print(m)


def _clipboard_set_file(path):
    """把本地文件放进剪贴板（POSIX file），供微信粘贴成图片/文件卡片。"""
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"send_image: not a file: {path}")
    safe = str(p).replace("\\", "\\\\").replace('"', '\\"')
    _osa(f'set the clipboard to (POSIX file "{safe}")')


def send_image(path):
    """发送本地图片到当前聊天：文件入剪贴板 → ⌘V → 回车。
    不打开聊天、不改 imagegen；调用方负责 open_chat / 白名单。
    会覆盖剪贴板（文件类型无法用 pb_save 完整还原）。"""
    activate_if_needed()
    if not in_chat_with():
        activate()
        if not in_chat_with():
            raise RuntimeError("send_image aborted: title check failed after activate")
    _, _, _, w, h = window()
    click((SIDEBAR_W + w) / 2, h - INPUT_H / 2)
    time.sleep(0.12)
    clear_input()
    _clipboard_set_file(path)
    time.sleep(0.15)
    _osa('tell application "System Events" to keystroke "v" using command down')
    time.sleep(0.8)  # 等图片预览落进输入区
    _key(36)
    time.sleep(0.45)
