"""生成全身环视/体型五视图（canonical/body）：横排 5 格全身 turnaround，写入 appearance 资产。
用法：.venv/bin/python tools/gen_canonical_body.py [-k 2] [--reuse]
官方 SOP：appearance/<profile>/sop/body-orbit-yikao（艺考模卡）。-i 共5张：body front CORE + 脸 sheet/front/lprofile/rprofile；脸以已批准脸五视图为准。评分：人体检测数 + 与正脸 canonical FeaturePrint 距离。
"""
from __future__ import annotations
import argparse, datetime, json, pathlib, shutil, subprocess, sys, time
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

VIEWS = ["lprofile", "l34", "front", "r34", "rprofile"]
SOP_DIR_REL = "sop/body-orbit-yikao"  # official 艺考模卡 SOP (user pt209u)
FACE_LOCK_PREFIX = """用内置 image_gen 工具生成一张图片（不要用代码/PIL 画，不要改动任何文件）。
FACE HARD LOCK (overrides anything below): facial identity comes ONLY from the approved face five-view references (sheet.png, front.png, lprofile.png, rprofile.png). Image 1 (body front) is for body proportions, outfit, posture, background and lighting ONLY; never take the face from it.
Reference order as attached: Image 1 = approved body front (CORE); Image 2 = sheet.png; Image 3 = front.png; Image 4 = lprofile.png; Image 5 = rprofile.png.
"""


def load_sop_prompt(root):
    """Canonical English five-view template from sop/body-orbit-yikao/五视图生成提示词.txt."""
    d = root / SOP_DIR_REL
    f = d / "五视图生成提示词.txt"
    if not f.exists():
        raise SystemExit(f"missing SOP five-view prompt: {f}")
    t = f.read_text()
    i = t.find("Use case: identity-preserve.")
    if i < 0:
        raise SystemExit(f"no English five-view prompt in {f}")
    # stop before any front-portrait section if present
    j = t.find("正面全身像最终提示词", i)
    body = t[i:j if j > 0 else None].strip()
    if "five-view" not in body.lower() and "five view" not in body.lower() and "五视图" not in body:
        # still accept if it clearly asks for five figures
        if "exactly FIVE" not in body and "exactly five" not in body.lower():
            raise SystemExit(f"SOP prompt does not look like five-view sheet: {f}")
    return FACE_LOCK_PREFIX + body + "\nGenerate one image and stop. $imagegen"


def split(sheet, dst):
    im = Image.open(sheet).convert("RGB")
    W, H = im.size
    w = W / 5
    out = {}
    for i, v in enumerate(VIEWS):
        p = dst / f"{v}.png"
        im.crop((int(i * w), 0, int((i + 1) * w), H)).save(p)
        out[v] = p
    return out


def person_count(path):
    try:
        import Vision
        import frontal_extract as fe
        r = Vision.VNDetectHumanRectanglesRequest.alloc().init()
        Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(fe._cgimage(path), {}).performRequests_error_([r], None)
        return len(list(r.results() or []))
    except Exception:
        try:
            import Vision
            import frontal_extract as fe
            r = Vision.VNDetectFaceRectanglesRequest.alloc().init()
            Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(fe._cgimage(path), {}).performRequests_error_([r], None)
            return len(list(r.results() or []))
        except Exception:
            return 0


def evaluate(views, face_ref):
    import gen_canonical as gc
    counts = {v: person_count(p) for v, p in views.items()}
    detected = sum(1 for c in counts.values() if c >= 1)
    try:
        fpd = float(gc.fp_dist(gc.featureprint(views["front"]), gc.featureprint(face_ref)))
    except Exception:
        fpd = 9.0
    # 越小越好
    total = fpd + 0.25 * (5 - detected)
    return {
        "humans_or_faces": counts,
        "cells_with_person": detected,
        "featureprint_dist_vs_face_front": round(fpd, 4),
        "total": round(total, 4),
    }


def pick_refs(root, n=4):
    br = root / "body_refs" / "body_refs.json"
    if br.exists():
        items = json.loads(br.read_text())["items"]
        return [root / it["file"] for it in items[:n]]
    raise SystemExit("缺少 body_refs/，请先准备全身参考")


