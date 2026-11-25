"""主动聊天：定时器入口 + 发送队列（launchd 可每 5 分钟跑一次 `--once`）。只发给联系人 demo_contact（备注「演示联系人-小熊」）。

调度真相源是队列里的 next_consider_at（兼容旧字段 next_check_at）：
- 每 5 分钟扫描节拍；next_consider_at 仅约束无节拍时的普通闲聊。
- 到点后 tick() 只让模型决定 send|skip；SKIP 也会写入下一次 next_consider_at。
- 成功发送后 next_consider_at = now + min_gap_sec（默认 25–45 分钟抖动）。

流程：
1. 未到 next_consider_at → WAIT。
2. proactive_scheduler.tick()：日程节拍优先 → 硬门槛 → 模型决定；DRAFT 入队。
3. 出队：TTL 内最早一条；发送前重检硬门槛；transport=ilink|onebot|qq|ui|auto；channel_policy=primary|mirror（默认 primary，不双发）。
4. 真发：写 chat.db / STATE（含 proactive_beats.done）/ 日记；after_send 刷新 next_consider_at。
   meal/surf 可附 material_store 本地图；WANT_IMAGE 仍走 chat_media。

python3 src/proactive_sender.py --once [--dry-run] [--force] [--now "2026-10-24 20:00"] [--no-ui]
"""
import copy
import argparse, contextlib, datetime, difflib, fcntl, json, pathlib, sys, time, uuid
from config import load
import memory, proactive_scheduler as ps, ui_lock as _ul
from sleep_chat import ProactiveSleepBlocked, check_proactive_send

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUEUE = pathlib.Path((load().get("identity_runtime") or {}).get("proactive_queue") or ROOT / "data" / "proactive_queue.json")
UI_LOCK = _ul.UI_LOCK
_CONTACT = load().get("contact") or {}
ALLOWED = {(str(_CONTACT.get("id") or ""), _CONTACT.get("remark") or "")}

def _persist_confirmed_piece(cid, item, piece, index, stamp):
    """Replayable bookkeeping for an already acknowledged proactive payload."""
    db = memory.db()
    try:
        memory.insert(db, cid, "me", piece,
            memory.fingerprint(cid, "me", piece, "[proactive]", f"{item['id']}:{index}"), type="proactive")
    finally:
        db.close()
    memory.append_delivery_diary(cid, f"我主动找他：「{piece}」（{item.get('topic', '')}）",
                                 f"proactive:{item['id']}:{index}", day=stamp.date().isoformat())
    import sleep_chat
    memory.mutate_state(cid, lambda st: {
        "last_reply_at": max(str(st.get("last_reply_at") or ""), _iso(stamp)),
        **sleep_chat.confirmed_outbound_patch(piece, st, stamp)})
    pending = (item.get("meta") or {}).get("pending_delivery")
    if pending:
        import brain
        brain.commit_delivery(cid, pending, delivery_id=f"proactive:{item['id']}", sent_text=piece, now=stamp)


def _actual_partial(item):
    actual = copy.deepcopy(item)
    actual["text"] = " / ".join(item["delivered_pieces"])
    actual.setdefault("meta", {})["has_image"] = "[图片]" in item["delivered_pieces"]
    actual["delivery_complete"] = False
    return actual


def _sync_image_continuations(q, qpath):
    """Repair durable enqueues and reconcile image receipts without a new round."""
    from channels import outbox_store
    for parent in q.get("items", []):
        continuations = parent.get("delivery_continuations") or parent.get("image_continuations") or []
        if not continuations or parent.get("status") not in ("hold", "partial", "unknown"):
            continue
        for channel in {entry["channel"] for entry in continuations}:
            with outbox_store.channel_lock(channel):
                data = outbox_store.load(channel)
                existing = {it["id"]: it for it in data["items"]}
                for entry in continuations:
                    if entry["channel"] != channel or entry.get("status") in ("sent", "dropped", "expired"):
                        continue
                    payload = entry["item"]
                    receipt = existing.get(payload["id"])
                    if receipt is None:
                        # The parent plan was saved before enqueue. Repeating
                        # that enqueue after a crash cannot overwrite a receipt.
                        data["items"].append(dict(payload))
                    else:
                        entry["status"] = receipt.get("status")
                        if receipt.get("status") == "sent":
                            entry["sent_at"] = receipt.get("sent_at")
                            path = payload.get("image_path")
                            if path and path not in parent.setdefault("delivered_image_paths", []):
                                parent["delivered_image_paths"].append(path)
                outbox_store.save(channel, data)
        parent["remaining_image_paths"] = [p for p in parent.get("planned_image_paths", [])
                                             if p not in parent.get("delivered_image_paths", [])]
        if not parent["remaining_image_paths"] and all(e.get("status") == "sent" for e in continuations):
            stamp = max([e.get("sent_at") or "" for e in continuations] + [parent.get("first_confirmed_at") or ""])
            parent.update(status="sent", note="all remaining payloads confirmed", sent_at=stamp)
            from proactive_limiter import record_success
            record_success(str((load().get("contact") or {}).get("id") or "demo_contact"), parent,
                           datetime.datetime.fromisoformat(parent.get("first_confirmed_at") or stamp))
            parent["accounted"] = True
        elif any(e.get("status") in ("dropped", "expired", "failed") for e in continuations):
            parent.update(status="dropped", note="剩余内容已取消，整轮未完成")
        save_q(q, qpath)


def _maybe_apply_unanswered_miss(cid, st, rows, pc, now):
    """未回主动轮次过软阈值 → 情绪轻沉 + tip（日封顶一次）。写回 emotion。"""
    try:
        import emotion as _emo
        import proactive_scheduler as _ps
        turns = _ps.count_unanswered_proactive_turns(rows, st)
        thr = _emo.miss_threshold(pc, now)
        emo0 = (st or {}).get("emotion") or _emo.default()
        emo1, meta = _emo.apply_unanswered_miss(
            emo0, turns=turns, threshold=thr, now=now,
            applied_day=(emo0 or {}).get("unanswered_miss_day"),
        )
        if meta.get("applied"):
            der = _emo.derived(emo1)
            memory.update_state(cid, emotion=emo1,
                                **{k: der[k] for k in ("mood", "energy") if k in der})
            st = {**(st or {}), "emotion": emo1, "mood": der.get("mood"), "energy": der.get("energy")}
        return st, meta
    except Exception as e:
        print(f"[proactive] unanswered_miss skip: {e!r}", file=__import__("sys").stderr)
        return st, {"applied": False, "skipped": "error"}


def _now(): return datetime.datetime.now().replace(microsecond=0)
def _iso(t): return t.isoformat(sep=" ", timespec="seconds")

def load_q(path=QUEUE):
    try: return json.loads(path.read_text())
    except FileNotFoundError: return {"next_consider_at": None, "next_check_at": None, "items": []}

def _relay_dir(cfg_full):
    out = ((cfg_full or {}).get("imagegen") or {}).get("out_dir")
    if not out:
        return None
    return pathlib.Path(str(out)).expanduser().resolve() / "pending"


def _relay_link(item):
    link = ((item or {}).get("meta") or {}).get("relay_resume") or {}
    return link if link.get("protocol") == 1 and link.get("intent_id") and link.get("pending_path") else None


def _seal_relay_queue(q):
    import image_relay_resume as irr
    for it in q.get("items") or []:
        link = _relay_link(it)
        if not link or not link.get("claimed"):
            continue
        target = irr.delivery_target(it.get("status"))
        if not target or link.get("sealed_status") == target:
            continue
        if irr.transition(link["pending_path"], link["intent_id"], target, reason=it.get("note")):
            link["sealed_status"] = target
            job = link.get("job_id")
            if target == "delivery_unknown" and it.get("status") in ("unknown", "partial"):
                print(f"[proactive] relay 回执未确认 job={job}，不重发", flush=True)
            elif target == "sent":
                print(f"[proactive] relay 已由原出站确认 job={job}", flush=True)
            elif target == "handed_off":
                print(f"[proactive] relay 交给既有 outbox job={job}", flush=True)
            elif target == "blocked_stale":
                print(f"[proactive] relay 停止续接 job={job} status={it.get('status')}", flush=True)


def _make_relay_intent(cid, item, beat, scene, caption):
    import image_relay_resume as irr
    slot = (beat or {}).get("image_slot") or (beat or {}).get("slot") or {}
    date = slot.get("date") or ((beat or {}).get("today") or {}).get("date") or ""
    if hasattr(date, "isoformat"):
        date = date.isoformat()
    return irr.make_intent(
        contact=str(cid), beat_id=(beat or {}).get("id") or "",
        done_key=(beat or {}).get("done_key") or ((item.get("meta") or {}).get("beat_done_key")) or "",
        date=str(date or ""), slot=slot, scene=scene or "",
        queue_id=item.get("id") or "", caption=caption or "",
    )


def _park_relay(item, job_path, job, intent):
    intent = (job or {}).get("send_intent") or (intent or {}).get("send_intent") or intent or {}
    item.setdefault("meta", {})["relay_resume"] = {
        "protocol": 1,
        "job_id": (job or {}).get("job_id"),
        "intent_id": intent.get("intent_id"),
        "pending_path": str(job_path),
        "scene": intent.get("scene") or (job or {}).get("scene") or "",
    }
    item["status"] = "image_wait"
    item["note"] = f"relay pending job={(job or {}).get('job_id')} status={(job or {}).get('status')}"


