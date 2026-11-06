"""语境向量 → 昵称。pick(message, state) -> (nickname, vector, reason)。
目标向量 = 基线 + STATE(mood/energy，有 life_* 时用 emotion_view_for 同槽 soft-bias) 调整 + 消息情绪调整；余弦相似度取 top-k，温度 softmax 采样，
默认锁定 state["active_nickname"]（合语境则不换）；换新时排除 recent_nicknames。难过/认真时排除 tease 高的昵称。
按性别过滤（只用 n + config.user_gender 的昵称）；role=tease 的调侃称呼只在调侃语境（tease_context）可选。
tier=core（小熊/维尼/🐻）日常高权重；tier=extension 低概率；禁错误姓氏称呼。"""
import json, math, random, pathlib, re
DIMS = ["sweet", "tease", "intimacy", "serious", "playful"]
LIB = pathlib.Path(__file__).resolve().parents[1] / "data" / "nicknames.json"
RECENT_N, TOP_K, TEMP = 30, 8, 0.03
BASE = [.32, .48, .36, .25, .55]  # 日常情侣：嘴损/玩闹偏多，甜与亲密默认压低；想你时由 coquet 抬
EMO = {  # 情绪: (关键词, 目标向量调整)
 "sad":    (["难过","伤心","哭","累","挂了","失败","烦","崩溃","委屈","不开心","心情不好","郁闷","失眠","病","疼","难受"], [.25,-.6,.25,.45,-.35]),
 "serious":(["认真","正经","说真的","严肃","跟你说个事","我想好了","问你","怎么办","重要","决定"], [0,-.5,.15,.6,-.3]),
 "angry":  (["生气","气死","滚","讨厌你","哼","不理你","烦死","别理我"], [.25,-.45,.2,.35,-.2]),
 "coquet": (["想你","想我","想你啦","好想你","想我想","抱抱","抱一下","想抱抱","亲亲","想亲亲","陪我","呜呜","要你","人家","贴贴","晚安","摸摸","爱你","么么","想你了","想我了"], [.38,-.08,.38,-.05,.08]),
 "happy":  (["哈哈","开心","耶","太好了","嘻嘻","好耶","笑死","赢了","过了"], [.05,.15,0,-.1,.3]),
 "tease":  (["笨蛋","傻","猪","你个","略略略","切","菜"], [-.05,.35,0,-.15,.25]),
}
_lib, _mtime = None, None
META = {}  # name -> {"gender": n/m/f, "role": None|"tease"}
TEASE_ROLE_MIN_TEASE, TEASE_ROLE_MIN_MOOD = .45, 6
TEASE_ROLE_BONUS = .015  # 调侃语境下给调侃称呼一点加分，否则向量上几乎进不了 top-k
BANNED_NAMES = {"王某某"}  # demo: 禁止误用的姓氏称呼（公开快照）

# tier：core=姓名/熊/维尼相关（日常高权重）；extension=甜称等（低概率）。勿与 TTS/avatar/schedule WEIGHTS 混淆。
CORE_WEIGHT_FLOOR = 1.25
EXTENSION_WEIGHT_CAP = 0.55
EXTENSION_PICK_P = 0.12  # 换称时抽到 extension 库的概率；其余走 core（硬分界）

def _tier_of(n):
    t = n.get("tier")
    if t in ("core", "extension"):
        return t
    if n.get("core") or n.get("near_core"):
        return "core"
    return "extension"

def _meta_row(n):
    tier = _tier_of(n)
    w = float(n.get("weight") or (CORE_WEIGHT_FLOOR if tier == "core" else EXTENSION_WEIGHT_CAP))
    if tier == "core":
        w = max(w, CORE_WEIGHT_FLOOR) if n.get("name") not in ("明明", "小杨") else w
    else:
        w = min(w, EXTENSION_WEIGHT_CAP)
    return {
        "gender": n.get("gender", "n"),
        "role": n.get("role"),
        "weight": w,
        "core": tier == "core",
        "tier": tier,
    }
