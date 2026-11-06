"""场景驱动的浮动穿搭向量（替代固定的「穿着得体」）。
场景库：data/outfit_scenes.json（tools/gen_outfit_scenes.py 生成，579 个，7 类：校园日常/体育/娱乐/约会/旅游/旅游国家/旅游景点）。
每个场景：id/name/category/aliases/hint（简短穿搭提示）/vec（formality 正式度、exposure 放开程度、trendiness 时髦度、
sportiness 运动感、coziness 居家舒适 + 扩展 warmth 保暖↔清凉、outdoor 户外、water 玩水）/cap（放开程度上限 0~3）。
选场景（rank）：用 src/mem.py 同款本地 bge-small-zh-v1.5 把 场景名+别名+提示 嵌入（缓存 data/outfit_scene_emb.npz，按内容哈希失效），
再嵌入上下文：画面描述(权重3) > 最近聊天(合计1.5，越近越重) > 作息活动(1) > 校园动态(0.5)，按来源加权余弦 + 别名命中加分；
加先验：旧 8 场景推断（作息/工作日周末/情绪/聊天正则 infer_weights）、周末晚上偏出门/约会、季节不符小幅扣分。
cosine top-k(8) 温度采样出主场景（权重0.7）+ 其余前几名软混合（0.3），加 ±0.05 抖动。模型不可用时退回别名+字符二元组匹配。
exposure 量化成 0~3 级并受主场景 cap 限制；最高一级也只是「更显身材、更精致」，始终得体、不暴露、不性感化；审核拦截时由 imagegen 逐级降级。
季节/天气：只用月份 + weather.get(place, cache_only=True)（默认 bda=海淀北舞，不额外联网）或调用方传入的 weather 文本；目的地场景（climate）用目的地气候。
用法：python3 outfit.py ["画面"] [--scene 约会]"""
import datetime, hashlib, json, math, os, pathlib, random, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENES_FILE = ROOT / "data" / "outfit_scenes.json"
EMB_FILE = ROOT / "data" / "outfit_scene_emb.npz"
DIMS = ("formality", "exposure", "trendiness", "sportiness", "coziness")
XDIMS = DIMS + ("warmth", "outdoor", "water")
_data = json.loads(SCENES_FILE.read_text())
SCENE_LIST = _data["scenes"]
BY_NAME = {d["name"]: d for d in SCENE_LIST}
BY_ID = {d["id"]: d for d in SCENE_LIST}
SCENES = {d["name"]: tuple(d["vec"][k] for k in DIMS) for d in SCENE_LIST}       # 兼容旧接口：名称 -> 5 维
LEGACY = ["上课/排练", "见老师/学生会正式场合", "朋友聚会", "约会", "准备见他", "和他在一起(居家/独处)", "宅宿舍/睡前", "外出日常"]
CATEGORIES = list(dict.fromkeys(d["category"] for d in SCENE_LIST))
ALIASES = {}
for _d in SCENE_LIST:
    for _a in _d["aliases"]: ALIASES.setdefault(_a, _d["name"])
EXPOSURE_TEXT = ["保守，领口和裙长都规矩，不露肩不露腰",
                 "日常自然、得体",
                 "稍微放开一点，修身剪裁，自然显出舞蹈生的线条",
                 "更放开、更显身材、更精致：合身剪裁、显腰线和身形，细节和妆发更用心，依然得体、不暴露"]
