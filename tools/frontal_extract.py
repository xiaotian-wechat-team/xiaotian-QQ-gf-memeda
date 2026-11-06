"""从 <profile>/raw/ 全部照片里用 Apple Vision（pyobjc）挑正脸，输出 <profile>/frontal/（前 N 张头肩裁图 + frontal_index.json），
并把其余人脸按 yaw 分桶写 <profile>/angles.json（front / l34 / r34 / lprofile / rprofile）。
打分（每张图取最大的那张脸）：
- 正脸：|yaw|、|roll|、|pitch|（弧度）越小越好；|yaw|>0.2 或 |roll|>0.3 不进正脸候选
- 清晰：脸框灰度 Laplacian 方差；光照：脸部均值亮度接近 0.5 且不过曝/欠曝；
- 睁眼：眼睛关键点 高/宽 比；无遮挡：VNFaceObservation 的 faceCaptureQuality + 关键点置信度；脸大：脸框面积占比
用法：.venv/bin/python tools/frontal_extract.py [--profile zhuzhuxiaoye] [-n 20]"""
import argparse, json, math, pathlib, sys
import numpy as np
from PIL import Image, ImageOps
import Vision, Quartz
from Foundation import NSURL
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

def _cgimage(path):
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, {Quartz.kCGImageSourceShouldAllowFloat: False}) if src else None

def _pts(region):
    if region is None: return None
    n = region.pointCount(); p = region.normalizedPoints()
    return np.array([[p[i].x, p[i].y] for i in range(n)])

def eye_open(pts):
    if pts is None or len(pts) < 4: return 0.0
    w = np.ptp(pts[:, 0]); h = np.ptp(pts[:, 1])
    return float(h / w) if w > 0 else 0.0

def geo_yaw(L):
    """连续的偏转估计：鼻尖相对两眼中点的水平偏移 / 眼距（Vision 的 yaw 是 45° 量化的）。约 0=正脸，±0.5≈3/4，>±0.9 侧脸。"""
    if not L: return 0.0
    le, re, nose = _pts(L.leftEye()), _pts(L.rightEye()), _pts(L.noseCrest()) if L.noseCrest() else _pts(L.nose())
    if le is None or re is None or nose is None or not len(le) or not len(re) or not len(nose): return 0.0
    lc, rc = le.mean(0), re.mean(0); d = abs(rc[0] - lc[0])
    return float((nose[-1][0] - (lc[0] + rc[0]) / 2) / d) if d > 1e-4 else 0.0

def _readable(path):
    if path.suffix.lower() != ".heic": return path
    import subprocess, tempfile
    dst = pathlib.Path(tempfile.gettempdir()) / "frontal_heic" / (path.stem + ".jpg"); dst.parent.mkdir(exist_ok=True)
    if not dst.exists(): subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(dst)], capture_output=True, check=True)
    return dst

def faces(path):
    cg = _cgimage(path)
    if cg is None: return [], None
    W, H = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    lm = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    q = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
    ok, err = h.performRequests_error_([lm], None)
    obs = list(lm.results() or [])
    if obs:
        q.setInputFaceObservations_(obs); h.performRequests_error_([q], None)
        obs = list(q.results() or obs)
    out = []
    for o in obs:
        bb = o.boundingBox(); L = o.landmarks()
        f = lambda x: (float(x) if x is not None else 0.0)
        out.append({"bbox": [bb.origin.x, 1 - bb.origin.y - bb.size.height, bb.size.width, bb.size.height],  # 左上原点
                    "yaw": f(o.yaw()), "roll": f(o.roll()), "pitch": f(o.pitch()) if hasattr(o, "pitch") else 0.0,
                    "capq": f(o.faceCaptureQuality()), "lmconf": float(L.confidence()) if L else 0.0,
                    "geo": geo_yaw(L),
                    "eyeL": eye_open(_pts(L.leftEye()) if L else None), "eyeR": eye_open(_pts(L.rightEye()) if L else None)})
    return out, (W, H)

def pix_stats(path, bbox):
    im = ImageOps.exif_transpose(Image.open(path)).convert("L"); W, H = im.size
    x, y, w, h = bbox; c = im.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))).resize((256, 256))
    a = np.asarray(c, dtype=np.float32) / 255
    lap = a[1:-1, 1:-1] * 4 - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    return float(lap.var()), float(a.mean()), float((a > 0.97).mean() + (a < 0.03).mean())

