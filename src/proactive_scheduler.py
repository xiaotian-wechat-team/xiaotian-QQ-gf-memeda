"""主动事件独立扫描；统一发送限制器最多减少 25% 的成功轮数。

Persona SSOT: data/persona_dossier.json.
忙碌只影响文案；未回不冻结、不延长间隔。全量节拍逐个评估，
普通闲聊保留 busy 考虑间隔，但不能挡住新节拍。睡觉、明确暂停、
用户待回复最多避让 10 分钟。这里只返回草稿/事件审计，不发送、不写 STATE。
"""
import argparse, datetime, math, random, re
from config import load
import memory, schedule, proactive, nickname

# 作息活动 → 主动意愿权重（越高越闲）；同时用于推算 Busy B
# 意愿权重：越高越闲。按 life_calendar 活动名/前缀对齐；缺省再看 bucket。
WEIGHTS = {"睡觉": 0, "上课": .2, "编导技法课": .2, "导演基础/镜头语言课": .2, "中外舞蹈史": .25,
           "现代舞基训": .1, "剧目排练": .15, "编创作业排练": .15, "话剧社排练": .2, "影像社拍片/剪片": .3,
           "学生会文艺部例会": .3, "学生会活动筹备": .3, "自习/写编创阐述": .4, "上课/自习": .35,
           "刚起床": .5, "刚起床赶课": .45, "准备睡觉": .95, "准备睡": .95, "自由时间/准备睡": 1.0,
           "回宿舍洗漱/准备睡": 1.05, "回宿舍洗漱": 1.0,
           "熬夜刷手机": .7,
           "吃午饭": 1.1, "食堂午饭": 1.1, "吃晚饭": 1.1, "赖床/吃早午饭": 1.0, "自由时间": 1.4,
           "宅在宿舍追剧": 1.3, "宿舍追剧摸鱼": 1.3, "宿舍刷小红书/抖音": 1.35,
           "宿舍和男友微信闲聊": 1.4, "回宿舍写编创阐述/作业": .55, "图书馆夜场自习": .45,
           "期末复习·图书馆": .25, "期末复习·图书馆夜场": .25, "图书馆自习": .4,
           "去图书馆假装学习": 1.2, "出门逛街/探店": .8, "和室友出去玩": .5,
           "电影公司实习·场记/场务": .2, "电影公司实习·资料整理/粗剪助理": .2, "电影公司实习·跟组半天": .2,
           "排练/课": .15}
# 前缀匹配（活动名以 key 开头）
WEIGHT_PREFIXES = [
    ("电影公司实习", .2), ("期末复习", .25), ("毕业答辩", .15), ("宿舍刷", 1.35),
    ("宿舍和男友", 1.4), ("宿舍追", 1.3), ("回宿舍写", .55), ("回宿舍洗漱", 1.0),
    ("图书馆", .45), ("自习", .45), ("刚起床", .45), ("准备睡", .95),
]
BUCKET_WEIGHTS = {
    "sleep": 0.0, "school_class": 0.22, "school_rehearsal": 0.15, "school_library": 0.4,
    "work_internship": 0.2, "campus_life": 0.9, "campus_fun": 0.7,
    "city_fun": 0.75, "outdoor_outing": 0.7, "weekend_home": 1.25, "travel": 0.6,
}
W_MAX = 1.4
BUSY_ACTS = schedule.CLASS_ACTS | schedule.BUSY_ACTS | {
    "上课/自习", "学生会文艺部例会", "自习/写编创阐述",
    "电影公司实习·场记/场务", "电影公司实习·资料整理/粗剪助理", "电影公司实习·跟组半天",
    "期末复习·图书馆", "期末复习·图书馆夜场", "图书馆夜场自习", "回宿舍写编创阐述/作业",
}
BUSY_BUCKETS = {"school_class", "school_rehearsal", "school_library", "work_internship", "sleep"}
FUTURE = re.compile(r"明天|后天|待会|等会|等下|一会儿|今晚|晚点|周[一二三四五六日末]|下周|考试|面试|答辩|ddl|DDL|作业|开会|体检|比赛|出差|回家")
EMPTY_HOOK = re.compile(r"^(在吗|在么|在不在|你好|嗨|哈喽|hey|hi)\s*[?？!！.。…]*$", re.I)
CARE_TASK = "你突然想起他了，想主动找他。他最近情绪不太好：别调侃，轻轻问一句他现在怎么样，或说你在，不追问细节。"
CONTENT_RULES = (
    "可以话多一点，但不要贫嘴：有新的事实、观察、感受或话题进展就自然分享，平实陈述也成立。"
    "先看最近聊天：同一件事没有进展不要换句话重讲；不要为凑条数编造细节。"
    "不必句句有梗、撒娇、昵称或反问，不必每条结尾都留问题；调侃只顺着已有语境。"
    "之前的问题他还没接，就先分享自己的新内容，不换个问法继续问。"
    "不要根据未回复推断他冷落你、制造委屈或索取回应。"
    "有内容可以正常发，不因他未回而停止报备；只有重复或无内容时输出 [SKIP]。"
)
DECIDE_RULES = (
    "别问「在吗/在么/在不在」，别查岗，别抱怨他不理你。"
    "结合已有信息简短说清一件事，不强求问句或互动钩子。"
    + CONTENT_RULES
)