CHAT_KEYS = [  # (正则, 场景, 权重)
    (r"约会|看电影|烛光|晚餐约|去见你约", "约会", 2.0),
    (r"见面|来找你|接你|去找你|马上到|等我|见你", "准备见他", 1.8),
    (r"在你家|你家|一起在家|陪你|抱抱|窝在一起|同居|我们家", "和他在一起(居家/独处)", 2.2),
    (r"老师|答辩|开会|例会|学生会|面试|汇报演出|晚会|主持|正式", "见老师/学生会正式场合", 1.5),
    (r"聚会|聚餐|室友|闺蜜|KTV|ktv|生日|蹦迪|酒吧|朋友", "朋友聚会", 1.5),
    (r"排练|上课|练功|基训|把杆|舞蹈房|排练厅|课上", "上课/排练", 1.5),
    (r"宿舍|睡了|困|睡前|被窝|追剧|宅|洗完澡", "宅宿舍/睡前", 1.5),
    (r"逛街|探店|咖啡|奶茶|图书馆|食堂|散步|出门|商场|公园", "外出日常", 1.2),
]
SCHED = [  # (活动关键词, {场景: 权重})
    (("编导技法", "镜头语言", "舞蹈史", "上课", "基训", "排练", "影像社"), {"上课/排练": 2.0}),
    (("学生会", "例会", "筹备"), {"见老师/学生会正式场合": 1.6, "上课/排练": 0.4}),
    (("睡觉", "准备睡觉", "熬夜", "刚起床", "赖床", "宅在宿舍"), {"宅宿舍/睡前": 2.0}),
    (("和室友出去玩",), {"朋友聚会": 1.8}),
    (("逛街", "探店", "图书馆", "吃午饭", "吃晚饭", "自习"), {"外出日常": 1.6}),
    (("自由时间",), {"外出日常": 0.8, "宅宿舍/睡前": 0.6, "准备见他": 0.4}),
]

def resolve(name):
    """显式场景：名称 / id / 别名（精确优先，其次包含），返回场景名。"""
    if not name: return None
    if name in BY_NAME: return name
    if name in BY_ID: return BY_ID[name]["name"]
    if name in ALIASES: return ALIASES[name]
    for k in sorted(ALIASES, key=len, reverse=True):
        if len(k) >= 2 and k in name: return ALIASES[k]
    for k in LEGACY:
        if name in k: return k
    raise ValueError(f"未知场景 {name!r}；可选类别 {CATEGORIES}，例：{LEGACY}")

def _add(w, k, x): w[k] = w.get(k, 0.0) + x

def from_text(text, w, scale=1.0):
    for pat, sc, x in CHAT_KEYS:
        if text and re.search(pat, text): _add(w, sc, x * scale)

def infer_weights(text="", now=None, activity=None, emo=None, campus_life=None, chat=None):
    """返回 {场景: 权重}（已归一化）。所有来源都是可选的，缺了就跳过。"""
    now = now or datetime.datetime.now(); w = {"外出日常": 0.3}
    if activity is None:
        try: import schedule; activity = schedule.current(now).get("activity", "")
        except Exception: activity = ""
    for keys, add in SCHED:
        if any(k in activity for k in keys):
            for sc, x in add.items(): _add(w, sc, x)
            break
    wd, h = now.weekday(), now.hour
    if (wd in (4, 5) and h >= 17) or (wd == 6 and 11 <= h < 21):   # 周五周六晚、周日白天：更可能见他
        _add(w, "约会", 0.4); _add(w, "准备见他", 0.3)
    if wd >= 5: _add(w, "外出日常", 0.3); _add(w, "朋友聚会", 0.2)
    for item in campus_life or []:                                    # 校园动态：参与/在忙 演出、学生会 -> 正式
        t = item if isinstance(item, str) else item.get("text", "")
        if re.search(r"参与|在忙|帮忙|主持|筹备", t) and re.search(r"演出|晚会|学生会|讲座|论坛|艺术节|迎新|汇报", t):
            _add(w, "见老师/学生会正式场合", 0.6)
        elif re.search(r"排练|剧目|工作坊", t): _add(w, "上课/排练", 0.3)
    if emo:                                                           # 情绪：开心+亲密 -> 更想见他；低落 -> 宅
        pad = emo.get("pad", {}); p = pad.get("pleasure", 0.3); inti = emo.get("intimacy", 60)
        if p > 0.4 and inti >= 50: _add(w, "准备见他", 0.4 * p); _add(w, "约会", 0.3 * p)
        if p < 0: _add(w, "宅宿舍/睡前", -0.8 * p)
        if emo.get("last_label") == "coquet": _add(w, "和他在一起(居家/独处)", 0.4)
    for i, m in enumerate(chat or []):                                # 最近聊天：越近权重越大
        from_text(m, w, 0.4 + 0.6 * (i + 1) / len(chat))
    from_text(text, w, 2.0)                                           # 画面描述本身最强
    s = sum(w.values()); return {k: v / s for k, v in sorted(w.items(), key=lambda kv: -kv[1]) if v > 0}

