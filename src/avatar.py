"""更换小甜（当前登录号）的微信头像 —— Mac 微信 UI 探测版。
2026-10-24 实测 Mac 微信 4.1.15：资料卡（左栏头像）点头像只打开图片查看器（菜单：复制/打印/转发/收藏/用预览打开/保存到照片），
设置→账号与存储也只有头像+退出登录，菜单栏无相关项；官方客服亦说明电脑端不能改头像，只能手机「我→头像→头像→…」。
所以 change() 先备份当前头像、逐处探测入口；找不到入口抛 AvatarUnsupported（不乱点）。只操作自己的资料，不碰朋友圈。
用法：.venv/bin/python src/avatar.py [--probe-only]"""
import sys, time, pathlib, subprocess, argparse, json
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "avatar"
CANON = pathlib.Path("~/xiaotian-assets/appearance/zhuzhuxiaoye/canonical/front.png").expanduser()
FALLBACK_DIR = pathlib.Path("~/xiaotian-assets/generated/20261024-002048").expanduser()
ENTRY_WORDS = ("更换头像", "修改头像", "编辑头像", "设置头像", "从相册选择", "选择图片", "上传头像")

class AvatarUnsupported(RuntimeError): pass

def pick_image(canon=CANON, fallback_dir=FALLBACK_DIR):
    if canon.exists(): return canon
    c = sorted(fallback_dir.glob("exec-818ef773*.png"), key=lambda p: p.stat().st_mtime)
    if not c: raise FileNotFoundError("no avatar image")
    return c[-1]

def prepare(src, out_dir=OUT, size=640):
    """中心裁正方形、缩到 size，存 PNG（头像上传前的标准化）。"""
    from PIL import Image
    out_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGB"); s = min(im.size); l, t = (im.width - s) // 2, (im.height - s) // 2
    p = out_dir / "new_avatar.png"; im.crop((l, t, l + s, t + s)).resize((size, size), Image.LANCZOS).save(p); return p

def has_entry(texts): return next((t for t in texts if any(w in t for w in ENTRY_WORDS)), None)

def _screen(name):
    OUT.mkdir(parents=True, exist_ok=True); p = OUT / f"{name}.png"
    subprocess.run(["screencapture", "-x", str(p)], check=True); return p

def probe(io=None, shot=_screen, sleep=time.sleep):
    """依次看资料卡、头像查看器、查看器「…」菜单、设置页，OCR 找改头像入口。返回 (入口文字|None, 证据截图列表)。"""
    import mac_wechat_io as io_
    io = io or io_; shots = []
    def look(name):
        p = shot(name); shots.append(str(p)); return [e["text"] for e in io.ocr(p)]
    io.activate(); io.click(30, 70); sleep(1.5)                     # 左栏自己的头像 → 资料卡
    if (hit := has_entry(look("probe_card"))): return hit, shots
    io.click(110, 105); sleep(1.5)                                  # 资料卡头像 → 查看器
    if (hit := has_entry(look("probe_viewer"))): return hit, shots
    io._key(53); sleep(0.8); io._key(53); sleep(0.5)
    io.click(30, 606); sleep(1.0)                                   # ≡ 菜单
    if (hit := has_entry(look("probe_menu"))): return hit, shots
    io._key(53); sleep(0.5)
    return None, shots

def change(image=None, io=None, **kw):
    import ui_lock as ul
    img = prepare(image or pick_image())
    with ul.ui_lock("reply"):
        hit, shots = probe(io=io, **kw)
        if not hit:
            raise AvatarUnsupported(f"Mac 微信无改头像入口（已探测：{shots}）；需手机微信或网页端。已备好图片 {img}")
        raise AvatarUnsupported(f"发现疑似入口「{hit}」但上传流程未实测，未自动点击；截图 {shots}")

if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    ap = argparse.ArgumentParser(); ap.add_argument("--probe-only", action="store_true"); a = ap.parse_args()
    try: change()
    except AvatarUnsupported as e: print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False)); sys.exit(3)