def _ts(s):
    try: return datetime.datetime.fromisoformat(str(s).replace(" ", "T")[:19]) if s else None
    except ValueError: return None

def weight(now):
    """意愿权重：精确名 → 前缀 → life_calendar bucket → 默认 0.8；再乘睡眠压力衰减。"""
    cur = schedule.current(now)
    act = (cur or {}).get("activity") or ""
    if act in WEIGHTS:
        w = WEIGHTS[act]
    else:
        w = None
        for pref, ww in WEIGHT_PREFIXES:
            if act.startswith(pref) or pref in act:
                w = ww
                break
        if w is None:
            bucket = None
            try:
                import life_calendar as lc
                slot = lc.slot_at(now)
                if slot:
                    bucket = slot.get("bucket")
            except Exception:
                bucket = None
            w = BUCKET_WEIGHTS[bucket] if bucket and bucket in BUCKET_WEIGHTS else .8
    try:
        sp = schedule.sleep_pressure(now)
        w = w * (1.0 - 0.85 * sp)
    except Exception:
        pass
    return max(0.0, float(w))

def busy_value(now, cfg=None):
    """B∈[0,100]：越大越忙。由作息意愿权重反推，忙碌活动再乘 persona busy_factor 抬高。"""
    cfg = cfg or {}
    w = weight(now)
    B = max(0.0, min(100.0, (W_MAX - w) / W_MAX * 100.0))
    act = schedule.current(now).get("activity")
    bucket = None
    try:
        import life_calendar as lc
        slot = lc.slot_at(now)
        if slot:
            bucket = slot.get("bucket")
    except Exception:
        pass
    if act in BUSY_ACTS or (bucket in BUSY_BUCKETS and bucket != "sleep"):
        bf = float(cfg.get("busy_factor", 0.3))
        bf = max(0.0, min(1.0, bf))
        B = 100.0 - (100.0 - B) * bf
    return max(0.0, min(100.0, B))

def consider_interval_sec(B, cfg=None, rng=random):
    """T_consider = t_min * (t_max/t_min)^(B/100) × jitter，再 clamp 到 [t_min, t_max]。"""
    cfg = cfg or {}
    t_min = float(cfg.get("consider_t_min_sec", 5))
    t_max = float(cfg.get("consider_t_max_sec", 1800))
    if t_min <= 0 or t_max < t_min: t_min, t_max = 5.0, 1800.0
    B = max(0.0, min(100.0, float(B)))
    T = t_min * ((t_max / t_min) ** (B / 100.0))
    j = cfg.get("consider_jitter") or [0.7, 1.3]
    jlo, jhi = float(j[0]), float(j[1])
    if jhi < jlo: jlo, jhi = 0.7, 1.3
    T *= rng.uniform(jlo, jhi)
    return max(t_min, min(t_max, T))

