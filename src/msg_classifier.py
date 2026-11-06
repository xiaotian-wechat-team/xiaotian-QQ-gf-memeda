"""本地确定性消息类型识别（不调 LLM）：Mac 微信截图 + Vision OCR 框 → 每个气泡 {type, text?, image_crop_path?, duration?, confidence, signals}。
type ∈ text / image / sticker / voice / other（链接卡片、文件、视频）/ system（时间戳等，不交给回复）。

信号（全部来自像素几何 + OCR 框，单位为窗口点）：
- 背景色 = 聊天区众数色（深色 ≈(30,30,31)，浅色 ≈(237,237,237)）；气泡色 = 调色板（对方灰/白，自己绿）。
- 分割：头像列（pane 两侧 ~62pt）之外的非背景像素 → 行带（空行 ≥ gap_pt 分开）→ 行带内按横向空隙分簇 → bbox。
- 气泡占比高 → 文字/语音/文件/链接：OCR 为 `3"` 形式 = 语音；含 KB/MB = 文件；大且有彩色缩略图块 = 链接卡片；否则文字。
- 无气泡但 OCR 已有聊天正文且外形矮宽（h≤52）→ 仍判文字（防浅色主题气泡色漂移把「干嘛呢」打成[图片]）。
- 无气泡：只有 OCR 小字 = system；有内容：
  边缘填充率低（透明底）= 表情包；方形且边长 ≤ sticker_max_pt = 表情包（动画表情多为 ~130pt 方块，实测）；
  更大或非方形 = 图片；右下 OCR `0:12` = 视频（other）。
- 置信度低（< menu_confirm_below）时可用右键菜单项确认：转文字→语音，添加到表情→表情包，另存为/打开→图片/文件，仅复制→文字。
纯函数可在测试里用合成图驱动；UI 操作（右键菜单）在 mac_wechat_io 里、ui_lock 内完成。"""
import re, hashlib, pathlib, datetime
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULTS = {
    "enabled": True,
    "avatar_margin_pt": 62,      # pane 左右头像列（实测头像 x∈[320,355] / [818,853]）
    "gap_pt": 5,                 # 行带间最小空行
    "col_gap_pt": 30,
    "bg_tol": 18,                # 与背景的 L1 距离阈值
    "pal_tol": 10,
    "bubble_frac": 0.35,
    "sticker_max_pt": 145,
    "image_min_pt": 60,
    "menu_confirm_below": 0.7,
    "media_dir": "data/media",
}
# them: 深色灰泡 + 浅色白泡 + 2026-10 浅色灰泡(实测 ~238,238,240，非纯白)
PALETTE_THEM = [(47, 47, 48), (255, 255, 255), (44, 44, 44), (238, 238, 240), (236, 236, 240)]
# me: 旧绿 + 2026-10 薄荷绿(实测 median ~157,242,159 / 156,240,156)
PALETTE_ME = [(53, 209, 140), (149, 236, 105), (169, 234, 122), (89, 178, 105),
              (157, 242, 159), (156, 240, 156), (152, 240, 152)]
VOICE_RE = re.compile(r"^\s*(\d{1,2})\s*[\"″”'’`]{1,2}\s*$")
VOICE_LOOSE = re.compile(r"(\d{1,2})\s*[\"″”'’`]{1,2}")
FILE_RE = re.compile(r"\d+(\.\d+)?\s*[KMG]B\b", re.I)
VIDEO_RE = re.compile(r"^\d{1,2}:\d{2}$")
PLACEHOLDER = {"image": "[图片]", "sticker": "[表情包]", "other": "[其他消息]"}
TYPE_ZH = {"partial": "残片", "text": "文字", "image": "图片", "sticker": "表情包", "voice": "语音", "other": "其他", "system": "系统"}

def placeholder(m):
    t = m["type"]
    if t == "voice": return f"[语音 {m['duration']}秒]" if m.get("duration") else "[语音]"
    if t == "other": return {"file": "[文件]", "link": "[链接卡片]", "video": "[视频]"}.get(m.get("subtype"), "[其他消息]")
    return PLACEHOLDER.get(t, m.get("text", ""))