def score(fc, sharp, mean, clip):
    frontal = max(0, 1 - abs(fc["geo"]) / 0.3) * max(0, 1 - abs(fc["yaw"]) / 0.8) * max(0, 1 - abs(fc["roll"]) / 0.5) * max(0, 1 - abs(fc["pitch"]) / 0.6)
    sharpn = min(1, sharp / 0.004); light = max(0, 1 - abs(mean - 0.5) / 0.4) * max(0, 1 - clip * 3)
    eyes = min(1, min(fc["eyeL"], fc["eyeR"]) / 0.28); occl = 0.5 * fc["capq"] + 0.5 * fc["lmconf"]
    size = min(1, fc["bbox"][2] * fc["bbox"][3] / 0.06)
    total = 0.35 * frontal + 0.15 * sharpn + 0.1 * light + 0.15 * eyes + 0.15 * occl + 0.1 * size
    return round(total, 4), dict(frontal=round(frontal, 3), sharp=round(sharpn, 3), light=round(light, 3), eyes=round(eyes, 3), unoccluded=round(occl, 3), size=round(size, 3))

def bucket(fc):
    """按 geo（连续）+ Vision yaw（量化）分桶。l/r = 鼻尖偏向画面左/右。"""
    g, y = fc["geo"], fc["yaw"]
    if abs(y) >= 1.2 or abs(g) > 0.9: side = "l" if (g or -y) < 0 else "r"; return f"{side}profile"
    if abs(g) <= 0.15 and abs(y) < 0.5: return "front"
    return ("l" if g < 0 else "r") + "34"

def crop(path, bbox, dst):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB"); W, H = im.size
    x, y, w, h = bbox; fw, fh = w * W, h * H; cx = (x + w / 2) * W
    cw, ch = min(W, fw * 2.6), min(H, fh * 3.2); x0 = max(0, min(W - cw, cx - cw / 2)); y0 = max(0, min(H - ch, y * H - fh * 0.9))
    c = im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))); c.thumbnail((1200, 1200)); c.save(dst, quality=93)

def main():
    from config import load
    ap = argparse.ArgumentParser(); ap.add_argument("--profile"); ap.add_argument("-n", type=int, default=20); a = ap.parse_args()
    cfg = load(); prof = a.profile or cfg["appearance"]["profile"]; root = pathlib.Path(cfg["appearance"]["assets_dir"]) / prof
    files = sorted(p for p in (root / "raw").rglob("*") if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith("."))
    rows = []
    for i, p in enumerate(files):
        try:
            q = _readable(p); fs, wh = faces(q)
            if not fs: continue
            fc = max(fs, key=lambda f: f["bbox"][2] * f["bbox"][3])
            if fc["bbox"][2] * fc["bbox"][3] < 0.01: continue
            s, parts = score(fc, *pix_stats(q, fc["bbox"]))
            rows.append(dict(rel=str(p.relative_to(root)), shoot=p.parent.name, faces=len(fs), score=s, parts=parts, bucket=bucket(fc),
                             **{k: round(v, 4) if isinstance(v, float) else v for k, v in fc.items()}))
        except Exception as e:
            print(f"skip {p.name}: {e}", file=sys.stderr)
        if i % 50 == 0: print(f"{i}/{len(files)}", file=sys.stderr)
    single = [r for r in rows if r["faces"] == 1]
    front = sorted((r for r in single if r["bucket"] == "front" and abs(r["roll"]) <= 0.3 and r["parts"]["eyes"] > 0.5), key=lambda r: -r["score"])[: a.n]
    out = root / "frontal"; out.mkdir(exist_ok=True)
    for old in out.glob("*.jpg"): old.unlink()
    for k, r in enumerate(front, 1):
        r["file"] = f"frontal/{k:02d}_{pathlib.Path(r['rel']).stem[:40]}.jpg"; crop(_readable(root / r["rel"]), r["bbox"], root / r["file"])
    (out / "frontal_index.json").write_text(json.dumps({"profile": prof, "scanned": len(files), "with_face": len(rows), "count": len(front),
        "criteria": "单人脸；Vision |yaw|<0.5 且 鼻尖偏移/眼距 |geo|<=0.15、|roll|<=0.3；睁眼分>0.5；总分=0.35正脸+0.15清晰+0.1光照+0.15睁眼+0.15无遮挡(faceCaptureQuality+landmark置信)+0.1脸大",
        "items": front}, ensure_ascii=False, indent=1))
    buckets = {}
    for r in single: buckets.setdefault(r["bucket"], []).append({k: r[k] for k in ("rel", "shoot", "score", "yaw", "geo", "roll", "bbox")})
    for v in buckets.values(): v.sort(key=lambda r: -r["score"])
    (root / "angles.json").write_text(json.dumps(buckets, ensure_ascii=False, indent=1))
    print(json.dumps({"frontal": len(front), "buckets": {k: len(v) for k, v in buckets.items()}}))

if __name__ == "__main__": main()
