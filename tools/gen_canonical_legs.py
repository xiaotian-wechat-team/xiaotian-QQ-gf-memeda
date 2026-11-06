"""生成腿部五视图模卡（canonical/legs）：横排 5 格下半身 turnaround，写入 appearance 资产。

对齐全身艺考 SOP（docs/body-orbit-yikao-sop.md）：左→右 lprofile / l34 / front / r34 / rprofile，
同款黑短裤+赤脚、浅灰棚拍、等分五列；裁切髋/大腿中段→脚。

用法：.venv/bin/python tools/gen_canonical_legs.py -k 1 [--reuse] [--promote]
不覆盖 face/body/hands sheet。默认 promote 写入 status=candidates（须人工验收后再 --approve）。
未 approved 时 refpick.legs_refs() 为空（no-op）。
"""
from __future__ import annotations
import argparse, datetime, json, os, pathlib, shutil, subprocess, sys, time
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

# 与 body 五视图同序：朝左纯侧 → 朝左45 → 正面 → 朝右45 → 朝右纯侧（下半身裁切）
VIEWS = ["lprofile", "l34", "front", "r34", "rprofile"]

LEG_PROMPT = """Use case: identity-preserve legs model card (腿部五视图模卡), following the same five-view orbit spirit as the full-body 艺考模卡.
Asset type: high-resolution photorealistic five-view lower-body composite sheet, clean horizontal contact sheet.
Input roles if attached:
- Body front / body sheet: authoritative for slender proportions, leg length, skin tone, black athletic shorts cut/hem, bare feet, pale-gray studio backdrop and lighting. Do NOT invent a different body type.
- Face sheet (if attached): skin tone / youth vibe ONLY — prefer NO face in frame (hips/upper-thighs to feet crop); tiny chin at most.
Primary request: create ONE horizontal sheet with exactly FIVE evenly spaced studio photographs of the SAME slender East Asian young woman (~20yo vibe, ~173cm / 50kg dancer build), lower body only, left-to-right angles matching body five-view orbit:
1) left-facing 90-degree profile of legs (nose/torso would point LEFT of canvas)
2) left-facing 45-degree three-quarter of legs
3) straight front lower body, feet parallel
4) right-facing 45-degree three-quarter of legs
5) right-facing 90-degree profile of legs
Whole lower body rotates with each view (full legs turnaround), not front-facing legs with only feet turned. Same person/legs in every cell.
Crop: from hips or mid-upper-thighs down to bare toes; face out of frame preferred. Long slender legs, natural skin tone. Outfit: plain opaque matte black fitted athletic short shorts (same cut as body canonical) + barefoot. No logos, no lingerie styling, no sneakers, no pants.
Posture: relaxed upright stance, weight evenly on both bare feet, feet close and naturally parallel in each viewing direction. No model strut, crossed legs, hip sway or dramatic pose.
Composition: wide landscape ~5:3, five equal-width columns, clean continuous pale-gray seamless studio backdrop and floor with subtle contact shadows. Consistent photographic scale; figures aligned at hip line and floor. Soft even studio light. No text, labels, watermarks, jewelry clutter, no extra people.
Constraints: realistic photography, coherent anatomy, feminine slender proportions consistent with body refs, exactly five lower-body views. Generate one image and stop.
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
                    help="copy best candidate to canonical/legs/; status stays 'candidates' until --approve")
    ap.add_argument("--approve", action="store_true",
                    help="DANGEROUS: mark legs.json approved (only after human review of real sheets)")
    a = ap.parse_args()
    cfg = load()
    root = pathlib.Path(cfg["appearance"]["assets_dir"]) / cfg["appearance"]["profile"]
    legs = root / "canonical" / "legs"
    cands = legs / "candidates"
    cands.mkdir(parents=True, exist_ok=True)

    # CORE refs: body front (proportions/outfit) + face sheet (skin vibe) + body sheet if present
    face_sheet = root / "canonical" / "sheet.png"
    body_front = root / "canonical" / "body" / "front.png"
    body_sheet = root / "canonical" / "body" / "sheet.png"
    ig = cfg.get("imagegen", {})
    codex = ig.get("cmd", "codex")
    results = []
    for k in range(1, a.k + 1):
        d = cands / str(k)
        d.mkdir(exist_ok=True)
        sheet_out = d / "sheet.png"
        if not (a.reuse and sheet_out.exists()):
            t0 = time.time() - 1
            # Match chat imagegen + ~/.codex: GPT-6-Astra + reasoning ultra (user default 6 astra ultra)
            model = (ig.get("model") or os.environ.get("IMAGEGEN_MODEL") or "gpt-6-astra").strip()
            effort = (
                os.environ.get("IMAGEGEN_REASONING_EFFORT")
                or ig.get("reasoning_effort")
                or "ultra"
            ).strip()
            cmd = [
                codex, "exec",
                "--skip-git-repo-check",
                "--sandbox", "workspace-write",
                "-C", str(d),
                "-c", f"model={model}",
                "-c", f"model_reasoning_effort={effort}",
            ]
            print(f"MODEL {model} reasoning_effort={effort}", flush=True)
            attached = []
            # Order mirrors body SOP spirit: body CORE first, then face for skin only
            for fp in (body_front, body_sheet, face_sheet):
                if fp.exists() and str(fp.resolve()) not in attached:
                    cmd += ["-i", str(fp.resolve())]
                    attached.append(str(fp.resolve()))
            (d / "cmd_refs.json").write_text(
                json.dumps({"image_inputs_attached": attached, "views": VIEWS}, ensure_ascii=False, indent=2)
            )
            (d / "prompt.txt").write_text(LEG_PROMPT)
            print("ATTACHED", len(attached), "refs → generate legs sheet", flush=True)
            r = subprocess.run(cmd, input=LEG_PROMPT, capture_output=True, text=True, timeout=900)
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
        raise SystemExit("no legs sheet candidates")
    best = results[0]
    print("BEST", best["sheet"])
    # -k 1 or explicit --promote lands files under canonical/legs with status=candidates
    if a.promote or a.k == 1:
        shutil.copy2(best["sheet"], legs / "sheet.png")
        for v in VIEWS:
            src = pathlib.Path(best["views"][v])
            if src.exists():
                shutil.copy2(src, legs / f"{v}.png")
        status = "approved" if a.approve else "candidates"
        if a.approve:
            print("WARNING: marking legs approved — ensure human reviewed real sheets", flush=True)
        meta = {
            "status": status,
            "kind": "legs_five_view",
            "sop_spirit": "body-orbit-yikao",
            "views": VIEWS,
            "layout": "横排 5 格下半身，从左到右 " + " / ".join(VIEWS),
            "outfit": "black fitted athletic shorts + barefoot (match body canonical)",
            "crop": "hips/upper-thighs to bare toes; face out of frame preferred",
            "promoted_from": best["dir"],
            "promoted_at_AsiaShanghai": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "aligned with docs/body-orbit-yikao-sop.md five-view orbit; refpick.legs_refs only when status=approved; does not overwrite face/body/hands",
            "face_body_hands_untouched": True,
            "model": (ig.get("model") or "gpt-6-astra"),
            "reasoning_effort": (ig.get("reasoning_effort") or "ultra"),
        }
        (legs / "legs.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        print("PROMOTED", legs / "sheet.png", "status=", status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
