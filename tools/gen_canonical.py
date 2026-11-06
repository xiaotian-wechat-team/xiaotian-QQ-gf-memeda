"""生成标准肖像五视图（canonical）：用 frontal/ 前 4 张正脸作参考，走本机 Codex image_gen（Magpie）生成横排 5 格五视图，
每个候选裁出 5 张单视图；用 Apple Vision 五官比例 + FeaturePrint 和正脸参考比相似度，选最好的一组写入 canonical/。
输出：<profile>/canonical/{sheet,front,l34,r34,lprofile,rprofile}.png + canonical.json；候选保留在 canonical/candidates/<k>/。
用法：.venv/bin/python tools/gen_canonical.py [-k 2] [--refs 4]"""
import argparse, datetime, json, pathlib, shutil, subprocess, sys, time
import numpy as np
from PIL import Image
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
VIEWS = ["lprofile", "l34", "front", "r34", "rprofile"]   # 画面从左到右
PROMPT = """用内置 image_gen 工具生成一张图片（不要用代码/PIL 画，不要改动任何文件）。
附带的参考图都是同一个年轻女生（小甜）的真实照片。请画她本人的「标准肖像五视图」角色设定参考表（character turnaround sheet）：
一张横向宽幅图片，从左到右等宽排列 5 个头肩肖像格子，顺序固定：1 左侧脸（脸朝画面左，正侧面 90°），2 左 3/4 侧脸（朝画面左约 45°），
3 正脸（直视镜头），4 右 3/4 侧脸（朝画面右约 45°），5 右侧脸（脸朝画面右，正侧面 90°）。
要求：5 格是同一个人、同一时刻、同一发型（和参考图一致的黑色长直发）、同样的素色浅灰背景、均匀柔和的影棚布光、同一件简洁素色圆领上衣；
表情自然中性、闭嘴；每格头部大小和高度一致、头顶留少量空白、只到肩部；
脸型关键：下颌与面颊要偏窄、线条利落（参考图里标为 narrow_ref 的照片是正确脸宽——颧骨以下收窄，勿画成圆脸/宽下巴）；
眼睛、眉毛、鼻子、嘴唇、肤色、黑色长直发严格保持她本人；不要美颜变形、不要换成别人；
真实摄影质感，不是插画；格子之间不要文字、不要编号、不要边框。生成一张后停止。 $imagegen"""

def vision_face(path):
    import frontal_extract as fe
    fs, _ = fe.faces(pathlib.Path(path))
    return max(fs, key=lambda f: f["bbox"][2] * f["bbox"][3]) if fs else None

def proportions(path):
    """正脸五官比例：用 Vision 关键点（眼距/脸宽、鼻长/脸高、嘴宽/眼距、眼到嘴/脸高）。"""
    import Vision, frontal_extract as fe
    cg = fe._cgimage(path); r = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {}).performRequests_error_([r], None)
    obs = list(r.results() or [])
    if not obs: return None
    o = max(obs, key=lambda o: o.boundingBox().size.width); L = o.landmarks(); P = fe._pts
    le, re, nose, lips, cont = P(L.leftEye()), P(L.rightEye()), P(L.nose()), P(L.outerLips()), P(L.faceContour())
    lc, rc = le.mean(0), re.mean(0); ed = np.linalg.norm(rc - lc); eyc = (lc + rc) / 2
    fw = np.ptp(cont[:, 0]); fh = eyc[1] - cont[:, 1].min()
    return np.array([ed / fw, (eyc[1] - nose[:, 1].min()) / fh, np.ptp(lips[:, 0]) / ed, (eyc[1] - lips.mean(0)[1]) / fh, np.ptp(le[:, 0]) / ed])

def featureprint(path):
    import Vision, frontal_extract as fe
    r = Vision.VNGenerateImageFeaturePrintRequest.alloc().init()
    Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(fe._cgimage(path), {}).performRequests_error_([r], None)
    return r.results()[0]

def fp_dist(a, b):
    va, vb = (np.frombuffer(bytes(x.data()), dtype=np.float32) for x in (a, b))   # pyobjc 拿不到 float* 出参，手算 L2
    return float(np.linalg.norm(va - vb))

def split(sheet, dst):
    im = Image.open(sheet).convert("RGB"); W, H = im.size; w = W / 5; out = {}
    for i, v in enumerate(VIEWS):
        p = dst / f"{v}.png"; im.crop((int(i * w), 0, int((i + 1) * w), H)).save(p); out[v] = p
    return out

