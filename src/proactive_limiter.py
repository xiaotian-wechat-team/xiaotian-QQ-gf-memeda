"""One send limiter. Persona SSOT: data/persona_dossier.json.

Share discovery and every send/retry use the same gate. Budget counts successful rounds,
not bubbles/images. The configured daily budget includes shares; legacy fixed budgets
retain their unanswered reduction when daily_contact_range is absent.
"""
import datetime
import math
import hashlib

ROUTINE_BEATS = frozenset(('morning', 'schedule_brief', 'meal', 'pre_sleep'))


def is_routine(beat):
    """Only scheduler event IDs qualify; generated wording cannot bypass caps."""
    return isinstance(beat, dict) and beat.get('id') in ROUTINE_BEATS


def routine_reason(now, st, beat):
    bs = st.get('proactive_beats') or {}
    done = bs.get('done', {}) if bs.get('day') == now.date().isoformat() else {}
    bid = beat.get('id')
    keys = [beat.get('done_key') or bid]
    if bid in ('morning', 'schedule_brief'):
        keys = ['morning', 'schedule_brief']
    if any(key in done for key in keys):
        return '本次日常报备已发送或正在补发'
    last = st.get('last_proactive_at')
    if last:
        try:
            if (now-datetime.datetime.fromisoformat(last)).total_seconds() < 120:
                return '日常报备短间隔不足 120 秒'
        except (ValueError, TypeError):
            pass
    return None


def rhythm_probability(now, cfg):
    """Evening bias, interpolated between configured local-hour anchors."""
    points = sorted((float(h), float(p)) for h, p in
                    cfg.get('contact_rhythm', {}).get('probability_by_hour', []))
    if not points:
        return 1.0
    hour = now.hour + now.minute / 60
    if hour <= points[0][0]:
        return points[0][1]
    for (a, pa), (b, pb) in zip(points, points[1:]):
        if hour <= b:
            return pa + (pb-pa) * (hour-a) / (b-a)
    return points[-1][1]