def blend(weights, jitter=0.05, rng=None):
    """按权重线性混合场景向量（8 维），每维加 ±jitter 抖动并裁到 [0,1]。前 5 维与旧版一致。"""
    rng = rng or random.Random(); tot = sum(weights.values()) or 1.0
    vec = [sum(wt * BY_NAME[sc]["vec"][d] for sc, wt in weights.items()) / tot for d in XDIMS]
    return {d: round(min(1.0, max(0.0, v + rng.uniform(-jitter, jitter))), 3) for d, v in zip(XDIMS, vec)}

def exposure_level(vec): return 0 if vec["exposure"] < 0.18 else 1 if vec["exposure"] < 0.4 else 2 if vec["exposure"] < 0.62 else 3

# mid-cold(0.2–0.4) bottoms: 长裤 50% / 厚连裤袜 30% / 丝袜类 20%
def warmth_text(w, rng=None):
    if w < 0.2: return "天冷，厚实保暖：长款羽绒服或厚大衣、围巾、靴子"
    if w < 0.4:
        rng = rng or random.Random()
        bottom = rng.choices(["长裤", "厚连裤袜", "丝袜类"], weights=[0.5, 0.3, 0.2], k=1)[0]
        return f"偏冷，外套加针织层次，下装{bottom}"
    if w < 0.65: return "温度适中，薄外套或长袖"
    if w < 0.82: return "偏暖，轻薄透气的单层"
    return "炎热，清爽轻薄、注意防晒"

_COLD_SHORT_RE = re.compile(
    r"运动短裙或短裤|短裙或短裤|百褶短裙|运动短裤/紧身裤|运动短裤|短裙裤|热裤|迷你裙|短裤|短裙"
)

def describe(vec, level=None, scene=None, rng=None):
    """vec -> 中文穿搭描述。scene（场景名或 dict）给出具体场景提示；level 0 时有 hint_safe 就用它（如游泳）。"""
    level = exposure_level(vec) if level is None else max(0, min(3, level))
    f, t, s, c = vec["formality"], vec["trendiness"], vec["sportiness"], vec["coziness"]
    parts = []
    sc = BY_NAME.get(scene) if isinstance(scene, str) else scene
    if sc:
        hint = sc.get("hint_safe") if level == 0 and sc.get("hint_safe") else sc["hint"]
        # 冷天：场景库 hint 常写死短裤短裙，替换为长裤以免冬天光腿
        if vec.get("warmth", 1.0) < 0.4:
            hint = _COLD_SHORT_RE.sub("长裤", hint)
        parts.append(f"场景「{sc['name']}」：" + hint)
    if f >= 0.7: parts.append("规矩得体，偏正式：衬衫/针织开衫配半身长裙或西装外套，干净利落")
    elif f >= 0.4: parts.append("精心搭配但不刻意正式")
    if s >= 0.6: parts.append("练功/排练装：贴身练功服或舞蹈T恤配宽松阔腿裤，头发扎起" if not sc or sc["category"] == "校园日常" else "运动感强，方便活动")
    elif s >= 0.35: parts.append("带点运动休闲元素，方便活动")
    if c >= 0.75: parts.append("居家舒适：柔软的家居服或宽松毛衣，素颜或淡妆")
    elif c >= 0.5: parts.append("偏舒适松弛")
    if t >= 0.75: parts.append("时髦有心机：当下流行的单品和配饰，妆容精致")
    elif t >= 0.5: parts.append("有点小时髦")
    # 冷/偏冷始终挂 warmth 文案（含室内）；户外或极热仍挂
    if "warmth" in vec and (vec.get("outdoor", 0) >= 0.3 or vec["warmth"] < 0.4 or vec["warmth"] > 0.82):
        parts.append(warmth_text(vec["warmth"], rng))
    if vec.get("warmth", 1.0) < 0.4:
        parts.append("下装勿短裤短裙光腿，须长裤或连裤袜/丝袜")
    if not parts: parts.append("日常休闲")
    return "穿搭：" + "；".join(parts) + f"。放开程度：{EXPOSURE_TEXT[level]}。整体好看、有分寸，不暴露、不性感化。"

