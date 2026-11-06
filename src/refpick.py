"""从当前 profile 的 raw/ 参考库（全部 451 张都是参考图；refs/ 只是精选快捷方式）里按场景挑参考图。
依赖 tools/build_ref_index.py 生成的 <profile>/index.json。规则：
- 从全部 usable（单人正脸清晰，当前 159 张）里挑，不按穿着过滤（2026-10-24 用户确认这些照片都是她的长相）
- 分数 = 质量 + 脸面积加分 + 场景命中加分 + 随机抖动；同一组（同一篇笔记/同一次拍摄）最多 1 张，保证多样
- 默认裁成头肩近景（以人脸框为中心，宽约 3 倍脸宽、高约 3.6 倍脸高，向下多留）：只给模型看长相，
  不带原图的穿着（锁定同一张脸），降低审核误拦，也避免照抄原图构图；heic/webp/png 一律用 sips 转 jpg 缓存到 <profile>/cache/
2026-10-24 起每次生图的参考 = canonical（标准肖像五视图，身份标准，tools/gen_canonical.py 生成）+ 其余照片随机 3 张（pick_identity）：
- canonical：<profile>/canonical/ 下 appearance.canonical_refs（默认 ["sheet.png"]，可改成 5 张单视图）；profile 目录可用
  appearance.profile_dir 直接指定（换人设只改 config）
- 其余 3 张：从 angles.json（tools/frontal_extract.py 按 yaw 分桶）里随机，排除生成 canonical 用的正脸源图，不同拍摄（raw 子目录）、
  尽量不同角度桶，index.json 里有泳装/内衣等 risky 标签的跳过，一律裁头肩（降低审核误拦）；只作角度/表情参考
2026-10-24 姿态五视图（tools/gen_canonical_extra.py）：canonical/up|down|back/sheet.png。
  正视 sheet.png 每次必附。姿态 sheet 由 extra_kind 向量相似度（场景/活动 ↔ up/down/back，相对 neutral 够自信才附）选取；
  嵌入不可用时退回窄姿态关键词（不含「看书/看手机」等活动词）。
2026-10-24 全身环视（docs/body-orbit-yikao-sop.md；用户 pt218u）：status=approved 时
  **必附两张五视图**：① canonical/sheet.png（脸）② canonical/body/sheet.png（身体环视；不是 body/front）。
  其余 3 槽（MAX_IMAGE_REFS=5）留给 scene_refs。有 body sheet 时不附姿态 sheet / other 头肩。
用法：python3 src/refpick.py "图书馆看书" [-n 3] [--identity]"""
import argparse, json, pathlib, random, subprocess
from config import load

ZH = {"outdoor": ("操场", "公园", "街", "户外", "校园", "门口", "树", "花", "天台", "外面", "散步"),
      "indoor": ("室内", "宿舍", "家", "教室", "排练", "练功房", "房间", "办公室", "学生会"),
      "cafe": ("咖啡", "奶茶", "餐厅", "吃", "饭", "甜品", "下午茶", "食堂"),
      "campus": ("图书馆", "教室", "剧场", "舞台", "排练", "上课", "片场", "拍摄", "导演"),
      "night": ("晚上", "夜", "灯", "深夜")}

def profile_root(cfg=None):
    a = (cfg or load())["appearance"]
    if a.get("profile_dir"): return pathlib.Path(a["profile_dir"]).expanduser()
    return pathlib.Path(a["assets_dir"]) / a["profile"]

def canonical_refs(cfg=None):
    """标准五视图（身份标准）。没生成过就返回 []（调用方退回旧的 pick）。"""
    cfg = cfg or load(); root = profile_root(cfg); d = root / "canonical"
    names = cfg["appearance"].get("canonical_refs") or ["sheet.png"]
    return [d / n for n in names if (d / n).exists()]

