"""日程绑定的主动节拍（软情侣分享，非系统报备）。

节拍（见 docs/proactive-scheduler.md「日程节拍」）：
  morning          起床后打招呼（must；忙也发，语气/长度随 bucket/energy 变；文案走模型）
  schedule_brief   今日行程随口报备（must；life_today slots，禁「系统/报备」腔）
  evening          傍晚/晚间打招呼式报备（must；软风格引导，禁固定「晚上好」模板）
  meal             吃饭窗口 + 可附图（可选/概率，非 must；WANT_IMAGE 优先；meal_period 匹配材料）
  surf_share       冲浪库偶发分享（仅小红书/抖音+必须截图；原链入 shares 供续聊评论区）
  activity_end     活动结束后说感受（中段安静不刷）
  daily_chat       当天必须有的闲聊触达（偏空闲槽）
  ask_his_day      高概率（非每日）问他今天/近期安排并记录（好奇心）
  silence_nudge    他久不回时可分享新近况；不因未回强化调侃或索取回应
  pre_sleep        睡前聊天（must；进入准备睡/自由时间末段）

主动附图：baobei/meal/activity_end 经时间钟摆 image_pendulum_allow（默认 p≈0.55）
  决定是否 WANT_IMAGE；纯文字合法。surf_share 仍强制截图。
  meal：钟摆关掉或 image={} → 纯文字（勿硬塞 WANT_IMAGE）；附图须贴当前饭点时段。
schedule_brief / activity_end(ask_him) ↔ ask_his_day：共用 user_day_curiosity.last_asked
  「今日已问安排」（回执绿=真发送成功；仅 ask_him 且真发送成功才 mark，不扫正文）。
activity_end：due_beats 设 ask_him；已问则省略「可问他在干嘛」。

状态：STATE.proactive_beats = {day, done, shared_material_ids, surf_target, shares[]}
只出任务/选题，不发送；发送仍走 proactive_sender。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import re
from typing import Any

# bucket → 冲浪检索词（与 life_calendar / collectors 话题对齐）
BUCKET_QUERIES = {
    "sleep": [],
    "school_class": ["北舞", "舞蹈编导", "上课", "校园"],
    "school_rehearsal": ["排练", "现代舞", "剧目", "北舞"],
    "campus_life": ["校园", "大学生", "食堂", "宿舍"],
    "weekend_home": ["宿舍", "追剧", "宅家", "懒"],
    "city_fun": ["探店", "citywalk", "剧本杀", "Livehouse", "逛展", "北京周末"],
    "outdoor_outing": ["徒步", "郊游", "爬山", "公园", "拍照"],
}
PHOTO_BIAS_Q = {
    "city_fun": "探店 逛展 剧本杀",
    "outdoor_outing": "徒步 郊游 拍照",
    "campus": "校园 北舞",
}
SURF_PLATFORMS = ("xiaohongshu", "douyin")  # 分享取材不含微博
SHARE_LOG_MAX = 40
MEAL_RE = re.compile(r"吃(早|午|晚)?饭|早午饭|午饭|晚饭|食堂|brunch|meal", re.I)
PREP_SLEEP_RE = re.compile(r"准备睡|自由时间|洗漱")
WAKE_HOME_RE = re.compile(r"刚起床|赖床|赶课")
BUSY_BUCKETS = {"school_class", "school_rehearsal", "sleep"}
ACTIVE_BUCKETS = {
    "school_class", "school_rehearsal", "city_fun", "outdoor_outing", "campus_life",
}

# 优先级：数字越小越先考虑（同窗多拍时）
BEAT_PRIORITY = {
    "pre_sleep": 1,
    "morning": 2,
    "schedule_brief": 3,
    "evening": 4,
    "meal": 5,
    "silence_nudge": 6,   # 久不回/未回轮次软找他：高于 surf；soft_reach 破 no_double
    "activity_end": 7,
    "surf_share": 8,
    "daily_chat": 9,
    "ask_his_day": 10,
}

SPEECH_RULES = (
    "规则见 skill；短气泡自然分享，平实陈述即可，不强求钩子或问题；用本轮称呼或省略；"
    "看 gate；禁报备腔；不适合 [SKIP]。"
)


def _hm(s: str) -> int:
    h, m = map(int, str(s).split(":")[:2])
    return h * 60 + m


def _now_min(now: dt.datetime) -> int:
    return now.hour * 60 + now.minute


def _slot_key(slot: dict | None, day: str | None = None) -> str:
    if not slot:
        return ""
    return f"{day or slot.get('date') or ''}|" \
           f"{slot.get('start')}|{slot.get('activity')}"


def _slot_from_day(day: dict | None, now: dt.datetime) -> dict | None:
    """按 now 从同一份 day.slots 取当前槽（与 life_calendar.slot_at 规则一致，但不另读草稿）。"""
    if not day:
        return None
    t = f"{now.hour:02d}:{now.minute:02d}"
    for s in day.get("slots") or []:
        a, b = s.get("start") or "00:00", s.get("end") or "24:00"
        if a <= t < b or (b == "24:00" and a <= t):
            return {**s, "date": day.get("date"), "kind": day.get("kind"),
                    "photo_bias": day.get("photo_bias")}
    return None


def _life(st: dict | None, now: dt.datetime):
    """今日安排可复用 STATE.life_today；此刻槽位按 now 从同一份 today 重算。

    不用 st.life_now（夜里 sync 的 sleep 会粘住），也不另走 lc.slot_at（会与注入/冻结的
    life_today 分歧，导致 morning/activity_end 对不上）。缺 today 时再回退 get_day/slot_at。
    """
    st = st or {}
    today = st.get("life_today") if isinstance(st.get("life_today"), dict) else None
    cur = None
    try:
        import life_calendar as lc
        if not today or str(today.get("date"))[:10] != now.date().isoformat():
            today = lc.get_day(now.date())
        cur = _slot_from_day(today, now)
        if cur is None:
            cur = lc.slot_at(now)
    except Exception:
        cur = st.get("life_now") if isinstance(st.get("life_now"), dict) else None
    return today, cur


def _beats_state(st: dict | None, now: dt.datetime) -> dict:
    raw = (st or {}).get("proactive_beats") or {}
    day = now.strftime("%Y-%m-%d")
    # shares 跨日保留（续聊评论区/选题）；done / 当日 ids / surf_target 按日重置
    shares = list(raw.get("shares") or [])[-SHARE_LOG_MAX:]
    if raw.get("day") != day:
        return {"day": day, "done": {}, "last_slot_key": "", "last_activity": "",
                "shared_material_ids": [], "surf_target": None, "shares": shares}
    done = dict(raw.get("done") or {})
    return {
        "day": day,
        "done": done,
        "last_slot_key": raw.get("last_slot_key") or "",
        "last_activity": raw.get("last_activity") or "",
        "shared_material_ids": list(raw.get("shared_material_ids") or []),
        "surf_target": raw.get("surf_target"),
        "shares": shares,
    }


def mark_beat_done(st_beats: dict, beat_id: str, now: dt.datetime, *,
                   slot: dict | None = None, material_id=None, share=None) -> dict:
    """返回可写回 STATE.proactive_beats 的新 dict。

    share: 可选 {id,platform,source_url,title,image_path,top_comments}，
    写入跨日 shares 日志，供续聊调原链/评论区。
    """
    out = dict(st_beats)
    done = dict(out.get("done") or {})
    done[beat_id] = now.strftime("%H:%M:%S")
    # meal/surf 可一天多次：用带后缀 id
    out["done"] = done
    out["day"] = now.strftime("%Y-%m-%d")
    if slot:
        out["last_slot_key"] = _slot_key(slot, out["day"])
        out["last_activity"] = slot.get("activity") or ""
    if material_id is not None:
        ids = list(out.get("shared_material_ids") or [])
        ids.append(material_id)
        out["shared_material_ids"] = ids[-30:]
    if share and isinstance(share, dict) and (share.get("source_url") or share.get("id") is not None):
        entry = {
            "id": share.get("id") if share.get("id") is not None else material_id,
            "platform": share.get("platform") or "",
            "source_url": share.get("source_url") or "",
            "title": (share.get("title") or "")[:80],
            "image_path": share.get("image_path") or "",
            "top_comments": list(share.get("top_comments") or [])[:5],
            "invitation": dict(share.get("invitation") or {}),
            "share_topic": share.get("share_topic") or "",
            "tags": list(share.get("tags") or []),
            "dedupe_keys": list(share.get("dedupe_keys") or []),
            "shared_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        shares = [s for s in list(out.get("shares") or [])
                  if not (entry["id"] is not None and s.get("id") == entry["id"])]
        shares.append(entry)
        out["shares"] = shares[-SHARE_LOG_MAX:]
    return out


def cfg_beats(pc: dict | None) -> dict:
    d = {
        "enabled": True,
        "max_beat_per_day": 14,
        "morning_window_min": 90,
        "evening_start": "17:00",  # 傍晚 must 报备窗（HH:MM）；文案走模型，禁固定「晚上好」
        "evening_end": "21:30",
        "pre_sleep_window_min": 50,
        "activity_end_grace_min": 30,
        "meal_want_image": True,  # feature on → image_pendulum_allow decides
        "meal_p": 0.45,  # 吃饭可选：按日+槽 start 稳定概率，非 must
        "baobei_want_image": True,  # 报备可附图；钟摆决定本窗是否真要图
        "activity_end_want_image": True,  # 活动结束可附图；钟摆同上
        "proactive_image_p": 0.55,  # 时间钟摆概率（date+30min桶+beat_id 稳定）
        "surf_min_per_day": 1,
        "surf_max_per_day": 5,
        "surf_min_gap_hours": 3,
        "surf_prefer_media": True,
        "surf_require_media": True,  # 分享必须带截图/图，禁纯文字
        "surf_platforms": list(SURF_PLATFORMS),
        "mid_quiet_buckets": ["school_class", "school_rehearsal", "work_internship", "sleep"],
        # 早/晚报备 + 行程 + 日触达 + 睡前；meal 不再 must（午饭可选）
        "must": ["morning", "schedule_brief", "evening", "daily_chat", "pre_sleep"],
        # bypass quiet_hours + mid_quiet_buckets（due_beats 中段安静也认）
        "bypass_quiet_for": ["morning", "schedule_brief", "evening", "pre_sleep"],
        # lottery max_per_day 之外，must 节拍单独计数
        "must_ignore_max_per_day": True,
        "surf_ignore_max_per_day": True,  # 冲浪次数另受 surf_target 约束
        # 久不回/未回主动轮次：非 must，soft_reach 破 no_double；日封顶防刷（无冻结）
        "silence_nudge_enabled": True,
        "silence_min_hours": 3.0,
        "silence_max_per_day": 1,
        "soft_reach": ["silence_nudge"],
        "user_schedule_curiosity_enabled": True,
        "user_schedule_curiosity_p": 0.72,
        "user_schedule_curiosity_min_gap_days": 1,
    }
    d.update((pc or {}).get("beats") or {})
    # 规范化平台列表
    plats = [str(x) for x in (d.get("surf_platforms") or SURF_PLATFORMS) if str(x) in SURF_PLATFORMS]
    d["surf_platforms"] = plats or list(SURF_PLATFORMS)
    lo = int(d.get("surf_min_per_day", 1)); hi = int(d.get("surf_max_per_day", 5))
    if lo < 0: lo = 0
    if hi < lo: hi = lo
    d["surf_min_per_day"], d["surf_max_per_day"] = lo, hi
    return d


def _pendulum_want_image(bcfg: dict, now: dt.datetime, beat_id: str, feature_key: str) -> bool:
    """Feature flag on + time pendulum → whether this beat should attach an image."""
    if not bcfg.get(feature_key, True):
        return False
    try:
        import schedule_fidelity as _sf
        p = float(bcfg.get("proactive_image_p", 0.55))
        return _sf.image_pendulum_allow(now, beat_id, p=p)
    except Exception:
        return True


def _stable_p_allow(key: str, p: float) -> bool:
    """Stable Bernoulli by key (date/slot/beat); used for optional meal etc."""
    try:
        p = float(p)
    except Exception:
        return True
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    h = int(hashlib.md5(str(key).encode()).hexdigest()[:8], 16)
    return (h % 10000) / 10000.0 < p


def surf_day_target(now: dt.datetime, bcfg: dict | None = None, *, st_beats: dict | None = None) -> int:
    """当日冲浪目标次数：在 [surf_min_per_day, surf_max_per_day] 内按日期稳定随机。

    若 st_beats 已写入 surf_target 则复用（便于观测）；否则用日期 hash，无需先写 STATE。
    """
    bcfg = bcfg or cfg_beats(None)
    lo = int(bcfg.get("surf_min_per_day", 1))
    hi = int(bcfg.get("surf_max_per_day", 5))
    if st_beats and st_beats.get("day") == now.strftime("%Y-%m-%d"):
        t = st_beats.get("surf_target")
        if isinstance(t, int) and lo <= t <= hi:
            return t
    span = max(1, hi - lo + 1)
    h = int(hashlib.md5(f"surf-target-{now.date().isoformat()}".encode()).hexdigest()[:8], 16)
    return lo + (h % span)


def mid_activity_quiet(slot: dict | None, bcfg: dict) -> bool:
    if not slot:
        return False
    if slot.get("bucket") in set(bcfg.get("mid_quiet_buckets") or BUSY_BUCKETS):
        # 末尾 grace 分钟留给 activity_end，不算「中段」
        return True
    return False


def _near_slot_end(slot: dict | None, now: dt.datetime, grace_min: int) -> bool:
    if not slot or not slot.get("end"):
        return False
    end = _hm(slot["end"])
    if end == 0:  # 24:00
        end = 24 * 60
    t = _now_min(now)
    return 0 <= (end - t) <= grace_min


def _mins_into_slot(slot: dict | None, now: dt.datetime) -> int:
    if not slot or not slot.get("start"):
        return 0
    return max(0, _now_min(now) - _hm(slot["start"]))


def _wake_slot(today: dict | None) -> dict | None:
    if not today:
        return None
    for s in today.get("slots") or []:
        if s.get("bucket") != "sleep":
            return s
    return None


def _prev_slot(today: dict | None, cur: dict | None) -> dict | None:
    if not today or not cur:
        return None
    slots = list(today.get("slots") or [])
    for i, s in enumerate(slots):
        if s.get("start") == cur.get("start") and s.get("activity") == cur.get("activity"):
            return slots[i - 1] if i > 0 else None
    return None


def _format_day_soft(today: dict | None) -> str:
    if not today:
        return ""
    bits = []
    for s in today.get("slots") or []:
        if s.get("bucket") == "sleep":
            continue
        bits.append(f"{s['start']}-{s['end']} {s['activity']}")
    return "；".join(bits[:5])


def _emotion_hint(st: dict | None, now: dt.datetime | None = None) -> str:
    """短提示：只给 gate 标签，不复述 SPEECH_RULES / 长 tip。

    与 brain 同路径：emotion_view_for（含 apply_daily_caps）+ sweet_mode，
    主动消息不能绕过 DAILY_SWEET_MAX / DAILY_HAPPY_SLOT_MAX。
    """
    try:
        import emotion
        now = now or __import__("timeutil").now_local()
        emo = (st or {}).get("emotion") or emotion.default()
        view, bias = emotion.emotion_view_for(st, now, emo)
        sm = emotion.sweet_mode(
            view, "",
            last_reply_at=(st or {}).get("last_reply_at"),
            now=now,
            schedule_bias=bias,
            consecutive_sweet=int((emo or {}).get("sweet_streak") or 0),
        )
        mode = (sm or {}).get("mode") or "idle"
        bits = [f"gate={mode}", f"prefer_idle={bool(bias.get('prefer_idle'))}"]
        if sm.get("daily_sweet_forced"):
            bits.append("daily_sweet_cap=1")
        if "daily_happy_cap" in (bias.get("reason_codes") or []):
            bits.append("daily_happy_cap=1")
        if bias.get("free_room"):
            bits.append("free_room=1")
        if (emo or {}).get("unanswered_miss_day") == now.strftime("%Y-%m-%d"):
            bits.append("miss_soft=1")
        return "; ".join(bits) + "."
    except Exception:
        return "gate=idle."


def surf_query(slot: dict | None, today: dict | None) -> str:
    parts = []
    if slot:
        parts.append(slot.get("activity") or "")
        parts.extend(slot.get("scene_hints") or [])
        parts.extend(BUCKET_QUERIES.get(slot.get("bucket") or "", []))
    if today:
        pb = today.get("photo_bias") or (slot or {}).get("photo_bias")
        if pb in PHOTO_BIAS_Q:
            parts.append(PHOTO_BIAS_Q[pb])
        if today.get("kind") in ("weekend", "holiday"):
            parts.append("北京周末 探店")
    # 去空去重
    seen, out = set(), []
    for p in parts:
        for tok in re.split(r"[\s/·,，]+", str(p)):
            tok = tok.strip()
            if len(tok) >= 2 and tok not in seen and tok not in ("睡觉", "迷迷糊糊"):
                seen.add(tok); out.append(tok)
    return " ".join(out[:8]) or "大学生 有趣"


def pick_surf_item(slot: dict | None, today: dict | None, *,
                   used_ids=(), prefer_media=True, require_media=False,
                   platforms=None, limit=12, share_state=None, now=None, rng=None) -> dict | None:
    """从 material_store 按日程特征捞一条；无库返回 None。

    分享默认平台仅小红书/抖音；require_media=True 时必须有本地图片（禁纯文字分享）。
    返回含 source_url / top_comments，便于写入 shares 与续聊评论区。
    """
    now = now or __import__("timeutil").now_local()
    q = surf_query(slot, today)
    try:
        import material_store as ms
    except Exception:
        return None
    plats = [p for p in (platforms or SURF_PLATFORMS) if p in SURF_PLATFORMS]
    if not plats:
        plats = list(SURF_PLATFORMS)
    hits: list[dict] = []
    for platform in plats:
        try:
            hits.extend(ms.search(q, platform=platform, limit=limit) or [])
        except Exception:
            continue
    # Always broaden beyond search/top-12; otherwise a batch of ski captures
    # crowds every other topic out before the diversity formula even runs.
    for platform in plats:
        try:
            hits.extend(ms.recent(max(300, limit), platform=platform) or [])
        except Exception:
            continue
    hits = list({(h.get("platform"), h.get("source_url")): h for h in hits}.values())
    if not hits:
        return None
    import share_dedupe
    used = set(used_ids or [])
    scored = []
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", q))
    for h in hits:
        if h.get("expires_at") and float(h["expires_at"]) <= now.timestamp():
            continue
        if h.get("id") in used or share_dedupe.duplicate(h, share_state):
            continue
        if h.get("platform") not in plats:
            continue
        title = (h.get("title") or h.get("text") or "").strip()
        if not title or len(title) < 4:
            continue
        # 跳过明显无趣/搜索页壳
        if any(x in title for x in ("搜索", "热搜榜", "登录", "验证码", "search_result")):
            continue
        url = (h.get("source_url") or "").strip()
        if not url:
            continue
        if "search_result" in url and not (
                h.get("platform") == "xiaohongshu"
                and share_dedupe.canonical_url(url).startswith("xiaohongshu:")):
            continue
        media = h.get("media") or []
        imgs = []
        for m in media:
            if m.get("kind") == "image" and m.get("local_path"):
                try:
                    import pathlib as _pl
                    if _pl.Path(m["local_path"]).is_file():
                        imgs.append(m)
                except Exception:
                    pass
        if require_media and not imgs:
            continue
        if prefer_media and not imgs:
            score_media = 0
        else:
            score_media = 2 if imgs else 0
        blob = title + " " + " ".join(h.get("tags") or [])
        overlap = sum(1 for t in tokens if t in blob)
        scored.append((overlap + score_media, h, imgs))
    if not scored:
        return None
    import share_selection
    history = ((share_state or {}).get("proactive_beats") or {}).get("shares") or []
    selected = share_selection.choose(scored, history, now, rng=rng)
    if selected is None:
        return None  # no fresh topic: skipping is preferable to filling a quota
    h, imgs, selected_topic, probability = selected
    import share_invitation
    invitation = share_invitation.plan(h, history, now)
    path = imgs[0]["local_path"] if imgs else None
    extra = h.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}
    comments = list(extra.get("top_comments") or [])[:5]
    return {
        "invitation": invitation,
        "share_topic": selected_topic,
        "selection_probability": probability,
        "dedupe_keys": sorted(share_dedupe.keys(h)),
        "id": h.get("id"),
        "platform": h.get("platform"),
        "source_url": (h.get("source_url") or "").strip(),
        "title": re.sub(r"\s+", " ", (h.get("title") or h.get("text") or "").strip())[:60],
        "image_path": path,
        "top_comments": comments,
        "query": q,
        "tags": h.get("tags") or [],
    }


def recent_shares(st: dict | None, n: int = 5, *, refresh: bool = True) -> list[dict]:
    """STATE 分享库（跨日）；refresh 时用 material_store 补最新评论/URL。"""
    bs = (st or {}).get("proactive_beats") or {}
    shares = list(bs.get("shares") or [])[-max(1, int(n)):]
    if not refresh:
        return shares
    try:
        import material_store as ms
    except Exception:
        return shares
    out = []
    for s in shares:
        entry = dict(s)
        try:
            it = ms.get(s.get("id")) if s.get("id") is not None else None
            if it is None and s.get("source_url"):
                it = ms.get_by_url(s.get("source_url"))
            if it:
                entry["source_url"] = it.get("source_url") or entry.get("source_url")
                entry["title"] = (it.get("title") or entry.get("title") or "")[:80]
                extra = it.get("extra") or {}
                if isinstance(extra, dict) and extra.get("top_comments"):
                    entry["top_comments"] = list(extra.get("top_comments") or [])[:5]
                media = it.get("media") or []
                for m in media:
                    if m.get("kind") == "image" and m.get("local_path"):
                        entry["image_path"] = m["local_path"]
                        break
        except Exception:
            pass
        out.append(entry)
    return out


def share_prompt_lines(st: dict | None, n: int = 3) -> list[str]:
    """给 brain.state_block：最近分享原链 + 热评，便于续聊评论区/选题。"""
    shares = recent_shares(st, n=n, refresh=True)
    if not shares:
        return []
    lines = ["最近分享（可聊截图/评论区/相关选题；别念系统字段）："]
    for i, s in enumerate(reversed(shares[-n:]), 1):
        plat = s.get("platform") or "?"
        title = (s.get("title") or "无题")[:36]
        url = (s.get("source_url") or "").strip()
        cms = [c for c in (s.get("top_comments") or []) if c][:2]
        bit = f"{i}. [{plat}] {title}"
        if url:
            bit += f" | {url}"
        if cms:
            bit += " | 热评：" + " / ".join(str(c)[:40] for c in cms)
        lines.append(bit)
    return lines



# Meal period bands for material / WANT_IMAGE scene fidelity.
# Prefer slot.meal_period stamped by life_calendar.get_day (same field everywhere).
_MEAL_PERIOD_KW: dict[str, tuple[str, ...]] = {
    "brunch": ("早午饭", "早午餐", "brunch"),
    "breakfast": ("早饭", "早餐", "豆浆", "包子", "油条"),
    "lunch": ("午饭", "午餐", "中饭", "中午吃", "食堂午饭"),
    "dinner": ("晚饭", "晚餐", "夜宵", "宵夜", "晚饭吃"),
}
_MEAL_PERIODS = frozenset({"breakfast", "brunch", "lunch", "dinner"})


def meal_period_for_slot(slot: dict | None, now: dt.datetime | None = None) -> str:
    """Return breakfast|brunch|lunch|dinner|any.

    Reads slot.meal_period (normalized on get_day) first; else keywords / hour band.
    """
    slot = slot or {}
    stamped = slot.get("meal_period")
    if stamped in _MEAL_PERIODS:
        return stamped
    try:
        import life_calendar as lc
        inferred = lc.compute_meal_period(slot, now)
        if inferred in _MEAL_PERIODS:
            return inferred
    except Exception:
        pass
    act = (slot.get("activity") or "")
    hints = " ".join(str(x) for x in (slot.get("scene_hints") or []))
    blob = f"{act} {hints}"
    for period, kws in _MEAL_PERIOD_KW.items():
        if any(k in blob for k in kws):
            return period
    h = (now or __import__("timeutil").now_local()).hour
    if 6 <= h <= 10:
        return "breakfast"
    if 11 <= h <= 14:
        return "lunch"
    if 17 <= h <= 21:
        return "dinner"
    return "any"


def material_fits_meal_period(item: dict | None, period: str) -> bool:
    """True only when material explicitly matches period. Unknown meal materials NOT reused."""
    if not item or period in ("", "any", None):
        return False if item else True
    mp = item.get("meal_period")
    if mp in _MEAL_PERIODS:
        return mp == period
    blob = " ".join([
        str(item.get("title") or ""),
        " ".join(str(t) for t in (item.get("tags") or [])),
        str(item.get("scene") or ""),
    ])
    mat_period = None
    for p, kws in _MEAL_PERIOD_KW.items():
        if any(k in blob for k in kws):
            mat_period = p
            break
    if mat_period is None:
        return False  # unknown → do not reuse
    return mat_period == period


def meal_image_hint(slot: dict | None, today: dict | None,
                    rng: random.Random | None = None,
                    now: dt.datetime | None = None) -> dict:
    """吃饭附图：优先当前槽 WANT_IMAGE generate；仅时段明确匹配的材料可复用。

    scene / meal_period 始终取自当前 slot（get_day 归一化字段），避免晚上塞午饭图。
    未知时段材料不复用 → 回落当前槽 generate。
    """
    now = now or __import__("timeutil").now_local()
    period = meal_period_for_slot(slot, now)
    hints = list((slot or {}).get("scene_hints") or [])
    act = (slot or {}).get("activity") or "吃饭"
    # scene MUST come from current slot — never a stale noon scene at night
    scene = (hints[0] if hints else act)[:20]
    # Prefer current-slot generate
    try:
        import schedule_shot as _ss
        hint = _ss.image_hint_for_beat("meal", slot, today, rng=rng)
        if isinstance(hint, dict):
            if not hint.get("scene"):
                hint["scene"] = scene
            hint["meal_period"] = period
            return hint
    except Exception:
        pass
    # Matching material only (unknown rejected)
    item = pick_surf_item(slot, today, prefer_media=True)
    if item and item.get("image_path") and material_fits_meal_period(item, period):
        return {"mode": "material", "image_path": item["image_path"],
                "material_id": item["id"], "scene": scene, "title": item["title"],
                "meal_period": period}
    return {"mode": "want_image", "scene": scene, "image_path": None, "material_id": None,
            "shot_type": "hand_closeup", "source": "meal_fallback", "meal_period": period}


def due_beats(now: dt.datetime, st: dict | None, pc: dict | None = None) -> list[dict]:
    """返回此刻可触发的节拍（未 done），已按优先级排序。"""
    bcfg = cfg_beats(pc)
    if not bcfg.get("enabled", True):
        return []
    today, cur = _life(st, now)
    from schedule_execution import effective, blocked
    cur = effective(cur)
    bs = _beats_state(st, now)
    done = bs["done"]

    out: list[dict] = []
    tmin = _now_min(now)
    wake = _wake_slot(today)
    bucket = (cur or {}).get("bucket")
    act = (cur or {}).get("activity") or ""

    def add(bid: str, **extra):
        if blocked(extra.get('ended')) or blocked(extra.get('slot')):
            return
        # meal/surf 允许一天多次：用窗口键
        key = extra.get("done_key") or bid
        if key in done:
            return
        # Busy/quiet affect wording, never event discovery.
        bypass_quiet = True
        must = bid in set(bcfg.get("must") or [])
        soft_reach = bid in set(bcfg.get("soft_reach") or [])
        out.append({
            "id": bid,
            "done_key": key,
            "must": must,
            "soft_reach": soft_reach,
            "bypass_quiet": bypass_quiet,
            "slot": cur,
            "today": today,
            "priority": BEAT_PRIORITY.get(bid, 50),
            **extra,
        })

    # morning：醒来后至傍晚都可触发；错过早窗用当前近况补充
    if wake and bucket != "sleep" and "morning" not in done:
        wstart = _hm(wake["start"])
        if wstart <= tmin < 17 * 60:
            busy = bucket in BUSY_BUCKETS or bool(WAKE_HOME_RE.search(act) and "赶" in act)
            add("morning", tone="busy_short" if busy else "lazy",
                note=f"wake={wake['start']} act={act}",
                catch_up=tmin > wstart + int(bcfg.get("morning_window_min", 90)))

    # schedule_brief：起床后 window 内、morning 已发或并行；附图经时间钟摆可选
    if wake and bucket != "sleep" and "schedule_brief" not in done:
        wstart = _hm(wake["start"])
        if wstart <= tmin < 17 * 60:
            ask_him = True
            try:
                import schedule_fidelity as _sf
                ask_him = not _sf.already_asked_his_day_today(st, now)
            except Exception:
                ask_him = True
            img = {}
            if _pendulum_want_image(bcfg, now, "schedule_brief", "baobei_want_image"):
                try:
                    import schedule_shot as _ss
                    img = _ss.image_hint_for_beat("schedule_brief", cur, today)
                except Exception:
                    try:
                        import shot_modes as _sm
                        img = _sm.baobei_image_hint(cur, today)
                    except Exception:
                        img = {}
            add("schedule_brief", day_line=_format_day_soft(today), image=img, ask_him=ask_him)

    # meal：每个吃饭槽生成候选，是否发送统一由 limiter 决定
    if cur and MEAL_RE.search(act) and bucket != "sleep":
        key = f"meal@{cur.get('start')}"
        img = meal_image_hint(cur, today, now=now) if _pendulum_want_image(
            bcfg, now, "meal", "meal_want_image"
        ) else {}
        add("meal", done_key=key, image=img)

    # activity_end：上一槽是活跃活动，刚进入新槽或当前槽末尾
    # ask_him 与 schedule_brief 同日回执：「计划问安排」；已绿则 False（不扫正文）
    prev = _prev_slot(today, cur)
    grace = int(bcfg.get("activity_end_grace_min", 30))
    ask_him_end = True
    try:
        import schedule_fidelity as _sf
        ask_him_end = not _sf.already_asked_his_day_today(st, now)
    except Exception:
        ask_him_end = True

    def _activity_end_image(ended_slot):
        if not _pendulum_want_image(bcfg, now, "activity_end", "activity_end_want_image"):
            return {}
        if ended_slot and ended_slot.get("photo_ok") is False:
            return {}
        try:
            import schedule_shot as _ss
            return _ss.image_hint_for_beat("activity_end", ended_slot or cur, today)
        except Exception:
            return {}

    if prev and prev.get("bucket") in ACTIVE_BUCKETS and prev.get("bucket") != "sleep":
        # 刚切换：当前已不是 prev，且距 prev.end 在 grace 内
        pend = _hm(prev.get("end") or "0:0")
        if pend == 0:
            pend = 24 * 60
        if 0 <= (tmin - pend) <= grace:
            key = f"activity_end@{prev.get('start')}"
            add("activity_end", done_key=key, ended=prev,
                slot=cur, image=_activity_end_image(prev), ask_him=ask_him_end)
    elif cur and cur.get("bucket") in ACTIVE_BUCKETS and _near_slot_end(cur, now, grace):
        # 槽末预告式感受（仍算 activity_end，key 用当前 start）
        key = f"activity_end@{cur.get('start')}"
        add("activity_end", done_key=key, ended=cur, image=_activity_end_image(cur),
            ask_him=ask_him_end)

    # Daily target is a ceiling, never a quota to catch up; retries use the same gate.
    target = surf_day_target(now, bcfg, st_beats=bs)
    bs["surf_target"] = target
    from proactive_limiter import surf_limit_reason
    if (bucket != "sleep"
            and not surf_limit_reason(now, st or {}, pc or {})
            and _mins_into_slot(cur, now) >= 15):
        item = pick_surf_item(
            cur, today,
            used_ids=bs.get("shared_material_ids"),
            share_state=st, now=now,
            prefer_media=bool(bcfg.get("surf_prefer_media", True)),
            require_media=bool(bcfg.get("surf_require_media", True)),
            platforms=bcfg.get("surf_platforms") or SURF_PLATFORMS,
        )
        if item and item.get("image_path") and item.get("source_url"):
            key = f"surf_share@{item['id']}"
            add("surf_share", done_key=key, material=item, surf_target=target)

    # daily_chat：白天空闲、今天还没有任何非 meal 的闲聊触达
    chatish = [k for k in done if k in ("morning", "daily_chat", "schedule_brief", "evening", "pre_sleep")
               or str(k).startswith("surf_share") or str(k).startswith("activity_end")]
    if (not chatish and bucket != "sleep"
            and tmin >= (_hm(wake["start"]) if wake else 10 * 60) + 120
            and tmin < 22 * 60):
        add("daily_chat")

    # ask_his_day：对他生活/课表的好奇心（与 schedule_brief/activity_end ask_him 同日互斥）
    if (bucket != "sleep"
            and "ask_his_day" not in done
            and tmin >= (_hm(wake["start"]) if wake else 10 * 60) + 90
            and tmin < 21 * 60):
        try:
            import schedule_fidelity as _sf
            brief_asks = any(
                b.get("id") in ("schedule_brief", "activity_end") and b.get("ask_him")
                for b in out
            )
            if brief_asks:
                pass  # 同刻报备/活动结束会问安排 → 跳过独立 ask_his_day
            elif _sf.should_ask_his_day(now, st, pc):
                add("ask_his_day")
        except Exception:
            pass

    # silence_nudge：久不回（小时）或未回主动轮次过软阈值 → 软撒娇找他（按轮次非气泡）
    if (bcfg.get("silence_nudge_enabled", True)
            and bucket != "sleep"):
        sil_done = sum(1 for k in done if str(k) == "silence_nudge" or str(k).startswith("silence_nudge@"))
        sil_max = int(bcfg.get("silence_max_per_day", 1) or 1)
        if sil_done < sil_max:
            try:
                import emotion as _emo
                h = _emo.hours_apart((st or {}).get("last_inbound_at") or (st or {}).get("last_reply_at"), now)
            except Exception:
                h = None
            min_h = float(bcfg.get("silence_min_hours", 3.0) or 3.0)
            turns = 0
            thr = int((pc or {}).get("unanswered_miss_at", 5) or 5)
            try:
                import emotion as _emo
                thr = int(_emo.miss_threshold(pc or {}, now))
                import proactive_scheduler as _ps
                # 无 rows 时用 STATE 轮次计数
                turns = int((st or {}).get("proactive_unanswered_turns") or 0)
            except Exception:
                turns = int((st or {}).get("proactive_unanswered_turns") or 0)
            by_hours = h is not None and h >= min_h
            by_turns = turns >= thr
            if by_hours or by_turns:
                add("silence_nudge",
                    hours_apart=round(float(h), 2) if h is not None else None,
                    silence_min_hours=min_h,
                    unanswered_turns=turns,
                    miss_threshold=thr)

    # evening：傍晚/晚间 must 报备（风格引导走模型；不写死「晚上好」）
    if bucket != "sleep" and "evening" not in done and not PREP_SLEEP_RE.search(act):
        try:
            est = _hm(str(bcfg.get("evening_start") or "17:00"))
            eend = _hm(str(bcfg.get("evening_end") or "21:30"))
        except Exception:
            est, eend = 17 * 60, 21 * 60 + 30
        if est <= tmin < eend:
            add("evening", note=f"evening_window={bcfg.get('evening_start')}-{bcfg.get('evening_end')}")

    # pre_sleep
    if bucket != "sleep" and "pre_sleep" not in done:
        # 在准备睡类活动，或距今晚 sleep 槽开始 ≤ window
        sleep_slot = None
        for s in (today or {}).get("slots") or []:
            if s.get("bucket") == "sleep" and _hm(s.get("start") or "0") == 0:
                continue
        # 当天最后一个非睡槽末 / 显式准备睡
        slots = [s for s in (today or {}).get("slots") or [] if s.get("bucket") != "sleep"]
        last = slots[-1] if slots else None
        win = int(bcfg.get("pre_sleep_window_min", 50))
        if PREP_SLEEP_RE.search(act) or (
            last and cur and cur.get("start") == last.get("start")
            and _near_slot_end(cur, now, win)
        ):
            add("pre_sleep")

    if bcfg.get("care_checkin_enabled") and bucket != "sleep" and not PREP_SLEEP_RE.search(act):
        windows = bcfg.get('care_windows')
        if windows:
            for start, end, theme in windows:
                if _hm(start) <= tmin < _hm(end):
                    add('care_checkin', done_key=f'care_checkin@{start}', care_theme=theme)
                    break
        else:
            care_hour = 11 + int(hashlib.sha256(f"care:{now.date()}".encode()).hexdigest()[:8], 16) % 7
            if care_hour * 60 <= tmin < 21 * 60:
                add("care_checkin")
    # Dedicated daily-life photo opportunity, still bounded by the shared budget.
    if (pc or {}).get("image_share_min_ratio") and cur and bucket != "sleep" and not PREP_SLEEP_RE.search(act):
        add("life_photo", done_key=f"life_photo@{cur.get('start')}")
    if (pc or {}).get("daily_contact_range"):
        seed = f"contact-order:{now.date()}:{sorted(done)}"
        for b in out:
            if b["id"] == "pre_sleep":
                b["priority"] = 0
            elif b['id'] in ('morning', 'schedule_brief', 'meal'):
                b['priority'] = .1 if b['id']=='morning' else .2 if b['id']=='schedule_brief' else .3
            else:
                h = hashlib.sha256((seed + b["id"]).encode()).hexdigest()
                weights = bcfg.get('selection_weights') or {}
                if weights:
                    import math
                    u = (int(h[:8], 16) + 1) / (2**32 + 1)
                    b['priority'] = 1 - math.log(u) / max(.01, float(weights.get(b['id'], 1)))
                else:
                    b["priority"] = 1 + int(h[:8], 16) / 2**32
    out.sort(key=lambda b: b["priority"])
    return out


def pick_beat(now: dt.datetime, st: dict | None, pc: dict | None = None) -> dict | None:
    due = due_beats(now, st, pc)
    return due[0] if due else None


def build_beat_task(beat: dict, st: dict | None = None, now: dt.datetime | None = None) -> str:
    """给 brain.reply(task=...) 的内部指令。"""
    bid = beat["id"]
    slot = beat.get("slot") or {}
    today = beat.get("today") or {}
    act = slot.get("activity") or "发呆"
    tone = slot.get("tone") or ""
    now = now or __import__("timeutil").now_local()
    emo = _emotion_hint(st, now)
    head = f"主动微信。此刻「{act}」（{tone}; {slot.get('bucket')}）。{emo}\n"

    if bid == "life_photo":
        body = "把此刻日程中的一个生活细节随手拍给男朋友，配一两句感受；不背行程表，不冒充旅游网图。"
    elif bid == "care_checkin":
        body = ("主动关心男朋友，像真的对他的生活好奇。结合最近聊天和已知日程，"
                "在关心状态、接着他提过的小事、好奇今天的新鲜事、随口问候里只选一个。"
                "最多一个具体、轻松的问题；也可以只接住他的辛苦，不强行提问。"
                "近几轮已经问过的问题别再问，不固定问吃饭/干嘛/忙不忙，不盘问、不催回复。"
                "不知道他的安排就问，不编造课题进展或活动。1–2句。")
        if beat.get('care_theme'):
            body += '本时段可参考：' + beat['care_theme'] + '；只是选题方向，结合聊天决定，不当作已发生事实。'
    elif bid == "morning":
        if beat.get("catch_up"):
            body = "早间漏了联系，现在随口说此刻近况；禁止说刚起床、早安或伪造早上已经发过。"
        elif beat.get("tone") == "busy_short":
            body = ("刚起床/赶课，很忙也可以：用你自己的话跟他打个早起招呼或报一声醒了/赶课，"
                    "1～2 条气泡，可带一句今日要干嘛的碎片，别展开。"
                    "禁止固定套话模板（勿只会「早上好」）；句式走模型，可撒娇可平淡可嘴损。")
        else:
            body = ("周末/赖床刚起：懒洋洋打个招呼，可提一句待会可能干嘛，"
                    "别写成行程表。禁止固定套话模板（勿只会「早上好」）；"
                    "变量：可话多一点但≤3气泡。")
        if beat.get("merged_keys"):
            body += f" 顺带一句今天安排，合成一轮，勿分开重复招呼：{beat.get('day_line') or _format_day_soft(today)}。"
    elif bid == "schedule_brief":
        line = beat.get("day_line") or _format_day_soft(today)
        img = beat.get("image") or {}
        ask_him = beat.get("ask_him")
        if ask_him is None:
            try:
                import schedule_fidelity as _sf
                ask_him = not _sf.already_asked_his_day_today(st, now)
            except Exception:
                ask_him = True
        ask_bit = ("可问他今天干嘛。" if ask_him
                   else "今天已经问过他安排了，别再问他干嘛，只说你自己的。")
        body = (f"像跟对象随口说今天大概安排，不要列表腔。内部参考（勿原文粘贴）：{line}。"
                f"挑 1～2 个亮点说人话。{ask_bit}禁止「报备/行程如下」。")
        if img.get("mode") == "want_image" and img.get("scene"):
            shot = img.get("shot_type") or "hand_closeup"
            scene = str(img["scene"])[:24]
            body += (f" 另起一行 WANT_IMAGE:[{shot}]{scene}。"
                     "配图是随手拍（系统已选好镜头：手部/第一视角等），别解释镜头术语。")
        # expose for sender mark (beat may be rebuilt without due_beats ask_him)
        beat["ask_him"] = bool(ask_him)
    elif bid == "meal":
        img = beat.get("image") or {}
        if img.get("mode") == "material" and img.get("image_path"):
            body = (f"在吃饭（{act}）。配一句短的吃饭碎碎念；系统会附带一张已有的生活/美食参考图。"
                    "不要说「发一张图给你系统」，就像随手拍的。不要再写 WANT_IMAGE。")
        elif img.get("mode") == "want_image" and img.get("scene"):
            # scene already from current slot in meal_image_hint
            scene = str(img.get("scene") or act)[:24]
            shot = img.get("shot_type") or "hand_closeup"
            body = (f"在吃饭（{act}）。短分享一口吃的/心情，并另起一行 WANT_IMAGE:[{shot}]{scene}。"
                    "配图镜头系统已选（手部/第一视角/对镜等），别解释镜头术语。配文别假精致。")
        else:
            # pendulum off / empty image {} → pure-text meal chatter (align schedule_brief)
            body = (f"在吃饭（{act}）。短分享一口吃的/心情，纯文字就好，别提配图。"
                    "配文别假精致。")
    elif bid == "surf_share":
        m = beat.get("material") or {}
        cms = [c for c in (m.get("top_comments") or []) if c][:2]
        cm_hint = (("评论区有人说：" + " / ".join(str(c)[:30] for c in cms) + "。")
                   if cms else "")
        url = (m.get("source_url") or "").strip()
        url_hint = (f"必须把原链单独贴出来：{url}。" if url
                    else "库里若有原链必须贴出；没有原链就别硬编。")
        body = (f"你刚在{m.get('platform') or '网上'}刷到有趣内容，想转给他（必须带原帖截图+原链）。"
                f"内容钩子（改写成你的话，别复制广告腔）：「{m.get('title','')}」。{cm_hint}"
                "要和你此刻日程气质贴合；1～3 条气泡完整安利/吐槽一次即可；可吐槽可安利。"
                f"系统会附原帖图片，不要再写 WANT_IMAGE；{url_hint}")
        import share_invitation
        body += share_invitation.prompt(m.get("invitation") or {})
    elif bid == "activity_end":
        ended = beat.get("ended") or slot
        ask_him = beat.get("ask_him")
        if ask_him is None:
            ask_him = True
            try:
                import schedule_fidelity as _sf
                ask_him = not _sf.already_asked_his_day_today(st, now)
            except Exception:
                ask_him = True
        ask_bit = ("可问他在干嘛。" if ask_him
                   else "今天已经问过他安排了，别再问他在干嘛，只说你自己的感受。")
        body = (f"「{ended.get('activity')}」刚告一段落（或快结束）。"
                f"用很短的话讲刚才的感受/一个细节。{ask_bit}别写复盘报告。")
        img = beat.get("image") or {}
        if img.get("mode") == "want_image" and img.get("scene"):
            shot = img.get("shot_type") or "pov_first_person"
            scene = str(img["scene"])[:24]
            body += (f" 若合适可另起一行 WANT_IMAGE:[{shot}]{scene}。"
                     "像随手拍的，别解释镜头术语。")
        # expose for sender mark (same day-ask receipt as schedule_brief)
        beat["ask_him"] = bool(ask_him)
    elif bid == "daily_chat":
        body = (f"今天还没怎么找他，现在（{act}）空闲点，主动丢一个有钩子的闲聊："
                "结合今日日程或天气/他提过的事；别查岗。"
                "禁止默认问他实习（他档案无实习）。")
    elif bid == "ask_his_day":
        known = ""
        try:
            blob = ((st or {}).get("user_day_curiosity") or {})
            if blob.get("summary"):
                known = f"你记得他提过：{str(blob.get('summary'))[:40]}；可顺着问变化，别审讯。"
        except Exception:
            pass
        body = (f"你有点好奇他今天/这阵子怎么安排的（课表、出门、忙啥），想随口问问。"
                f"{known}"
                "用女朋友的好奇心，1～2 气泡；一次只问一个开放式问题；别查岗、别列表审问。"
                "禁止默认他有实习/问「实习忙不忙」。"
                "问完即可，系统会记录他的回答。")
    elif bid == "silence_nudge":
        body = (
            f"这会儿想到他，可以分享此刻「{act}」里尚未说过的一件近况或感受。"
            "他没回复不代表冷落你，不提高调侃/委屈强度，不催读、不查岗、不问「在吗」。"
            "已有信息没有新进展就 [SKIP]；不为重新引起注意编故事、换话题追问或反复撒娇。"
        )
    elif bid == "evening":
        body = ("傍晚/晚上这会儿找他打个照面式报备：像刚忙完/回宿舍/吃完饭后随口说一句近况或心情，"
                "可勾一句他那边怎样，别写成行程表/查岗。"
                "禁止固定套话模板（勿只会「晚上好」）；句式必须由模型生成，可软可损可平淡；1～3 气泡。")
    elif bid == "pre_sleep":
        body = ("快睡或洗漱准备睡了：跟他道晚安/嘱咐早点休息，可回一句今天印象深的小事。"
                "黏一点但别哭腔；1～3 条气泡。禁止只会复读固定晚安模板。")
    else:
        body = f"结合此刻「{act}」自然分享一句。"

    return head + body + "\n" + SPEECH_RULES


def beat_topic_name(beat: dict) -> str:
    return f"beat:{beat['id']}"