def _revalidate_relay(job, now, st, beat, cid):
    """Schedule, place, age and scene gates. A failure must not send."""
    intent = (job or {}).get("send_intent") or {}
    if str(intent.get("contact") or "") != str(cid):
        return False, "relay contact mismatch"
    snap = (job or {}).get("photo_schedule")
    if not isinstance(snap, dict):
        return False, "relay photo_schedule missing"
    try:
        from photo_schedule import check
        check(snap, now)
    except Exception as exc:
        return False, f"relay photo_schedule {exc}"
    if job.get("status") == "done":
        result = job.get("result_path")
        meta = pathlib.Path(result).parent / "run.json" if result else None
        if not result or not pathlib.Path(result).is_file():
            return False, "relay result missing"
        if meta is None or not meta.is_file():
            return False, "relay run.json missing"
        try:
            record = json.loads(meta.read_text())
        except Exception:
            return False, "relay run.json unreadable"
        if record.get("photo_schedule") != snap:
            return False, "relay run.json schedule mismatch"
        try:
            from photo_schedule import check_file
            check_file(result, now)
        except Exception as exc:
            return False, f"relay result {exc}"
    import life_calendar as lc
    live = lc.resolve_life_now(st or {}, now) or {}
    scene = intent.get("scene") or job.get("scene") or ""
    from schedule_fidelity import outbox_item_stale, scene_matches_slot
    slot = live or (snap.get("slot") or {})
    if scene and not scene_matches_slot(str(scene), slot):
        return False, "relay scene no longer matches slot"
    view = {
        "created": job.get("created_at") or "",
        "text": intent.get("caption") or "",
        "scene": scene,
        "image_path": job.get("result_path") if job.get("status") == "done" else None,
        "photo_schedule": snap,
        "slot_bucket": (snap.get("slot") or {}).get("bucket"),
        "slot_start": (snap.get("slot") or {}).get("start"),
        "beat_id": intent.get("beat_id") or "",
        "reason": "proactive relay resume",
    }
    stale, why = outbox_item_stale(view, now=now, slot=slot)
    if stale:
        return False, f"relay stale {why}"
    from schedule_execution import beat_blocked
    if beat_blocked(beat or {}, now, st):
        return False, "relay beat blocked"
    return True, ""


def _poll_relay_waits(q, now, st, cid):
    """Inspect protocol waits only. Returns (ready_item_or_None, changed)."""
    import image_relay_resume as irr
    ready = None
    changed = False
    for it in q.get("items") or []:
        if it.get("status") != "image_wait":
            continue
        link = (it.get("meta") or {}).get("relay_resume") or {}
        if link.get("protocol") != 1 or not link.get("intent_id") or not link.get("pending_path"):
            it.update(status="image_isolated", note="relay 无协议或无意图，不补发")
            print(f"[proactive] relay 隔离 item={it.get('id')} reason=no-protocol", flush=True)
            changed = True
            continue
        path = pathlib.Path(link["pending_path"])
        try:
            data = json.loads(path.read_text()) if path.is_file() else None
        except Exception:
            data = None
        if not isinstance(data, dict):
            it.update(status="image_isolated", note="relay pending 缺失，不重新生成")
            print(f"[proactive] relay 隔离 item={it.get('id')} reason=missing", flush=True)
            changed = True
            continue
        kind = irr.assess(data, link["intent_id"])
        job = data.get("job_id")
        if kind in ("legacy", "mismatch", "unknown"):
            it.update(status="image_isolated", note=f"relay {kind} status={data.get('status')}，不发送")
            print(f"[proactive] relay 隔离 job={job} kind={kind} status={data.get('status')}", flush=True)
            changed = True
            continue
        if kind in ("failed", "broken", "closed"):
            it.update(status="unknown" if kind == "closed" else "dropped",
                      note=f"relay {kind} status={data.get('status')}，不重发")
            print(f"[proactive] relay 不重发 job={job} kind={kind} status={data.get('status')}", flush=True)
            changed = True
            continue
        beat = (it.get("meta") or {}).get("beat_meta") or {}
        ok, why = _revalidate_relay(data, now, st, beat, cid)
        if not ok:
            it.update(status="dropped", note=why)
            irr.block(path, link["intent_id"], why)
            print(f"[proactive] relay 过期或场景失效 job={job} reason={why}", flush=True)
            changed = True
            continue
        if kind == "open":
            print(f"[proactive] relay 等待 job={job} intent={link['intent_id']} status={data.get('status')}", flush=True)
            continue
        if kind == "ready" and ready is None:
            print(f"[proactive] relay 就绪 job={job} intent={link['intent_id']}，重检后走原出站", flush=True)
            ready = it
    return ready, changed


def save_q(q, path=QUEUE):
    try:
        _seal_relay_queue(q)
    except Exception as exc:
        print(f"[proactive] relay seal skip: {exc!r}", file=sys.stderr)
    path.parent.mkdir(exist_ok=True); tmp = path.with_suffix(".tmp")
    # 双写兼容：旧 launchd/脚本读 next_check_at，新逻辑读 next_consider_at
    if q.get("next_consider_at") and not q.get("next_check_at"):
        q["next_check_at"] = q["next_consider_at"]
    elif q.get("next_check_at") and not q.get("next_consider_at"):
        q["next_consider_at"] = q["next_check_at"]
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=1)); tmp.replace(path)

def _next_at(q):
    return q.get("next_consider_at") or q.get("next_check_at")

def _set_next(q, when):
    s = _iso(when) if isinstance(when, datetime.datetime) else when
    q["next_consider_at"] = s; q["next_check_at"] = s

def ui_lock(role="proactive", path=None):
    """兼容旧接口；主动发送只做非阻塞尝试，回复优先（见 ui_lock.py）。"""
    return _ul.ui_lock(role, path=path or UI_LOCK)

def check_target(full):
    c = full["contact"]
    if (str(c["id"]), c.get("remark", "")) not in ALLOWED: raise PermissionError(f"联系人 {c['id']}/{c.get('remark')} 不在主动消息白名单")
    return c

def similar(a, b): return difflib.SequenceMatcher(None, memory.norm(a), memory.norm(b)).ratio()

def is_dup(text, q, rows, thr=.8, *, now=None, exclude_id=None):
    # rows: (sender, text) | (sender, text, sent_at) | (sender, text, sent_at, type)
    me_texts = []
    for r in rows or []:
        if not r:
            continue
        s = r[0] if not isinstance(r, dict) else r.get("sender") or r.get("from")
        txt = r[1] if not isinstance(r, dict) else r.get("text")
        if s == "me" and txt:
            me_texts.append(txt)
    items = [i for i in q["items"] if (exclude_id is None or i.get("id") != exclude_id)
             and i["status"] in ("sent", "queued", "sending", "unknown", "partial")]
    if now is not None:
        from proactive_repetition import repeated, meal_report
        history = list(rows or []) + [
            ("me", i["text"], i.get("sent_at") or i.get("created")) for i in items]
        # Legacy injected histories lack timestamps; treat only those as recent.
        history = [(r[0], r[1], now.isoformat()) if not isinstance(r, dict) and len(r) == 2 else r
                   for r in history]
        if repeated(text, history, now, similarity_threshold=thr):
            return True
        if meal_report(text):
            # Meal changes/negations must not fall through to a string ratio
            # that equates "咸" with "不咸", or yesterday's meal with today's.
            return False
    past = [i["text"] for i in items][-20:] + me_texts[-20:]
    return any(similar(text, p) >= thr for p in past if p)

def enqueue(q, text, topic, now, ttl_min=30, meta=None):
    it = {"id": uuid.uuid4().hex[:8], "created": _iso(now), "expires": _iso(now + datetime.timedelta(minutes=ttl_min)),
          "text": text, "topic": topic, "status": "queued", "note": "", "meta": meta or {}}
    q["items"].append(it); return it

def due(q, now):
    for it in q["items"]:
        if it["status"] != "queued": continue
        if datetime.datetime.fromisoformat(it["expires"]) < now: it["status"] = "expired"; continue
        return it
    return None

def verify_window(io, remark, click=True):
    """返回 (ok, 原因)。click=False 只截图 OCR（dry-run 用），不点不输。"""
    if click: io.open_chat()
    io.capture()
    head = io.norm("".join(e["text"] for e in io.ocr() if e["x"] > io.SIDEBAR_W and e["y"] < io.HEADER_H))
    if io.norm(remark) not in head: return False, f"标题栏 OCR 不含「{remark}」：{head[:40]!r}"
    vis = [m for m in io.read_messages() if m["from"] in ("me", "them")]
    if vis and vis[-1]["from"] == "them": return False, "屏幕上最后一条是他发的，交给回复流程"
    return True, "窗口核对通过"


