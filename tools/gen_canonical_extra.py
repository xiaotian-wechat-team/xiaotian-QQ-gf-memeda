"""在标准五视图（canonical/sheet.png）之外，再生成 3 套姿态五视图，流程同 tools/gen_canonical.py：
  up   仰头：5 个偏转角（左侧脸 / 左 3/4 / 正脸 / 右 3/4 / 右侧脸），头部上仰约 20-30°
  down 低头：同上，头部下低约 20-30°
  back 背影：正背面 / 左后 3/4 / 右后 3/4 / 从左肩回头 / 从右肩回头
参考图：frontal_index.json 按 score 排序的前几张里，只取清晰度过关（脸部 Laplacian 方差 >= --min-sharp）的 3-4 张，
外加 canonical/sheet.png（主五视图，锁发型/衣着/背景风格；身份仍以真实正脸为准）。
每套至少 3 个候选（codex exec --json，按 thread_id 收图，可并行）；拒收：比例不对 / 格数不对 / 朝向顺序反 / 漏脸（仰头低头 5 格都要有脸，
背影只有后 2 格回头有脸、前 3 格不应出现正脸）；评分（越小越好）沿用 gen_canonical：
  up/down：中间格 五官比例误差 + 0.3*FeaturePrint 距离 + 0.2*漏脸 + 0.2*顺序错 + 0.2*俯仰方向错（相对主五视图正脸 pitch）
  back：回头两格（可见脸）五官比例误差 + 0.3*FeaturePrint 均值 + 发型一致性（头发颜色/占比与主五视图比）+ 0.2*漏脸/多脸
最优仍明显差于主五视图（> --redo 默认 0.30）时自动再生成一轮（最多 --rounds 轮）。
输出：canonical/<set>/{sheet,v1..v5 命名}.png + <set>.json，候选在 canonical/<set>/candidates/<k>/；并更新 canonical/canonical.json 的 extra_sheets。
已有候选每次都会重新评分参与比较；已有最优 <= --redo 的套不再生成；--rescore 只重评不生成。
用法：.venv/bin/python tools/gen_canonical_extra.py [--sets up,down,back] [-k 3] [--rescore] [--jobs 3] [--redo 0.30]"""
import argparse, concurrent.futures as cf, datetime, json, pathlib, shutil, subprocess, sys, time
import numpy as np
from PIL import Image
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gen_canonical as gc

COMMON = ("要求：5 格是同一个人、同一时刻、同一发型（和参考图一致的黑色长直发，长度、刘海、发量都与第 5 张主五视图一致）、同样的素色浅灰背景、"
          "均匀柔和的影棚布光、同一件简洁素色圆领上衣（不露肩）；表情自然中性、闭嘴；每格人物大小和高度一致，头肩到上半身；"
          "脸型、五官比例、眼睛、眉毛、鼻子、嘴唇、肤色严格保持参考图里她本人的样子，不要美颜变形、不要换成别的长相；"
          "真实摄影质感，不是插画；格子之间不要文字、不要编号、不要边框；只画 5 格，不要多也不要少。生成一张后停止。 $imagegen")
HEAD = ("用内置 image_gen 工具生成一张图片（不要用代码/PIL 画，不要改动任何文件）。\n"
        "附带的参考图都是同一个年轻女生（小甜）：前几张是她的真实正脸照片（身份标准），最后一张是她的标准肖像五视图（发型、衣着、背景、布光以它为准）。\n")
SETS = {
    "up": {"views": ["up_lprofile", "up_l34", "up_front", "up_r34", "up_rprofile"], "label": "仰头",
           "prompt": HEAD + "请画她本人的「仰头五视图」角色设定参考表：一张横向宽幅图片，从左到右等宽排列 5 个格子，每格她都微微仰头（下巴抬起约 25°，视线略向上），"
                     "偏转角顺序固定：1 左侧脸（脸朝画面左，90°），2 左 3/4（朝画面左约 45°），3 正面，4 右 3/4（朝画面右约 45°），5 右侧脸（脸朝画面右，90°）。\n" + COMMON},
    "down": {"views": ["down_lprofile", "down_l34", "down_front", "down_r34", "down_rprofile"], "label": "低头",
             "prompt": HEAD + "请画她本人的「低头五视图」角色设定参考表：一张横向宽幅图片，从左到右等宽排列 5 个格子，每格她都微微低头（下巴内收约 25°，视线向下、眼睛仍可见），"
                       "偏转角顺序固定：1 左侧脸（脸朝画面左，90°），2 左 3/4（朝画面左约 45°），3 正面，4 右 3/4（朝画面右约 45°），5 右侧脸（脸朝画面右，90°）。\n" + COMMON},
    "back": {"views": ["back", "back_l34", "back_r34", "over_lshoulder", "over_rshoulder"], "label": "背影",
             "prompt": HEAD + "请画她本人的「背影五视图」角色设定参考表：一张横向宽幅图片，从左到右等宽排列 5 个格子，顺序固定：1 正背面（完全背对镜头，只看到后脑和长发），"
                       "2 左后 3/4（背对镜头、身体朝画面左后方转约 45°，只露一点左脸颊轮廓），3 右后 3/4（背对镜头、身体朝画面右后方转约 45°，只露一点右脸颊轮廓），"
                       "4 背对镜头从左肩回头看镜头（能看到她的脸），5 背对镜头从右肩回头看镜头（能看到她的脸）。头发自然披在背后，能看清发长和发尾。\n" + COMMON},
}