def body_orbit_approved(cfg=None):
    """canonical/body 是否可附：body.json status==approved，且未用 BODY_ORBIT_REFS=0 / appearance.body_orbit_attach=false 关掉。"""
    import os
    cfg = cfg or load()
    if os.environ.get("BODY_ORBIT_REFS", "1") in ("0", "false", "no"): return False
    if cfg.get("appearance", {}).get("body_orbit_attach") in (False, 0, "0", "false", "no"): return False
    root = profile_root(cfg)
    meta = root / "canonical" / "body" / "body.json"
    try:
        st = json.loads(meta.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return False
    return st == "approved"

def body_orbit_refs(cfg=None):
    """必附身体五视图：canonical/body/sheet.png（0~1 张）。未批准或缺失 → []。
    不用 body/front；调用方插在 face sheet 之后；cap_refs 与 face sheet 一并 HARD 保留。"""
    if not body_orbit_approved(cfg): return []
    sheet = profile_root(cfg) / "canonical" / "body" / "sheet.png"
    return [sheet] if sheet.exists() else []

def hands_approved(cfg=None):
    """canonical/hands 是否可附：hands.json status==approved，且未用 HANDS_REFS=0 关掉。"""
    import os, json
    cfg = cfg or load()
    if os.environ.get("HANDS_REFS", "1") in ("0", "false", "no"): return False
    if cfg.get("appearance", {}).get("hands_attach") in (False, 0, "0", "false", "no"): return False
    root = profile_root(cfg)
    meta = root / "canonical" / "hands" / "hands.json"
    try:
        st = json.loads(meta.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return False
    return st == "approved"

def hands_refs(cfg=None):
    """手部五视图：canonical/hands/sheet.png（0~1 张）。未批准或缺失 → []。"""
    if not hands_approved(cfg): return []
    sheet = profile_root(cfg) / "canonical" / "hands" / "sheet.png"
    return [sheet] if sheet.exists() else []

def legs_approved(cfg=None):
    """canonical/legs 是否可附：legs.json status==approved，且未用 LEGS_REFS=0 关掉。
    无真实模卡时保持未批准 → 本函数 False（禁止假装 approved）。"""
    import os, json
    cfg = cfg or load()
    if os.environ.get("LEGS_REFS", "1") in ("0", "false", "no"): return False
    if cfg.get("appearance", {}).get("legs_attach") in (False, 0, "0", "false", "no"): return False
    root = profile_root(cfg)
    meta = root / "canonical" / "legs" / "legs.json"
    try:
        st = json.loads(meta.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return False
    return st == "approved"

def legs_refs(cfg=None):
    """腿部模卡：canonical/legs/sheet.png（0~1 张）。未批准或缺失 → []（no-op）。"""
    if not legs_approved(cfg): return []
    sheet = profile_root(cfg) / "canonical" / "legs" / "sheet.png"
    return [sheet] if sheet.exists() else []

# 姿态五视图选择：优先用 bge 向量（与 outfit/scene_refs 同款）把场景/活动跟 up/down/back 原型比相似度；
# 仅当最佳姿态相对「正面端正」有足够置信度时才附姿态 sheet。无明确匹配时只发正视五视图。
# EXTRA_KW 只作嵌入不可用或向量不够自信时的窄兜底（明确姿态词；绝不用「看书/看手机」等活动词）。
EXTRA_KW = {"back": ("背影", "背对", "背身", "回头", "回眸", "转身", "从背后", "从后面", "后背", "背面", "back view", "from behind", "looking back", "over the shoulder"),
            "up": ("仰头", "抬头", "仰望", "仰着", "往上看", "向上看", "看天", "望天", "看星星", "看月亮", "看烟花", "看樱花", "看雪", "下巴抬", "looking up", "chin up"),
            "down": ("低头", "垂眸", "垂眼", "俯身", "往下看", "向下看", "看脚下", "looking down", "head down", "chin down")}
POSE_PROTOS = {
    "up": "仰头抬头仰望看天空看星星看月亮看烟花下巴抬起，头部上扬 looking up chin up head tilted upward",
    "down": "低头垂眸俯身往下看下巴低，头部俯视 looking down chin down head tilted downward",
    "back": "背影背对从背后转身回眸后背，背向镜头 from behind back view looking back over the shoulder",
    "neutral": "正面端正日常站立坐着自拍自然直立看镜头 front facing upright casual selfie natural head posture looking at camera",
}
# 向量门槛：score>=MIN_SCORE 且 相对 neutral 的 margin>=MIN_MARGIN 才算自信命中（可用环境变量覆盖）
POSE_MIN_SCORE = 0.52
POSE_MIN_MARGIN = 0.10
_pose_emb = None  # cached {kind: vec}

def _pose_embeddings():
    """归一化姿态原型嵌入；失败返回 None（调用方退回 EXTRA_KW）。"""
    global _pose_emb
    if _pose_emb is not None: return _pose_emb or None
    try:
        import os, numpy as np, outfit
        m = outfit._load_model()
        if not m: _pose_emb = False; return None
        keys = list(POSE_PROTOS)
        v = m.encode([POSE_PROTOS[k] for k in keys], normalize_embeddings=True, show_progress_bar=False)
        _pose_emb = {k: np.asarray(v[i], dtype="float32") for i, k in enumerate(keys)}
        return _pose_emb
    except Exception as e:
        import sys
        print(f"[refpick] 姿态嵌入不可用，退回关键词：{e}", file=sys.stderr)
        _pose_emb = False; return None

def _kw_kind(scene):
    """窄关键词兜底：明确姿态词；多命中时 back > up > down。"""
    s = (scene or "").lower()
    for k in ("back", "up", "down"):
        if any(w in s for w in EXTRA_KW[k]): return k
    return None

def extra_kind(scene, *, min_score=None, min_margin=None, use_embed=True):
    """场景/活动 → 'back' / 'up' / 'down' / None。
    先向量相似度（相对 neutral 有置信度才采纳），否则窄关键词兜底。测试可设 use_embed=False。"""
    import os
    if use_embed and os.environ.get("XIAOTIAN_TEST") != "1" and os.environ.get("POSE_EMBED", "1") not in ("0", "false", "no"):
        emb = _pose_embeddings()
        if emb is not None and (scene or "").strip():
            try:
                import numpy as np, outfit
                m = outfit._load_model()
                q = m.encode([scene], normalize_embeddings=True, show_progress_bar=False)[0]
                sims = {k: float(np.dot(q, emb[k])) for k in ("up", "down", "back", "neutral")}
                best = max(("up", "down", "back"), key=lambda k: sims[k])
                ms = float(os.environ.get("POSE_MIN_SCORE", min_score if min_score is not None else POSE_MIN_SCORE))
                mm = float(os.environ.get("POSE_MIN_MARGIN", min_margin if min_margin is not None else POSE_MIN_MARGIN))
                if sims[best] >= ms and (sims[best] - sims["neutral"]) >= mm:
                    return best
            except Exception as e:
                import sys
                print(f"[refpick] 姿态向量匹配失败：{e}", file=sys.stderr)
    return _kw_kind(scene)

def extra_sheet(scene, cfg=None):
    """命中姿态且 canonical/<kind>/sheet.png 存在时返回该路径，否则 None。"""
    k = extra_kind(scene)
    if not k: return None
    p = profile_root(cfg) / "canonical" / k / "sheet.png"
    return p if p.exists() else None

def _canonical_sources(root):
    try: return set(json.loads((root / "canonical" / "canonical.json").read_text()).get("source_refs_raw", []))
    except (OSError, ValueError): return set()

def others(n=3, cfg=None, seed=None, exclude=()):
    """其余角度参考：angles.json 随机 n 张，不同拍摄、尽量不同角度桶，跳过 canonical 源图与 risky，裁头肩。exclude：rel 或 sha1。"""
    cfg = cfg or load(); root = profile_root(cfg); rng = random.Random(seed)
    buckets = json.loads((root / "angles.json").read_text())
    try: risky = {i["rel"] for i in json.loads((root / "index.json").read_text())["items"] if i.get("risky_labels")}
    except (OSError, ValueError): risky = set()
    skip = _canonical_sources(root) | risky | set(exclude)
    pool = {b: [r for r in v if r["rel"] not in skip and _sha(r["rel"]) not in skip] for b, v in buckets.items()}
    for v in pool.values(): rng.shuffle(v)
    order = [b for b in pool if pool[b]]; rng.shuffle(order)
    out, shoots = [], set()
    while len(out) < n and any(pool[b] for b in order):
        for b in order:   # 轮流从各角度桶取，保证角度多样
            while pool[b]:
                r = pool[b].pop()
                if r.get("shoot") in shoots and len(shoots) < len({x.get("shoot") for v in pool.values() for x in v} | shoots): continue
                out.append(r); shoots.add(r.get("shoot")); break
            if len(out) >= n: break
    return [crop_any(root, r) for r in out]

def _sha(rel): import hashlib; return hashlib.sha1(rel.encode()).hexdigest()[:12]

def crop_any(root, r):
    """angles.json 条目（bbox 左上原点归一化）→ 头肩 jpg 缓存。"""
    dst = root / "cache" / f"ang_{_sha(r['rel'])}_face.jpg"
    if not dst.exists():
        from PIL import Image, ImageOps
        dst.parent.mkdir(exist_ok=True); src = root / r["rel"]
        if src.suffix.lower() == ".heic":
            tmp = dst.with_suffix(".src.jpg"); subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(tmp)], capture_output=True, check=True); src = tmp
        im = ImageOps.exif_transpose(Image.open(src)).convert("RGB"); W, H = im.size
        x, y, w, h = r["bbox"]; fw, fh = w * W, h * H; cx = (x + w / 2) * W
        cw, ch = min(W, fw * 3.0), min(H, fh * 3.6); x0 = max(0, min(W - cw, cx - cw / 2)); y0 = max(0, min(H - ch, y * H - fh))
        c = im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))); c.thumbnail((1200, 1200)); c.save(dst, quality=92)
    return dst