def _quiet_bounds(now, cfg):
    """安静时段起止（HH:MM）。终点=当日模糊 wake（优先 life_calendar 落盘，再 schedule.day_plan）。"""
    if now.weekday() >= 5:
        pair = list(cfg.get("quiet_hours_weekend") or cfg.get("quiet_hours") or ["00:30", "10:00"])
    else:
        pair = list(cfg.get("quiet_hours") or ["00:30", "09:00"])
    qs = pair[0] if pair else "00:30"
    qe = None
    try:
        import life_calendar as lc
        day = lc.get_day(now.date()) or {}
        sb = day.get("sleep_bounds") or {}
        wh = sb.get("wake")
        if not wh:
            slots = day.get("slots") or []
            if slots and slots[0].get("bucket") == "sleep":
                wh = slots[0].get("end")
        if wh and isinstance(wh, str) and ":" in wh:
            qe = wh
    except Exception:
        pass
    if not qe:
        try:
            wake = int(schedule.day_plan(now.date())["wake"])
            qe = f"{wake // 60:02d}:{wake % 60:02d}"
        except Exception:
            qe = pair[1] if len(pair) > 1 else ("10:00" if now.weekday() >= 5 else "09:00")
    # 起点轻度日抖动（±12 分），仍落在深夜
    try:
        import hashlib
        h = int(hashlib.md5(f"quiet-start-{now.date().isoformat()}".encode()).hexdigest()[:8], 16)
        off = (h % 25) - 12  # -12..+12
        sh, sm = map(int, qs.split(":"))
        sm2 = sh * 60 + sm + off
        sm2 = max(0, min(2 * 60, sm2))  # clamp 00:00..02:00
        qs = f"{sm2 // 60:02d}:{sm2 % 60:02d}"
    except Exception:
        pass
    return qs, qe


def _quiet(now, cfg):
    """工作日/周末安静时段；终点=当日模糊 wake，非固定整点。"""
    t = now.strftime("%H:%M")
    qs, qe = _quiet_bounds(now, cfg)
    return (qs <= t < qe) if qs < qe else (t >= qs or t < qe)

def resting(now, cfg): return schedule.is_sleeping(now) or _quiet(now, cfg)

def recent(cid, n=30):
    """(sender, text, ts, type) 最近 n 条，旧→新。type 可空（旧行）。"""
    rows = memory.db().execute(
        "SELECT sender,text,ts,type FROM messages WHERE contact=? ORDER BY id DESC LIMIT ?",
        (cid, n)).fetchall()
    return rows[::-1]


def count_unanswered_proactive_turns(rows, st=None):
    """未回主动 = proactive 轮次，不是微信气泡/行数。

    优先 STATE.proactive_unanswered_turns（发送时 +1，他回消息归零）；
    否则数尾部 type=='proactive'；再否则不把普通回复气泡当主动轮。
    一条 surf 配文+图在落库时是 1 行 type=proactive → 计 1。
    """
    st = st or {}
    if rows and rows[-1][0] == "them":
        return 0
    n = st.get("proactive_unanswered_turns")
    if n is not None:
        try:
            return max(0, int(n))
        except (TypeError, ValueError):
            pass
    turns = 0
    for row in reversed(rows or []):
        s = row[0]
        if s == "them":
            break
        if s != "me":
            continue
        typ = row[3] if len(row) > 3 else None
        if typ == "proactive":
            turns += 1
        # 非 proactive 的 me（普通回复气泡）不计入主动轮次
    return turns


def miss_freq_factor(unanswered_turns, cfg, now=None):
    """Compatibility: unanswered messages never slow event scanning or send spacing."""
    return 1.0


def effective_max_per_day(cfg, unanswered_turns, now=None):
    from proactive_limiter import daily_budget
    return daily_budget(cfg, unanswered_turns, now)


def context(rows, now, st=None):
    """从最近聊天提取：他是否有未回、待办、情绪、未回主动轮次。"""
    them = []
    for r in rows:
        s, txt, ts = r[0], r[1], r[2]
        if s == "them":
            them.append((txt, _ts(ts)))
    # Reply failures must not freeze all proactive contact indefinitely. Do not
    # mark the inbound replied: only release this scheduling gate after 10 min.
    last_inbound = _ts(rows[-1][2]) if rows and rows[-1][0] == "them" else None
    pending = last_inbound is not None and 0 <= (now - last_inbound).total_seconds() < 600
    day = [t for t, ts in them if ts is None or (now - ts).total_seconds() < 36 * 3600]
    mentions = [t for t in day if FUTURE.search(t or "")][-2:]
    emos = [nickname.emotion(t)[0] for t, _ in them[-5:]]
    low = sum(e in ("sad", "angry") for e in emos) >= 1 and (not emos or emos[-1] not in ("happy", "coquet"))
    unanswered = count_unanswered_proactive_turns(rows, st)
    return {"pending_from_him": pending, "mentions": mentions, "low_mood": low,
            "emotions": emos, "unanswered_proactive": unanswered,
            "unanswered_proactive_turns": unanswered,
            "last_contact_at": __import__("idle_contact").last_contact(st or {}, rows)}