def pick_refs(root, n=4, min_sharp=200.0, top=8):
    """frontal_index 前 top 张按 score 排序，只保留脸部清晰度 >= min_sharp 的，取 n 张。返回 [(path, score, sharp)]。"""
    items = json.loads((root / "frontal" / "frontal_index.json").read_text())["items"][:top]
    ok = [(root / it["file"], it["score"], sharpness(root / it["file"]), it["shoot"]) for it in items]
    ok = [x for x in ok if x[2] >= min_sharp]
    return [x[:3] for x in ok[:n]]

def sharpness(path):
    im = Image.open(path).convert("L"); W, H = im.size; a = np.asarray(im.resize((512, max(1, int(512 * H / W)))), float)
    c = a[a.shape[0] // 5: a.shape[0] * 3 // 5, a.shape[1] // 4: a.shape[1] * 3 // 4]
    lap = c[1:-1, 1:-1] * 4 - c[:-2, 1:-1] - c[2:, 1:-1] - c[1:-1, :-2] - c[1:-1, 2:]
    return round(float(lap.var()), 1)

def split(sheet, dst, views):
    im = Image.open(sheet).convert("RGB"); W, H = im.size; w = W / 5; out = {}
    for i, v in enumerate(views):
        p = dst / f"{v}.png"; im.crop((int(i * w), 0, int((i + 1) * w), H)).save(p); out[v] = p
    return out

def hair_stats(path):
    """头发：画面上 2/3 里的暗像素（亮度 < 0.25）占比 + 平均颜色。"""
    a = np.asarray(Image.open(path).convert("RGB").resize((128, 256)), float) / 255; a = a[: 170]
    m = a.mean(2) < 0.25
    return float(m.mean()), (a[m].mean(0) if m.any() else np.zeros(3))

def hair_dist(views, main_views):
    hs = [hair_stats(p) for p in views]; ms = [hair_stats(p) for p in main_views]
    fa, ca = np.mean([h[0] for h in hs]), np.mean([h[1] for h in hs], 0); fb, cb = np.mean([h[0] for h in ms]), np.mean([h[1] for h in ms], 0)
    return float(abs(fa - fb) + np.abs(ca - cb).sum())

def layout_ok(sheet):
    W, H = Image.open(sheet).size; return W / H >= 2.2, round(W / H, 3)

def evaluate(kind, views, refs, main_dir, base_pitch=0.0):
    names = SETS[kind]["views"]; faces = {v: gc.vision_face(views[v]) for v in names}
    rp = [x for x in (gc.proportions(r) for r in refs) if x is not None]; ref_mean = np.mean(rp, 0)
    def prop(p):
        x = gc.proportions(p); return float(np.abs(x - ref_mean).sum() / ref_mean.sum()) if x is not None else 9.0
    def fpd(p): return float(np.mean([gc.fp_dist(gc.featureprint(p), gc.featureprint(r)) for r in refs]))
    ev = {"geo": {v: (round(f["geo"], 3) if f else None) for v, f in faces.items()},
          "pitch": {v: (round(f["pitch"], 3) if f else None) for v, f in faces.items()}}
    pen, why = 0.0, []
    wide, ar = layout_ok(next(iter(views.values())).parent / "sheet.png"); ev["aspect"] = ar
    if not wide: pen += 1.0; why.append("比例不是横排 5 格")
    if kind in ("up", "down"):
        det = sum(f is not None for f in faces.values()); pen += 0.2 * (5 - det)
        if det < 5: why.append(f"只检出 {det}/5 张脸")
        g = [faces[v]["geo"] for v in names if faces[v]]
        # 低头时鼻梁关键点被压缩，geo 跨度很小、不可靠（实测 0.3 以内）：跨度 < 0.6 只记 order_unverified，交人工目检，不罚分
        if len(g) >= 2 and g[-1] - g[0] < 0.6: ev["order_unverified"] = True; inv = 0
        else: inv = sum(1 for i in range(len(g)) for j in range(i + 1, len(g)) if g[i] > g[j] + 0.05)
        pen += 0.2 * min(inv, 3)
        if inv: why.append(f"偏转顺序有 {inv} 处颠倒")
        ps = [faces[v]["pitch"] for v in names if faces[v]]; dp = (np.median(ps) - base_pitch) if ps else 0.0
        ev["pitch_delta"] = round(float(dp), 3)
        c = names[2]; pe = prop(views[c]); fd = fpd(views[c]); ev.update(prop_err=round(pe, 4), featureprint_dist=round(fd, 4), faces_detected=det)
        total = pe + 0.3 * fd + pen
    else:
        vis = names[3:]; det = sum(faces[v] is not None for v in vis); pen += 0.2 * (2 - det)
        if det < 2: why.append(f"回头格只检出 {det}/2 张脸")
        front_in_back = [v for v in names[:3] if faces[v] and abs(faces[v]["geo"]) < 0.3 and faces[v]["eyeL"] > 0.1 and faces[v]["eyeR"] > 0.1]
        if front_in_back: pen += 0.2 * len(front_in_back); why.append(f"背面格出现正脸 {front_in_back}")
        vp = [views[v] for v in vis if faces[v]] or [views[v] for v in vis]
        pe = float(np.mean([min(prop(p), 1.0) for p in vp])); fd = float(np.mean([fpd(p) for p in vp]))
        hd = hair_dist([views[v] for v in names], [main_dir / f"{v}.png" for v in gc.VIEWS])
        ev.update(prop_err=round(pe, 4), featureprint_dist=round(fd, 4), hair_dist=round(hd, 4), faces_detected=det)
        total = pe + 0.3 * fd + 0.3 * hd + pen
    ev.update(penalty=round(pen, 3), reject=why, total=round(float(total), 4)); return ev

def run_codex(codex, prompt, refs, d, timeout=900):
    """codex exec --json；按 thread_id 去 generated_images/<thread_id>/ 收图（并行安全），兜底按时间收。"""
    import imagegen, codex_session
    t0 = time.time() - 1
    cmd = [codex, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(d)]
    for r in refs: cmd += ["-i", str(r)]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    (d / "codex.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    tid = codex_session.thread_id(r.stdout); gi = imagegen.codex_home() / "generated_images"
    imgs = sorted((gi / tid).glob("*.png"), key=lambda p: p.stat().st_mtime) if tid and (gi / tid).exists() else []
    imgs = imgs or sorted((p for p in d.rglob("*.png") if p.name not in ("sheet.png",) and p.parent != d), key=lambda p: p.stat().st_mtime)
    imgs = [p for p in imgs if p.stat().st_mtime >= t0]
    return imgs, r, tid

def gen_candidate(kind, k, refs, can, codex):
    d = can / kind / "candidates" / str(k); d.mkdir(parents=True, exist_ok=True)
    import imagegen
    imgs, r, tid = run_codex(codex, SETS[kind]["prompt"], refs, d)
    if not imgs:
        log = r.stdout + r.stderr; return k, None, {"blocked": imagegen.blocked(log), "exit": r.returncode, "thread_id": tid}
    shutil.copy2(imgs[-1], d / "sheet.png"); return k, d, {"thread_id": tid}

def write_set(kind, can, results, failed, refs, root):
    best = min(results, key=lambda x: (bool(x[2]["reject"]), x[2]["total"])); k, d, ev = best; out = can / kind
    for n in ["sheet"] + SETS[kind]["views"]: shutil.copy2(d / f"{n}.png", out / f"{n}.png")
    meta = {"set": kind, "label": SETS[kind]["label"], "date": datetime.date.today().isoformat(), "generator": "codex exec image_gen (本机 Magpie)",
            "source_refs": [str(r.relative_to(root)) for r in refs], "chosen_candidate": k,
            "candidates": {str(kk): e for kk, _, e in sorted(results, key=lambda x: x[0])}, "failed_candidates": failed,
            "views": {v: f"{v}.png" for v in SETS[kind]["views"]}, "sheet": "sheet.png",
            "layout": "横排 5 格，从左到右 " + " / ".join(SETS[kind]["views"]), "prompt": SETS[kind]["prompt"]}
    (out / f"{kind}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1)); return meta

def update_index(can, metas):
    p = can / "canonical.json"; idx = json.loads(p.read_text()) if p.exists() else {}
    ex = idx.setdefault("extra_sheets", {})
    for kind, m in metas.items():
        ex[kind] = {"label": m["label"], "sheet": f"{kind}/sheet.png", "json": f"{kind}/{kind}.json", "views": {v: f"{kind}/{f}" for v, f in m["views"].items()},
                    "chosen_candidate": m["chosen_candidate"], "score": m["candidates"][str(m["chosen_candidate"])]["total"]}
    idx["extra_note"] = "extra_sheets = 姿态五视图（仰头/低头/背影），refpick 按场景关键词在 sheet.png 之后附一张；身份标准仍是 sheet.png"
    p.write_text(json.dumps(idx, ensure_ascii=False, indent=1))

def main():
    from config import load
    ap = argparse.ArgumentParser(); ap.add_argument("--sets", default="up,down,back"); ap.add_argument("-k", type=int, default=3)
    ap.add_argument("--refs", type=int, default=4); ap.add_argument("--min-sharp", type=float, default=200.0)
    ap.add_argument("--redo", type=float, default=0.30); ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--rescore", action="store_true", help="不生成，只对已有候选重新评分并重选"); a = ap.parse_args()
    cfg = load(); import refpick; root = refpick.profile_root(cfg); can = root / "canonical"
    picked = pick_refs(root, a.refs, a.min_sharp); print("refs", [(p.name, s, sh) for p, s, sh in picked])
    if len(picked) < 3: sys.exit("清晰正脸不足 3 张")
    frefs = [p for p, _, _ in picked]; refs = frefs + [can / "sheet.png"]
    base = gc.vision_face(can / "front.png"); base_pitch = base["pitch"] if base else 0.0
    codex = cfg.get("imagegen", {}).get("cmd", "codex"); metas = {}
    kinds = [s for s in a.sets.split(",") if s]
    state = {}
    for kind in kinds:   # 已有候选（含之前几轮）一律重新评分参与比较；新候选编号接着往后排，不覆盖
        st = state[kind] = {"results": [], "failed": {}, "next": 1}; cd = can / kind / "candidates"
        for d in sorted((x for x in cd.glob("*") if x.name.isdigit()), key=lambda x: int(x.name)) if cd.exists() else []:
            st["next"] = max(st["next"], int(d.name) + 1)
            if (d / "sheet.png").exists():
                ev = evaluate(kind, split(d / "sheet.png", d, SETS[kind]["views"]), frefs, can, base_pitch); st["results"].append((int(d.name), d, ev)); print(kind, d.name, "(已有)", ev["total"], ev["reject"], flush=True)
            else: st["failed"][d.name] = {"note": "没有 sheet.png（无产出/被拦截），见 codex.log"}
    for rnd in range(1, 0 if a.rescore else a.rounds + 1):
        todo = []
        for kind in kinds:
            st = state[kind]; good = [x for x in st["results"] if not x[2]["reject"]]
            if (rnd > 1 or st["results"]) and good and min(x[2]["total"] for x in good) <= a.redo: continue
            n = a.k if rnd == 1 else max(2, a.k - 1)
            for _ in range(n): todo.append((kind, st["next"])); st["next"] += 1
        if not todo: break
        with cf.ThreadPoolExecutor(a.jobs) as ex:
            futs = {ex.submit(gen_candidate, kind, k, refs, can, codex): kind for kind, k in todo}
            for f in cf.as_completed(futs):
                kind = futs[f]; k, d, info = f.result()
                if d is None: state[kind]["failed"][str(k)] = info; print(kind, k, "无产出", info, file=sys.stderr); continue
                ev = evaluate(kind, split(d / "sheet.png", d, SETS[kind]["views"]), frefs, can, base_pitch); ev.update(info or {})
                state[kind]["results"].append((k, d, ev)); print(kind, k, ev, flush=True)
    for kind in kinds:
        st = state[kind]
        if not st["results"]: print(kind, "没有候选成功", file=sys.stderr); continue
        metas[kind] = write_set(kind, can, st["results"], st["failed"], frefs, root)
    update_index(can, metas); print(json.dumps({k: (m["chosen_candidate"], m["candidates"][str(m["chosen_candidate"])]["total"]) for k, m in metas.items()}, ensure_ascii=False))

if __name__ == "__main__": main()