def _arr(img):
    from PIL import Image
    im = img if hasattr(img, "convert") else Image.open(img)
    return np.asarray(im.convert("RGB")).astype(np.int16)

def _bg(a):
    flat = a.reshape(-1, 3)[:: 7]
    vals, cnt = np.unique(flat, axis=0, return_counts=True)
    return tuple(int(v) for v in vals[cnt.argmax()])

def _near(a, cols, tol):
    m = np.zeros(a.shape[:2], bool)
    for c in cols: m |= np.abs(a - np.array(c, np.int16)).sum(-1) <= tol
    return m

def _runs(v, mingap):
    out, s, last = [], None, None
    for i in np.flatnonzero(v):
        if s is None: s = last = i
        elif i - last > mingap: out.append((s, last)); s = last = i
        else: last = i
    if s is not None: out.append((s, last))
    return out

def segment(a, k, pane, cfg):
    """a: 整窗 RGB 数组；k: 像素/点；pane=(x0,y0,x1,y1) 点。返回 [(x,y,w,h) 点]。"""
    x0, y0, x1, y1 = pane; mg = cfg["avatar_margin_pt"]
    px0, px1, py0, py1 = int((x0 + mg) * k), int((x1 - mg) * k), int(y0 * k), int(y1 * k)
    sub = a[py0:py1, px0:px1]
    bg = _bg(sub)
    mask = np.abs(sub - np.array(bg, np.int16)).sum(-1) > cfg["bg_tol"]
    boxes = []
    for r0, r1 in _runs(mask.sum(1) >= 2, cfg["gap_pt"] * k):
        band = mask[r0:r1 + 1]
        for c0, c1 in _runs(band.sum(0) >= 1, cfg["col_gap_pt"] * k):
            rows = np.flatnonzero(band[:, c0:c1 + 1].any(1))
            if len(rows) == 0: continue
            ya, yb = r0 + rows[0], r0 + rows[-1]
            if (yb - ya) < 4 * k and (c1 - c0) < 4 * k: continue   # 噪点
            boxes.append(((px0 + c0) / k, (py0 + ya) / k, (c1 - c0 + 1) / k, (yb - ya + 1) / k))
    return boxes, bg

def _inside(e, b):
    cx, cy = e["x"] + e["w"] / 2, e["y"] + e["h"] / 2
    return b[0] - 2 <= cx <= b[0] + b[2] + 2 and b[1] - 2 <= cy <= b[1] + b[3] + 2