def library():
    """Reload when nicknames.json mtime changes; on a bad reload keep the previous good library."""
    global _lib, _mtime
    m = LIB.stat().st_mtime
    if _lib is None or m != _mtime:
        try:
            raw = json.loads(LIB.read_text())["nicknames"]
            lib = []
            for n in raw:
                if n.get("name") in BANNED_NAMES:
                    continue
                vec = n.get("vec") or {}
                if not all(d in vec for d in DIMS):
                    continue  # skip incomplete rows
                lib.append((n["name"], [float(vec[d]) for d in DIMS]))
            META.clear(); META.update({
                n["name"]: _meta_row(n) for n in raw
                if n.get("name") not in BANNED_NAMES and n.get("vec") and all(d in n["vec"] for d in DIMS)
            })
            _lib, _mtime = lib, m
        except Exception:
            if _lib is None: raise
    return _lib

NEG = ["不", "没", "别", "不是", "没有", "不再"]
_QUOTED = re.compile(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"|‘[^’]*’')
_PARTS = re.compile(r"[，,。.!！?？；;\n]+")
_SUBJECT = re.compile(r"导师|老师|老板|同事|朋友|室友|同学|父母|爸妈|爸爸|妈妈|他们|她们|他|她|我|你")
_FEEDBACK = re.compile(
    r"你(?:怎么|为什么|干嘛|好|太|很|这么|那么|有点|有些|真的|又|一直|总是|在|是在|就|只会|都|对我|说话|回我|刚才|刚刚|变得|越来越)*"
    r"(?:凶|敷衍|冷漠|冷淡|不耐烦|不关心|不理我)|"
    r"(?:你在)?敷衍我|(?:我(?:感觉|觉得|有点)?|感觉|觉得)?被(?:你)?敷衍|"
    r"(?:你)?(?:对我|跟我)(?:好|太|很|这么|那么|有点)?凶"
)


def relationship_feedback(text):
    """Direct complaints about our response need care, even without 情绪 words."""
    for part in _PARTS.split(_QUOTED.sub("", text or "")):
        if re.search(r"(?:他说|她说|导师说|老师说|朋友说|室友说|比如|假如|如果|假设)", part):
            continue
        if re.search(r"(?:不觉得|没觉得|不是说|没有说|别说).{0,4}你", part):
            continue
        match = _FEEDBACK.search(part)
        if match:
            subjects = list(_SUBJECT.finditer(part[:match.start()]))
            if subjects and subjects[-1].group() not in ("我", "你"):
                continue
            return True
    return False


def feedback_prompt_tip(text):
    if not relationship_feedback(text):
        return ""
    return ("关系反馈：他觉得刚才被凶或敷衍。先具体接住他的感受，承认刚才语气不妥并放软回应；"
            "本轮收起嘴损，不说自找的/活该，不怪他敏感，不用反问或继续挖苦代替回应。")


def is_relationship_conflict(text):
    """Only anger directed at this relationship may reduce its intimacy value."""
    t = _QUOTED.sub("", text or "")
    return bool(re.search(r"讨厌你|不理你|别理我|(?:对你|跟你|生你的).{0,5}(?:气|烦)|"
                          r"你(?:把我|让我|可把我)?(?:气死|气坏|惹火)|你.{0,3}(?:滚|闭嘴)", t))


def _hit(msg, k):
    """Keyword hit, ignoring occurrences directly preceded by a negation (不难过/没生气)."""
    i = msg.find(k)
    while i != -1:
        if not any(msg[max(0, i - len(n)):i] == n for n in NEG): return True
        i = msg.find(k, i + 1)
    return False


def _feeling_hit(msg, keyword):
    """Require the keyword to describe a feeling, with the right subject."""
    for part in _PARTS.split(msg):
        if not _hit(part, keyword):
            continue
        for match in re.finditer(re.escape(keyword), part):
            prefix, suffix = part[:match.start()], part[match.end():]
            if any(prefix.endswith(n) for n in NEG):
                continue
            if keyword == "病" and not (prefix.endswith("生") or re.match(r"了|得|着|倒|痛", suffix)):
                continue  # 病毒/病理/病例 are topics, not illness disclosures.
            if keyword == "菜" and not re.search(r"(?:我|你)(?:真|太|好|这么|那么|有点|很|也太)*$", prefix):
                continue  # 吃菜/买菜/做菜 do not ask to be teased.
            if keyword == "切" and re.match(r"菜|肉|片|块|丝|水果|换|实|割", suffix):
                continue
            if keyword == "猪" and re.match(r"肉|蹄|肝|排|骨|血|耳", suffix):
                continue
            subjects = list(_SUBJECT.finditer(prefix))
            if subjects and subjects[-1].group() not in ("我", "你"):
                # "我被导师气死了" describes the user's emotion, unlike 导师生气了.
                if not re.search(r"我(?:被|让)", prefix):
                    continue
            if re.search(r"(?:他说|她说|导师说|老师说|比如|假如|如果|假设)", prefix):
                continue
            return True
    return False


def emotion(msg):
    msg = _QUOTED.sub("", msg or "")
    hits = {e: sum(_feeling_hit(msg, k) for k in kws) for e, (kws, _) in EMO.items()}
    if relationship_feedback(msg):
        hits["sad"] = max(hits.values(), default=0) + 1
    best = max(hits, key=hits.get)
    return (best if hits[best] else "neutral"), hits

def enrich_state_mood(state, now=None):
    """Overlay mood/energy from emotion.emotion_view_for when life_* is present.

    Unit tests that only pass {mood,energy} are unchanged. Chat/proactive STATE
    with life_today/life_now gets the same slot-bound soft-bias as chat/image.
    """
    st = dict(state or {})
    if not isinstance(st.get("life_today"), dict) and not isinstance(st.get("life_now"), dict):
        return st
    try:
        import emotion as _em
        import datetime as _dt
        now = now or _dt.datetime.now()
        emo = st.get("emotion") or _em.default()
        view, bias = _em.emotion_view_for(st, now, emo)
        energy_bias = 0.0
        fu = (bias or {}).get("fields_used") or {}
        if fu.get("energy") is not None:
            try:
                energy_bias = float(fu["energy"])
            except (TypeError, ValueError):
                energy_bias = 0.0
        else:
            try:
                import schedule
                energy_bias = float(schedule.current(now).get("energy") or 0)
            except Exception:
                pass
        d = _em.derived(view, energy_bias)
        st["mood"] = d["mood"]
        st["energy"] = d["energy"]
    except Exception:
        pass
    return st


def target(msg, state, now=None):
    state = enrich_state_mood(state, now)
    v, why = BASE[:], []
    mood = state.get("mood")            # 0~10
    if isinstance(mood, (int, float)):
        m = (mood - 5) / 5               # -1~1
        v[1] += .15 * m; v[4] += .15 * m; v[0] += .05 * -min(m, 0) * 2
        why.append(f"mood={mood}")
    en = state.get("energy")            # 0~10
    if isinstance(en, (int, float)):
        v[4] += .2 * (en - 5) / 5; why.append(f"energy={en}")
    # hour-band soft lean (same clock as emotion.hour_soft_bias; no 白天/晚上宣告)
    try:
        import emotion as _em
        import datetime as _dt
        hb = _em.hour_soft_bias(now or _dt.datetime.now())
        tl = float(hb.get("tease_lean") or 0)
        pl = float(hb.get("playful_lean") or 0)
        sl = float(hb.get("sweet_lean") or 0)
        if tl or pl or sl:
            v[0] += 0.10 * sl          # sweet
            v[1] += 0.14 * tl          # tease
            v[2] += 0.10 * sl          # intimacy
            v[4] += 0.14 * pl          # playful
            why.append(f"hour={hb.get('hour_band')}")
    except Exception:
        pass
    emo, _ = emotion(msg)
    if emo != "neutral":
        v = [a + b for a, b in zip(v, EMO[emo][1])]
    why.append(f"情绪={emo}")
    return [min(1, max(0, x)) for x in v], emo, why

def cos(a, b):
    return sum(x*y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)) or 1)

