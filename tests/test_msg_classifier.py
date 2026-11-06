"""离线：合成深色/浅色微信截图（880x640 点，1x 或 2x）驱动 msg_classifier；不碰微信。"""
import sys, pathlib, random
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import pytest
from PIL import Image, ImageDraw
import msg_classifier as mc

DARK = dict(bg=(30, 30, 31), them=(47, 47, 48), me=(53, 209, 140))
LIGHT = dict(bg=(237, 237, 237), them=(255, 255, 255), me=(149, 236, 105))
# 2026-10 Mac 微信浅色实测：对方灰泡≠纯白，自己薄荷绿≠旧绿
LIGHT_SOFT = dict(bg=(250, 250, 250), them=(238, 238, 240), me=(157, 242, 159))

def shot(items, theme=DARK, k=1):
    """items: (kind, x, y, w, h, extra)。返回 (img, ocr)。"""
    im = Image.new("RGB", (880 * k, 640 * k), theme["bg"]); d = ImageDraw.Draw(im); ocr = []
    d.rectangle([0, 0, 300 * k, 640 * k], fill=(40, 40, 40))
    R = lambda x, y, w, h: [x * k, y * k, (x + w) * k - 1, (y + h) * k - 1]
    for kind, x, y, w, h, ex in items:
        side = "me" if x > 590 else "them"
        av = 818 if side == "me" else 320
        d.rectangle(R(av, y, 36, 36), fill=(180, 140, 120))    # 头像
        if kind in ("text", "voice", "file"):
            d.rounded_rectangle(R(x, y, w, h), 4 * k, fill=theme[side])
            d.rectangle(R(x + 14, y + 11, w - 28, 15), fill=(220, 220, 220) if theme is DARK else (30, 30, 30))
            ocr.append({"text": ex, "x": x + 14, "y": y + 11, "w": w - 28, "h": 15})
        elif kind == "image":
            rnd = random.Random(1)
            for yy in range(0, h, 4):
                for xx in range(0, w, 4):
                    d.rectangle(R(x + xx, y + yy, 4, 4), fill=(rnd.randrange(60, 250), rnd.randrange(60, 250), rnd.randrange(60, 250)))
            if ex: ocr.append({"text": ex, "x": x + w - 40, "y": y + h - 18, "w": 30, "h": 12})
        elif kind == "sticker_t":   # 透明底卡通
            d.ellipse(R(x, y, w, h), fill=(250, 200, 60)); d.ellipse(R(x + w // 3, y + h // 3, 10, 10), fill=(0, 0, 0))
        elif kind == "sticker_o":   # 不透明方块动画表情（实测 ~130pt）
            d.rectangle(R(x, y, w, h), fill=(200, 170, 120)); d.ellipse(R(x + 20, y + 20, w - 40, h - 40), fill=(240, 240, 240))
        elif kind == "time":
            d.rectangle(R(x, y, w, h), fill=(110, 110, 110)); ocr.append({"text": ex, "x": x, "y": y, "w": w, "h": h})
        elif kind == "link":
            d.rounded_rectangle(R(x, y, w, h), 4 * k, fill=theme[side])
            d.rectangle(R(x + 12, y + 10, w - 24, 14), fill=(200, 200, 200)); d.rectangle(R(x + 12, y + 30, 120, 12), fill=(150, 150, 150))
            d.rectangle(R(x + w - 60, y + 30, 48, 48), fill=(230, 60, 40))
            ocr += [{"text": "一篇文章标题", "x": x + 12, "y": y + 10, "w": w - 24, "h": 14}, {"text": "摘要", "x": x + 12, "y": y + 30, "w": 120, "h": 12}]
    return im, ocr

def run(items, **kw):
    im, ocr = shot(items, **kw)
    return [m for m in mc.classify_screen(im, ocr, 880, 640, save=False) if m["type"] != "partial"]

@pytest.mark.parametrize("theme", [DARK, LIGHT, LIGHT_SOFT])
@pytest.mark.parametrize("k", [1, 2])
def test_each_type(theme, k):
    ms = run([("text", 365, 80, 120, 37, "干嘛呢"), ("voice", 365, 140, 90, 37, '6"'),
              ("sticker_o", 370, 200, 132, 132, None), ("image", 370, 350, 160, 120, None)], theme=theme, k=k)
    assert [m["type"] for m in ms] == ["text", "voice", "sticker", "image"]
    assert ms[0]["text"] == "干嘛呢" and ms[1]["duration"] == 6 and ms[0]["from"] == "them"
    assert all("bubble_frac" in m["signals"] for m in ms)

def test_transparent_sticker_and_me_side():
    ms = run([("sticker_t", 370, 100, 120, 120, None), ("text", 600, 260, 200, 37, "我的消息")])
    assert ms[0]["type"] == "sticker" and ms[0]["confidence"] >= 0.8
    assert ms[1]["type"] == "text" and ms[1]["from"] == "me"

def test_voice_with_red_dot_and_zhuanwenzi():
    ms = run([("voice", 365, 100, 200, 37, '••转文字6"')])
    assert ms[0]["type"] == "voice" and ms[0]["duration"] == 6 and ms[0]["confidence"] >= 0.9

def test_system_portrait_image_video_file_link():
    ms = run([("time", 572, 70, 32, 11, "01:55"), ("image", 370, 100, 120, 200, None),
              ("image", 370, 320, 200, 120, "0:12")])
    assert [m["type"] for m in ms] == ["system", "image", "other"] and ms[2]["subtype"] == "video"
    ms = run([("file", 365, 100, 240, 37, "报告.pdf 1.2MB"), ("link", 365, 200, 260, 90, None)])
    assert [m.get("subtype") for m in ms] == ["file", "link"]

def test_partial_dropped_and_crop_saved(tmp_path):
    im, ocr = shot([("image", 370, 40, 160, 40, None), ("sticker_o", 370, 200, 132, 132, None)])
    ms = mc.classify_screen(im, ocr, 880, 640, media_dir=tmp_path)
    assert ms[0]["type"] == "partial"
    p = pathlib.Path(ms[1]["image_crop_path"]); assert p.exists() and p.parent.parent == tmp_path
    assert Image.open(p).size == (132, 132)

def test_menu_confirm_and_placeholders():
    m = {"type": "image", "confidence": 0.6, "signals": {}}
    mc.apply_menu(m, ["复制", "添加到表情", "转发"]); assert m["type"] == "sticker" and m["confidence"] >= 0.9
    v = {"type": "voice", "confidence": 0.5, "signals": {}}
    mc.apply_menu(v, ["转文字", "删除"]); assert v["type"] == "voice"
    assert mc.placeholder({"type": "voice", "duration": 3}) == "[语音 3秒]"
    assert mc.placeholder({"type": "sticker"}) == "[表情包]" and mc.placeholder({"type": "other", "subtype": "link"}) == "[链接卡片]"

def test_merge_into_read_chat(monkeypatch, tmp_path):
    m = pytest.importorskip("mac_wechat_io")
    im, ocr = shot([("text", 365, 80, 120, 37, "干嘛呢"), ("voice", 365, 140, 90, 37, '6"'), ("sticker_o", 370, 200, 132, 132, None)])
    p = tmp_path / "s.png"; im.save(p)
    monkeypatch.setattr(m, "SHOT", p)
    msgs = m.read_messages(_els=(ocr, 880, 640))
    out = m.classify_merge(msgs, ocr, 880, 640, menu_ok=False, shot=p)
    assert [(x["type"], x["text"]) for x in out] == [("text", "干嘛呢"), ("voice", "[语音 6秒]"), ("sticker", "[表情包]")]
    assert pathlib.Path(out[2]["media"]).exists()
    # 复制读取只处理文字
    called = []
    monkeypatch.setattr(m, "copy_bubble", lambda box, t: called.append(t) or t)
    monkeypatch.setattr(m, "activate_if_needed", lambda: None); monkeypatch.setattr(m, "in_chat_with", lambda *a: True)
    monkeypatch.setattr(m, "pb_save", lambda: []); monkeypatch.setattr(m, "pb_restore", lambda i: None)
    m._COPIED.clear(); import contextlib
    m.copy_refine(out, lock=contextlib.nullcontext)
    assert called == ["干嘛呢"]

def test_brain_prompt_has_type_note():
    import brain
    tn = brain.type_note_for([("them", "[表情包]")])
    assert "表情包" in tn
    assert "{TYPE_NOTE}" not in brain.build_prompt.__code__.co_consts
    assert not hasattr(brain, "TYPE_NOTE")

def test_menu_real_image_labels():
    real = ['复制', '区 编辑', '^ 转发….', '6 收藏', '多选', '提醒', '引用', '用窗口打开', '另存为⋯', '打开方式', '删除']
    m = {"type": "sticker", "confidence": 0.6, "signals": {}}
    mc.apply_menu(m, real); assert m["type"] == "image"
    m = {"type": "image", "confidence": 0.6, "signals": {}}
    mc.apply_menu(m, ["复制", "添加到表情⋯", "另存为⋯"]); assert m["type"] == "sticker"


def test_live_soft_palette_text_not_image():
    """回归：浅色灰泡+薄荷绿下「干嘛呢」必须是 text，不能再被打成 image。"""
    ms = run([("text", 365, 80, 85, 37, "干嘛呢"), ("text", 365, 140, 141, 37, "给我发个照片看看"),
              ("text", 600, 200, 200, 37, "崽崽你倒是打字啊")], theme=LIGHT_SOFT, k=2)
    assert [m["type"] for m in ms] == ["text", "text", "text"]
    assert ms[0]["text"] == "干嘛呢" and ms[0]["from"] == "them"
    assert ms[2]["from"] == "me"


def test_ocr_fallback_when_palette_unknown():
    """气泡色完全不在调色板时，OCR 正文+矮宽条仍应判 text（防再漂移）。"""
    weird = dict(bg=(250, 250, 250), them=(200, 180, 160), me=(100, 180, 220))  # 故意不在 PALETTE
    ms = run([("text", 365, 80, 85, 37, "你干嘛呢")], theme=weird, k=1)
    assert ms[0]["type"] == "text" and ms[0]["text"] == "你干嘛呢"