# ---------------- 季节 / 天气（不额外联网；小甜穿搭默认 BDA）----------------
SEASON = {12: .1, 1: .05, 2: .12, 3: .32, 4: .5, 5: .68, 6: .85, 7: .92, 8: .9, 9: .7, 10: .48, 11: .28}

def season_warmth(now=None, weather=None, place="bda"):
    """weather：调用方给的天气文本（如 weather.get("bda")["text"]）。
    没有且 now≈当前时刻时，只读 weather 工具缓存（cache_only，默认 BDA=海淀北舞，不读用户燕园）；
    历史/未来 now 用月份，避免串季。"""
    now = now or datetime.datetime.now()
    if weather is None:
        try:
            import weather as _w
            if abs((now - datetime.datetime.now()).total_seconds()) < 6 * 3600:
                r = _w.get(place or "bda", cache_only=True)
                weather = r.get("text")
        except Exception:
            weather = None
    m = re.search(r"(-?\d+)°C", weather or "")
    return round(min(1.0, max(0.0, (int(m.group(1)) + 5) / 38)), 3) if m else SEASON[now.month]

# ---------------- 向量检索 ----------------
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
_model, _emb = None, None

def scene_text(d): return f"{d['name']}；{'、'.join(d['aliases'])}；{d['hint']}"

def _load_model():
    global _model
    if _model is not None: return _model or None
    if os.environ.get("OUTFIT_EMBED", "1") == "0": _model = False; return None
    try:
        try: import mem; name = mem.cfg()["embed_model"]
        except Exception: name = "BAAI/bge-small-zh-v1.5"
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from sentence_transformers import SentenceTransformer
        try: _model = SentenceTransformer(name, local_files_only=True)
        except TypeError: _model = SentenceTransformer(name)
    except Exception as e:
        print(f"[outfit] 嵌入模型不可用，退回别名匹配：{e}", file=sys.stderr); _model = False
    return _model or None

def scene_embeddings():
    """场景嵌入矩阵（行已归一化）；按 场景文本+模型名 的哈希缓存到 data/outfit_scene_emb.npz。"""
    global _emb
    if _emb is not None: return _emb
    m = _load_model()
    if not m: return None
    import numpy as np
    texts = [scene_text(d) for d in SCENE_LIST]
    h = hashlib.sha1(("\n".join(texts) + str(getattr(m, "model_card_data", ""))[:0]).encode()).hexdigest()
    try:
        z = np.load(EMB_FILE)
        if str(z["hash"]) == h: _emb = z["emb"]; return _emb
    except Exception: pass
    _emb = m.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False).astype("float32")
    try: np.savez(EMB_FILE, emb=_emb, hash=h)
    except Exception as e: print(f"[outfit] 写嵌入缓存失败：{e}", file=sys.stderr)
    return _emb

def _bigrams(t): t = re.sub(r"\W", "", t or ""); return {t[i:i + 2] for i in range(len(t) - 1)} | set(t)

def _lexical(src):
    a = _bigrams(src)
    return [len(a & b) / (len(a) + len(b) or 1) * 2 for b in (_bigrams(scene_text(d)) for d in SCENE_LIST)]

def alias_hits(src):
    """场景名/别名在文本中出现：返回 {下标: 加分(长词更高)}。"""
    out = {}
    if not src: return out
    for i, d in enumerate(SCENE_LIST):
        best = max((len(k) for k in [d["name"]] + d["aliases"] if k and k in src), default=0)
        if best: out[i] = min(1.0, 0.5 + 0.25 * best)
    return out

def _sources(text, activity, chat, campus_life):
    src = []
    if text: src.append((text, 3.0))
    chat = [c for c in (chat or []) if c][-6:]
    if chat:
        ws = [0.4 + 0.6 * (i + 1) / len(chat) for i in range(len(chat))]; k = 1.5 / sum(ws)
        src += [(c, w * k) for c, w in zip(chat, ws)]
    if activity: src.append((activity, 1.0))
    camp = [it if isinstance(it, str) else it.get("text", "") for it in (campus_life or [])]
    camp = [c for c in camp if c]
    if camp: src += [(c, 0.5 / len(camp)) for c in camp]
    return src