def _ilink_peer_and_ctx():
    """从 persona aliases + ilink_ctx.json 取 peer_id / context_token。"""
    from config import load, ROOT
    import json
    p = load().get("persona") or {}
    aliases = p.get("contact_aliases") or {}
    peer = None
    for k, v in aliases.items():
        if str(v) == "demo_contact" and k.startswith("wechat_ilink:") and not k.endswith(":*"):
            peer = k.split(":", 1)[1]
            break
    ctx_path = ROOT / "data" / "ilink_ctx.json"
    ctx_map = {}
    try:
        ctx_map = json.loads(ctx_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        ctx_map = {}
    from channels.persona_map import allowed_peer
    if peer and not allowed_peer("wechat_ilink", peer):
        peer = None
    token = ctx_map.get(peer) if peer else None
    return peer, token



def _unpack_send_result(res):
    """Normalize send_via_* return to (ok, detail, partial)."""
    if not isinstance(res, (tuple, list)):
        return False, repr(res), None
    ok = res[0]
    why = res[1] if len(res) > 1 else ""
    partial = res[2] if len(res) > 2 else None
    return ok, why, partial


def send_via_ilink(text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None):
    """一次性 WeixinBot 发送。返回 (ok, detail, partial).

    Share lane order: caption bubbles (bubble_split) → link_url (intact, no
    split) → image. Normal chat passes link_url=None.
    """
    try:
        from weixin_ilink import WeixinBot
        from channels.ilink_weixin import _creds_path
        import weixin_ilink.api as api
    except Exception as e:
        return False, f"ilink import: {e!r}", None
    peer, token = _ilink_peer_and_ctx()
    if not peer:
        return False, "no ilink peer in aliases/ctx", None
    creds = _creds_path()
    if not creds.is_file():
        return False, f"missing creds {creds}", None
    bot = WeixinBot(credentials_file=str(creds), auto_emoji=True)
    captures: list = []
    _orig = api.send_message

    def _wrap(opts, msg, *a, **kw):
        # Image upload can take time; check again at the actual sendmessage call.
        if before_send:
            before_send()
        res = _orig(opts, msg, *a, **kw)
        captures.append(res if isinstance(res, dict) else {"raw": res})
        return res

    api.send_message = _wrap  # type: ignore
    sent_bubbles: list[str] = []
    link = (link_url or "").strip()
    caption = (text or "") or ""
    link_sent = False

    def _partial(image_pending: bool = False):
        from channels.outbox_send import bubbles_suffix
        return {
            "sent_bubbles": list(sent_bubbles),
            "remaining_text": bubbles_suffix(caption, len(sent_bubbles)),
            "orig_text": caption,
            "link_url": None if link_sent else (link or None),
            "image_pending": bool(image_pending),
        }

    def _persist_sent():
        if on_confirmed or (not sent_bubbles and not link_sent):
            return
        try:
            from channels.outbox_send import persist_outbound_bubble, channel_tag
            from config import load as _load
            cid = str((_load().get("persona") or {}).get("primary_contact_id") or "demo_contact")
            tag = channel_tag("ilink")
            for i, bub in enumerate(sent_bubbles):
                persist_outbound_bubble(cid, tag, bub, occ=f"proactive:{i}", type_="outbox")
            if link_sent and link:
                persist_outbound_bubble(cid, tag, link, occ="proactive:link", type_="outbox")
        except Exception as e:
            print(f"[proactive] persist partial ilink skip: {e!r}", flush=True)

    def _check_batch(batch):
        if not batch:
            from channels.send_receipts import DeliveryUnknown
            raise DeliveryUnknown("ilink send fail: no sendmessage response captured")
        for res in batch:
            ret = res.get("ret")
            errmsg = str(res.get("errmsg") or res.get("error") or "")
            mid = res.get("message_id")
            bad = (ret not in (None, 0, "0")) or ("prepare failed" in errmsg.lower())
            if bad:
                return False, (
                    f"ilink prepare/ret fail ret={ret!r} errmsg={errmsg!r} "
                    f"(stale context_token? need inbound refresh)"
                )
            if mid is None:
                from channels.send_receipts import DeliveryUnknown
                raise DeliveryUnknown(f"ilink send fail: missing message_id ret={ret!r}")
        return True, ""

    def _gap_params():
        try:
            from bubble_split import bubble_gap_sec
            try:
                import sleep_chat as _sc
                _mode, _ = _sc.peek_mode(memory.read_state("demo_contact"))
                _sk, _gk = (
                    (_sc.split_params(_mode), _sc.gap_params(_mode))
                    if _mode in ("pre_sleep", "asleep")
                    else ({}, {})
                )
            except Exception:
                _sk, _gk = {}, {}
            return _sk, _gk, bubble_gap_sec
        except Exception:
            from bubble_split import bubble_gap_sec
            return {}, {}, bubble_gap_sec

    def _send_caption():
        if not caption.strip():
            return True, ""
        from channels.share_send import split_caption_bubbles
        _sk, _gk, bubble_gap_sec = _gap_params()
        bubbles = split_caption_bubbles(caption, **_sk) or [caption]
        for i, bub in enumerate(bubbles):
            if i:
                time.sleep(bubble_gap_sec(bub, **_gk))
            before = len(captures)
            if before_send:
                before_send()
            bot.send_text(peer, bub, context_token=token)
            okb, err = _check_batch(captures[before:])
            if not okb:
                return False, err
            sent_bubbles.append(bub)
            if on_confirmed:
                on_confirmed(bub)
        try:
            time.sleep(float(bubble_gap_sec(bubbles[-1], **_gk) or 0.8))
        except Exception:
            time.sleep(0.8)
        return True, ""

    def _send_link():
        nonlocal link_sent
        if not link:
            return True, ""
        from bubble_split import bubble_gap_sec
        before = len(captures)
        if before_send:
            before_send()
        bot.send_text(peer, link, context_token=token)  # intact — no split_bubbles
        okb, err = _check_batch(captures[before:])
        if not okb:
            return False, err
        link_sent = True
        if on_confirmed:
            on_confirmed(link)
        try:
            time.sleep(float(bubble_gap_sec(link) or 0.8))
        except Exception:
            time.sleep(0.8)
        return True, ""

    try:
        need_image = bool(image_path)
        if not caption.strip() and not link and not need_image:
            return False, "ilink send fail: empty text and no image", None
        okc, err = _send_caption()
        if not okc:
            _persist_sent()
            return False, err, _partial(image_pending=need_image or bool(link))
        okl, err = _send_link()
        if not okl:
            _persist_sent()
            return False, err, _partial(image_pending=need_image)
        if need_image:
            from channels.ilink_weixin import ILinkWeixinRunner
            runner = ILinkWeixinRunner(credentials_file=creds, dry=False)
            runner._bot = bot
            runner._ctx[peer] = token or ""
            if before_send:
                before_send()
            try:
                runner._send_image_checked(peer, image_path, token, caption=None, before_send=before_send)
                if on_confirmed:
                    on_confirmed("[图片]")
            except ProactiveSleepBlocked:
                raise
            except Exception as e:
                _persist_sent()
                return False, f"ilink image fail: {e}", {**_partial(image_pending=True), "delivery_status": getattr(e, "delivery_status", "unknown")}
        mids = [c.get("message_id") for c in captures if c.get("message_id") is not None]
        return True, f"ilink:{peer[:24]}:mid={mids[-1] if mids else '?'}", None
    except ProactiveSleepBlocked as e:
        _persist_sent()
        return False, str(e), {**_partial(), "sleep_suppressed": True}
    except Exception as e:
        _persist_sent()
        return False, f"ilink send fail: {e!r}", {**_partial(image_pending=bool(image_path)),
            "delivery_status": getattr(e, "delivery_status", "unknown")}
    finally:
        api.send_message = _orig  # type: ignore



def _onebot_peer_and_cfg():
    """QQ 白名单 peer + channels.qq 段。"""
    from config import load
    p = load().get("persona") or {}
    ch = (p.get("channels") or {}).get("qq") or {}
    peer = None
    allow = ch.get("allow_user_ids") or ch.get("allow_from") or []
    if isinstance(allow, (str, int)):
        allow = [allow]
    if allow:
        peer = str(allow[0]).strip()
    if not peer:
        for k, v in (p.get("contact_aliases") or {}).items():
            if str(v) == "demo_contact" and str(k).startswith("qq_onebot:") and not str(k).endswith(":*"):
                peer = str(k).split(":", 1)[1]
                break
    from channels.persona_map import allowed_peer
    if peer and not allowed_peer("qq_onebot", peer):
        peer = None
    return peer, ch


def _onebot_http_ok(res) -> bool:
    """OneBot v11 HTTP: SENT only when retcode==0 (or "0").

    status=="ok" alone with nonzero/missing retcode must NOT count as success.
    """
    if not isinstance(res, dict):
        return False
    return (res.get("status") == "ok" and res.get("retcode") in (0, "0")
            and isinstance(res.get("data"), dict) and res["data"].get("message_id") is not None)


def send_via_onebot(text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None):
    """OneBot HTTP 发送。caption→link(intact)→image；见 send_via_ilink."""
    try:
        from channels.onebot_qq import (
            send_private_text_http,
            send_private_image_http,
        )
    except Exception as e:
        return False, f"onebot import: {e!r}", None
    peer, ch = _onebot_peer_and_cfg()
    if not peer:
        return False, "no qq peer in allow_user_ids/aliases", None
    api = (ch.get("http_api") or "http://127.0.0.1:3000").rstrip("/")
    token = ch.get("ws_token") or ch.get("http_token") or ""
    sent_bubbles: list[str] = []
    message_ids = []
    link = (link_url or "").strip()
    caption = (text or "") or ""
    link_sent = False

    def _partial(image_pending: bool = False):
        from channels.outbox_send import bubbles_suffix
        return {
            "sent_bubbles": list(sent_bubbles),
            "remaining_text": bubbles_suffix(caption, len(sent_bubbles)),
            "orig_text": caption,
            "link_url": None if link_sent else (link or None),
            "image_pending": bool(image_pending),
        }

    def _persist_sent():
        if on_confirmed or (not sent_bubbles and not link_sent):
            return
        try:
            from channels.outbox_send import persist_outbound_bubble, channel_tag
            from config import load as _load
            cid = str((_load().get("persona") or {}).get("primary_contact_id") or "demo_contact")
            tag = channel_tag("onebot")
            for i, bub in enumerate(sent_bubbles):
                persist_outbound_bubble(cid, tag, bub, occ=f"proactive:{i}", type_="outbox")
            if link_sent and link:
                persist_outbound_bubble(cid, tag, link, occ="proactive:link", type_="outbox")
        except Exception as e:
            print(f"[proactive] persist partial onebot skip: {e!r}", flush=True)

    def _gap_params():
        try:
            from bubble_split import bubble_gap_sec
            try:
                import sleep_chat as _sc
                _mode, _ = _sc.peek_mode(memory.read_state("demo_contact"))
                _sk, _gk = (
                    (_sc.split_params(_mode), _sc.gap_params(_mode))
                    if _mode in ("pre_sleep", "asleep")
                    else ({}, {})
                )
            except Exception:
                _sk, _gk = {}, {}
            return _sk, _gk, bubble_gap_sec
        except Exception:
            from bubble_split import bubble_gap_sec
            return {}, {}, bubble_gap_sec

    try:
        need_image = bool(image_path)
        if not caption.strip() and not link and not need_image:
            return False, "onebot send fail: empty text and no image", None
        if caption.strip():
            from channels.share_send import split_caption_bubbles
            _sk, _gk, bubble_gap_sec = _gap_params()
            bubbles = split_caption_bubbles(caption, **_sk) or [caption]
            for i, bub in enumerate(bubbles):
                if i:
                    time.sleep(bubble_gap_sec(bub, **_gk))
                if before_send:
                    before_send()
                res = send_private_text_http(api, peer, bub, token=token)
                if not _onebot_http_ok(res):
                    _persist_sent()
                    return False, f"onebot http fail: {res!r}", _partial(
                        image_pending=need_image or bool(link)
                    )
                message_ids.append((res.get("data") or {}).get("message_id"))
                sent_bubbles.append(bub)
                if on_confirmed:
                    on_confirmed(bub)
            try:
                time.sleep(float(bubble_gap_sec(bubbles[-1], **_gk) or 0.8))
            except Exception:
                time.sleep(0.8)
        if link:
            from bubble_split import bubble_gap_sec
            if before_send:
                before_send()
            res = send_private_text_http(api, peer, link, token=token)  # intact
            if not _onebot_http_ok(res):
                _persist_sent()
                return False, f"onebot http fail: {res!r}", _partial(image_pending=need_image)
            message_ids.append((res.get("data") or {}).get("message_id"))
            link_sent = True
            if on_confirmed:
                on_confirmed(link)
            try:
                time.sleep(float(bubble_gap_sec(link) or 0.8))
            except Exception:
                time.sleep(0.8)
        if need_image:
            if before_send:
                before_send()
            res = send_private_image_http(
                api, peer, str(image_path), caption=None, token=token
            )
            if not _onebot_http_ok(res):
                _persist_sent()
                return False, f"onebot http fail: {res!r}", _partial(image_pending=True)
            message_ids.append((res.get("data") or {}).get("message_id"))
            if on_confirmed:
                on_confirmed("[图片]")
        return True, f"onebot:{peer}:http message_ids={message_ids}", None
    except ProactiveSleepBlocked as e:
        _persist_sent()
        return False, str(e), {**_partial(), "sleep_suppressed": True}
    except Exception as e:
        _persist_sent()
        return False, f"onebot send fail: {e!r}", {**_partial(image_pending=bool(image_path)),
            "delivery_status": getattr(e, "delivery_status", "unknown")}



def _channel_to_transport(ch: str) -> str:
    c = (ch or "").lower()
    if c in ("qq_onebot", "qq", "onebot"):
        return "onebot"
    if c in ("wechat_ilink", "ilink", "wechat"):
        return "ilink"
    if c == "ui":
        return "ui"
    return "ilink"


def _resolve_transport(pc):
    t = (pc.get("transport") or "auto").lower()
    if t == "qq":
        return "onebot"
    if t in ("ilink", "ui", "onebot", "auto"):
        return t
    return "auto"


def _resolve_send_plan(pc):
    """Return ordered transports for this proactive send.

    Defaults: channel_policy=primary, primary_channel=wechat_ilink.
    primary → ONLY primary_channel transport (never dual-send; shares/beats
    included). A conflicting proactive.transport=ilink|onebot is overridden
    by primary_channel under primary (transport=ui still honored).
    mirror → primary then the other of {ilink, onebot} (never dual-ui).
    """
    policy = (pc.get("channel_policy") or "primary").lower()
    if policy not in ("primary", "mirror"):
        policy = "primary"
    primary_ch = pc.get("primary_channel") or "wechat_ilink"
    pref = _channel_to_transport(primary_ch)
    t = _resolve_transport(pc)
    if t == "auto":
        t = pref
        if t == "ilink":
            try:
                peer, _tok = _ilink_peer_and_ctx()
                from channels.ilink_weixin import _creds_path
                if not (peer and _creds_path().is_file()):
                    t = "ui"
            except Exception:
                t = "ui"
    elif policy == "primary" and t in ("ilink", "onebot") and pref in ("ilink", "onebot"):
        # Decouple: primary_channel is the source of truth for proactive/shares.
        t = pref
    plan = [t]
    if policy == "mirror" and t in ("ilink", "onebot"):
        other = "onebot" if t == "ilink" else "ilink"
        plan.append(other)
    return plan, policy


def run_once(*args, **kwargs):
    """Single worker even when launchd and a manual E2E tick overlap."""
    from config import snapshot
    import inspect
    call = inspect.signature(_run_once).bind_partial(*args, **kwargs)
    full = call.arguments.get("cfg_full") or load()
    with snapshot(full):
        return _run_pinned(call, full)


def _run_pinned(call, full):
    path = pathlib.Path(call.arguments.get("qpath") or (full.get("identity_runtime") or {}).get("proactive_queue") or QUEUE)
    call.arguments.update(cfg_full=full, qpath=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "WAIT", "reason": "主动 worker 正在运行"}
        try:
            result = _run_once(*call.args, **call.kwargs)
            if not call.arguments.get("dry", False):
                from personalization_runtime import report_loaded
                report_loaded("proactive", full)
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _audit_events(q, events, now):
    day = now.date().isoformat()
    if q.get("events_day") != day:
        q["events_day"], q["events"] = day, {}
    for event in events:
        key = event["key"]
        old = q["events"].get(key) or {}
        q["events"][key] = {**old, **event, "first_seen": old.get("first_seen") or _iso(now),
                            "last_seen": _iso(now)}


def _run_once(now=None, dry=False, force=False, ui=True, cfg_full=None, io=None, draft_fn=None, qpath=QUEUE, rows=None, st=None, rng=None):
    import random
    fixed_now = now
    send_now = lambda: fixed_now if fixed_now is not None else _now()
    full = cfg_full or load(); pc = full.get("proactive", {}); c = check_target(full); cid = str(c["id"]); now = now or _now()
    from runtime_policy import allowed, workflow_enabled
    if not workflow_enabled("proactive", full) or (not dry and not allowed("proactive", full)):
        return {"action": "SKIP", "reason": "identity proactive workflow/capability disabled"}
    rng = rng or random.Random()
    # Seed emotion/beats only on live runs.
    try:
        if not dry:
            memory.ensure_defaults(cid, now=now)
    except Exception as e:
        print(f"[proactive] ensure_defaults/heal skip: {e!r}", file=__import__("sys").stderr)
    q = load_q(qpath)
    import idle_contact
    try:
        raw_state = st if st is not None else memory.read_state(cid)
        clock_rows = rows if rows is not None else (ps.recent(cid) if (pc.get("idle_contact") or {}).get("enabled") else [])
        idle_clock = idle_contact.advance(now, raw_state, clock_rows, pc, q.get("idle_contact"))
        if idle_clock:
            q["idle_contact"] = idle_clock
            save_q(q, qpath)
    except Exception as exc:
        return {"action": "SKIP", "reason": f"久未联系计时检查失败: {exc!r}"}
    # sleep_chat：刷新晚安超时 → asleep，并强压制
    try:
        import sleep_chat as _sc
        if not dry and st is None:
            def refresh_sleep(current):
                _, fresh = _sc.refresh(current, now)
                return {_sc.STATE_KEY: fresh} if fresh != _sc.blob(current) else {}
            # A concurrent goodnight must not be replaced with an earlier snapshot.
            sleep_state = memory.mutate_state(cid, refresh_sleep)
        else:
            sleep_state = st if isinstance(st, dict) else memory.read_state(cid)
            _, fresh = _sc.refresh(sleep_state, now)
            if fresh is not None:
                sleep_state = {**sleep_state, _sc.STATE_KEY: fresh}
            if st is not None:
                st = sleep_state
        why = _sc.should_suppress_proactive(sleep_state, now)
        if why:
            return {"action": "SKIP", "reason": why, "idle_contact": idle_clock}
    except Exception as e:
        print(f"[proactive] sleep_chat skip: {e!r}", file=__import__("sys").stderr)
        return {"action": "SKIP", "reason": f"睡眠状态检查失败: {e!r}"}
    if not pc.get("enabled") and not force: return {"action": "SKIP", "reason": "proactive.enabled=false"}
    log = {"now": _iso(now)}
    from proactive_limiter import record_success
    for delivered in q.get("items", []):
        if not dry and delivered.get("status") in ("sending", "unknown", "partial", "sent") and delivered.get("delivered_pieces"):
            stamp = datetime.datetime.fromisoformat(delivered.get("first_confirmed_at") or delivered["delivery_started_at"])
            if delivered.get("accounted") is not True:
                for index, piece in enumerate(delivered["delivered_pieces"]):
                    _persist_confirmed_piece(cid, delivered, piece, index, stamp)
                record_success(cid, delivered if delivered["status"] == "sent" else _actual_partial(delivered), stamp)
                delivered["accounted"] = True
            if delivered["status"] == "sending":
                delivered["status"] = "unknown"
        if delivered.get("status") == "sent" and delivered.get("accounted") is False and not dry:
            record_success(cid, delivered, datetime.datetime.fromisoformat(delivered["sent_at"]))
            delivered["accounted"] = True
    if not dry and any(i.get("status") == "hold" for i in q.get("items", [])):
        from channels.outbox_store import load as load_outbox
        retried = {i.get("id"): i for channel in ("onebot", "ilink")
                   for i in load_outbox(channel).get("items", []) if i.get("status") == "sent"}
        for held in q.get("items", []):
            receipt = retried.get(held.get("id"))
            if (held.get("status") == "hold" and not held.get("delivery_continuations")
                    and receipt and receipt.get("proactive_round")):
                sent_at = receipt.get("sent_at") or _iso(now)
                record_success(cid, held, datetime.datetime.fromisoformat(sent_at))
                held.update(status="sent", sent_at=sent_at, accounted=True)
    if not dry:
        _sync_image_continuations(q, qpath)
    import share_dedupe as sd
    if not dry and (any(sd.material_of(i) for i in q.get("items", []))
                    or (st is None and memory.read_state(cid).get("proactive_beats", {}).get("shares"))):
        migrated = sd.migrate(cid, q.get("items", []))
        if st is not None:
            st = {**st, sd.STATE_KEY: migrated.get(sd.STATE_KEY, {})}
    nca = _next_at(q)
    item = due(q, now)
    if item is None and not dry:
        _relay_ready, _relay_changed = _poll_relay_waits(q, now, st if isinstance(st, dict) else None, cid)
        if _relay_changed:
            save_q(q, qpath)
        if _relay_ready is not None:
            item = _relay_ready
    if not item:
        st0 = st if st is not None else memory.read_state(cid)
        rows0 = rows if rows is not None else ps.recent(cid)
        import proactive_beats as pb
        # Pending transport retries own their events until TTL; don't generate duplicates.
        st0 = {**st0, "proactive_beats": pb._beats_state(st0, now)}
        if q.get("events_day") == now.date().isoformat():
            st0["_declined_surf_ids"] = {
                key.split("@", 1)[1] for key, event in (q.get("events") or {}).items()
                if key.startswith("surf_share@") and event.get("status") == "model_declined"
            }
        for pending in q.get("items", []):
            if (pending.get("status") in ("sending", "unknown", "partial", "image_wait") or
                    (pending.get("status") == "hold" and pending.get("expires", "") >= _iso(now))):
                pm = (pending.get("meta") or {}).get("beat_meta") or {}
                for key in [pm.get("done_key") or pm.get("id"), *(pm.get("merged_keys") or [])]:
                    if key:
                        st0["proactive_beats"]["done"][key] = "pending_transport"
        candidates = pb.due_beats(now, st0, pc)
        if not candidates and not (idle_clock or {}).get("due") and nca and datetime.datetime.fromisoformat(nca) > now and not force:
            save_q(q, qpath)
            return {"action": "WAIT", "reason": f"未到下次普通闲聊考虑 {nca}"}
        if not dry:
            st0, _miss = _maybe_apply_unanswered_miss(cid, st0, rows0, pc, now)
        r = ps.tick(now, dict(pc, enabled=True), st0, rows0, rng, force=force, draft_fn=draft_fn, idle_clock=idle_clock)
        events = r.get("events") or []
        if r.get("beat") and r.get("action") == "SKIP":
            for event in events:
                if event["key"].split("@")[0] == r["beat"]:
                    event.update(status="model_declined" if r.get("share_declined") else "generation_skipped",
                                 reason=r["reason"])
        _audit_events(q, events, now)
        nxt = r.get("next_consider") or r["next_check"]
        # SKIP 后仍按忙闲排下次考虑；成功入队后若最终发送再 after_send 覆盖
        _set_next(q, nxt); log.update(tick=r["action"], reason=r["reason"], topic=r["topic"], busy=r.get("busy"))
        draft = r.get("draft")
        bare_share = r.get("beat") == "surf_share" and draft == ""
        if r["action"] != "DRAFT" or (not draft and not bare_share):
            save_q(q, qpath); return {"action": "SKIP", "reason": r["reason"], "events": r.get("events", []), "next_consider_at": _next_at(q), "next_check_at": _next_at(q)}
        if draft and r.get("beat") not in (idle_contact.BEAT, "surf_share") and is_dup(draft, q, rows0, now=now):
            save_q(q, qpath); return {"action": "SKIP", "reason": "草稿与最近消息重复，丢弃", "draft": draft}
        meta = {"beat": r.get("beat"), "beat_done_key": r.get("beat_done_key"), "beat_meta": r.get("beat_meta")}
        if r.get("photo_intent"):
            meta["photo_intent"] = r["photo_intent"]
        if draft_fn is None and r.get("beat") != "surf_share" and not r.get("idle_fallback"):
            import brain
            meta["pending_delivery"] = copy.deepcopy(brain.LAST.get("pending_delivery"))
        item = enqueue(q, draft, r["topic"], now, pc.get("ttl_min", 30), meta=meta)
        save_q(q, qpath)
    # 发送前重检（状态可能在入队后变化）
    st1 = st if st is not None else memory.read_state(cid); rows1 = rows if rows is not None else ps.recent(cid)
    _beat_for_gate = (item.get("meta") or {}).get("beat_meta")
    is_surf = ((_beat_for_gate or {}).get("id") == "surf_share"
               or str(item.get("topic") or "").endswith("surf_share"))
    share_url = (sd.material_of(item).get("source_url") or "").strip() if is_surf else ""
    from proactive_limiter import is_routine
    if is_routine(_beat_for_gate):
        # Old queued reports may still carry the previous mandatory-image flag.
        _beat_for_gate.pop('image_required', None)
    from schedule_execution import beat_blocked
    if beat_blocked(_beat_for_gate, send_now(), st1):
        item.update(status='dropped', note='外出计划未确认或已取消')
        save_q(q, qpath)
        return dict(action='DROP', reason=item['note'])
    g = ps.gates(send_now(), st1, pc, ps.context(rows1, send_now(), st1), beat=_beat_for_gate)
    if g:
        item["status"], item["note"] = "dropped", g; save_q(q, qpath); return {"action": "DROP", "reason": g, "draft": item["text"]}
    if sd.duplicate(sd.material_of(item), st1, owner=item["id"]):
        item["status"], item["note"] = "dropped", "分享内容已发送或待确认，禁止重复"
        save_q(q, qpath)
        return {"action": "DROP", "reason": item["note"]}
    if (not is_surf and (_beat_for_gate or {}).get("id") != idle_contact.BEAT
            and is_dup(item["text"], q, rows1, now=send_now(), exclude_id=item["id"])):
        item.update(status="dropped", note="待发内容与近期报备重复")
        save_q(q, qpath)
        return {"action": "DROP", "reason": item["note"]}
    # Keep the same scene from drafting to generation. A later quota check may
    # require a photo, but it cannot invent a new scene for an existing caption.
    import proactive_media
    bm = _beat_for_gate or {}
    drafted_photo = (item.get("meta") or {}).get("photo_intent")
    planned_scene = proactive_media.planned_scene(bm)
    photo_required = not is_surf and (proactive_media.required(st1, send_now(), pc, bm)
                                       or bm.get("image_required"))
    photo_expected = not is_surf and bool(drafted_photo or planned_scene or bm.get("idle_style") == "photo")
    _photo_schedule = None
    _intent = None
    _img_ready = None  # (scene, caption, path|None, err|None, paths[], texts[])
    photo_error = None
    try:
        import chat_media as _cm
        if not is_surf:
            if drafted_photo:
                _intent = {**drafted_photo, "caption": item["text"]}
            else:
                _intent = _cm.parse_image_intent(item["text"])
            if _intent is None and planned_scene:
                _intent = {"scene": planned_scene, "caption": item["text"]}
            photo_expected = photo_expected or _intent is not None
        _relay_intent = None
        if _intent and bm.get("id") != idle_contact.BEAT and photo_required:
            _relay_intent = _make_relay_intent(
                cid, item, _beat_for_gate or {}, _intent["scene"], _intent.get("caption") or "")
        _relay_filled = False
        if _intent and not dry and _relay_intent:
            import image_relay_resume as _irr
            _found = None
            _link = _relay_link(item)
            if _link:
                try:
                    _found = (pathlib.Path(_link["pending_path"]), json.loads(pathlib.Path(_link["pending_path"]).read_text()))
                except Exception:
                    _found = None
                if _found is None:
                    item.update(status="image_isolated", note="relay pending 缺失，不重新生成")
                    save_q(q, qpath)
                    print(f"[proactive] relay 隔离 item={item.get('id')} reason=missing", flush=True)
                    return {"action": "DROP", "reason": item["note"]}
            else:
                _directory = _relay_dir(full)
                if _directory:
                    _found = _irr.find_reusable(_directory, _relay_intent["send_intent"]["intent_id"])
            if _found:
                _path, _data = _found
                _kind = _irr.assess(_data, _relay_intent["send_intent"]["intent_id"])
                if _kind == "open":
                    _park_relay(item, _path, _data, _relay_intent)
                    save_q(q, qpath)
                    print(f"[proactive] relay 复用未完成 job={_data.get('job_id')} intent={_relay_intent['send_intent']['intent_id']}", flush=True)
                    return {"action": "WAIT", "reason": "图片转接未完成，保留发送意图",
                            "relay_job": _data.get("job_id"), "relay_status": _data.get("status")}
                if _kind == "ready":
                    _ok, _why = _revalidate_relay(_data, send_now(), st1, _beat_for_gate or {}, cid)
                    if not _ok:
                        _irr.block(_path, _data["send_intent"]["intent_id"], _why)
                        item.update(status="dropped", note=_why)
                        save_q(q, qpath)
                        print(f"[proactive] relay 过期或场景失效 job={_data.get('job_id')} reason={_why}", flush=True)
                        return {"action": "DROP", "reason": _why}
                    if not _irr.claim(_path, _data["send_intent"]["intent_id"]):
                        item.update(status="unknown", note="relay 已领取但无发送回执，不重发")
                        save_q(q, qpath)
                        print(f"[proactive] relay 已领取 job={_data.get('job_id')}，不重发", flush=True)
                        return {"action": "HOLD", "reason": item["note"], "delivery_status": "unknown"}
                    item.setdefault("meta", {})["relay_resume"] = {
                        "protocol": 1, "claimed": True, "job_id": _data.get("job_id"),
                        "intent_id": _data["send_intent"]["intent_id"], "pending_path": str(_path),
                        "scene": (_data.get("send_intent") or {}).get("scene") or _intent["scene"],
                    }
                    _result = _data.get("result_path")
                    _cap = (_data.get("send_intent") or {}).get("caption") or _intent.get("caption") or ""
                    _photo_schedule = _data.get("photo_schedule")
                    _img_ready = ((_data.get("send_intent") or {}).get("scene") or _intent["scene"], _cap, _result, None, [_result], [_cap])
                    _relay_filled = True
                elif _link or _kind in ("unknown", "closed", "failed", "broken", "legacy", "mismatch"):
                    item.update(status="unknown" if _kind == "closed" else "image_isolated",
                                note=f"relay {_kind}，不发送")
                    save_q(q, qpath)
                    print(f"[proactive] relay 不发送 job={_data.get('job_id')} kind={_kind}", flush=True)
                    return {"action": "HOLD" if _kind == "closed" else "DROP", "reason": item["note"],
                            "delivery_status": "unknown" if _kind == "closed" else None}
        if _intent and not dry and not _relay_filled:
            try:
                _bm0 = (item.get("meta") or {}).get("beat_meta") or {}
                _img_meta = (_bm0.get("image") or {})
                _shot = _intent.get("shot_type") or _img_meta.get("shot_type")
                _bits = _img_meta.get("shot_bits")
                _slot = _bm0.get("image_slot") or _bm0.get("slot")
                if not isinstance(_slot, dict):
                    _slot = None
                _date = None
                if _slot and _slot.get("date"):
                    _date = _slot.get("date")
                elif _slot and _slot.get("_date"):
                    _date = _slot.get("_date")
                # 主动节拍：按 proactive_image_count_p 采样 N∈[1,5]，plan_chat_images 一次出齐
                _plan_kw = {"cfg": full}
                if _relay_intent:
                    _plan_kw["relay_resume"] = _relay_intent
                _plan = _cm.plan_chat_images(
                    _intent["scene"],
                    caption=_intent.get("caption") or "",
                    shot_type=_shot, shot_bits=_bits,
                    slot=_slot,
                    image_lane="proactive",
                    **({"n_images": 1} if _bm0.get("id") == idle_contact.BEAT else {}),
                    date=_date,
                    **_plan_kw,
                )
                _photo_schedule = _plan.get("photo_schedule")
                _paths = list(_plan.get("paths") or [])
                _path = _paths[0] if _paths else None
                _img_ready = (
                    _intent["scene"],
                    _intent.get("caption") or "",
                    _path,
                    None,
                    _paths,
                    list(_plan.get("texts") or []),
                )
            except Exception as e:
                import imagegen as _imagegen
                if isinstance(e, _imagegen.RelayPendingError) and _relay_intent and e.resume_protocol == 1:
                    try:
                        _data = json.loads(pathlib.Path(e.job_path).read_text())
                    except Exception:
                        _data = {"job_id": e.job_id, "status": e.status, "send_intent": _relay_intent.get("send_intent")}
                    _park_relay(item, e.job_path, _data, _relay_intent)
                    save_q(q, qpath)
                    print(f"[proactive] relay 等待 job={e.job_id} intent={_relay_intent['send_intent']['intent_id']} status={e.status}", flush=True)
                    return {"action": "WAIT", "reason": "图片转接未完成，保留发送意图",
                            "relay_job": e.job_id, "relay_status": e.status}
                _img_ready = (_intent["scene"], _intent.get("caption") or "", None, e, [], [])
                photo_error = e
                print(f"[proactive] pre-generate failed: {e!r}", file=__import__("sys").stderr)
        elif _intent and dry:
            _img_ready = (_intent["scene"], _intent.get("caption") or "", None, None, [], [])
    except Exception as exc:
        photo_error = exc
        _img_ready = None
        print(f"[proactive] photo preparation failed: {exc!r}", file=sys.stderr)

    # 节拍附带的 material_store 真图（meal / surf_share）
    _beat_img = None
    _meta = item.get("meta") or {}
    _bm = _meta.get("beat_meta") or {}
    for key in (("material",) if is_surf else ("image", "material")):
        blob = _bm.get(key) or {}
        path = blob.get("image_path") if isinstance(blob, dict) else None
        if path and pathlib.Path(path).is_file():
            _beat_img = path
            break
    # Only the stored original link and screenshot may be sent with a share.
    if is_surf and (not _beat_img or not share_url):
        item["status"], item["note"] = "dropped", "surf_share 无截图" if not _beat_img else "surf_share 无原链"
        save_q(q, qpath)
        return {"action": "DROP", "reason": item["note"], "draft": item["text"]}

    try:
        check_proactive_send(st if st is not None else memory.read_state(cid), send_now(), beat=_beat_for_gate or {})
    except ProactiveSleepBlocked as exc:
        item.update(status="dropped", note=str(exc))
        save_q(q, qpath)
        return {"action": "DROP", "reason": item["note"]}
    generated_image = bool(_img_ready and _img_ready[2] and pathlib.Path(_img_ready[2]).is_file())
    has_image = generated_image or bool(_beat_img)
    if not dry and photo_expected and not generated_image:
        from photo_schedule import SchedulePhotoError
        reason = (f"照片场景校验未通过：{photo_error}" if isinstance(photo_error, SchedulePhotoError)
                  else "照片没有生成成功，本轮不发送")
        item.update(status="dropped", note=reason)
        save_q(q, qpath)
        return {"action": "DROP", "reason": reason}
    item.setdefault("meta", {})["has_image"] = has_image
    if not dry and photo_required and not has_image:
        item.update(status="dropped", note="本轮需要图片，但草稿没有可用照片，本轮不发送")
        save_q(q, qpath)
        return {"action": "DROP", "reason": item["note"]}

    plan, policy = _resolve_send_plan(pc)
    transport = plan[0]
    _primary_sent = False
    _send_notes = []

    def _img_ready_paths():
        """Paths from proactive plan_chat_images (N≥1 one-shot); empty if none."""
        if not _img_ready:
            return []
        if len(_img_ready) >= 5 and _img_ready[4]:
            return [str(x) for x in _img_ready[4] if x]
        if _img_ready[2] is not None:
            return [str(_img_ready[2])]
        return []

    def _prepare_send_payload():
        """Return (caption, image_path, link_url, image_paths) — link never glued into caption.

        image_path = first path (compat); image_paths = full N-list for one-shot multi send.
        """
        send_paths = []
        send_path = _beat_img
        send_text = item["text"]
        ready_paths = _img_ready_paths()
        if ready_paths:
            send_paths = ready_paths
            send_path = ready_paths[0]
            send_text = (_img_ready[1] if _img_ready else None) or send_text
            # multi: prefer plan texts[0] as opener if present
            if _img_ready and len(_img_ready) >= 6 and _img_ready[5]:
                t0 = _img_ready[5][0]
                if t0:
                    send_text = t0
            send_text = "\n".join(
                ln for ln in (send_text or "").splitlines()
                if not ln.strip().upper().startswith("WANT_IMAGE")
            )
        elif _img_ready and _img_ready[1]:
            send_text = _img_ready[1]
        elif send_path:
            send_paths = [str(send_path)]
        link_url = ""
        if is_surf:
            from channels.share_send import peel_caption_and_link
            send_text, link_url = peel_caption_and_link(send_text, source_url=share_url)
        if _bm.get("id") == idle_contact.BEAT and _bm.get("idle_style") == "photo" and send_path:
            send_text = ""
            send_paths = send_paths[:1]
        return send_text, send_path, link_url, send_paths

    def _is_surf_require_media():
        if is_surf:
            return True
        try:
            return bool((pc.get("beats") or {}).get("surf_require_media", True)) and (
                "surf_share" in str((_bm.get("done_key") or ""))
            )
        except Exception:
            return False

    def _enqueue_channel_outbox(channel_key, peer, send_text, why, send_path=None,
                                   *, sent_bubbles=None, orig_text=None, link_url=None,
                                   image_index=None, suffix=False):
        """Enqueue fail-closed send. Returns (ok, oid). Carries image_path when present.

        send_text should already be the *unsent suffix* when some bubbles succeeded.
        sent_bubbles / orig_text let flush resume without resending the prefix.
        Skip enqueue when suffix empty and no image remains.
        """
        try:
            if channel_key == "ilink":
                from channels import ilink_weixin as _iw
                ob = _iw._load_outbox()
                save = _iw._save_outbox
                tag = "ilink"
            else:
                from channels import onebot_qq as _ob
                ob = _ob._load_outbox()
                save = _ob._save_outbox
                tag = "onebot"
            # Nothing left to deliver
            if not (send_text or "").strip() and not send_path and not link_url:
                print(f"[proactive] skip empty outbox enqueue ({tag})", flush=True)
                return True, None
            ids = {x.get("id") for x in (ob.get("items") or [])}
            oid = item.get("id") or f"hold-{int(time.time())}"
            if image_index is not None:
                oid = f"{oid}:img{image_index}"
            elif suffix:
                oid = f"{oid}:suffix"
            if oid not in ids:
                it = {
                    "id": oid,
                    "peer": peer,
                    "contact": cid,
                    "text": send_text or "",
                    "reason": f"proactive {tag}: {why}",
                    "created": _iso(now),
                    "expires": item.get("expires"),
                    "status": "pending",
                    "channel": tag,
                }
                if image_index is not None or suffix:
                    it["proactive_parent_id"] = item["id"]
                    it["proactive_progress"] = {k: copy.deepcopy(item[k]) for k in ("id", "meta", "text", "expires")}
                    if send_path:
                        it["require_media"] = True
                elif not _primary_sent:
                    it["proactive_round"] = {k: item[k] for k in ("id", "meta", "text", "expires")}
                if send_path:
                    it["image_path"] = str(send_path)
                    if _photo_schedule:
                        it["photo_schedule"] = _photo_schedule
                if link_url:
                    it["link_url"] = str(link_url)
                if sent_bubbles:
                    it["sent_bubbles"] = list(sent_bubbles)
                    it["sent_bubble_count"] = len(sent_bubbles)
                if orig_text is not None:
                    it["orig_text"] = orig_text
                if _is_surf_require_media() or ((photo_expected or photo_required) and send_path):
                    it["require_media"] = True
                    it["surf_require_media"] = _is_surf_require_media()
                # slot stamp for flush freshness (查岗 / slot_changed)
                _slot = (_bm.get("slot") or {}) if isinstance(_bm, dict) and not is_surf else {}
                if _slot.get("bucket"):
                    it["slot_bucket"] = _slot.get("bucket")
                if _slot.get("start"):
                    it["slot_start"] = _slot.get("start")
                if _bm.get("id"):
                    it["beat_id"] = _bm.get("id")
                # Stamp ask_him so flush success can green 「今日已问安排」
                # (immediate SENT path already marks; HOLD/enqueue must NOT).
                try:
                    import schedule_fidelity as _sf
                    _mark_ask = _sf.beat_meta_marks_ask_his_day(_bm)
                except Exception:
                    _mark_ask = (
                        (_bm.get("id") == "ask_his_day")
                        or str(_bm.get("done_key") or "").startswith("ask_his_day")
                        or (_bm.get("id") in ("schedule_brief", "activity_end")
                            and _bm.get("ask_him"))
                    )
                if _mark_ask:
                    it["ask_him"] = True
                if image_index is not None or suffix:
                    # Save enough information to repair a crash between queue
                    # and outbox writes. Continuations never charge a new round.
                    entries = item.setdefault("delivery_continuations", [])
                    previous = [entry["item"]["id"] for entry in entries if entry["channel"] == tag]
                    if previous:
                        it["after_delivery_id"] = previous[-1]
                    if not any(e["item"]["id"] == oid for e in entries):
                        entries.append({"channel": tag, "item": dict(it)})
                        save_q(q, qpath)
                ob.setdefault("items", []).append(it)
                save(ob)
                print(f"[proactive] queued {tag} outbox id={oid}", flush=True)
            return True, oid
        except Exception as e:
            print(f"[proactive] outbox enqueue skip: {e!r}", flush=True)
            return False, None

    def _clear_channel_outbox(channel_key, oid):
        """Mark same-id outbox entry sent/cleared after UI SENT (avoid double-send on flush)."""
        if not oid:
            return
        try:
            if channel_key == "ilink":
                from channels import ilink_weixin as _iw
                ob = _iw._load_outbox(); save = _iw._save_outbox
            else:
                from channels import onebot_qq as _ob
                ob = _ob._load_outbox(); save = _ob._save_outbox
            changed = False
            for it in (ob.get("items") or []):
                if it.get("id") == oid and it.get("status") in (None, "pending", "hold", "补发"):
                    it["status"] = "sent"
                    it["sent_at"] = _iso(now)
                    it["note"] = "cleared after UI SENT (mutually exclusive with outbox)"
                    changed = True
            if changed:
                save(ob)
                print(f"[proactive] cleared {channel_key} outbox id={oid} after UI SENT", flush=True)
        except Exception as e:
            print(f"[proactive] outbox clear skip: {e!r}", flush=True)

    def _before_send():
        if _photo_schedule:
            try:
                from photo_schedule import check
                check(_photo_schedule, send_now())
            except ValueError as exc:
                raise ProactiveSleepBlocked(str(exc)) from exc
        # Validate the whole attachment set before any caption leaves. A
        # missing later image must not silently turn this into a partial plan.
        for path in item.get("planned_image_paths") or _prepare_send_payload()[3]:
            if not pathlib.Path(path).is_file():
                raise ProactiveSleepBlocked("图片结果缺失，取消本轮剩余内容")
            try:
                from photo_schedule import check_file
                check_file(path, send_now())
            except (ValueError, OSError) as exc:
                raise ProactiveSleepBlocked(f"图片校验失败：{exc}") from exc
        # Live runs reread state/time after generation and every inter-bubble gap.
        try:
            current = st if st is not None else memory.read_state(cid)
        except Exception as exc:
            raise ProactiveSleepBlocked(f"睡眠状态读取失败，停止主动发送: {exc!r}") from exc
        check_proactive_send(current, send_now(), beat=_beat_for_gate or {})
        if (_beat_for_gate or {}).get("id") == idle_contact.BEAT:
            if current.get("proactive_paused"):
                raise ProactiveSleepBlocked("用户要求暂停主动联系")
            delivered = bool(item.get("delivered_pieces"))
            fresh_rows = [] if delivered else (rows if rows is not None else ps.recent(cid))
            reason = idle_contact.stale_reason(_beat_for_gate, current, fresh_rows, delivered=delivered)
            if reason:
                raise ProactiveSleepBlocked(reason)

    def _account_partial():
        if item.get("delivered_pieces"):
            record_success(cid, _actual_partial(item), datetime.datetime.fromisoformat(item["first_confirmed_at"]))
            item["accounted"] = True

    def _sleep_drop(reason, *, keep_relay=False):
        if item.get("delivered_pieces"):
            _account_partial()
            keep_relay = False
        link = _relay_link(item)
        if link and link.get("claimed") and keep_relay and not item.get("delivered_pieces"):
            import image_relay_resume as _irr
            if _irr.rollback_unsent(link["pending_path"], link["intent_id"]):
                link["claimed"] = False
                link.pop("sealed_status", None)
                item.update(status="image_wait", note=reason)
                save_q(q, qpath)
                print(f"[proactive] relay 延后 job={link.get('job_id')} reason={reason}", flush=True)
                return {"action": "WAIT", "reason": reason, "relay_job": link.get("job_id")}
            item.update(status="unknown", note=f"relay 回执不明，不重发：{reason}")
            save_q(q, qpath)
            return {"action": "HOLD", "reason": item["note"], "delivery_status": "unknown"}
        if link and not keep_relay:
            import image_relay_resume as _irr
            _irr.block(link["pending_path"], link["intent_id"], reason)
        item.update(status="dropped", note=reason)
        save_q(q, qpath)
        return {"action": "DROP", "reason": reason, "draft": item["text"]}

    try:
        _before_send()
    except ProactiveSleepBlocked as exc:
        return _sleep_drop(str(exc), keep_relay=True)

    # Image planning can replace the caption; recheck the actual payload after
    # that slow operation against fresh history before any transport side effect.
    if not is_surf and (_beat_for_gate or {}).get("id") != idle_contact.BEAT:
        final_caption = _prepare_send_payload()[0]
        fresh_rows = rows if rows is not None else ps.recent(cid)
        if final_caption and is_dup(final_caption, q, fresh_rows, now=send_now(), exclude_id=item["id"]):
            return _sleep_drop("最终配文与近期报备重复")

    # Claim durably before the first caption/link/image leaves the process.
    # Outbox retries keep this round id; a new round can never claim it again.
    if not dry and sd.material_of(item) and not sd.reserve(cid, item, now):
        item["status"], item["note"] = "dropped", "分享内容已占用，禁止重复发送"
        save_q(q, qpath)
        return {"action": "DROP", "reason": item["note"]}

    # Reserve the whole round before network I/O. A process crash leaves an
    # unresolved round; it must never be replayed blindly after restart.
    if not dry:
        item.update(status="sending", delivery_started_at=_iso(send_now()))
        item["planned_image_paths"] = _prepare_send_payload()[3]
        item["remaining_image_paths"] = list(item["planned_image_paths"])
        save_q(q, qpath)

    def _confirmed_piece(piece):
        stamp = send_now()
        pieces = item.setdefault("delivered_pieces", [])
        index = len(pieces)
        pieces.append(piece)
        if piece == "[图片]" and item.get("current_image_path"):
            path = str(item["current_image_path"])
            if path not in item.setdefault("delivered_image_paths", []):
                item["delivered_image_paths"].append(path)
            item["remaining_image_paths"] = [p for p in item.get("planned_image_paths", [])
                                             if p not in item["delivered_image_paths"]]
        item.setdefault("first_confirmed_at", _iso(stamp))
        save_q(q, qpath)
        _persist_confirmed_piece(cid, item, piece, index, stamp)

    def _unresolved(why, partial):
        status = (partial or {}).get("delivery_status")
        if status != "unknown" and not item.get("delivered_pieces"):
            return None
        item.update(status="unknown" if status == "unknown" else "partial", note=why)
        _account_partial()
        save_q(q, qpath)
        return {"action": "HOLD", "reason": why, "delivery_status": item["status"],
                "sent_bubbles": list(item.get("delivered_pieces") or []), "round_id": item["id"]}

    def _primary_failure(channel, why, partial):
        # Only explicitly unsent payloads are retryable. An ambiguous ACK must
        # never be turned into a new send, even if an earlier bubble succeeded.
        if partial is None and item.get("delivered_pieces"):
            partial = {"delivery_status": "unknown"}
        result = _unresolved(why, partial)
        if (partial or {}).get("delivery_status") == "unknown":
            return result
        item.update(status="partial" if item.get("delivered_pieces") else "hold", note=why)
        text, image, link, paths = _prepare_send_payload()
        if partial:
            if "remaining_text" in partial:
                text = partial["remaining_text"]
            else:
                from channels.outbox_send import split_outbound_bubbles
                bubbles = split_outbound_bubbles(text)
                delivered = [piece for piece in item.get("delivered_pieces", [])
                             if piece != "[图片]" and piece != link]
                if bubbles[:len(delivered)] != delivered:
                    return _unresolved("已发前缀与计划不一致，不猜测重发", {"delivery_status": "unknown"})
                text = "\n".join(bubbles[len(delivered):])
            link = "" if link in item.get("delivered_pieces", []) else partial.get("link_url", link)
            if not partial.get("image_pending", str(image) not in item.get("delivered_image_paths", [])):
                image = None
        peer = _ilink_peer_and_ctx()[0] if channel == "ilink" else _onebot_peer_and_cfg()[0]
        _enqueue_channel_outbox(channel, peer, text, why, send_path=image, link_url=link, suffix=True)
        for index, path in enumerate(paths[1:], start=2):
            if path not in item.get("delivered_image_paths", []):
                _enqueue_channel_outbox(channel, peer, "", why, send_path=path, image_index=index)
        save_q(q, qpath)
        return {"action": "HOLD", "reason": why, "delivery_status": item["status"],
                "sent_bubbles": list(item.get("delivered_pieces") or []), "round_id": item["id"],
                "remaining_image_paths": list(item.get("remaining_image_paths") or [])}

    def _extra_images(channel, paths):
        send_image = send_via_ilink if channel == "ilink" else send_via_onebot
        for index, path in enumerate(paths[1:], start=2):
            item["current_image_path"] = path
            save_q(q, qpath)
            try:
                ok, why, partial = _unpack_send_result(send_image(
                    "", image_path=path, link_url=None, before_send=_before_send, on_confirmed=_confirmed_piece))
            except ProactiveSleepBlocked as exc:
                return _sleep_drop(str(exc))
            except Exception as exc:
                ok, why, partial = False, str(exc), {"delivery_status": getattr(exc, "delivery_status", "unknown")}
            if ok:
                continue
            if (partial or {}).get("sleep_suppressed"):
                return _sleep_drop(why)
            status = (partial or {}).get("delivery_status")
            result = _unresolved(why, partial) or {"action": "HOLD", "reason": why}
            if status != "unknown":
                peer = _ilink_peer_and_ctx()[0] if channel == "ilink" else _onebot_peer_and_cfg()[0]
                for remaining_index, remaining_path in enumerate(paths[index-1:], start=index):
                    _enqueue_channel_outbox(channel, peer, "", why, send_path=remaining_path,
                                            image_index=remaining_index)
            result["remaining_image_paths"] = list(item.get("remaining_image_paths") or [])
            return result
        return None

    # ---- primary transport ----
    if transport == "ilink":
        if dry:
            _dry_text, _dry_path, _dry_link, _dry_paths = _prepare_send_payload()
            item["status"], item["note"] = "dry", f"ilink policy={policy}"
            save_q(q, qpath)
            return {"action": "DRY", "reason": "ilink", "would_send": _dry_text,
                    "would_link": _dry_link,
                    "would_image": (_dry_paths[0] if _dry_paths else None) or _dry_path or _beat_img or ((_img_ready or (None, None, None, None))[2]),
                    "channel_policy": policy, "plan": plan}
        send_text, send_path, send_link, send_paths = _prepare_send_payload()
        item["current_image_path"] = str(send_path) if send_path else None
        ok, why, partial = _unpack_send_result(
            send_via_ilink(send_text, image_path=send_path, link_url=send_link or None, before_send=_before_send, on_confirmed=_confirmed_piece)
        )
        if not ok and (partial or {}).get("sleep_suppressed"):
            return _sleep_drop(why, keep_relay=not item.get("delivered_pieces"))
        if not ok:
            return _primary_failure("ilink", why, partial)
        else:
            if send_path:
                pending_images = _extra_images("ilink", send_paths)
                if pending_images:
                    return pending_images
                item["note"] = f"ilink_images:{len(send_paths)}"
                _n = max(1, len(send_paths or [send_path]))
                item["text"] = ((send_text + " ") if send_text else "") + (f"[图片x{_n}]" if _n > 1 else "[图片]")
            else:
                item["note"] = why
            ui = False
            _primary_sent = True
            _send_notes.append(why)
    elif transport == "onebot":
        if dry:
            _dry_text, _dry_path, _dry_link, _dry_paths = _prepare_send_payload()
            item["status"], item["note"] = "dry", f"onebot policy={policy}"
            save_q(q, qpath)
            return {"action": "DRY", "reason": "onebot", "would_send": _dry_text,
                    "would_link": _dry_link,
                    "would_image": (_dry_paths[0] if _dry_paths else None) or _dry_path or _beat_img or ((_img_ready or (None, None, None, None))[2]),
                    "channel_policy": policy, "plan": plan}
        send_text, send_path, send_link, send_paths = _prepare_send_payload()
        item["current_image_path"] = str(send_path) if send_path else None
        ok, why, partial = _unpack_send_result(
            send_via_onebot(send_text, image_path=send_path, link_url=send_link or None, before_send=_before_send, on_confirmed=_confirmed_piece)
        )
        if not ok and (partial or {}).get("sleep_suppressed"):
            return _sleep_drop(why, keep_relay=not item.get("delivered_pieces"))
        if not ok:
            return _primary_failure("onebot", why, partial)
        if send_path:
            pending_images = _extra_images("onebot", send_paths)
            if pending_images:
                return pending_images
            item["note"] = f"onebot_images:{len(send_paths)}"
            _n = max(1, len(send_paths or [send_path]))
            item["text"] = ((send_text + " ") if send_text else "") + (f"[图片x{_n}]" if _n > 1 else "[图片]")
        else:
            item["note"] = why
        ui = False
        _primary_sent = True
        _send_notes.append(why)
    else:
        _primary_sent = False

    # mirror secondary (best-effort; never fails the whole SENT)
    if _primary_sent and policy == "mirror" and len(plan) > 1:
        send_text, send_path, send_link, send_paths = _prepare_send_payload()
        for sec in plan[1:]:
            if sec == "onebot":
                ok2, why2, partial2 = _unpack_send_result(
                    send_via_onebot(send_text, image_path=send_path, link_url=send_link or None, before_send=_before_send, on_confirmed=_confirmed_piece)
                )
                if not ok2 and (partial2 or {}).get("sleep_suppressed"):
                    _send_notes.append(f"mirror cancelled: {why2}")
                    break
                if not ok2 and (partial2 or {}).get("delivery_status") == "unknown":
                    _send_notes.append(f"mirror unknown, no retry: {why2}")
                    continue
                if not ok2:
                    peer, _ch = _onebot_peer_and_cfg()
                    _et, _ep, _sb, _ot, _lk = send_text, send_path, None, None, send_link
                    if isinstance(partial2, dict):
                        _et = partial2.get("remaining_text", send_text)
                        _sb = partial2.get("sent_bubbles")
                        _ot = partial2.get("orig_text")
                        if "link_url" in partial2:
                            _lk = partial2.get("link_url") or None
                        if not partial2.get("image_pending"):
                            _ep = None
                    _enqueue_channel_outbox(
                        "onebot", peer, _et, why2, send_path=_ep,
                        sent_bubbles=_sb, orig_text=_ot, link_url=_lk,
                    )
                    print(f"[proactive] mirror onebot HOLD→outbox: {why2}", flush=True)
                else:
                    _send_notes.append(f"mirror:{why2}")
            elif sec == "ilink":
                ok2, why2, partial2 = _unpack_send_result(
                    send_via_ilink(send_text, image_path=send_path, link_url=send_link or None, before_send=_before_send, on_confirmed=_confirmed_piece)
                )
                if not ok2 and (partial2 or {}).get("sleep_suppressed"):
                    _send_notes.append(f"mirror cancelled: {why2}")
                    break
                if not ok2 and (partial2 or {}).get("delivery_status") == "unknown":
                    _send_notes.append(f"mirror unknown, no retry: {why2}")
                    continue
                if not ok2:
                    peer, _tok = _ilink_peer_and_ctx()
                    _et, _ep, _sb, _ot, _lk = send_text, send_path, None, None, send_link
                    if isinstance(partial2, dict):
                        _et = partial2.get("remaining_text", send_text)
                        _sb = partial2.get("sent_bubbles")
                        _ot = partial2.get("orig_text")
                        if "link_url" in partial2:
                            _lk = partial2.get("link_url") or None
                        if not partial2.get("image_pending"):
                            _ep = None
                    _enqueue_channel_outbox(
                        "ilink", peer, _et, why2, send_path=_ep,
                        sent_bubbles=_sb, orig_text=_ot, link_url=_lk,
                    )
                    print(f"[proactive] mirror ilink HOLD→outbox: {why2}", flush=True)
                else:
                    _send_notes.append(f"mirror:{why2}")
        if _send_notes:
            item["note"] = "; ".join(str(x) for x in _send_notes if x)


    if ui:
        io = io or __import__("mac_wechat_io")
        try:
            with ui_lock("proactive"):
                ok, why = verify_window(io, c["remark"], click=not dry)
                if ok and not dry and _ul.reply_pending(): ok, why = False, "回复优先：核对期间来了新消息"
                if not ok:
                    item["note"] = why; save_q(q, qpath); return {"action": "HOLD", "reason": why, "draft": item["text"]}
                if dry:
                    item["status"], item["note"] = "dry", why; save_q(q, qpath)
                    if is_surf:
                        cap, path, link, _ = _prepare_send_payload()
                        return {"action": "DRY", "reason": why, "would_send": cap,
                                "would_link": link, "would_image": path}
                    import chat_media as _cm
                    intent = _cm.parse_image_intent(item["text"])
                    out = {"action": "DRY", "reason": why, "would_send": item["text"]}
                    if intent: out["would_want_image"] = intent["scene"]; out["would_caption"] = intent.get("caption")
                    return out
                if _img_ready and (_img_ready[2] is not None or (len(_img_ready) >= 5 and _img_ready[4])):
                    scene = _img_ready[0]
                    cap = _img_ready[1]
                    paths = _img_ready_paths() or ([str(_img_ready[2])] if _img_ready[2] is not None else [])
                    texts = list(_img_ready[5]) if len(_img_ready) >= 6 else []
                    if cap and not texts:
                        _before_send()
                        io.send_message(cap)
                    for _i, path in enumerate(paths):
                        line = texts[_i] if _i < len(texts) else (cap if _i == 0 else None)
                        if line:
                            _before_send()
                            io.send_message(line)
                        _before_send()
                        io.send_image(str(path))
                    item["note"] = f"image:{paths[0]}" if paths else "image"
                    _n = len(paths)
                    item["text"] = (cap + " " if cap else "") + (f"[图片x{_n}:{scene}]" if _n > 1 else f"[图片:{scene}]")
                elif _beat_img:
                    send_text, send_path, send_link, _ = _prepare_send_payload()
                    if send_text:
                        _before_send()
                        io.send_message(send_text)
                    if send_link:
                        _before_send()
                        io.send_message(send_link)
                    _before_send()
                    io.send_image(send_path)
                    if is_surf:
                        item["text"] = " / ".join(part for part in (send_text, send_link, "[图片]") if part)
                elif _img_ready and _img_ready[1]:
                    return _sleep_drop("照片没有生成成功，本轮不发送")
                else:
                    _before_send()
                    io.send_message(item["text"])
        except ProactiveSleepBlocked as exc:
            return _sleep_drop(str(exc), keep_relay=not item.get("delivered_pieces"))
        except _ul.LockBusy as e:
            item["note"] = str(e); save_q(q, qpath); return {"action": "HOLD", "reason": str(e), "draft": item["text"]}
        # UI SENT succeeded → clear any same-id outbox entry (P0 mutual exclusion safety net)
        if not dry:
            _clear_channel_outbox("ilink", item.get("id"))
            _clear_channel_outbox("onebot", item.get("id"))
    elif not dry and not locals().get("_primary_sent"):
        raise RuntimeError("真发必须经过微信窗口核对（不能 --no-ui），或改 proactive.transport=ilink|onebot")
    elif dry and not locals().get("_primary_sent"):
        item["status"], item["note"] = "dry", "未核对窗口(--no-ui)"; save_q(q, qpath)
        return {"action": "DRY", "reason": "--no-ui", "would_send": item["text"]}
    if item.get("delivered_pieces"):
        item["text"] = " / ".join(item["delivered_pieces"])
        item.setdefault("meta", {})["has_image"] = "[图片]" in item["delivered_pieces"]
    # Persist delivery before accounting. A restart replays this receipt, never the send.
    item["accounted"] = False
    item["status"], item["sent_at"] = "sent", _iso(now)
    bm = (item.get("meta") or {}).get("beat_meta") or {}
    keys = [bm.get("done_key") or bm.get("id"), *(bm.get("merged_keys") or [])]
    _audit_events(q, [{"key": k, "status": "sent" if i == 0 else "merged",
                       "round_id": item["id"]} for i, k in enumerate(keys) if k], now)
    save_q(q, qpath)
    current = record_success(cid, item, now)
    turns = int(current.get("proactive_unanswered_turns", 0))
    item["accounted"] = True
    if not item.get("delivered_pieces"):
        db = memory.db(); seq = db.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
        memory.insert(db, cid, "me", item["text"], memory.fingerprint(cid, "me", item["text"], "[proactive]", item["id"]),
                      type="proactive")
        if hasattr(db, "close"):
            db.close()
        memory.append_diary(cid, f"我主动找他：「{item['text']}」（{item['topic']}）")
    item["status"], item["sent_at"] = "sent", _iso(now)
    _set_next(q, ps.next_consider(now, {**st1, "last_proactive_at": _iso(now),
                                        "proactive_unanswered_turns": turns},
                                  pc, rng, after_send=True, unanswered_turns=turns))
    save_q(q, qpath)
    return {"action": "SENT", "text": item["text"], "round_id": item["id"], "beat": bm.get("id"),
            "proactive_count": current.get("proactive_count"), "transport": transport,
            "next_consider_at": _next_at(q)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--now"); ap.add_argument("--no-ui", action="store_true")
    a = ap.parse_args(); now = datetime.datetime.fromisoformat(a.now) if a.now else None
    r = run_once(now, dry=a.dry_run, force=a.force, ui=not a.no_ui)
    print(f"[{_iso(_now())}] [proactive] " + json.dumps(r, ensure_ascii=False))
