#!/usr/bin/env python3
"""A/B：旧管线（无 scene_refs）vs 新管线（默认 match；insert 需 --modes 显式加入）。约 4 个场景各生成一对，FeaturePrint 对 canonical 正脸打分，拷到导出目录。
用法（Magpie 已批准）：
  .venv/bin/python tools/ab_realism_scene_refs.py [--scenes campus-001,campus-021,...]
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

DEFAULT_SCENES = [
    ("campus-001", "在舞蹈排练厅练功，白天窗光"),
    ("campus-021", "走在教学楼走廊，手里拿着书"),
    ("campus-035", "在学校附近咖啡店靠窗座位喝咖啡"),
    ("campus-009", "在图书馆自习看书"),  # 不要写「低头」——会误挂 down/sheet 并强迫歪头/低头
]

def score_vs_canonical(img, cfg):
    """越小越好：FeaturePrint L2 to canonical front（若有）或 sheet。"""
    import numpy as np
    from PIL import Image
    try:
        import gen_canonical as gc
    except Exception as e:
        return {"error": f"import gen_canonical: {e}"}
    root = pathlib.Path(cfg["appearance"]["assets_dir"]) / cfg["appearance"]["profile"]
    front = root / "canonical" / "front.png"
    sheet = root / "canonical" / "sheet.png"
    ref = front if front.exists() else sheet
    if not ref.exists():
        return {"error": "no canonical"}
    try:
        # crop center face-ish of generated vertical photo: upper-middle third
        im = Image.open(img).convert("RGB"); W, H = im.size
        crop = im.crop((int(W*0.2), int(H*0.08), int(W*0.8), int(H*0.45)))
        tmp = pathlib.Path(img).with_suffix(".face_crop.jpg"); crop.save(tmp, quality=90)
        d = gc.fp_dist(gc.featureprint(tmp), gc.featureprint(ref))
        return {"featureprint_dist": round(float(d), 4), "ref": str(ref), "crop": str(tmp)}
    except Exception as e:
        return {"error": str(e)[:200]}

def run_one(scene_text, outfit_scene, mode, out_root):
    import imagegen
    from config import load
    cfg = load()
    env_save = {k: os.environ.get(k) for k in ("SCENE_REFS", "SCENE_REFS_MODE", "REALISM_SEED")}
    try:
        if mode == "old":
            os.environ["SCENE_REFS"] = "0"
        else:
            os.environ["SCENE_REFS"] = "1"
            os.environ["SCENE_REFS_MODE"] = mode  # match|insert
        os.environ["REALISM_SEED"] = "42"  # stabilize modifiers across A/B
        tag = f"{outfit_scene}_{mode}_{time.strftime('%H%M%S')}"
        # generate_local writes its own timestamped dir under out_dir; we then copy
        cmd, imgs = imagegen.generate(scene_text, dry=False, cfg=cfg, outfit_scene=outfit_scene)
        dest = out_root / tag; dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for p in imgs:
            shutil.copy2(p, dest / p.name); copied.append(str(dest / p.name))
        # also copy outfit/run if present beside first image
        if imgs:
            parent = imgs[0].parent
            for name in ("outfit.json", "run.json", "codex.log"):
                src = parent / name
                if src.exists(): shutil.copy2(src, dest / name)
            (dest / "meta.json").write_text(json.dumps({
                "mode": mode, "scene_text": scene_text, "outfit_scene": outfit_scene,
                "cmd": cmd, "images": copied, "src_dir": str(parent),
                "score": score_vs_canonical(imgs[0], cfg) if imgs else None,
            }, ensure_ascii=False, indent=1))
        return dest, copied
    finally:
        for k, v in env_save.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", default="", help="comma scene ids; default built-in 4")
    ap.add_argument("--modes", default="old,match", help="comma modes; default old,match（insert 比例易偏，需显式加）")
    ap.add_argument("--out", default=str(pathlib.Path.home() / "xiaotian-assets/generated/realism_ab"))
    a = ap.parse_args()
    from config import load
    scenes = DEFAULT_SCENES
    if a.scenes:
        idset = set(a.scenes.split(","))
        all_sc = {s["id"]: s for s in json.loads((ROOT/"data/outfit_scenes.json").read_text())["scenes"]}
        scenes = [(i, f"{all_sc[i]['name']}，自然随手拍") for i in a.scenes.split(",") if i in all_sc]
    out_root = pathlib.Path(a.out); out_root.mkdir(parents=True, exist_ok=True)
    summary = []
    for sid, text in scenes:
        for mode in a.modes.split(","):
            print(f"=== {sid} mode={mode} ===", flush=True)
            try:
                dest, imgs = run_one(text, sid, mode, out_root)
                meta = json.loads((dest/"meta.json").read_text())
                summary.append({"scene": sid, "mode": mode, "dir": str(dest), "images": imgs, "score": meta.get("score")})
                print("ok", dest, meta.get("score"), flush=True)
            except Exception as e:
                summary.append({"scene": sid, "mode": mode, "error": str(e)[:400]})
                print("FAIL", e, flush=True)
    (out_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