# ---------------- 地点上下文 ----------------
PUBLIC_PLACES = ("图书馆", "自习室", "教室", "课堂", "实验室", "食堂", "操场", "体育馆", "校园", "学校", "公司", "办公室",
                 "咖啡馆", "咖啡厅", "书店", "商场", "餐厅", "公园", "街上", "地铁", "车站", "机场", "户外", "野外", "海边",
                 "山上", "景区", "博物馆", "美术馆", "电影院", "演唱会", "外面", "出门", "路上")
HOME_PLACES = ("在家", "家里", "宿舍", "卧室", "床上", "被窝", "沙发", "睡前", "你家", "我们家", "居家")
HOME_HINT = ("家居服", "拖鞋", "睡衣", "居家", "被窝", "睡裙")

def place_context(text):
    """文本里点名的地点：返回 (公共地点列表, 是否点名居家)。"""
    t = text or ""
    return [w for w in PUBLIC_PLACES if w in t], any(w in t for w in HOME_PLACES)

def is_home_scene(d):
    return any(w in d.get("hint", "") for w in HOME_HINT) or d.get("category") == "居家" or "居家" in d["name"]

def place_adjust(text):
    """主文本点名公共地点（且没提居家）→ 场景文本含该地点的加分、居家风场景减分；点名居家则反过来轻微加分。"""
    pub, home = place_context(text); adj = [0.0] * len(SCENE_LIST)
    if not pub and not home: return adj
    for i, d in enumerate(SCENE_LIST):
        st = scene_text(d)
        if pub and not home:
            if any(w in st for w in pub): adj[i] += 0.25
            if is_home_scene(d): adj[i] -= 0.3
        elif home and not pub and is_home_scene(d): adj[i] += 0.1
    return adj

def rank(text="", now=None, activity=None, emo=None, campus_life=None, chat=None, weather=None, k=None, use_embed=True, shot_type=None):
    """所有场景打分并排序：[(场景名, 分数)]。分数 = 加权(余弦 + 0.3*别名命中) / 总权重 + 先验。
    shot_type：imagegen/聊天镜头透传；legs_focus 等对相关场景轻量加分，未知值忽略。"""
    now = now or datetime.datetime.now()
    if activity is None:
        try: import schedule; activity = schedule.current(now).get("activity", "")
        except Exception: activity = ""
    src = _sources(text, activity, chat, campus_life)
    n = len(SCENE_LIST); score = [0.0] * n; tot = sum(w for _, w in src) or 1.0
    emb = scene_embeddings() if use_embed else None
    if emb is not None and src:
        q = _model.encode([QUERY_PREFIX + t for t, _ in src], normalize_embeddings=True, show_progress_bar=False)
        sims = q @ emb.T
        for (t, w), row in zip(src, sims):
            for i in range(n): score[i] += w * float(row[i])
    else:
        for t, w in src:
            for i, x in enumerate(_lexical(t)): score[i] += w * x
    for t, w in src:
        for i, b in alias_hits(t).items(): score[i] += w * 0.3 * b
    score = [x / tot for x in score]
    score = [x + a for x, a in zip(score, place_adjust(text))]   # 地点上下文压过动作词（"图书馆看书"≠居家看书）
    legacy = infer_weights(text, now, activity=activity, emo=emo, campus_life=campus_life, chat=chat)  # 旧推断作为先验
    sw, wd, h = season_warmth(now, weather), now.weekday(), now.hour
    going_out = (wd in (4, 5) and h >= 17) or (wd >= 5 and 10 <= h < 22)
    for i, d in enumerate(SCENE_LIST):
        score[i] += 0.15 * legacy.get(d["name"], 0.0)
        if going_out and d["category"] in ("娱乐", "约会", "旅游景点"): score[i] += 0.02
        if not d.get("climate"): score[i] -= 0.08 * max(0.0, abs(d["vec"]["warmth"] - sw) - 0.35)
    # 镜头轻偏置（不覆盖主文本/聊天；仅微调）
    st = None
    if shot_type:
        try:
            import shot_modes as _sm
            st = _sm.normalize_shot(shot_type) or str(shot_type)
        except Exception:
            st = str(shot_type)
    if st == "legs_focus":
        keys = ("腿", "丝袜", "短裙", "袜", "美腿")
        for i, d in enumerate(SCENE_LIST):
            blob = scene_text(d)
            if any(w in blob for w in keys):
                score[i] += 0.04
    elif st == "hand_closeup":
        keys = ("手", "特写", "细节")
        for i, d in enumerate(SCENE_LIST):
            blob = scene_text(d)
            if any(w in blob for w in keys):
                score[i] += 0.02
    out = sorted(((d["name"], round(x, 4)) for d, x in zip(SCENE_LIST, score)), key=lambda kv: -kv[1])
    return out[:k] if k else out

