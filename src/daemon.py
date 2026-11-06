"""OCR/mac-UI 实时回复主循环（对照/应急；默认不真发）。

**非线上主路径。** 微信真发 = iLink（`channels/ilink_weixin.py` + `bridge.handle_inbound`）。
本模块仅在 `wechat_outbound=ui` 且 `ocr_daemon_enabled=true` 时发送；否则 `_refuse_ocr_send`。
launchd：`com.xiaotian.wechat` 默认 RunAtLoad=false / disable-ocr。

行为摘要（OCR 专用，不是「入站冻结」）：
- 去重 = 屏幕与 DB 尾部对齐；指纹紧循环 → UI 锁 OCR/回复
- 生成锁外；拟人打字 2~15s；发送前重读，期间新消息合并重写
- `max_regenerate`：对方连发超限则放弃**本轮**，`pending_unreplied` 强制**下一轮仍回**
  （防过期半截回复，不是「他不停她就一直不回」；ilink 无此逻辑）
- 回复优先：reply_pending，主动发送让路（ui_lock）。--once / --dry-run 单次 tick。
"""
import argparse, random, time, traceback, sys
from config import load
import importlib, memory, brain, emotion, schedule, chat_events
CFG = load(); CID = CFG["contact"]["id"]
# WeChat 真发主路径 = iLink（config.wechat_outbound=ilink）。OCR mac daemon 默认禁用，避免抢同一会话。
OCR_DAEMON_ENABLED = bool(CFG.get("ocr_daemon_enabled", False)) and (CFG.get("wechat_outbound") or "ilink") == "ui"
# backend: "mac"(默认, Mac 微信 + Vision OCR) | "iphone"(WDA) — 仅对照/调试；真发见 channels/ilink
wechat_io = importlib.import_module({"mac": "mac_wechat_io", "iphone": "wechat_io"}[CFG.get("backend", "mac")])

def _refuse_ocr_send(context="daemon"):
    """iLink-only 策略：禁止 OCR/mac UI 发送路径与 iLink 抢聊。"""
    if OCR_DAEMON_ENABLED:
        return False
    print(
        f"[{context}] wechat_outbound=ilink / ocr_daemon_enabled=false — "
        "OCR mac daemon 不发送。请用 tools/ilink_weixin_run.py；主动消息走 proactive.transport=ilink。",
        file=sys.stderr, flush=True,
    )
    return True

def _fp(sender, text, prev, msg_id_hint):
    return memory.fingerprint(CID, sender, text, prev, msg_id_hint)

def plan_new(c, msgs):
    """Return visible msgs (dicts) not yet in DB, via sequence alignment against the DB tail."""
    vis = [m for m in msgs if m["from"] in ("me", "them")]
    pairs = [(m["from"], m["text"]) for m in vis]
    db_tail = [(s, t) for _, s, t in memory.tail(c, CID, 50)]
    new, ok = memory.new_suffix(db_tail, pairs)
    if not ok:
        # No overlap with a non-empty DB (scrolled far / OCR drift): only trust the trailing 'them' run.
        k = len(vis)
        while k > 0 and vis[k - 1]["from"] == "them": k -= 1
        print(f"[warn] screen/DB no overlap; taking trailing {len(vis)-k} 'them' msgs", file=sys.stderr)
        return vis[k:]
    return vis[len(vis) - len(new):] if new else []

def ingest(c, new):
    seq = c.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
    prev = (memory.tail(c, CID, 1) or [(0, "", "")])[-1][2]
    for i, m in enumerate(new):
        memory.insert(c, CID, m["from"], m["text"], _fp(m["from"], m["text"], prev, seq + i + 1), commit=False,
                      type=m.get("type"), sent_at=m.get("sent_at"))
        prev = m["text"]
    c.commit()