def allowed_genders(user_gender=None):
    """小甜叫用户：只用中性(n)和与用户性别一致的昵称。user_gender 缺省读 config.user_gender（默认 m）。"""
    if user_gender is None:
        try:
            from config import load
            user_gender = load().get("user_gender", "m")
        except Exception:
            user_gender = "m"
    return {"n", user_gender}

def tease_context(t, emo, state, now=None):
    """调侃称呼（role=tease，如 大冤种/大聪明/甲方/王老师/领导/老板）只在：非难过/认真/生气、目标 tease 维度高、心情好。"""
    state = enrich_state_mood(state, now)
    if emo in ("sad", "serious", "angry"): return False
    if t[1] < TEASE_ROLE_MIN_TEASE or t[3] >= .5: return False
    mood = state.get("mood")
    if isinstance(mood, (int, float)): return mood >= TEASE_ROLE_MIN_MOOD
    return emo in ("happy", "tease")  # 不知道心情时，只在对方明显在闹/开心时

def _nick_ok(name, vec, emo, tease_ok, ok_g):
    """active 昵称在本轮语境是否还能用（性别/调侃/难过过滤）。"""
    meta = META.get(name, {})
    if name in BANNED_NAMES:
        return False
    if meta.get("gender", "n") not in ok_g:
        return False
    if meta.get("role") == "tease" and not tease_ok:
        return False
    if emo in ("sad", "serious", "angry") and vec[1] > .25:
        return False
    return True