def choose(ranked, k=8, temperature=0.03, rng=None):
    """top-k 温度采样主场景；返回 (主场景, 混合权重, top 列表[带概率])。"""
    rng = rng or random.Random(); top = ranked[:k]; m = top[0][1]
    ps = [math.exp((s - m) / temperature) for _, s in top]; z = sum(ps); ps = [p / z for p in ps]
    primary = rng.choices([n for n, _ in top], ps)[0]
    pp = dict(zip([n for n, _ in top], ps)); rest = [(n, pp[n]) for n, _ in top[:4] if n != primary]; rz = sum(p for _, p in rest)
    if rz < 0.05: weights = {primary: 1.0}            # 其余候选概率可忽略：不混合
    else:
        share = min(0.3, rz); weights = {primary: round(1 - share, 4)}
        weights.update({n: round(p / rz * share, 4) for n, p in rest if p / rz * share >= 0.01})
    info = [{"name": n, "id": BY_NAME[n]["id"], "category": BY_NAME[n]["category"], "score": s, "p": round(p, 3)} for (n, s), p in zip(top, ps)]
    return primary, weights, info

def _state():
    try:
        import memory; return memory.read_state("demo_contact")
    except Exception: return {}

def _chat(n=6):
    try:
        import memory; c = memory.db(); return [t for _, t in memory.history(c, "demo_contact", n) if t]
    except Exception: return []

def prefer_place_scene(text="", activity=None, chat=None):
    """画面/日程/聊天里的更具体地点别名（如 798）压过粗粒度场景名（如 看展）。
    已含 798/美术馆 等细地点时只升级不降级，禁止再塌成「看展」。
    画面/日程命中优先于聊天旧话，避免「排练」等聊天残留压过当前地点（如长城）。"""
    def _best(blob):
        if not blob:
            return None
        best = None  # (alias_len, name_len, name)
        for d in SCENE_LIST:
            for k in [d["name"]] + list(d.get("aliases") or []):
                if not k or len(k) < 2 or k not in blob:
                    continue
                key = (len(k), len(d["name"]), d["name"])
                if best is None or key > (best[0], best[1], best[2]):
                    best = key
        return best[2] if best else None

    def _str_bits(*xs):
        out = []
        for x in xs:
            if isinstance(x, str) and x:
                out.append(x)
        return out

    def _chat_bits(c):
        if c is None or isinstance(c, bool):
            return []
        if isinstance(c, str):
            return [c] if c else []
        try:
            seq = list(c)
        except TypeError:
            return []
        return [x for x in seq if isinstance(x, str) and x]

    primary_blob = " ".join(_str_bits(text, activity))
    chat_blob = " ".join(_chat_bits(chat))
    primary = _best(primary_blob)
    name = primary or _best(chat_blob)
    if not name:
        return None
    coarse = None
    if text:
        try:
            coarse = resolve(text)
        except ValueError:
            coarse = None
    if coarse and coarse != name:
        # 只允许用画面/日程里的更细地点升级；聊天命中不得把已解析地点抢走
        if name != primary:
            return None
        # 粗名更短或是细名的真子集语义（看展 vs 798艺术区）→ 升级
        if len(coarse) <= len(name):
            return name
        return None
    if not coarse:
        return name
    return None