def rhythm_reason(now, st, cfg, beat=None):
    """Shared by discovery/send/retry; polling cannot repeatedly reroll luck."""
    opts = cfg.get('contact_rhythm') or {}
    if not opts.get('enabled'):
        return None
    used = int(st.get('proactive_count', 0)) if st.get('proactive_day') == now.date().isoformat() else 0
    for before, ceiling in opts.get('cumulative_caps', []):
        if now.hour + now.minute / 60 < float(before):
            if used >= int(ceiling):
                return f'分时额度已达 {ceiling} 轮，留给稍晚联系'
            break
    # Closing goodnight follows sleep time, never the optional daytime lottery.
    if (beat or {}).get('id') == 'pre_sleep':
        return None
    window = max(1, int(opts.get('window_minutes', 30)))
    minute = (now.hour * 60 + now.minute) // window * window
    anchor = now.replace(hour=minute//60, minute=minute%60, second=0, microsecond=0)
    probability = min(1., max(0., rhythm_probability(anchor, cfg)))
    key = f'contact-rhythm:{now.date()}:{minute}'
    draw = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 2**32
    if draw >= probability:
        return f'本时段随机留白（触发概率 {probability:.0%}，晚间更高）'
    return None


def daily_budget(cfg, unanswered, now=None):
    if cfg.get("daily_contact_range"):
        lo, hi = [max(0, int(v)) for v in cfg["daily_contact_range"]]
        hi = min(max(lo, hi), int(cfg.get("max_per_day", 5)))
        lo = min(lo, hi)
        day = (now or __import__("timeutil").now_local()).date().isoformat()
        draw = int(hashlib.sha256(("contact-budget:" + day).encode()).hexdigest()[:8], 16)
        return lo + draw % (hi - lo + 1)
    base = max(1, int(cfg.get("max_per_day", 12)))
    u = max(0, int(unanswered or 0))
    opts = cfg.get("limiter") or {}
    mid = min(.25, max(0., float(opts.get("moderate_reduction", 1 / 6))))
    high = min(.25, max(mid, float(opts.get("max_reduction", .25))))
    reduction = 0 if u <= 3 else mid if u <= 6 else high
    return max(1, math.ceil(base * (1 - reduction) - 1e-9))


def reserved_keys(now, st):
    bs = st.get("proactive_beats") or {}
    done = bs.get("done", {}) if bs.get("day") == now.date().isoformat() else {}
    reserved = []
    if now.hour < 17 and not any(k in done for k in ("morning", "schedule_brief")):
        reserved.append("morning")
    if now.strftime("%H:%M") < "21:30" and "evening" not in done:
        reserved.append("evening")
    if "pre_sleep" not in done:
        reserved.append("pre_sleep")
    return reserved


def surf_limit_reason(now, st, cfg):
    """Successful shares only; applies across days and to delayed outbox sends."""
    import proactive_beats as pb
    bc = pb.cfg_beats(cfg)
    bs = pb._beats_state(st, now)
    limit = pb.surf_day_target(now, bc, st_beats=bs)
    stamps = []
    for share in bs.get("shares") or []:
        try:
            stamps.append(datetime.datetime.fromisoformat(share["shared_at"]).replace(tzinfo=None))
        except (KeyError, ValueError, TypeError):
            continue
    # done also covers older receipts without a material/share log entry.
    done = bs.get("done") or {}
    count = max(sum(str(k).startswith("surf_share") for k in done),
                sum(t.date() == now.date() for t in stamps))
    if count >= limit:
        return f"分享今日上限 {limit} 次（不补足配额）"
    for key, value in done.items():
        if str(key).startswith("surf_share"):
            try:
                stamps.append(datetime.datetime.fromisoformat(bs["day"] + " " + value))
            except (TypeError, ValueError):
                pass
    gap = max(0., float(bc.get("surf_min_gap_hours", 3)))
    if stamps and (now.replace(tzinfo=None) - max(stamps)).total_seconds() < gap * 3600:
        return f"分享间隔不足 {gap:g} 小时"
    return None


def limit_reason(now, st, cfg, ctx, beat=None):
    beat = beat or {}
    import idle_contact
    if beat.get("id") == idle_contact.BEAT:
        if not (cfg.get("idle_contact") or {}).get("enabled"):
            return "久未联系计时已关闭"
        current = dict(st)
        if ctx.get("last_contact_at"):
            current["last_reply_at"] = idle_contact.iso(ctx["last_contact_at"])
        return idle_contact.stale_reason(beat, current)
    if is_routine(beat):
        return routine_reason(now, st, beat)
    if beat.get("id") == "surf_share":
        reason = surf_limit_reason(now, st, cfg)
        if reason:
            return reason
    last = st.get('last_budgeted_proactive_at', st.get("last_proactive_at"))
    if st.get("last_idle_contact_at"):
        last = max(last or "", st["last_idle_contact_at"])
    if last:
        try:
            elapsed = (now - datetime.datetime.fromisoformat(last)).total_seconds()
        except (TypeError, ValueError):
            elapsed = float("inf")
        gap = max(0, int((cfg.get("limiter") or {}).get("merge_window_sec", 600)))
        spacing = (cfg.get("limiter") or {}).get("spacing_range_sec")
        rhythm = cfg.get('contact_rhythm') or {}
        if rhythm.get('enabled'):
            try:
                last_hour = datetime.datetime.fromisoformat(last).hour
                key = 'evening_spacing_sec' if last_hour >= int(rhythm.get('evening_hour', 17)) else 'day_spacing_sec'
                spacing = rhythm.get(key, spacing)
            except (TypeError, ValueError):
                pass
        if spacing:
            lo, hi = sorted(max(gap, int(v)) for v in spacing)
            draw = int(hashlib.sha256(("contact-gap:" + str(last)).encode()).hexdigest()[:8], 16)
            gap = lo + draw % (hi - lo + 1)
        if elapsed < gap:
            return f"合并窗口：距上次主动不足 {gap} 秒"
    turns = ctx.get("unanswered_proactive_turns", ctx.get("unanswered_proactive", 0))
    budget = daily_budget(cfg, turns, now)
    used = int(st.get("proactive_count", 0)) if st.get("proactive_day") == now.date().isoformat() else 0
    if cfg.get("daily_contact_range"):
        if used >= budget:
            return f"今天主动联系已达 {budget} 轮（含分享）"
        bs = st.get("proactive_beats") or {}
        done = bs.get("done", {}) if bs.get("day") == now.date().isoformat() else {}
        care_count = sum(k == 'care_checkin' or str(k).startswith('care_checkin@') for k in done)
        care_needed = max(0, int((cfg.get('beats') or {}).get('care_min_rounds', 1)) - care_count)
        # Reserve conversational opportunities; shares cannot consume all ten.
        if ((cfg.get("beats") or {}).get("care_checkin_enabled")
                and care_needed and now.hour < 22
                and used >= budget - care_needed and beat.get("id") not in ("care_checkin", "pre_sleep")):
            return f"留 {care_needed} 轮主动关心你的近况"
        return rhythm_reason(now, st, cfg, beat)
    reserved = reserved_keys(now, st)
    bid = beat.get("id")
    protected = (bid in reserved or (bid == "schedule_brief" and "morning" in reserved))
    # Protect already-reserved reports if a new unanswered tier lowers today's budget.
    ceiling = max(budget, min(int(cfg.get("max_per_day", 12)), used + len(reserved))) if protected else budget
    if used >= ceiling:
        return f"今天次数已满（轻量上限 {budget}）"
    if not protected and used >= max(0, budget - len(reserved)):
        return f"保留早晚睡前报备额度（余 {budget - used} 轮）"
    return None


def record_success(cid, item, now):
    """Idempotent round receipt, shared by immediate sends and outbox retries.

    Incoming messages and receipt counters share memory's contact lock. A reply
    arriving while generation/send is running cannot be overwritten by a stale st.
    """
    import memory
    import proactive_beats as pb
    receipt = str(item["id"])
    meta = item.get("meta") or {}
    bm = meta.get("beat_meta") or {}
    routine = is_routine(bm)
    idle = bm.get("id") == "idle_contact"
    key = meta.get("beat_done_key") or bm.get("done_key") or bm.get("id")
    def change(st):
        receipts = dict(st.get("proactive_receipts") or {})
        if receipt in receipts:
            return {}
        receipts[receipt] = now.isoformat(sep=" ", timespec="seconds")
        receipts = dict(sorted(receipts.items(), key=lambda x: x[1])[-256:])
        day = now.date().isoformat()
        state_day = str(st.get("proactive_day") or "")
        count = int(st.get("proactive_count", 0)) if state_day == day else 0
        stamp = receipts[receipt]
        kv = {"proactive_receipts": receipts,
              "last_proactive_at": max(str(st.get("last_proactive_at") or ""), stamp)}
        if state_day <= day:
            kv.update(proactive_day=day, proactive_count=count + (0 if routine or idle else 1))
        if idle:
            old = st.get("proactive_idle") or {}
            n = int(old.get("count", 0)) if old.get("day") == day else 0
            if str(old.get("day") or "") <= day:
                kv["proactive_idle"] = {"day": day, "count": n + 1}
            kv["last_idle_contact_at"] = max(str(st.get("last_idle_contact_at") or ""), stamp)
        if routine:
            old = st.get('proactive_routine') or {}
            if str(old.get('day') or '') <= day:
                n = int(old.get('count', 0)) if old.get('day') == day else 0
                kv['proactive_routine'] = {'day': day, 'count': n + 1}
            if 'last_budgeted_proactive_at' not in st:
                kv['last_budgeted_proactive_at'] = st.get('last_proactive_at')
        elif not idle:
            kv['last_budgeted_proactive_at'] = max(str(st.get('last_budgeted_proactive_at') or ''), stamp)
        import proactive_media
        kv.update(proactive_media.receipt_patch(st, item, now))
        import share_dedupe
        kv.update(share_dedupe.receipt_patch(st, item, now))
        # Recovery may happen after a real inbound or on a later day: never undo it.
        if str(st.get("last_inbound_at") or "") < stamp:
            kv["proactive_unanswered_turns"] = int(st.get("proactive_unanswered_turns", 0)) + 1
            import sleep_chat as sc
            if sc.is_goodnight_text(item.get("text")):
                _, sleep = sc.note_outbound(item["text"], st, now)
                kv[sc.STATE_KEY] = sleep
        bs = pb._beats_state(st, now)
        if key and str((st.get("proactive_beats") or {}).get("day") or "") <= day:
            material = bm.get("material") or {}
            bs = pb.mark_beat_done(bs, key, now, slot=bm.get("slot"),
                                  material_id=material.get("id"), share=material or None)
            for merged in bm.get("merged_keys") or []:
                bs = pb.mark_beat_done(bs, merged, now, slot=bm.get("slot"))
            kv["proactive_beats"] = bs
            import schedule_fidelity as sf
            if sf.beat_meta_marks_ask_his_day(bm, done_key=key):
                kv["user_day_curiosity"] = sf.mark_asked_his_day(
                    st.get("user_day_curiosity"), now, question=(item.get("text") or "")[:80])
        return kv
    return memory.mutate_state(cid, change)


def record_outbox_success(item, now):
    """Only the primary proactive round carries a receipt; mirror sends do not."""
    original = item.get("proactive_round")
    if original:
        return record_success(str(item.get("contact") or "demo_contact"), original, now)


def inbound_patch(text, now):
    import re
    patch = {"proactive_unanswered_turns": 0,
             "last_inbound_at": now.strftime("%Y-%m-%d %H:%M:%S")}
    if re.search(r"^(?:你)?(?:先|暂时)?(?:别打扰我|不要打扰我|别主动找我|不要主动找我)[了啦。！!\s]*$", text.strip()):
        patch["proactive_paused"] = True
    elif re.search(r"^(?:你)?(?:可以找我了|恢复主动聊天|恢复主动联系|可以主动找我了)[。！!\s]*$", text.strip()):
        patch["proactive_paused"] = False
    return patch


def retry_reason(item, now):
    """Recheck primary proactive retries against current state, not their old slot."""
    original = item.get("proactive_round")
    if not original:
        return None
    if original.get("expires") and now > datetime.datetime.fromisoformat(original["expires"]):
        return "主动草稿已过期"
    import memory
    import proactive_scheduler as ps
    from config import load
    st = memory.read_state(str(item.get("contact") or "demo_contact"))
    if str(original["id"]) in (st.get("proactive_receipts") or {}):
        return "主动轮次已确认发送"
    bm = dict((original.get("meta") or {}).get("beat_meta") or {})
    bm.pop("slot", None)
    reason = ps.gates(now, st, load().get("proactive", {}),
                    {"pending_from_him": False,
                     "unanswered_proactive_turns": st.get("proactive_unanswered_turns", 0)}, bm)
    if reason:
        return reason
    import proactive_media
    if proactive_media.required(st, now, load().get("proactive", {}), bm) and not (original.get("meta") or {}).get("has_image"):
        return "本轮需要图片，旧纯文字草稿不补发"
    import share_dedupe as sd
    if sd.material_of(original) and not sd.reserve(str(item.get("contact") or "demo_contact"), original, now, retry=True):
        return "分享内容重复，禁止补发"
    return None


def serialized_retry(fn):
    """Outbox retries and the proactive worker share one cross-process send lock."""
    import functools
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        import fcntl
        import pathlib
        from channels import outbox_store
        path = outbox_store.DB_PATH.parent / "proactive_queue.worker.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return []
            try:
                return fn(*args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return wrapped