def pick_identity(scene=None, n_other=3, cfg=None, seed=None, exclude=(), shot_type=None):
    """返回 (canonical, others)。
    默认：face sheet（HARD）+ approved body/sheet.png（HARD，身体五视图）。
    shot_type=hand_closeup：只附 hands/sheet（HARD），不强制 face+body。
    shot_type=pov_first_person：可选附 hands（软）+ 弱 face sheet；不附 body；场景槽留给 scene_refs。
    shot_type=mirror_selfie：face sheet HARD；hands 软可选；不附 body。
    shot_type=legs_focus：legs/sheet HARD（仅 status=approved）独占 1 身份槽（不软挂 face→k_scene=4）；否则 body 软 + 弱 face；无假批准。
    （配合 shot_modes.skips_hard_face_body + imagegen.build_prompt：禁止把 ref#1 标成正脸五视图。）
    有 body sheet 时（默认路径）：不附姿态 sheet、others=[]（其余 3 槽留给 imagegen scene_refs）。
    无 body 时：保持旧逻辑（可选姿态 sheet + other 头肩）。
    总数由 imagegen.cap_refs 硬裁 ≤ MAX_IMAGE_REFS（默认 5）。"""
    cfg = cfg or load()
    try:
        import shot_modes as sm
        shot = sm.normalize_shot(shot_type)
    except Exception:
        shot = shot_type
    if shot == "hand_closeup":
        hands = hands_refs(cfg)
        if hands:
            return list(hands), []
        # 无手模时退回弱 face（仍尽量不挂 body）
        can = list(canonical_refs(cfg))[:1]
        return can, []
    if shot == "pov_first_person":
        can = []
        # POV：场景为主；可挂 face sheet 作弱身份（非 body）；可选 hands 供入镜手臂
        face = list(canonical_refs(cfg))[:1]
        can.extend(face)
        for h in hands_refs(cfg):
            if h not in can:
                can.append(h)
        return can, []
    if shot == "mirror_selfie":
        # 对镜：脸 HARD；手软；不挂 body（避免模卡棚拍装）
        can = list(canonical_refs(cfg))[:1]
        for h in hands_refs(cfg):
            if h not in can:
                can.append(h)
        return can, []
    if shot in ("legs_focus",):
        # 腿部：approved legs sheet 独占 1 身份槽（不软挂 face，留给 scene_refs）；
        # 否则 body 软 + 弱 face（不假装有腿模）
        legs = legs_refs(cfg)
        if legs:
            return list(legs), []
        can = list(canonical_refs(cfg))[:1]
        body = body_orbit_refs(cfg)
        for b in body:
            if b not in can: can.append(b)
        return can, []
    if shot == "portrait":
        # 上半身/脸：face HARD，不强制 body
        return list(canonical_refs(cfg))[:1], []
    can = list(canonical_refs(cfg))
    body = body_orbit_refs(cfg) if can else []
    for b in body:
        if b not in can: can.append(b)
    if body:
        # 脸五视图 + 身体五视图必附；剩余槽给场景，不占姿态/other
        return can, []
    if can and cfg["appearance"].get("extra_sheets", True):
        ex = extra_sheet(scene, cfg)
        if ex and ex not in can: can.append(ex)
    try: oth = others(n_other, cfg, seed, exclude) if can else None
    except FileNotFoundError: oth = None
    if oth is None: oth = pick(scene or "", n_other, cfg, seed, exclude=exclude)
    return can, oth

