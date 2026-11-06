"""给当前 appearance profile 的 raw/ 全部参考图建索引 → <profile>/index.json。
用 tools/vision/refscan（Apple Vision）取人脸质量、脸面积、yaw 和场景标签，再派生：
- usable：恰好 1 张脸、脸面积 ≥1.5%、质量 ≥0.35、|yaw| ≤0.5
- modest：场景标签里没有泳装/内衣/床品/浴室类标签；covered：有外套/牛仔裤/毛衣等明确遮盖标签（Vision 没有着装尺度分类，只是启发式）
- scene：indoor/outdoor/cafe/campus/street/food/... 粗分类，用于按场景挑图
用法：.venv/bin/python tools/build_ref_index.py [--profile zhuzhuxiaoye]"""
import argparse, hashlib, json, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from config import load
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
RISKY = {"swimsuit", "swimwear", "bikini", "lingerie", "underwear", "brassiere", "bathrobe", "bathtub", "bathroom", "beach", "swimming_pool",
         "sauna", "bed", "bedding", "bedroom", "nude", "shirtless", "sleepwear", "pajamas", "spa", "tights", "stockings"}
COVERED = {"jacket", "coat", "sweater", "jeans", "hoodie", "suit", "blazer", "scarf", "trousers", "pants", "cardigan", "uniform"}
SCENES = {
    "outdoor": ("outdoor", "sky", "street", "park", "tree", "grass", "garden", "plant", "flower", "road", "city", "building", "structure"),
    "indoor": ("indoor", "room", "interior", "furniture", "home", "office"),
    "cafe": ("cafe", "coffee", "restaurant", "table", "drink", "beverage", "cup", "food", "dessert"),
    "campus": ("library", "book", "classroom", "school", "stage", "theater", "dance", "auditorium"),
    "night": ("night", "light", "neon", "dark"),
    "mirror": ("mirror", "phone", "selfie"),
}

def tags(labels):
    l = " ".join(labels).lower()
    sc = [k for k, ws in SCENES.items() if any(w in l for w in ws)]
    risky = [x for x in labels if x.lower() in RISKY]
    covered = [x for x in labels if x.lower() in COVERED]
    return sc, risky, covered

def build(profile=None, cfg=None):
    cfg = cfg or load(); a = cfg["appearance"]; prof = profile or a["profile"]
    root = pathlib.Path(a["assets_dir"]) / prof; raw = root / "raw"
    files = sorted(p for p in raw.rglob("*") if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith("."))
    exe = pathlib.Path(__file__).parent / "vision" / "refscan"
    if not exe.exists() or exe.stat().st_mtime < exe.with_suffix(".swift").stat().st_mtime:
        subprocess.run(["swiftc", "-O", str(exe.with_suffix(".swift")), "-o", str(exe)], check=True)
    rows = []
    for i in range(0, len(files), 40):
        out = subprocess.run([str(exe), *map(str, files[i:i + 40])], capture_output=True, text=True, timeout=600).stdout
        rows += [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    items = []
    for r in rows:
        p = pathlib.Path(r["path"]); labels = list(r.get("labels", {}))
        sc, risky, covered = tags(labels)
        usable = r.get("faces") == 1 and r.get("face_area", 0) >= 0.015 and r.get("quality", 0) >= 0.35 and abs(r.get("yaw", 99)) <= 0.5
        items.append({"rel": str(p.relative_to(root)), "group": p.parent.name, "sha1": hashlib.sha1(p.read_bytes()).hexdigest()[:12],
                      "w": r.get("w"), "h": r.get("h"), "faces": r.get("faces", 0), "face_area": round(r.get("face_area", 0), 4),
                      "quality": round(r.get("quality", 0), 3), "yaw": round(r.get("yaw", 99), 2), "bbox": [round(v, 4) for v in r.get("bbox", [])], "labels": r.get("labels", {}),
                      "scene": sc, "risky_labels": risky, "modest": not risky, "covered": covered, "usable": bool(usable), "error": r.get("error")})
    idx = {"profile": prof, "count": len(items), "usable": sum(i["usable"] for i in items),
           "usable_modest": sum(i["usable"] and i["modest"] for i in items), "items": items}
    (root / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1))
    return root / "index.json", idx

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--profile"); a = ap.parse_args()
    p, idx = build(a.profile); print(p, idx["count"], "usable", idx["usable"], "usable+modest", idx["usable_modest"])