def _min_gap_sec(cfg):
    """成功主动发送之间的最小间隔（秒）。兼容旧 min_gap_hours。"""
    if "min_gap_sec" in cfg:
        g = cfg["min_gap_sec"]
        if isinstance(g, (list, tuple)) and len(g) >= 2: return float(g[0]), float(g[1])
        return float(g), float(g)
    h = float(cfg.get("min_gap_hours", 3))
    return h * 3600, h * 3600

def gates(now, st, cfg, ctx, beat=None):
    """硬性条件；返回 None 表示可以进入模型决定，否则返回 SKIP 理由。
    beat: proactive_beats 选中的节拍 dict；must/bypass_quiet 可软化部分门槛。
    """
    beat = beat or {}
    # 会话已睡（双方道晚安后静默）：强压制主动，区别于日程睡觉槽
    try:
        import sleep_chat as _sc
        why = _sc.should_suppress_proactive(st, now, beat=beat)
        if why:
            # persist refreshed asleep blob opportunistically
            try:
                import memory as _mem
                _mode, _blob = _sc.refresh(st, now)
                if _blob != (_sc.blob(st)):
                    cid = str((st or {}).get("contact_id") or "")
                    # scheduler may not know cid; caller often demo_contact — update via patch on st only
                    st[_sc.STATE_KEY] = _blob  # in-memory for this tick
            except Exception:
                pass
            return why
    except Exception as exc:
        return f"睡眠状态检查失败，停止主动发送: {exc!r}"
    # A beat drafted for a sleep slot is invalid even if it is retried later.
    slot = beat.get("slot") or {}
    if slot.get("bucket") == "sleep" or slot.get("activity") == "睡觉":
        return "小甜在睡觉"
    if st.get("proactive_paused"):
        return "用户要求暂停主动联系"
    if ctx["pending_from_him"]: return "他最后一条还没回，交给回复流程"
    from proactive_limiter import limit_reason
    return limit_reason(now, st, cfg, ctx, beat)


def topic_weights(cfg, ctx, now):
    w = dict(cfg.get("topics") or {"share_self": .35, "callback": .3, "context": .2, "playful": .15})
    if ctx["mentions"]: w["callback"] = w.get("callback", 0) * 3 + .3
    if ctx["low_mood"]: w["playful"] = 0; w["care"] = .6
    if weight(now) < .5: w["share_self"] = w.get("share_self", 0) * 1.5; w["playful"] = w.get("playful", 0) * .5
    return {k: v for k, v in w.items() if v > 0}

def build_task(topic, ctx):
    t = CARE_TASK if topic == "care" else proactive.TOPIC_TASKS.get(topic, proactive.TOPIC_TASKS["share_self"])
    t = t + DECIDE_RULES
    if topic == "callback" and ctx["mentions"]:
        t += "他之前提到过：" + " / ".join(f"「{m[:40]}」" for m in ctx["mentions"]) + "。挑一件自然地问后续。"
    return t

def bad_draft(text):
    """空招呼 / 在吗 一类无钩子草稿。"""
    if not text or not str(text).strip(): return True
    return bool(EMPTY_HOOK.match(str(text).strip()))

def repeated_draft(text, rows, now):
    """Reject a repeated recent own message, not a new report on the same topic.

    Conservative exact comparison ignores spacing/punctuation only. No semantic
    similarity threshold: a changed meal, time, feeling or fact must remain valid.
    """
    import unicodedata
    def norm(value):
        return "".join(c for c in unicodedata.normalize("NFKC", str(value))
                       if not c.isspace() and unicodedata.category(c)[0] not in "PZ")
    candidate = norm(text)
    if len(candidate) < 6:
        return False
    for row in rows or []:
        if len(row) < 3 or row[0] != "me":
            continue
        stamp = _ts(row[2])
        if stamp is not None and 0 <= (now - stamp).total_seconds() <= 6 * 3600:
            if candidate == norm(row[1]):
                return True
    return False


def next_consider(now, st=None, cfg=None, rng=random, after_send=False, unanswered_turns=None):
    """下一次「考虑」时间（唤醒模型决定，不是必发）。
    unanswered_turns 仅兼容旧调用；不再拉长间隔。
    """
    cfg = cfg if cfg is not None else load().get("proactive", {}); st = st or {}
    if unanswered_turns is None:
        unanswered_turns = int(st.get("proactive_unanswered_turns") or 0)
    freq_f = miss_freq_factor(unanswered_turns, cfg, now)
    t = now
    if resting(t, cfg):
        for _ in range(24 * 6):
            t += datetime.timedelta(minutes=10)
            if not resting(t, cfg): break
        return t + datetime.timedelta(minutes=rng.uniform(0, 30))
    if after_send:
        lo, hi = _min_gap_sec(cfg)
        return now + datetime.timedelta(seconds=rng.uniform(lo, hi) * freq_f)
    B = busy_value(now, cfg)
    return now + datetime.timedelta(seconds=consider_interval_sec(B, cfg, rng) * freq_f)