def scene_tags(scene):
    return {k for k, ws in ZH.items() if any(w in scene for w in ws)}

def candidates(idx, exclude=()):
    return [i for i in idx["items"] if i["usable"] and not i.get("error") and i["sha1"] not in exclude]

def score(it, want):
    return it["quality"] + min(it["face_area"], 0.15) * 2 + 0.25 * len(want & set(it["scene"]))

def pick(scene, n=3, cfg=None, seed=None, crop=True, exclude=()):
    """exclude：要避开的 sha1（审核拦截后换一组重试用）。"""
    root = profile_root(cfg); idx = json.loads((root / "index.json").read_text())
    want = scene_tags(scene); rng = random.Random(seed)
    ranked = sorted(candidates(idx, set(exclude)), key=lambda i: score(i, want) + rng.uniform(0, 0.15), reverse=True)
    out, groups = [], set()
    for it in ranked:
        if it["group"] in groups: continue
        out.append(it); groups.add(it["group"])
        if len(out) >= n: break
    return [as_jpg(root, it, crop) for it in out]

def crop_box(it):
    """头肩框（像素）：返回 (x, y, w, h)，已夹在图内。"""
    W, H = it["w"], it["h"]; bx, by, bw, bh = it["bbox"]
    cx = (bx + bw / 2) * W; fw, fh = bw * W, bh * H
    w = min(W, fw * 3.0); h = min(H, fh * 3.6)
    x = max(0, min(W - w, cx - w / 2)); y = max(0, min(H - h, by * H - fh * 1.0))
    return int(x), int(y), int(w), int(h)

def as_jpg(root, it, crop=True):
    src = root / it["rel"]; crop = crop and len(it.get("bbox") or []) == 4
    dst = root / "cache" / f"{it['sha1']}{'_face' if crop else ''}.jpg"
    if not dst.exists():
        dst.parent.mkdir(exist_ok=True)
        subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)], capture_output=True, check=True)
        if crop:
            x, y, w, h = crop_box(it)
            subprocess.run(["sips", "-c", str(h), str(w), "--cropOffset", str(y), str(x), str(dst)], capture_output=True, check=True)
        subprocess.run(["sips", "-Z", "1200", str(dst)], capture_output=True, check=True)
    return dst

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("scene"); ap.add_argument("-n", type=int, default=3); ap.add_argument("--identity", action="store_true"); a = ap.parse_args()
    if a.identity:
        c, o = pick_identity(a.scene, a.n); [print("canonical", p) for p in c]; [print("other", p) for p in o]
    else:
        for p in pick(a.scene, a.n): print(p)