def main():
    from config import load
    import imagegen

    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=2)
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--sop-front", action="store_true", help="use SOP 正面全身像 as body CORE instead of approved canonical/body/front.png")
    a = ap.parse_args()
    cfg = load()
    root = pathlib.Path(cfg["appearance"]["assets_dir"]) / cfg["appearance"]["profile"]
    refs = pick_refs(root)
    face_ref = root / "canonical" / "front.png"
    if not face_ref.exists():
        face_ref = refs[0]
    body = root / "canonical" / "body"
    cands = body / "candidates"
    cands.mkdir(parents=True, exist_ok=True)
    ig = cfg.get("imagegen", {})
    codex = ig.get("cmd", "codex")
    results = []
    for k in range(1, a.k + 1):
        d = cands / str(k)
        d.mkdir(exist_ok=True)
        if not (a.reuse and (d / "sheet.png").exists()):
            t0 = time.time() - 1
            cmd = [codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(d)]
            # SOP body-orbit-yikao ref layout (max 5 -i): body front CORE, face sheet, face front, lprofile, rprofile.
            # Never attach old body sides / body_refs / old body sheet. Face lock = approved face five-views.
            body_front = (root / "canonical" / "body" / "front.png").resolve()
            if getattr(a, "sop_front", False):
                body_front = (root / SOP_DIR_REL / "正面全身像-灰上衣黑短裤.png").resolve()
            if not body_front.exists():
                raise SystemExit(f"missing body front CORE: {body_front}")
            cmd += ["-i", str(body_front)]
            for name in ("sheet.png", "front.png", "lprofile.png", "rprofile.png"):
                fp = (root / "canonical" / name).resolve()
                if not fp.exists():
                    raise SystemExit(f"missing face lock ref: {fp}")
                cmd += ["-i", str(fp)]
            # record attached refs
            attached = []
            i = 0
            while i < len(cmd):
                if cmd[i] == "-i" and i + 1 < len(cmd):
                    attached.append(cmd[i + 1]); i += 2
                else:
                    i += 1
            (d / "cmd_refs.json").write_text(__import__("json").dumps({"image_inputs_attached": attached}, ensure_ascii=False, indent=2))
            print("ATTACHED", len(attached), "image -i refs", flush=True)
            # cap refs roughly
            r = subprocess.run(cmd, input=load_sop_prompt(root), capture_output=True, text=True, timeout=900)
            (d / "codex.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
            imgs = imagegen._new_images(t0) or imagegen._from_rollouts(t0, d)
            if not imgs:
                print(f"候选 {k} 无产出 blocked={imagegen.blocked(r.stdout + r.stderr)}，见 {d/'codex.log'}", file=sys.stderr)
                continue
            shutil.copy2(imgs[-1], d / "sheet.png")
        views = split(d / "sheet.png", d)
        ev = evaluate(views, face_ref)
        results.append((ev["total"], k, ev))
        print(k, ev)
    if not results:
        sys.exit("没有候选生成成功")
    best = min(results)[1]
    bd = cands / str(best)
    for n in ["sheet"] + VIEWS:
        shutil.copy2(bd / f"{n}.png", body / f"{n}.png")
    meta = {
        "profile": cfg["appearance"]["profile"],
        "date": datetime.date.today().isoformat(),
        "generator": "codex exec image_gen (本机 Magpie) via tools/gen_canonical_body.py",
        "kind": "full_body_orbit",
        "height_cm": 173,
        "weight_kg": 50,
        "figure": "model_slender",
        "source_body_refs": [str(r.relative_to(root)) for r in refs],
        "face_lock_ref": "canonical/sheet.png",
        "chosen_candidate": best,
        "candidates": {str(k): ev for _, k, ev in sorted(results, key=lambda x: x[1])},
        "views": {v: f"{v}.png" for v in VIEWS},
        "sheet": "sheet.png",
        "layout": "横排 5 格全身，从左到右 " + " / ".join(VIEWS),
        "note": "全身环视图 = 体型/头身比标准；脸仍以 canonical/sheet.png 为身份标准",
    }
    (body / "body.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    # patch canonical.json
    cj = root / "canonical" / "canonical.json"
    idx = json.loads(cj.read_text()) if cj.exists() else {}
    idx["body_orbit"] = {
        "label": "全身环视",
        "dir": "body",
        "sheet": "body/sheet.png",
        "json": "body/body.json",
        "views": {v: f"body/{v}.png" for v in VIEWS},
        "chosen_candidate": best,
        "score": min(results)[0],
    }
    cj.write_text(json.dumps(idx, ensure_ascii=False, indent=2))

    # Promote approved front_preview over generated front; rebuild sheet
    preview = body / "front_preview.png"
    if preview.exists():
        shutil.copy2(preview, body / "front.png")
        # rebuild sheet from 5 views with approved front
        panels = [Image.open(body / f"{v}.png").convert("RGB") for v in VIEWS]
        h = max(im.height for im in panels)
        panels = [im.resize((int(im.width * h / im.height), h), Image.Resampling.LANCZOS) if im.height != h else im for im in panels]
        w = sum(im.width for im in panels)
        sheet = Image.new("RGB", (w, h), (240, 240, 240))
        x = 0
        for im in panels:
            sheet.paste(im, (x, 0)); x += im.width
        sheet.save(body / "sheet.png")
        print("promoted front_preview → front.png and rebuilt sheet.png", flush=True)

    print("best", best, body)


if __name__ == "__main__":
    main()