def tick(c, dry):
    if not dry and _refuse_ocr_send("tick"):
        return None
    wechat_io.open_chat()
    msgs = chat_events.annotate(wechat_io.read_messages(), in_chat=True)   # open_chat() 已定位到 demo_contact
    new = plan_new(c, msgs)
    vis = [m for m in msgs if m["from"] in ("me", "them")]
    last = vis[-1] if vis else None
    if dry:
        theirs = [m for m in new if m["from"] == "them"] or ([last] if last and last["from"] == "them" else [])
        if not theirs: print("[dry-run] nothing to reply"); return None
        hist = memory.history(c, CID, CFG["history_for_prompt"]) + [memory.Msg(m["from"], m["text"], m.get("sent_at"), m.get("type")) for m in new]
        text = brain.reply(CID, hist, dry=True)
        scene = (brain.LAST or {}).get("want_image")
        print("[dry-run] would send:", text, (f" WANT_IMAGE:{scene}" if scene else "")); return text
    ingest(c, new)
    theirs = [m for m in new if m["from"] == "them"]
    if theirs:  # emotion drift (PAD) from what he said
        st = memory.read_state(CID); emo = st.get("emotion")
        for m in theirs:
            if m.get("type") != "notice": emo, _ = emotion.appraise(emo, m["text"])
        memory.update_state(CID, emotion=emo, **{k: v for k, v in emotion.derived(emo).items() if k in ("mood", "energy")})
    if not theirs or (last and last["from"] == "me"): return None
    hist = memory.history(c, CID, CFG["history_for_prompt"])
    t0 = time.monotonic()
    text = brain.reply(CID, hist)
    scene = (brain.LAST or {}).get("want_image")
    _last_them = next(
        (m.get("text") for m in reversed(theirs or []) if isinstance(m, dict) and (m.get("text") or "").strip()),
        "",
    )
    try:
        import photo_scene as _ps
        if scene and not _ps.allow_inbound_image_gen(_last_them):
            print(f"[reply] drop want_image (no explicit photo ask) text={_last_them!r:.40}", flush=True)
            scene = None
    except Exception as e:
        print(f"[reply] photo ask gate skip: {e!r}", file=sys.stderr)
    img_plan = None
    if scene:
        try:
            import chat_media
            ig = CFG.get("imagegen") or {}
            _asked = False
            try:
                import photo_scene as _ps
                _asked = _ps.allow_inbound_image_gen(_last_them)
            except Exception:
                _asked = False
            try:
                import life_calendar as _lc
                _now = _lc.now_local()
            except Exception:
                _now = __import__("timeutil").now_local()
            _shot = (brain.LAST or {}).get("shot_type")
            _bits = (brain.LAST or {}).get("shot_bits")
            if _asked and ig.get("hold_on_before_generate", True) and __import__("os").environ.get("IMAGEGEN_HOLD_ON", "1") not in ("0", "false", "no"):
                try:
                    hold = chat_media.hold_on_line(user_text=_last_them, shot_type=_shot)
                    if hasattr(wechat_io, "open_chat"): wechat_io.open_chat()
                    wechat_io.send_message(hold)
                    print(f"[reply] hold_on: {hold}", flush=True)
                except Exception as he:
                    print(f"[reply] hold_on send failed: {he!r}", file=sys.stderr)
            # 用户话术硬锁镜头（route_user_shot）→ shot_lock，禁止串卡
            img_plan = chat_media.plan_chat_images(
                scene, caption=text, now=_now, shot_type=_shot, shot_bits=_bits,
                shot_lock=bool(_shot), user_text=_last_them, image_lane="demand",
            )
        except Exception as e:
            print(f"[reply] want_image generate failed, text-only: {e!r}", file=sys.stderr); scene = None
    n_img = (img_plan or {}).get("n_images") or 0
    time.sleep(human_wait(time.monotonic() - t0, text or ("[图片]" if n_img else ""), RC))
    if img_plan and img_plan.get("paths"):
        import chat_media
        sent = chat_media.send_prepared_images(img_plan, io=wechat_io, open_chat_first=False, send_caption_first=True)
        # 模型配文已并入 texts[0]；多图时不再单独重发 text
        log_text = " ".join(t for t in (img_plan.get("texts") or []) if t)
        log_text = (log_text + " " if log_text else "") + f"[图片x{sent['n_images']}:{scene}]"
    else:
        from bubble_split import split_bubbles, bubble_gap_sec
        _mb = (brain.LAST or {}).get("reply_bubble_budget")
        bubbles = split_bubbles(text, **({"max_bubbles": int(_mb)} if _mb else {})) or ([text] if text else [])
        for i, bub in enumerate(bubbles):
            if i:
                time.sleep(bubble_gap_sec(bub))
            wechat_io.send_message(bub)
        log_text = " / ".join(bubbles) if bubbles else text
    seq = c.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
    memory.insert(c, CID, "me", log_text, _fp("me", log_text, theirs[-1]["text"], seq + 1))
    memory.mark_replied(c, CID)
    said = " / ".join(f"「{m['text']}」" for m in theirs)
    memory.append_diary(CID, f"TA说{said}，我回「{log_text}」")
    memory.update_state(CID, last_reply_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return log_text

# OCR-only knobs（ilink/bridge 不读 max_regenerate）。typing/think 键名与 config.reply 对齐供对照。
DEFAULT_RC = {"poll_active_sec": 0.2, "poll_idle_max_sec": 0.2, "idle_after_sec": 20, "backoff_factor": 1.5,
              "full_check_sec": 60, "human_typing": True, "typing_sec_per_char": 0.3, "typing_min_sec": 2, "typing_max_sec": 15,
              "think_max_awake_sec": 5, "think_max_sleeping_sec": 300, "max_regenerate": 2}
RC = {**DEFAULT_RC, **CFG.get("reply", {})}

def typing_delay(text, rc=RC, rng=random):
    """拟人打字目标时长：按字数线性 + 少量抖动，夹在 [typing_min_sec, typing_max_sec]（默认 2~15s）。关闭时 0。
    这是「想要的人类手感」上限目标，不是必定再 sleep 这么久——见 human_wait。"""
    if not rc.get("human_typing"): return 0.0
    d = len(text or "") * rc["typing_sec_per_char"] + rng.uniform(0.5, 1.5)
    return max(rc["typing_min_sec"], min(rc["typing_max_sec"], d))

def think_delay(rc=RC, now=None, rng=random, sleeping=None):
    """作息「看到消息前」延时目标：一律 think_max_awake_sec 短垫（不再叠睡觉长等待）。

    sleeping 参数保留兼容旧调用/测试，但不再抬高 cap。
    """
    _ = sleeping  # API compat; ignored (no long sleeping wait)
    cap = float(rc.get("think_max_awake_sec") or 5)
    return min(schedule.reply_delay(now, rng), cap)

def human_wait(elapsed, text, rc=RC, think_fn=None, typing_fn=None):
    """把 LLM 墙钟时间从拟人等待里扣掉，避免「LLM 已很久 + 再叠打字」。

    Before: delay = max(0, think - elapsed) + typing          # typing 总叠在 LLM 之后
    After:  delay = max(0, think - elapsed) + max(0, typing - elapsed)
            → LLM 已 ≥ typing 目标则打字垫 ≈0；仅当 LLM 比想要的手感更快时才补差。
    think / typing 各自相对同一 elapsed 取垫，互不把对方当已消耗。
    """
    th = (think_fn or (lambda: think_delay(rc)))()
    ty = (typing_fn or (lambda t: typing_delay(t, rc)))(text)
    e = max(0.0, float(elapsed or 0.0))
    return max(0.0, th - e) + max(0.0, ty - e)

class Backoff:
    """活跃间隔 poll_active_sec；idle 时最多退避到 poll_idle_max_sec（默认与 active 相同=紧循环即时触发）。
    有变化立即回到 poll_active_sec。"""
    def __init__(s, rc=RC): s.rc = rc; s.cur = rc["poll_active_sec"]; s.quiet_since = None
    def update(s, changed, now):
        if changed: s.cur = s.rc["poll_active_sec"]; s.quiet_since = None
        else:
            if s.quiet_since is None: s.quiet_since = now
            if now - s.quiet_since >= s.rc["idle_after_sec"]: s.cur = min(s.rc["poll_idle_max_sec"], s.cur * s.rc["backoff_factor"])
        return s.cur

def _reply_and_release(hist):
    """生成回复后立刻释放 mem0 的 Qdrant 本地锁（嵌入模型留在内存），否则常驻进程会独占
    data/mem0_qdrant，reflect.py / proactive_sender / import_memory 都打不开。"""
    try: return brain.reply(CID, hist)
    finally:
        try:
            import mem; mem.release()
        except Exception as e: print(f"[mem] release: {e}", file=sys.stderr)

_SINGLE = None
def single_instance():
    """data/daemon.lock 非阻塞 flock，持有到进程结束。防止 launchd daemon 与手动 daemon.py 双开重复回复。"""
    global _SINGLE
    if _SINGLE: return True
    import fcntl, os, config
    f = open(config.ROOT / "data" / "daemon.lock", "a+")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: f.close(); return False
    f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush(); _SINGLE = f; return True

def warmup_async():
    """后台预热 mem0/HF 嵌入，冷加载不挡首条回复。失败只记日志。"""
    import threading
    def _w():
        t = time.monotonic()
        try:
            import mem; mem.warmup(); print(f"[warmup] mem0 ready in {time.monotonic()-t:.1f}s", flush=True)
        except Exception as e: print(f"[warmup] mem0 skipped: {e}", file=sys.stderr, flush=True)
    threading.Thread(target=_w, name="warmup", daemon=True).start()

class Loop:
    """可注入 io / lock / clock / sleep / brain 的实时循环；测试全用假的。"""
    def __init__(s, c, io=None, rc=RC, lock=None, priority=None, clock=time.monotonic, sleep=time.sleep,
                 reply_fn=None, think_fn=None, typing_fn=None, log=print):
        import ui_lock as ul
        s.c, s.io, s.rc, s.clock, s.sleep, s.log = c, io or wechat_io, rc, clock, sleep, log
        s.lock = lock or (lambda: ul.ui_lock("reply")); s.priority = priority or ul.reply_priority
        s.reply_fn = reply_fn or _reply_and_release
        s.think_fn = think_fn or (lambda: think_delay(rc)); s.typing_fn = typing_fn or (lambda t: typing_delay(t, rc))
        s.last_fp, s.last_full, s.backoff = None, None, Backoff(rc)
        s.pending_unreplied = False  # 连发放弃后强制下一轮再检，避免 last_fp 已对齐而漏回

    def fingerprint(s):
        try: return s.io.chat_fingerprint()
        except Exception as e: s.log(f"[fp] {e}", file=sys.stderr); return None

    def read(s):
        """持 UI 锁：一次截图 OCR；不在目标聊天才走 open_chat（点击）。"""
        with s.lock():
            ok, msgs = s.io.read_chat()
            if not ok: s.io.open_chat(); ok, msgs = s.io.read_chat()
            msgs = chat_events.annotate(msgs, in_chat=ok)   # 面板内系统项只在核对为 demo_contact 时作为输入
            s.last_fp = s.fingerprint()   # 以读完时的画面为基准
        return msgs

    def absorb(s, msgs):
        """入库新消息 + 情绪；返回 (本轮 TA 新消息, 是否需要回)。"""
        new = plan_new(s.c, msgs); ingest(s.c, new)
        theirs = [m for m in new if m["from"] == "them"]
        if theirs:
            st = memory.read_state(CID); emo = st.get("emotion")
            for m in theirs:
                if m.get("type") != "notice": emo, _ = emotion.appraise(emo, m["text"])
            memory.update_state(CID, emotion=emo, **{k: v for k, v in emotion.derived(emo).items() if k in ("mood", "energy")})
            # 日程后门（不启额外服务）：「下周陪我出去玩」→ yield + 追问 / 学业拒绝
            try:
                import schedule_pipeline as sp
                for m in theirs:
                    if m.get("type") == "notice":
                        continue
                    ov = sp.note_inbound_user_message(m.get("text") or "", cid=CID)
                    if ov.get("triggered"):
                        s.log(f"[schedule_override] triggered refused={ov.get('refused')} ask={ov.get('wechat_ask')!r}")
                        try:
                            memory.update_state(CID, schedule_override_pending=ov.get("pending"))
                        except Exception:
                            pass
            except Exception as e:
                s.log(f"[schedule_override] hook failed: {e!r}", file=sys.stderr)
        vis = [m for m in msgs if m["from"] in ("me", "them")]
        return theirs, bool(vis and vis[-1]["from"] == "them")

    def wait_watch(s, secs):
        """分片睡，期间盯着聊天区；变了返回 True（他又发了）。"""
        end = s.clock() + secs
        while (left := end - s.clock()) > 0:
            s.sleep(min(s.rc["poll_active_sec"], left))
            fp = s.fingerprint()
            if fp is not None and fp != s.last_fp: return True
        return False

    def respond(s, theirs):
        """生成（锁外）→ 拟人延时（盯变化）→ 持锁重读确认 → 发送。期间来新消息就合并重写，最多 max_regenerate 次。
        theirs 可为空：上一轮放弃后 DB 已有未回 them，本轮靠 need 触发，用 history 出回复。
        若 brain.LAST["want_image"]：锁外经 imagegen 生图，锁内先文字气泡再 send_image（chat_media，图注不糊一条）。"""
        said_all = list(theirs)
        for _ in range(s.rc["max_regenerate"] + 1):
            t0 = s.clock(); text = s.reply_fn(memory.history(s.c, CID, CFG["history_for_prompt"]))
            import brain as _brain
            scene = (_brain.LAST or {}).get("want_image")
            _last_them = ""
            try:
                for m in reversed(said_all or []):
                    if isinstance(m, dict) and m.get("from") == "them" and m.get("text"):
                        _last_them = m["text"]; break
                    if isinstance(m, dict) and m.get("text") and not m.get("from"):
                        _last_them = m["text"]; break
                if not _last_them:
                    for _s, _tx in reversed(memory.history(s.c, CID, 8) or []):
                        if _s != "me" and _tx:
                            _last_them = _tx; break
            except Exception:
                pass
            try:
                import photo_scene as _ps
                if scene and not _ps.allow_inbound_image_gen(_last_them):
                    s.log(f"[reply] drop want_image (no explicit photo ask) text={_last_them!r:.40}")
                    scene = None
            except Exception as e:
                s.log(f"[reply] photo ask gate skip: {e!r}", file=sys.stderr)
            img_plan = None
            if scene:
                try:
                    import chat_media
                    ig = CFG.get("imagegen") or {}
                    _asked = False
                    try:
                        import photo_scene as _ps
                        _asked = _ps.allow_inbound_image_gen(_last_them)
                    except Exception:
                        _asked = False
                    try:
                        import life_calendar as _lc
                        _now = _lc.now_local()
                    except Exception:
                        _now = __import__("timeutil").now_local()
                    _shot = (_brain.LAST or {}).get("shot_type")
                    _bits = (_brain.LAST or {}).get("shot_bits")
                    if _asked and ig.get("hold_on_before_generate", True) and __import__("os").environ.get("IMAGEGEN_HOLD_ON", "1") not in ("0", "false", "no"):
                        try:
                            hold = chat_media.hold_on_line(user_text=_last_them, shot_type=_shot)
                            with s.lock():
                                if hasattr(s.io, "open_chat"): s.io.open_chat()
                                s.io.send_message(hold)
                            s.log(f"[reply] hold_on: {hold}")
                        except Exception as he:
                            s.log(f"[reply] hold_on send failed: {he!r}", file=sys.stderr)
                    # 用户话术硬锁镜头 → shot_lock，禁止串卡
                    img_plan = chat_media.plan_chat_images(
                        scene, caption=text, now=_now, shot_type=_shot, shot_bits=_bits,
                        shot_lock=bool(_shot), user_text=_last_them, image_lane="demand",
                    )
                except Exception as e:
                    s.log(f"[reply] want_image generate failed, text-only: {e!r}", file=sys.stderr)
                    scene = None
            n_img = (img_plan or {}).get("n_images") or 0
            delay = human_wait(s.clock() - t0, text or ("[图片]" if n_img else ""), s.rc, think_fn=s.think_fn, typing_fn=s.typing_fn)
            if s.wait_watch(delay):
                more, need = s.absorb(s.read()); said_all += more
                if need: continue
            with s.lock():
                ok, msgs = s.io.read_chat(); msgs = chat_events.annotate(msgs, in_chat=ok)
                more, need = s.absorb(msgs) if ok else ([], True)
                if more: said_all += more; continue          # 刚好又来了：重写
                # hold_on 会把末条变成 me，need=False；若已有待发图仍应发出，勿整轮当「已被回过」取消
                pending_imgs = bool(img_plan and img_plan.get("paths"))
                if not ok or (not need and not pending_imgs): return None
                if pending_imgs:
                    import chat_media
                    if not hasattr(s.io, "send_image"):
                        raise RuntimeError("io missing send_image")
                    sent = chat_media.send_prepared_images(img_plan, io=s.io, open_chat_first=False, send_caption_first=True)
                    log_text = " ".join(t for t in (img_plan.get("texts") or []) if t)
                    log_text = (log_text + " " if log_text else "") + f"[图片x{sent['n_images']}:{scene}]"
                else:
                    from bubble_split import split_bubbles, bubble_gap_sec
                    _mb = (brain.LAST or {}).get("reply_bubble_budget")
                    bubbles = split_bubbles(text, **({"max_bubbles": int(_mb)} if _mb else {})) or ([text] if text else [])
                    for i, bub in enumerate(bubbles):
                        if i:
                            s.sleep(bubble_gap_sec(bub))
                        s.io.send_message(bub)
                    log_text = " / ".join(bubbles) if bubbles else text
                s.last_fp = s.fingerprint()
            seq = s.c.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
            memory.insert(s.c, CID, "me", log_text, _fp("me", log_text, said_all[-1]["text"] if said_all else "", seq + 1))
            memory.mark_replied(s.c, CID)
            memory.append_diary(CID, "TA说" + " / ".join(f"「{m['text']}」" for m in said_all) + f"，我回「{log_text}」")
            memory.update_state(CID, last_reply_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            s.pending_unreplied = False
            return log_text
        # OCR-LEGACY：非入站冻结。超 max_regenerate 只丢本轮草稿；pending 强制下轮再回。ilink 无此分支。
        s.log("[reply] 对方一直在发，放弃本轮，下一轮再回", file=sys.stderr)
        s.pending_unreplied = True; s.last_fp = None  # 强制下一轮 OCR，不能等 idle/full_check
        return None

    def step(s):
        """一轮：返回本轮后该睡多久。"""
        now = s.clock(); fp = s.fingerprint()
        full = s.last_full is None or now - s.last_full >= s.rc["full_check_sec"]
        changed = fp is None or fp != s.last_fp
        if not (changed or full or s.pending_unreplied): return s.backoff.update(False, now)
        if full: s.last_full = now
        with s.priority():
            theirs, need = s.absorb(s.read())
            # need=True 即使 theirs 空也要回：连发放弃后消息已在 DB，不能因 plan_new 为空就漏回
            sent = s.respond(theirs) if need else None
            if sent or not need: s.pending_unreplied = False
            elif need and not sent: s.pending_unreplied = True
        # 画面变化 / 新消息 / 仍有未回 → 立刻回到最快；纯定时全检不重置退避
        return s.backoff.update(bool(theirs or sent or s.pending_unreplied or (changed and fp is not None)), s.clock())

    def run(s, max_steps=None):
        if _refuse_ocr_send("start"):
            return
        if s.io is wechat_io and not single_instance():
            print("[start] 另一个实时回复循环已在运行（data/daemon.lock），本进程退出", file=sys.stderr, flush=True); return
        n = 0
        while max_steps is None or n < max_steps:
            try: d = s.step()
            except Exception: traceback.print_exc(); d = s.rc["poll_idle_max_sec"]
            s.sleep(d); n += 1

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(); c = memory.db()
    if a.once or a.dry_run:
        try:
            from ui_lock import ui_lock
            with ui_lock("reply"): tick(c, a.dry_run)
        except Exception: traceback.print_exc()
        return
    print(f"[start] daemon pid={__import__('os').getpid()} contact={CID} backend={CFG.get('backend','mac')}", flush=True)
    try:
        import life_calendar as lc
        lc.sync_to_memory(CID, write_mem0=True)
        print("[start] life_calendar synced to STATE/mem0", flush=True)
    except Exception as e:
        print(f"[start] life_calendar sync skipped: {e!r}", file=sys.stderr, flush=True)
    warmup_async(); Loop(c).run()

if __name__ == "__main__": main()
