"""写实感提示词修饰（X 上常用的「真实照片」短语：直闪、运动模糊、胶片颗粒、iPhone 随手拍、0.5x 超广角、CCD 等）。
库：data/realism_modifiers.json（~100 条，每条 id/en/zh/group/weight/time/tags/vec/ban_tags/exclusive）。
没有固定规则、没有常驻修饰——像一层小网络做加权：
  logit = log(weight) + 2·Σ vec[d]·(scene_vec[d]-0.5) + 0.6·|tags ∩ 场景标签| + 时段项（day/night 匹配 +0.5，不匹配 -1.5）
  p = softmax(logit / T)；数量 n 也随机（默认 P(0,1,2,3)=0.3/0.35/0.25/0.1），按 p 无放回抽样。
场景只改变概率，任何修饰都可能偶尔出现；唯一的硬约束：
  ① ban_tags 与场景标签相交（如 见老师/正式/寺庙/教堂/剧场/博物馆 禁闪光灯）；
  ② 每组最多一个、exclusive 键互斥；
  ③ 人脸身份：库里所有模糊/畸变都只作用于背景/肢体，提示词末尾再加一句保护脸和得体的话。
自拍/随手拍：场景含「自拍」等时强制注入 camera_device 自拍相关修饰（front_cam_selfie / mirror_selfie 等）；
偶发「给你拍」类生活照按 realism_casual_photo_p（默认 0.4）偏置手机随手拍短语。不改动 COUNT_P / ban_tags。
用法：python3 realism.py [场景名] [--seed N] [--hour H]"""
import json, math, pathlib, random, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = json.loads((ROOT / "data" / "realism_modifiers.json").read_text())
MODS = LIB["modifiers"]; GROUPS = LIB["groups"]; BY_ID = {m["id"]: m for m in MODS}
COUNT_P = (0.3, 0.35, 0.25, 0.1)
# 自拍 / 手机随手拍相关（均来自 data/realism_modifiers.json，不另起情绪轴）
SELFIE_CORE_IDS = ("front_cam_selfie", "mirror_selfie", "elevator_mirror")
PHONE_SNAP_IDS = ("iphone_snapshot", "phone_main_cam", "friend_took_it", "front_cam_selfie", "mirror_selfie")
SELFIE_RELATED_IDS = SELFIE_CORE_IDS + ("iphone_snapshot", "phone_main_cam", "friend_took_it", "high_angle", "vertical_phone")
DEFAULT_CASUAL_PHOTO_P = 0.4
_SELFIE_RE = re.compile(
    r"自拍|前置摄像|前置镜头|对镜|化妆镜|电梯镜|mirror\s*selfie|front\s*cam|selfie",
    re.I,
)
_CASUAL_PHOTO_RE = re.compile(
    r"给你拍|拍给你|随手拍|生活照|发张照|拍一张|拍张照|帮你拍|拍照给你|拍给你看|给你看一眼|甩一张|甩张照",
    re.I,
)

def _blob(scene=None, text=""):
    d = scene
    if isinstance(scene, str):
        try:
            import outfit
            d = outfit.BY_NAME.get(scene) or {"name": scene}
        except Exception:
            d = {"name": scene}
    d = d or {}
    return " ".join([d.get("name", ""), " ".join(d.get("aliases", []) or []), text or "", str(d.get("hint", "") or "")])

def detect_photo_mode(scene=None, text=""):
    """返回 'selfie' | 'casual' | None。自拍优先于偶发生活照。"""
    blob = _blob(scene, text)
    if _SELFIE_RE.search(blob):
        return "selfie"
    if _CASUAL_PHOTO_RE.search(blob):
        return "casual"
    return None

def _prefer_mirror(blob):
    return bool(re.search(r"对镜|化妆镜|电梯镜|镜前|mirror", blob or "", re.I))

def _allowed_ids(ids, tags):
    out = []
    for i in ids:
        m = BY_ID.get(i)
        if m and allowed(m, tags):
            out.append(i)
    return out