def pick(message, state, rng=random, user_gender=None, sticky=True, now=None):
    """默认 sticky：复用 STATE.active_nickname，避免每轮换狗/猪/崽。
    sticky=False 或 active 不合语境时才重新采样（并避开 recent）。
    now：交给 soft-bias mood overlay（与聊天/生图同槽）。"""
    state = enrich_state_mood(state, now)
    t, emo, why = target(message, state, now=now)
    recent = set((state.get("recent_nicknames") or [])[-RECENT_N:])
    lib = library(); ok_g = allowed_genders(user_gender)
    tease_ok = tease_context(t, emo, state, now=now)
    lib_d = dict(lib)
    active = (state.get("active_nickname") or "").strip() if sticky else ""
    active_day = (state.get("active_nickname_day") or "").strip()
    import datetime as _dt
    # sticky day follows soft-bias `now` (tests / replay); wall clock if omitted
    _now = now or _dt.datetime.now()
    today = (_now.date() if isinstance(_now, _dt.datetime) else _dt.date.today()).isoformat()
    # 同一天默认锁定称呼，避免一天换七八次；难过/认真时若 active 不合规再换
    if active and active in lib_d and _nick_ok(active, lib_d[active], emo, tease_ok, ok_g):
        if (not active_day) or active_day == today or emo in ("sad", "serious", "angry"):
            v = lib_d[active]
            reason = f"{'，'.join(why)}；锁定 active_nickname={active}（日={active_day or today}）"
            if META.get(active, {}).get("role") == "tease":
                reason += "；调侃称呼(role=tease)"
            return active, dict(zip(DIMS, v)), reason
    cand = [(n, v) for n, v in lib if n not in recent and n not in BANNED_NAMES and META.get(n, {}).get("gender", "n") in ok_g]
    if not tease_ok:
        cand = [(n, v) for n, v in cand if META.get(n, {}).get("role") != "tease"]
    if emo in ("sad", "serious", "angry"):  # 不调侃
        cand = [(n, v) for n, v in cand if v[1] <= .25] or cand
    if not cand:
        cand = [(n, v) for n, v in lib if META.get(n, {}).get("gender", "n") in ok_g]
    # 硬分界：多数时候只在 core 里采；小概率改走 extension（甜称等）
    has_core = any(META.get(n, {}).get("tier") == "core" for n, _ in cand)
    has_ext = any(META.get(n, {}).get("tier") == "extension" for n, _ in cand)
    pick_tier = None
    if has_core and has_ext:
        pick_tier = "extension" if rng.random() < EXTENSION_PICK_P else "core"
        tiered = [(n, v) for n, v in cand if META.get(n, {}).get("tier") == pick_tier]
        if tiered:
            cand = tiered
    def bonus(n):
        b = TEASE_ROLE_BONUS if tease_ok and META.get(n, {}).get("role") == "tease" else 0
        # weight：同 tier 内再排序（core 内部小熊等更高）
        w = float(META.get(n, {}).get("weight") or 1.0)
        return b, w
    scored = []
    for n, v in cand:
        b, w = bonus(n)
        scored.append((cos(t, v) * w + b, n, v))
    top = sorted(scored, reverse=True)[:TOP_K]
    w = [math.exp((s - top[0][0]) / TEMP) for s, _, _ in top]
    s, n, v = rng.choices(top, weights=w)[0]
    reason = f"{'，'.join(why)}；目标={dict(zip(DIMS, [round(x,2) for x in t]))}；cos={s:.3f}（top{TOP_K}温度{TEMP}采样，避开最近{len(recent)}个）"
    if META.get(n, {}).get("role") == "tease": reason += "；调侃称呼(role=tease)"
    return n, dict(zip(DIMS, v)), reason

def role(name):
    library(); return META.get(name, {}).get("role")

def remember(state, name):
    """返回更新后的 recent_nicknames 列表（调用方写回 STATE.json）。"""
    return ((state.get("recent_nicknames") or []) + [name])[-RECENT_N:]