def classify_blob(a, k, box, bg, ocr_in, mid, pane, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    x, y, w, h = box
    crop = a[int(y * k):int((y + h) * k), int(x * k):int((x + w) * k)]
    side = "them" if x + w / 2 < mid else "me"
    pal = PALETTE_THEM if side == "them" else PALETTE_ME
    bubble = float(_near(crop, pal, cfg["pal_tol"]).mean()) if crop.size else 0.0
    nonbg = np.abs(crop - np.array(bg, np.int16)).sum(-1) > cfg["bg_tol"]
    e = max(1, int(min(crop.shape[:2]) * 0.12))   # 边缘采样避开圆角
    edges = np.concatenate([nonbg[0, e:-e], nonbg[-1, e:-e], nonbg[e:-e, 0], nonbg[e:-e, -1]]) if min(crop.shape[:2]) > 2 * e + 2 else nonbg.ravel()
    edge_fill = float(edges.mean()) if edges.size else 0.0
    text = "".join(o["text"] for o in sorted(ocr_in, key=lambda o: (o["y"], o["x"])))
    txt_area = sum(o["w"] * o["h"] for o in ocr_in) / max(1.0, w * h)
    sig = {"side": side, "w": round(w), "h": round(h), "bubble_frac": round(bubble, 2), "edge_fill": round(edge_fill, 2),
           "ocr": text[:40], "ocr_area": round(float(txt_area), 2), "partial": bool(y <= pane[1] + 1 or y + h >= pane[3] - 1)}
    if sig["partial"] and h < 40:   # 被聊天区上下边缘裁掉的残片：不判，等完整出现
        return {"from": side, "box": tuple(round(float(v), 1) for v in box), "signals": sig, "type": "partial", "confidence": 0.0, "text": None}
    out = {"from": side, "box": tuple(round(float(v), 1) for v in box), "signals": sig}
    if bubble >= cfg["bubble_frac"]:
        bare = text.replace("转文字", "").strip("•·. ")
        mv = VOICE_RE.match(bare) or (len(bare) <= 6 and VOICE_LOOSE.search(bare))
        if mv:   # 实测深色模式：波形 + `6"`，右侧红点(未听) + 「转文字」按钮落在同一簇
            sig["voice_hint"] = "转文字" in text
            return {**out, "type": "voice", "duration": int(mv.group(1)), "confidence": 0.95 if "转文字" in text else 0.9, "text": None}
        if FILE_RE.search(text):
            return {**out, "type": "other", "subtype": "file", "confidence": 0.8, "text": text}
        hsv_col = _colorful(crop) if crop.size else 0
        sig["colorful"] = round(hsv_col, 2)
        if w >= 200 and h >= 60 and hsv_col > 0.08 and len(ocr_in) >= 2:
            return {**out, "type": "other", "subtype": "link", "confidence": 0.65, "text": text}
        if not text:
            # 空泡：语音(OCR漏字) vs 贴纸浅色内芯误匹配新灰泡色。方形贴纸尺寸优先表情包。
            square = 0.75 <= w / max(h, 1) <= 1.33
            big = max(w, h)
            if square and cfg["image_min_pt"] <= big <= cfg["sticker_max_pt"] * 1.15:
                return {**out, "type": "sticker", "confidence": 0.7, "text": None}
            if big > cfg["sticker_max_pt"] and (not square or big > cfg["sticker_max_pt"] * 1.2):
                return {**out, "type": "image", "confidence": 0.55, "text": None}
            return {**out, "type": "voice", "duration": None, "confidence": 0.5, "text": None}
        return {**out, "type": "text", "text": text, "confidence": 0.95 if bubble > 0.5 else 0.8}
    # 无气泡
    if text and (h <= 26 or txt_area > 0.45) and max(w, h) < cfg["image_min_pt"] * 2:
        return {**out, "from": "system", "type": "system", "text": text, "confidence": 0.8}
    # 气泡色漂移兜底：OCR 已读出聊天正文、外形是矮宽条 → 当文字，绝不能落成 image/sticker。
    # 真实照片/表情包很少整段中文/字母落在 h≤52 的横条里（语音时长已在有泡路径处理）。
    if text and h <= 52 and w >= 28:
        bare = text.replace("转文字", "").strip("•·. ")
        mv = VOICE_RE.match(bare) or (len(bare) <= 6 and VOICE_LOOSE.search(bare))
        if mv and not re.search("[\u4e00-\u9fffA-Za-z]", bare):
            return {**out, "type": "voice", "duration": int(mv.group(1)), "confidence": 0.7, "text": None}
        if FILE_RE.search(text):
            return {**out, "type": "other", "subtype": "file", "confidence": 0.75, "text": text}
        if re.search("[\u4e00-\u9fffA-Za-z]", bare) or (bare and not VOICE_LOOSE.search(bare)):
            return {**out, "type": "text", "text": text, "confidence": 0.78, "text_fallback": True}
    if max(w, h) < 16:
        return {**out, "type": "other", "confidence": 0.3, "text": text or None}
    vid = [o for o in ocr_in if VIDEO_RE.match(o["text"].strip()) and o["y"] > y + h * 0.6]
    if vid and edge_fill > 0.7:
        return {**out, "type": "other", "subtype": "video", "confidence": 0.75, "text": None}
    square = 0.75 <= w / max(h, 1) <= 1.33 or (sig["partial"] and w <= cfg["sticker_max_pt"])
    big = max(w, h)
    if edge_fill < 0.6:
        conf = 0.85 if big <= cfg["sticker_max_pt"] * 1.4 else 0.6
        return {**out, "type": "sticker", "confidence": conf, "text": None}
    if big <= cfg["sticker_max_pt"] and square:
        return {**out, "type": "sticker", "confidence": 0.72, "text": None}
    if big > cfg["sticker_max_pt"] or not square:
        return {**out, "type": "image", "confidence": 0.85 if big > cfg["sticker_max_pt"] * 1.2 else 0.65, "text": None}
    return {**out, "type": "image", "confidence": 0.5, "text": None}

def _colorful(crop):
    c = crop.astype(np.float32)
    mx, mn = c.max(-1), c.min(-1)
    sat = (mx - mn) / np.maximum(mx, 1)
    green = _near(crop, PALETTE_ME, 14)
    return float(((sat > 0.3) & (mx > 60) & ~green).mean())

def apply_menu(m, labels):
    """右键菜单项作确认信号（OCR 项带「⋯」等尾巴，按包含匹配）。真机图片菜单：复制/编辑/转发/收藏/多选/提醒/引用/用窗口打开/另存为⋯/打开方式/删除。"""
    labs = [re.sub(r"[\s.…⋯]+$", "", str(l).strip()) for l in labels or []]
    has = lambda k: any(k in l for l in labs)
    m["signals"]["menu"] = labs[:20]
    new = None
    if has("转文字"): new = "voice"
    elif has("添加到表情") or has("添加表情"): new = "sticker"
    elif has("另存为") and m["type"] in ("image", "sticker", "other"): new = "image" if m["type"] != "other" else "other"
    elif m["type"] == "voice" and has("复制") and m.get("text"): new = "text"
    if new:
        if new != m["type"]: m["signals"]["menu_override"] = f"{m['type']}->{new}"
        m["type"] = new; m["confidence"] = max(m["confidence"], 0.9)
    return m

def save_crop(img_arr, k, m, media_dir=None):
    from PIL import Image
    x, y, w, h = m["box"]
    crop = img_arr[int(y * k):int((y + h) * k), int(x * k):int((x + w) * k)].astype(np.uint8)
    hsh = hashlib.blake2b(crop.tobytes(), digest_size=6).hexdigest()
    d = pathlib.Path(media_dir or ROOT / DEFAULTS["media_dir"]) / datetime.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{m['type']}_{hsh}.png"
    if not p.exists(): Image.fromarray(crop).save(p)
    return str(p)

def classify_screen(img, ocr_els, ww, wh, sidebar=300, header=60, input_h=150, cfg=None, save=True, media_dir=None):
    """整窗截图 + 整窗 OCR → 按 y 排序的消息列表（含 system）。"""
    cfg = {**DEFAULTS, **(cfg or {})}
    a = _arr(img); k = a.shape[1] / ww
    pane = (sidebar, header, ww, wh - input_h)
    mid = (sidebar + ww) / 2
    boxes, bg = segment(a, k, pane, cfg)
    els = [e for e in ocr_els if e["x"] > sidebar + 5 and header < e["y"] < wh - input_h]
    out = []
    for b in boxes:
        m = classify_blob(a, k, b, bg, [e for e in els if _inside(e, b)], mid, pane, cfg)
        if save and m["type"] in ("image", "sticker") or (save and m.get("subtype") == "video"):
            m["image_crop_path"] = save_crop(a, k, m, media_dir)
        out.append(m)
    return sorted(out, key=lambda m: m["box"][1])

def annotate(img, msgs, ww, path):
    from PIL import Image, ImageDraw, ImageFont
    im = (img if hasattr(img, "convert") else Image.open(img)).convert("RGB")
    k = im.width / ww; dr = ImageDraw.Draw(im)
    try: font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", int(13 * k))
    except Exception: font = ImageFont.load_default()
    col = {"text": "#3fa9f5", "image": "#ff9800", "sticker": "#e040fb", "voice": "#00e676", "other": "#ffeb3b", "system": "#9e9e9e", "partial": "#555555"}
    for m in msgs:
        x, y, w, h = [v * k for v in m["box"]]; c = col.get(m["type"], "red")
        dr.rectangle([x, y, x + w, y + h], outline=c, width=max(2, int(2 * k)))
        lab = f"{m['type']}{' ' + str(m['duration']) + 's' if m.get('duration') else ''} {m['confidence']:.2f}"
        dr.rectangle([x, max(0, y - 16 * k), x + len(lab) * 7 * k, y], fill=c)
        dr.text((x + 2, max(0, y - 16 * k)), lab, fill="black", font=font)
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True); im.save(path)
    return path