def inject_selfie_mods(chosen, tags, rng, mode="selfie", blob=""):
    """把自拍/手机随手拍修饰强制或偏置进已选列表，遵守 group/exclusive/ban。

    mode=selfie：至少注入 1 个核心自拍（或退回 iphone_snapshot）；可再补 vertical_phone 若组空。
    mode=casual：注入 1 个 PHONE_SNAP（含 iphone_snapshot 等）。
    若原 n=0，注入后至少 1 条。
    """
    chosen = list(chosen or [])
    tags = set(tags or [])
    if mode == "selfie":
        order = (["mirror_selfie", "elevator_mirror", "front_cam_selfie", "iphone_snapshot"]
                 if _prefer_mirror(blob) else
                 ["front_cam_selfie", "iphone_snapshot", "mirror_selfie", "phone_main_cam"])
        pool = _allowed_ids(order, tags)
        if not pool:
            pool = _allowed_ids(SELFIE_RELATED_IDS, tags)
        if not pool:
            return chosen
        # 已有自拍核心则只确保 phone snap 氛围可选补 vertical_phone
        have_core = any(i in chosen for i in SELFIE_CORE_IDS)
        have_related = any(i in chosen for i in SELFIE_RELATED_IDS)
        if have_core or (have_related and any(i in chosen for i in SELFIE_CORE_IDS + ("iphone_snapshot",))):
            # 已覆盖；若只有 framing 无 device，仍补一个 device
            if not any(BY_ID[i]["group"] == "camera_device" for i in chosen if i in BY_ID):
                pick_id = pool[0]
            else:
                return chosen
        else:
            pick_id = pool[0]
        m = BY_ID[pick_id]
        chosen = [i for i in chosen
                  if BY_ID[i]["group"] != m["group"]
                  and not (set(BY_ID[i].get("exclusive", [])) & set(m.get("exclusive", [])))]
        chosen.insert(0, pick_id)
        # 自拍时再尽量带一个竖屏/构图（不与 exclusive 冲突时）
        for extra in ("vertical_phone", "high_angle"):
            em = BY_ID.get(extra)
            if not em or not allowed(em, tags):
                continue
            if em["group"] in {BY_ID[i]["group"] for i in chosen}:
                continue
            if set(em.get("exclusive", [])) & {x for i in chosen for x in BY_ID[i].get("exclusive", [])}:
                continue
            if extra not in chosen:
                chosen.append(extra)
                break
        return chosen
    # casual
    pool = _allowed_ids(
        ["iphone_snapshot", "phone_main_cam", "friend_took_it", "front_cam_selfie", "vertical_phone"],
        tags,
    )
    if not pool:
        return chosen
    if any(i in chosen for i in PHONE_SNAP_IDS + ("vertical_phone",)):
        return chosen
    pick_id = rng.choice(pool[:4]) if hasattr(rng, "choice") else pool[0]
    m = BY_ID[pick_id]
    chosen = [i for i in chosen
              if BY_ID[i]["group"] != m["group"]
              and not (set(BY_ID[i].get("exclusive", [])) & set(m.get("exclusive", [])))]
    chosen.insert(0, pick_id)
    return chosen