def evaluate(views, refs):
    rp = [x for x in (proportions(r) for r in refs) if x is not None]; ref_mean = np.mean(rp, 0)
    fp = proportions(views["front"]); faces = {v: vision_face(p) for v, p in views.items()}
    prop = float(np.abs(fp - ref_mean).sum() / ref_mean.sum()) if fp is not None else 9.0
    fpd = float(np.mean([fp_dist(featureprint(views["front"]), featureprint(r)) for r in refs]))
    order_ok = sum(1 for v, f in faces.items() if f is not None)
    # 越小越好：五官比例相对误差 + 0.3*FeaturePrint 距离 + 每缺一张检测到的脸罚 0.2
    return {"prop_err": round(prop, 4), "featureprint_dist": round(fpd, 4), "faces_detected": order_ok,
            "geo": {v: (round(f["geo"], 3) if f else None) for v, f in faces.items()},
            "total": round(prop + 0.3 * fpd + 0.2 * (5 - order_ok), 4)}

def main():
    from config import load
    import imagegen
    ap = argparse.ArgumentParser(); ap.add_argument("-k", type=int, default=2); ap.add_argument("--refs", type=int, default=4)
    ap.add_argument("--reuse", action="store_true", help="不重新生成，只对已有候选重新评分"); a = ap.parse_args()
    cfg = load(); root = pathlib.Path(cfg["appearance"]["assets_dir"]) / cfg["appearance"]["profile"]
    fidx = json.loads((root / "frontal" / "frontal_index.json").read_text())
    refs, shoots = [], set()   # 前 N 张，尽量不同拍摄
    for it in fidx["items"]:
        if it["shoot"] in shoots and len(refs) < a.refs - 1 and any(i["shoot"] not in shoots for i in fidx["items"]): continue
        refs.append(root / it["file"]); shoots.add(it["shoot"])
        if len(refs) >= a.refs: break
    # User narrow-jaw refs first (face width guide); keep a couple frontal for identity
    narrow_dir = root / "canonical" / "face_refs"
    narrow = [p for p in sorted(narrow_dir.glob("narrow_ref_*.jpg")) if p.exists()]
    if narrow:
        refs = narrow + [r for r in refs if r not in narrow][: max(0, a.refs - len(narrow))]
    can = root / "canonical"; cands = can / "candidates"; cands.mkdir(parents=True, exist_ok=True)
    ig = cfg.get("imagegen", {}); codex = ig.get("cmd", "codex"); results = []
    for k in range(1, a.k + 1):
        d = cands / str(k); d.mkdir(exist_ok=True)
        if not (a.reuse and (d / "sheet.png").exists()):
            t0 = time.time() - 1; cmd = [codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(d)]
            for r in refs: cmd += ["-i", str(r)]
            r = subprocess.run(cmd, input=PROMPT, capture_output=True, text=True, timeout=900)
            (d / "codex.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
            imgs = imagegen._new_images(t0) or imagegen._from_rollouts(t0, d)
            if not imgs: print(f"候选 {k} 无产出（blocked={imagegen.blocked(r.stdout + r.stderr)}），见 {d/'codex.log'}", file=sys.stderr); continue
            shutil.copy2(imgs[-1], d / "sheet.png")
        views = split(d / "sheet.png", d); ev = evaluate(views, refs); results.append((ev["total"], k, ev)); print(k, ev)
    if not results: sys.exit("没有候选生成成功")
    best = min(results)[1]; bd = cands / str(best)
    for n in ["sheet"] + VIEWS: shutil.copy2(bd / f"{n}.png", can / f"{n}.png")
    (can / "canonical.json").write_text(json.dumps({
        "profile": cfg["appearance"]["profile"], "date": datetime.date.today().isoformat(), "generator": "codex exec image_gen (本机 Magpie)",
        "source_refs": [str(r.relative_to(root)) for r in refs],
        "source_refs_raw": [next((i["rel"] for i in fidx["items"] if root / i["file"] == r), str(r.relative_to(root))) for r in refs],
        "narrow_face_refs": [str(r.relative_to(root)) for r in refs if "face_refs" in str(r)],
        "chosen_candidate": best,
        "candidates": {str(k): ev for _, k, ev in sorted(results, key=lambda x: x[1])},
        "views": {v: f"{v}.png" for v in VIEWS}, "sheet": "sheet.png", "layout": "横排 5 格，从左到右 " + " / ".join(VIEWS),
        "note": "canonical = 身份标准（每次生图的第一参考）；评分越小越好 = 五官比例误差 + 0.3*FeaturePrint 距离 + 漏检罚分"},
        ensure_ascii=False, indent=1))
    print("best", best, can)

if __name__ == "__main__": main()
