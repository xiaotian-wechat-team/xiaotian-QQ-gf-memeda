"""生成手部五视图模卡（canonical/hands）：横排 5 格纤细手模，写入 appearance 资产。
用法：.venv/bin/python tools/gen_canonical_hands.py [-k 1] [--reuse] [--promote]
不覆盖 face/body sheet。肤色/气质锁定小甜 identity（手部，无表情）。
"""
from __future__ import annotations
import argparse, datetime, json, pathlib, shutil, subprocess, sys, time
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

# 左→右五格：手背 / 3/4手背 / 掌心正面 / 3/4掌侧 / 侧面轮廓
VIEWS = ["dorsal", "dorsal34", "palm", "palm34", "side"]

HAND_PROMPT = """Use case: identity-preserve hand model card.
Asset type: high-resolution photorealistic five-view hand composite sheet (手部五视图模卡), clean horizontal contact sheet.
Primary request: create ONE horizontal sheet with exactly FIVE evenly spaced studio photographs of the SAME pair of slender feminine East Asian young-woman hands (about 20 years old vibe), left-to-right angles:
1) back of hand (dorsal), fingers gently relaxed
2) three-quarter dorsal
3) open palm facing camera
4) three-quarter palm/side
5) pure side silhouette of the hand/edge
Same hands in every cell. Slender elegant fingers, natural short-to-medium neat nails with soft nude/sheer pink polish (not loud), fair skin matching the attached face/body identity references' skin tone. No face, no torso, no clothing logos — hands/wrists only, optional soft forearm crop.
Composition: wide landscape ~5:3, five equal columns, pale-gray seamless studio backdrop, soft even light, consistent scale, hands fill most of each cell height. No text, labels, watermarks, jewelry clutter (tiny plain ring OK at most once), no extra people.
Constraints: realistic photography, coherent anatomy, exactly five hand views, feminine slender proportions consistent with a ~173cm / 50kg dancer build. Generate one image and stop.
Input roles if attached: face/body sheets are ONLY for skin tone / youth / identity vibe — do NOT draw a face. Hand sheet is the deliverable.
Generate one image and stop. $imagegen"""


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


def main():
    from config import load
    import imagegen

    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=1)
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--promote", action="store_true",
                    help="copy best candidate to canonical/hands/ and mark hands.json approved")
    a = ap.parse_args()
    cfg = load()
    root = pathlib.Path(cfg["appearance"]["assets_dir"]) / cfg["appearance"]["profile"]
    hands = root / "canonical" / "hands"
    cands = hands / "candidates"
    cands.mkdir(parents=True, exist_ok=True)

    # identity refs for skin vibe (do not overwrite face/body)
    face_sheet = root / "canonical" / "sheet.png"
    body_front = root / "canonical" / "body" / "front.png"
    ig = cfg.get("imagegen", {})
    codex = ig.get("cmd", "codex")
    results = []
    for k in range(1, a.k + 1):
        d = cands / str(k)
        d.mkdir(exist_ok=True)
        sheet_out = d / "sheet.png"
        if not (a.reuse and sheet_out.exists()):
            t0 = time.time() - 1
            cmd = [codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(d)]
            attached = []
            for fp in (face_sheet, body_front):
                if fp.exists():
                    cmd += ["-i", str(fp.resolve())]
                    attached.append(str(fp.resolve()))
            (d / "cmd_refs.json").write_text(json.dumps({"image_inputs_attached": attached}, ensure_ascii=False, indent=2))
            print("ATTACHED", len(attached), "refs → generate hand sheet", flush=True)
            r = subprocess.run(cmd, input=HAND_PROMPT, capture_output=True, text=True, timeout=900)
            (d / "codex.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
            imgs = imagegen._new_images(t0) or imagegen._from_rollouts(t0, d)
            if not imgs:
                print(f"candidate {k}: no image", file=sys.stderr)
                continue
            shutil.copy2(imgs[0], sheet_out)
            print(f"candidate {k}: {sheet_out} ({time.time()-t0:.1f}s)", flush=True)
        if sheet_out.exists():
            views = split(sheet_out, d)
            results.append({"k": k, "dir": str(d), "sheet": str(sheet_out), "views": {v: str(p) for v, p in views.items()}})

    (cands / "scores.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    if not results:
        raise SystemExit("no hand sheet candidates")
    best = results[0]
    print("BEST", best["sheet"])
    if a.promote or a.k == 1:
        # promote best → canonical/hands/
        shutil.copy2(best["sheet"], hands / "sheet.png")
        for v in VIEWS:
            src = pathlib.Path(best["views"][v])
            if src.exists():
                shutil.copy2(src, hands / f"{v}.png")
        meta = {
            "status": "approved",
            "kind": "hand_five_view",
            "views": VIEWS,
            "promoted_from": best["dir"],
            "promoted_at_AsiaShanghai": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "slender feminine hand template; does not overwrite face/body sheets",
            "face_body_untouched": True,
        }
        (hands / "hands.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        print("PROMOTED", hands / "sheet.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