GUARD_ZH = "（以上写实效果只作用于画面和背景；她的脸始终清晰、与参考图一致，整体得体、不性感化。）"
GUARD_EN = "(realism effects apply to the photo/background only; her face stays sharp and identical to the references; tasteful, not sexualized)"
TAG_RULES = [  # (正则, 标签)
    (r"见老师|学生会|正式|答辩|面试|汇报|例会|讲座|典礼|毕业", "formal"),
    (r"寺|庙|教堂|清真寺|佛|祠|陵|宗教|朝拜", "sacred"),
    (r"话剧|音乐剧|歌剧|芭蕾舞剧|舞剧|交响|相声|剧场|看电影|影院", "theater"),
    (r"博物馆|美术馆|展览|故宫|卢浮宫|艺术馆", "museum"),
    (r"KTV|ktv|蹦迪|酒吧|派对|聚会|生日|年会|跨年|万圣|圣诞|剧本杀|桌游|麻将", "party"),
    (r"KTV|ktv", "ktv"), (r"酒吧|爵士", "bar"),
    (r"演唱会|音乐节|Livehouse|livehouse|演出", "concert"),
    (r"夜|晚|蹦迪|酒吧|烟花|夜市|夜景|霓虹|跨年", "night"),
    (r"跑|球|健身|游泳|滑雪|骑行|攀岩|比赛|瑜伽|运动|射箭|冲浪|滑板|马拉松|体育", "sport"),
    (r"跑步|球|滑雪|骑行|攀岩|比赛|冲浪|滑板|马拉松|蹦迪", "sport_action"),
    (r"舞|排练|练功|基训|把杆|蹦迪", "dance"),
    (r"海|沙滩|海边|海岛|潜水|冲浪|泳", "beach"), (r"泳|水上|漂流|潜水|温泉|冲浪|水", "water"),
    (r"雪|滑雪|冰", "snow"), (r"山|湖|森林|草原|公园|植物园|花|红叶|野餐|徒步|露营", "nature"),
    (r"街|城市|商场|逛|探店|夜市|地铁|外滩|涩谷|时代广场", "city"),
    (r"宿舍|睡前|被窝|追剧", "dorm"), (r"家|居家|独处|做饭", "home"),
    (r"试衣|换装|穿搭|镜", "fitting"), (r"健身|瑜伽", "gym"),
    (r"吃|饭|餐|咖啡|奶茶|火锅|甜品|烧烤|食堂|探店", "food"), (r"咖啡|猫咖|狗咖", "cafe"),
    (r"上课|图书馆|自习|教室|校园|食堂|社团", "campus"), (r"雨", "rain"),
]
CAT_TAGS = {"校园日常": ["campus", "daily"], "体育": ["sport", "outdoor"], "娱乐": ["party", "indoor"], "约会": ["date"],
            "旅游": ["travel", "outdoor"], "旅游国家": ["travel", "outdoor"], "旅游景点": ["travel", "spot", "outdoor"]}

def scene_tags(scene=None, text=""):
    """场景（outfit 场景名 / dict）+ 画面文本 -> 标签集合。"""
    d = scene
    if isinstance(scene, str):
        try: import outfit; d = outfit.BY_NAME.get(scene) or {"name": scene}
        except Exception: d = {"name": scene}
    d = d or {}
    blob = " ".join([d.get("name", ""), " ".join(d.get("aliases", [])), text or ""])
    tags = set(CAT_TAGS.get(d.get("category"), []))
    for pat, t in TAG_RULES:
        if re.search(pat, blob): tags.add(t)
    if d.get("vec", {}).get("outdoor", 0.5) < 0.3: tags.add("indoor")
    if tags & {"sacred", "formal", "theater", "museum"}: tags.discard("party")
    return tags

def is_night(hour=None, tags=()):
    return "night" in tags if hour is None else (hour >= 19 or hour < 6)

def allowed(m, tags):
    return not (set(m.get("ban_tags", [])) & set(tags))

def logits(tags, vec=None, night=False):
    vec = vec or {}; out = {}
    for m in MODS:
        if not allowed(m, tags): continue
        s = math.log(max(m["weight"], 1e-6))
        s += 2 * sum(w * (vec.get(d, 0.5) - 0.5) for d, w in m.get("vec", {}).items())
        s += 0.6 * len(set(m.get("tags", [])) & set(tags))
        t = m.get("time", "any")
        if t != "any": s += 0.5 if (t == "night") == night else -1.5
        out[m["id"]] = s
    return out

def probs(tags, vec=None, night=False, temperature=1.0):
    lg = logits(tags, vec, night); mx = max(lg.values())
    e = {k: math.exp((v - mx) / temperature) for k, v in lg.items()}; z = sum(e.values())
    return {k: v / z for k, v in e.items()}