def wear_request_bits(*blobs):
    """从画面/用户话/聊天里提取显式穿搭要求（黑丝等），拼进最终穿搭句。"""
    blob = " ".join(x for x in blobs if isinstance(x, str) and x.strip())
    if not blob:
        return []
    bits = []
    if "黑丝" in blob:
        bits.append("下装配合黑色丝袜/黑丝（按用户要求）")
    elif "白丝" in blob:
        bits.append("下装配合白色丝袜/白丝（按用户要求）")
    elif "肉丝" in blob or "肉色丝" in blob:
        bits.append("下装配合肉色丝袜（按用户要求）")
    elif "丝袜" in blob or "连裤袜" in blob:
        bits.append("下装配合丝袜/连裤袜（按用户要求）")
    return bits


def plan(text="", scene=None, now=None, rng=None, jitter=0.05, k=8, temperature=0.03, **ctx):
    """返回 {"scene","category","weights","top","vector","level","cap","text","emotion","emotion_text"}。
    scene 显式覆盖推断（名称/id/别名）。ctx 可传 activity/emo/campus_life/chat/weather，不传就自动读取。
    emotion_text：与 STATE.emotion + schedule soft-bias view（PAD→mood/energy）一致的表情加权提示，供 imagegen 缓解僵硬表情。"""
    rng = rng or random.Random(); now = now or datetime.datetime.now()
    # 情绪始终可读（显式 scene 时也要挂表情提示；测试可传 emo=None → emotion.default）
    if "emo" not in ctx or "campus_life" not in ctx:
        st = _state(); ctx.setdefault("emo", st.get("emotion")); ctx.setdefault("campus_life", st.get("campus_life"))
    # 手动要图：user_text 并入 text/chat，保证「黑丝」等进入地点升级与 rank/_sources
    ut = ctx.get("user_text")
    if isinstance(ut, str) and ut.strip():
        ut = ut.strip()
        if ut not in (text or ""):
            text = f"{text} {ut}".strip() if text else ut
        chat0 = ctx.get("chat")
        if isinstance(chat0, (list, tuple)):
            chat_l = [c for c in chat0 if c]
            if ut not in chat_l:
                ctx["chat"] = chat_l + [ut]
        elif not chat0:
            ctx["chat"] = [ut]
    sc = resolve(scene)
    if not sc:
        # 无显式 scene 时：活动/聊天里的地点别名可覆盖过粗的 text（798 > 看展）
        if "chat" not in ctx:
            try:
                ctx["chat"] = _chat()
            except Exception:
                ctx["chat"] = []
        if "activity" not in ctx or ctx.get("activity") is None:
            try:
                import schedule
                ctx["activity"] = schedule.current(now).get("activity", "")
            except Exception:
                ctx.setdefault("activity", "")
        upgrade = prefer_place_scene(text, ctx.get("activity"), ctx.get("chat"))
        if upgrade:
            sc = upgrade
    # rank 只吃白名单参数；shot_type/user_text/slot 等不得再 TypeError 拖进保守兜底
    _RANK_KEYS = ("activity", "emo", "campus_life", "chat", "weather", "k", "use_embed", "shot_type")
    rank_ctx = {k: ctx[k] for k in _RANK_KEYS if k in ctx}
    if sc: primary, weights, top = sc, {sc: 1.0}, [{"name": sc, "id": BY_NAME[sc]["id"], "category": BY_NAME[sc]["category"], "score": None, "p": 1.0}]
    else:
        if "chat" not in rank_ctx: rank_ctx["chat"] = ctx.get("chat") or _chat()
        primary, weights, top = choose(rank(text, now, **rank_ctx), k, temperature, rng)
        # rank 仍可能抽到「看展」：用地点别名再挡一道，禁止细地点塌粗
        upgrade = prefer_place_scene(text, ctx.get("activity"), ctx.get("chat"))
        if upgrade and upgrade != primary and len(upgrade) >= len(primary):
            primary, weights, top = upgrade, {upgrade: 1.0}, [{"name": upgrade, "id": BY_NAME[upgrade]["id"], "category": BY_NAME[upgrade]["category"], "score": None, "p": 1.0}]
    d = BY_NAME[primary]; vec = blend(weights, jitter, rng)
    if not d.get("climate"):   # 本地场景：保暖度跟季节/天气走（室内场景只轻微受影响）
        sw = season_warmth(now, ctx.get("weather")); a = 0.4 + 0.5 * d["vec"]["outdoor"]
        vec["warmth"] = round(min(1.0, max(0.0, (1 - a) * vec["warmth"] + a * sw)), 3)
    caps = [BY_NAME[n]["cap"] for n in weights]; cap = min(d["cap"], max(caps))
    lvl = min(exposure_level(vec), cap)
    # 作息 energy 偏置与 brain.state_block 一致；表情走 schedule soft-bias view（与聊天同槽）
    energy_bias = 0.0
    try:
        import schedule
        energy_bias = float(schedule.current(now).get("energy") or 0)
    except Exception:
        pass
    try:
        import emotion as _em
        emo_raw = ctx.get("emo")
        st_for_bias = ctx.get("st")
        if st_for_bias is None:
            try:
                st_for_bias = _state()
            except Exception:
                st_for_bias = {"emotion": emo_raw} if emo_raw is not None else {}
            if emo_raw is None and isinstance(st_for_bias, dict):
                emo_raw = st_for_bias.get("emotion")
        emo_view, bias = _em.emotion_view_for(st_for_bias or {}, now, emo_raw)
        # prefer life-slot energy from bias feed when present (fatigue vector)
        fu = (bias or {}).get("fields_used") or {}
        if fu.get("energy") is not None:
            try:
                energy_bias = float(fu["energy"])
            except (TypeError, ValueError):
                pass
        emeta = _em.expression_cue(emo_view, energy_bias)
        emeta["schedule_free_room"] = bool((bias or {}).get("free_room"))
        emeta["schedule_reason_codes"] = list((bias or {}).get("reason_codes") or [])
        # persist happy-slot counter when this photo used outing/free smile (not PAD writeback)
        # skip under pytest / explicit st= test doubles — never mutate real STATE from unit tests
        import os as _os
        _persist_ok = (ctx.get("persist_happy_slot", True)
                       and not _os.environ.get("PYTEST_CURRENT_TEST")
                       and ctx.get("st") is None)
        if _persist_ok and (bias or {}).get("free_room") and float(emeta.get("mood") or 0) >= 6.5:
            try:
                import memory as _mem
                cid = "demo_contact"
                st0 = _mem.read_state(cid) or {}
                emo0 = dict(st0.get("emotion") or _em.default())
                emo1 = _em.bump_daily_happy_slot(emo0, bias, now)
                if emo1.get("daily_happy_slots") != emo0.get("daily_happy_slots"):
                    _mem.update_state(cid, emotion=emo1)
            except Exception as _e:
                print(f"[outfit] daily_happy_slot persist skipped: {_e!r}", file=sys.stderr)
    except Exception as e:
        print(f"[outfit] emotion cue 失败，跳过：{e}", file=sys.stderr)
        emeta = {"text": "", "mood": None, "energy": None, "label": None}
    outfit_text = describe(vec, lvl, primary, rng)
    # 用户显式穿搭（黑丝等）挂到最终文案，避免只进 rank 却不进 prompt
    chat_blob = " ".join(
        x for x in (ctx.get("chat") or []) if isinstance(x, str) and x
    )
    extra = wear_request_bits(text, ctx.get("user_text"), chat_blob)
    if extra:
        outfit_text = outfit_text.rstrip("。") + "；" + "；".join(extra) + "。"
    return {"scene": primary, "category": d["category"], "weights": weights, "top": top[:5], "vector": vec,
            "level": lvl, "cap": cap, "text": outfit_text,
            "emotion": emeta, "emotion_text": emeta.get("text") or ""}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("text", nargs="?", default=""); ap.add_argument("--scene")
    ap.add_argument("--text-only", action="store_true", help="只输出穿搭文字（给 imagegen_macbook.sh 用）"); a = ap.parse_args()
    r = plan(a.text, a.scene)
    print(r["text"] if a.text_only else json.dumps(r, ensure_ascii=False, indent=1))