# 兼容旧名
def next_check(now, st=None, cfg=None, rng=random):
    return next_consider(now, st, cfg, rng)

def tick(now=None, cfg=None, st=None, rows=None, rng=random, force=False, use_llm=True, draft_fn=None, idle_clock=None):
    """一次考虑。永不发送、不写 STATE。
    返回 {action: DRAFT|SKIP, reason, topic, draft, next_consider, next_check, busy, p}。
    action=DRAFT 表示模型决定 send（带钩子草稿）；SKIP 表示不发。
    """
    full = load(); cfg = cfg if cfg is not None else full.get("proactive", {}); cid = full["contact"]["id"]
    now = now or __import__("timeutil").now_local()
    st = st if st is not None else memory.read_state(cid)
    rows = rows if rows is not None else recent(cid)
    ctx = context(rows, now, st)
    B = busy_value(now, cfg)
    turns = int(ctx.get("unanswered_proactive_turns") or 0)
    nxt = next_consider(now, st, cfg, rng, unanswered_turns=turns)
    out = {"next_consider": nxt, "next_check": nxt, "context": ctx, "topic": None, "draft": None,
           "p": 0, "busy": B, "unanswered_turns": turns, "miss_freq_factor": miss_freq_factor(turns, cfg, now)}
    if not cfg.get("enabled", False) and not force:
        return dict(out, action="SKIP", reason="proactive.enabled=false")

    # Enumerate first, limit second. A rejected optional event must never hide another.
    beat = None
    out["events"] = []
    try:
        import proactive_beats as beats
        candidates = beats.due_beats(now, st, cfg)
    except Exception as e:
        candidates = []
        out["beat_error"] = repr(e)
    import idle_contact
    idle = idle_contact.candidate(idle_clock, st, now)
    if idle:
        candidates.insert(0, idle)
    eligible = []
    for candidate in candidates:
        import proactive_media
        if candidate.get("id") != idle_contact.BEAT:
            candidate = proactive_media.prepare(candidate, st, now, cfg)
        if candidate is None:
            continue
        reason = gates(now, st, cfg, ctx, beat=candidate)
        out["events"].append({"key": candidate.get("done_key") or candidate["id"],
                              "status": "limited" if reason else "triggered", "reason": reason})
        if not reason:
            eligible.append(candidate)
    if eligible:
        beat = dict(eligible[0])
        # One natural message covers waking + today's plan; one round of budget.
        if beat["id"] in ("morning", "schedule_brief"):
            other = "schedule_brief" if beat["id"] == "morning" else "morning"
            brief = next((b for b in eligible if b["id"] == other), None)
            if brief:
                beat["merged_keys"] = [brief["done_key"]]
                beat["day_line"] = beat.get("day_line") or brief.get("day_line")
    elif candidates:
        return dict(out, action="SKIP", reason=(out["events"][0]["reason"] if out["events"] else "当前日程不适合拍照，等待合适时机"), beat=None)
    out["beat"] = (beat or {}).get("id")
    out["beat_meta"] = None
    if beat:
        out["beat_meta"] = {k: beat[k] for k in (
            "id", "done_key", "must", "soft_reach", "bypass_quiet", "material", "image", "ended", "tone",
            "surf_target", "hours_apart", "merged_keys", "catch_up", "ask_him", "image_required", "image_slot",
            "idle_clock", "idle_style",
        ) if k in beat}
        sl = beat.get("slot") or {}
        out["beat_meta"]["slot"] = {k: sl.get(k) for k in
            ("start", "end", "activity", "bucket", "tone", "energy", "scene_hints", "photo_ok", "scene_id", "photo_note", "location", "outing_activity_id", "leg") if k in sl}

    g = gates(now, st, cfg, ctx, beat=beat)
    if g: return dict(out, action="SKIP", reason=g)

    if beat and beat["id"] == idle_contact.BEAT:
        out.update(topic="beat:" + idle_contact.BEAT, p=1.0,
                   beat_done_key=beat["done_key"])
        task = idle_contact.task(beat)
        if not use_llm:
            return dict(out, action="DRAFT", reason="清醒期久未联系计时已到（未调模型）", task=task)
        if beat.get("idle_style") == "photo":
            return dict(out, action="DRAFT", reason="久未联系，随手发一张照片", idle_fallback=True,
                        draft="WANT_IMAGE:" + beat["image"]["scene"])
        try:
            import brain
            d = (draft_fn or (lambda: brain.reply(cid, [(r[0], r[1]) for r in rows[-10:]],
                                                 dry=True, now=now, task=task)))()
            if d:
                d = "\n".join(line for line in d.splitlines() if not line.strip().upper().startswith("WANT_IMAGE"))
        except Exception as exc:
            d = None
            out["generation_error"] = type(exc).__name__
        if bad_draft(d) or repeated_draft(d, rows, now):
            d = idle_contact.fallback(rows, idle_clock["anchor_at"])
            out["idle_fallback"] = True
        return dict(out, action="DRAFT", reason="清醒期久未联系计时已到", draft=d)

    if beat:
        import proactive_beats as beats
        topic = beats.beat_topic_name(beat)
        task = beats.build_beat_task(beat, st, now=now)
        if beat.get("image_required"):
            task += " 本轮会附图，配文自然衔接图片与聊天，不解释生图技术或配额。图只来自实际日程中的场景。"
        task += " " + CONTENT_RULES
        if B >= 60:
            task += " 此刻忙，简短说一句近况即可；忙碌或对方未回本身不是沉默理由。"
        out["topic"] = topic
        out["p"] = 1.0
        if not use_llm:
            return dict(out, action="DRAFT", reason=f"节拍 {beat['id']} busy={B:.0f}（未调模型）", task=task)
        import brain
        d = (draft_fn or (lambda: brain.reply(cid, memory.history(memory.db(), cid, 10), dry=True, now=now, task=task)))()
        if d is None:
            return dict(out, action="SKIP", reason=f"节拍 {beat['id']} 模型沉默 [SKIP]", draft=None)
        if repeated_draft(d, rows, now):
            return dict(out, action="SKIP", reason="近期已说过同样内容，等新进展", draft=None)
        if bad_draft(d):
            return dict(out, action="SKIP", reason=f"节拍 {beat['id']} 草稿无钩子，丢弃", draft=d)
        return dict(out, action="DRAFT", reason=f"节拍 {beat['id']} 发送", draft=d,
                    beat_done_key=beat.get("done_key") or beat["id"])

    import proactive_media
    if proactive_media.required(st, now, cfg):
        return dict(out, action="SKIP", reason="等待日程图片机会，不用文字补足配额")
    # 弱化 lottery：默认 probability=0 → 交给模型决定；>0 时仍可作软过滤
    p = float(cfg.get("probability", 0) or 0)
    if p > 0:
        p = min(1.0, p * max(weight(now), 0.05) * (1.3 if ctx["mentions"] else 1))
    out["p"] = p
    if p > 0 and not force and rng.random() >= p:
        return dict(out, action="SKIP", reason=f"软过滤未过 p={p:.2f}")
    w = topic_weights(cfg, ctx, now); topic = rng.choices(list(w), weights=list(w.values()))[0]
    out["topic"] = topic
    if not use_llm:
        return dict(out, action="DRAFT", reason=f"考虑触发 busy={B:.0f}（未调模型）", task=build_task(topic, ctx))
    import brain
    d = (draft_fn or (lambda: brain.reply(cid, memory.history(memory.db(), cid, 10), dry=True, now=now, task=build_task(topic, ctx))))()
    if d is None:
        return dict(out, action="SKIP", reason=f"模型选择沉默 [SKIP] busy={B:.0f}", draft=None)
    if repeated_draft(d, rows, now):
        return dict(out, action="SKIP", reason="近期已说过同样内容，等新进展", draft=None)
    if bad_draft(d):
        return dict(out, action="SKIP", reason=f"草稿无具体钩子，丢弃 busy={B:.0f}", draft=d)
    return dict(out, action="DRAFT", reason=f"模型决定发送 busy={B:.0f}", draft=d)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--now"); ap.add_argument("--force", action="store_true"); ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(); now = datetime.datetime.fromisoformat(a.now) if a.now else None
    r = tick(now, force=a.force, use_llm=not a.no_llm)
    print(f"[scheduler] {r['action']}：{r['reason']}；话题={r['topic']}；busy={r['busy']:.0f}；下次考虑 {r['next_consider']:%Y-%m-%d %H:%M:%S}（只出草稿，不会发送）")
    if r.get("draft"): print("[scheduler] 草稿:", r["draft"])