def pick(scene=None, text="", vec=None, hour=None, rng=None, seed=None, temperature=1.0, count_p=COUNT_P, tags=None,
         photo_mode=None, casual_p=None, force_selfie=None):
    """返回 {"ids","en","zh","tags","night","n","seed","photo_mode"}。可复现：传 seed 或 rng。

    photo_mode: 'selfie'|'casual'|None；None 时用 detect_photo_mode(scene, text)。
    force_selfie=True 等同 selfie。selfie 路径必注入自拍相关修饰；casual 以 casual_p（默认 0.4）概率注入手机随手拍。
    COUNT_P / ban_tags / exclusive 规则保持不变。
    """
    if rng is None:
        seed = random.randrange(1 << 30) if seed is None else seed; rng = random.Random(seed)
    if vec is None and isinstance(scene, (str, dict)):
        try: import outfit; d = outfit.BY_NAME.get(scene) if isinstance(scene, str) else scene; vec = (d or {}).get("vec")
        except Exception: vec = None
    tags = set(tags) if tags is not None else scene_tags(scene, text)
    night = is_night(hour, tags)
    if night: tags.add("night")
    if force_selfie:
        mode = "selfie"
    elif photo_mode is None:
        mode = detect_photo_mode(scene, text)
    else:
        mode = photo_mode
    # 自拍场景：采样前抬高相关修饰 logit（偏置），再 ensure 注入
    p = probs(tags, vec, night, temperature)
    if mode == "selfie":
        for i in SELFIE_RELATED_IDS:
            if i in p:
                p[i] = p[i] * 4.0
        z = sum(p.values()) or 1.0
        p = {k: v / z for k, v in p.items()}
    elif mode == "casual":
        for i in PHONE_SNAP_IDS:
            if i in p:
                p[i] = p[i] * 2.0
        z = sum(p.values()) or 1.0
        p = {k: v / z for k, v in p.items()}
    n = rng.choices(range(len(count_p)), count_p)[0]
    chosen, used_g, used_x = [], set(), set()
    pool = dict(p)
    while len(chosen) < n and pool:
        ids = list(pool); k = rng.choices(ids, [pool[i] for i in ids])[0]; m = BY_ID[k]
        chosen.append(k); used_g.add(m["group"]); used_x |= set(m.get("exclusive", []))
        pool = {i: v for i, v in pool.items() if i not in chosen and BY_ID[i]["group"] not in used_g
                and not (set(BY_ID[i].get("exclusive", [])) & used_x)}
    blob = _blob(scene, text)
    do_inject = False
    if mode == "selfie":
        do_inject = True
    elif mode == "casual":
        cp = DEFAULT_CASUAL_PHOTO_P if casual_p is None else float(casual_p)
        do_inject = rng.random() < max(0.0, min(1.0, cp))
    if do_inject:
        chosen = inject_selfie_mods(chosen, tags, rng, mode=mode if mode == "selfie" else "casual", blob=blob)
    return {"ids": chosen, "en": [BY_ID[i]["en"] for i in chosen], "zh": [BY_ID[i]["zh"] for i in chosen],
            "groups": [BY_ID[i]["group"] for i in chosen], "tags": sorted(tags), "night": night,
            "n": len(chosen), "seed": seed, "photo_mode": mode}

def text(sel, lang="zh"):
    """选中修饰 -> 提示词片段（空选择返回空串）。"""
    if not sel or not sel.get("ids"): return ""
    return ("写实细节：" + "；".join(sel["zh"]) + "。" + GUARD_ZH) if lang == "zh" else \
           ("Realism: " + "; ".join(sel["en"]) + ". " + GUARD_EN)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("scene", nargs="?", default=""); ap.add_argument("--seed", type=int)
    ap.add_argument("--hour", type=int); ap.add_argument("--text-only", action="store_true"); a = ap.parse_args()
    import sys; sys.path.insert(0, str(ROOT / "src"))
    s = pick(a.scene or None, a.scene, hour=a.hour, seed=a.seed)
    print(text(s) if a.text_only else json.dumps(s, ensure_ascii=False, indent=1))
