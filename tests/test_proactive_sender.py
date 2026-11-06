"""Legacy script assertions run in a fresh process under pytest.

Their module-level mocks must not leak into other regression modules.
Direct .venv/bin/python execution retains the original test behavior.
"""
if __name__ != "__main__":
    def test_isolated_script():
        import os, pathlib, subprocess, sys
        env = {**os.environ, "XIAOTIAN_TEST": "1", "RELAY_ENV_FILE": "/nonexistent/xiaotian-test-relay.env"}
        result = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve())],
                                env=env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
else:
    """离线：发送队列 / 去重 / 重检 / 窗口核对 / 白名单 / next_consider_at。用假的 io，不碰微信；memory 写入全部打桩。"""
    import sys, pathlib, datetime, random, tempfile, copy
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    # Isolate outbox SQLite — never touch production data/outbox.db
    import tempfile as _tmpmod
    from channels import outbox_store as _obs
    _OBS_TMP = pathlib.Path(_tmpmod.mkdtemp()) / "outbox_test.db"
    _obs.DB_PATH = _OBS_TMP
    import proactive_sender as S, config, memory
    memory.ensure_defaults = lambda *a, **k: None
    memory.read_state = lambda *a, **k: {}
    memory.update_state = lambda *a, **k: None
    D = datetime.datetime; full = config.load(); assert full["proactive"]["enabled"] is True
    assert full["proactive"]["probability"] == 0
    assert full["proactive"]["min_gap_sec"] == [1800, 3000]
    on = copy.deepcopy(full); on["proactive"]["image_share_min_ratio"] = 0; on["proactive"].update(probability=0, beats={"enabled": False})
    # This legacy suite checks ordinary proactive scheduling; deadlines have their own integration suite.
    on["proactive"].pop("idle_contact", None)
    sat20 = D(2026, 10, 24, 20, 0)
    tmp = pathlib.Path(tempfile.mkdtemp()); qp = lambda n: tmp / f"q{n}.json"

    class FakeIO:
        SIDEBAR_W, HEADER_H = 300, 60
        def __init__(s, title="小熊", last="me"): s.t, s.last, s.sent, s.clicks = title, last, [], 0
        norm = staticmethod(lambda t: "".join((t or "").split()))
        def open_chat(s): s.clicks += 1
        def capture(s): pass
        def ocr(s): return [{"text": s.t, "x": 400, "y": 20}]
        def read_messages(s): return [{"from": "them", "text": "hi"}, {"from": s.last, "text": "x"}]
        def send_message(s, t): s.sent.append(t)
    S.ui_lock = lambda path=None: __import__("contextlib").nullcontext()
    import ui_lock as _ul
    _ul.reply_pending = lambda: False
    S._ul.reply_pending = lambda: False
    def _bind_state(bag):
        def _mutate(cid, change):
            state = bag.setdefault("state", {})
            state.update(change(state))
            return state
        memory.mutate_state = _mutate
        def _upd(cid, **kv):
            bag.setdefault("state", {}).update(kv)
        return _upd
    draft = lambda: "下课啦 今天排练排到腿软"
    kw = dict(cfg_full=on, rows=[], st={}, rng=random.Random(0), draft_fn=draft)
    # 白名单
    bad = copy.deepcopy(on); bad["contact"]["id"] = "9999"
    try: S.run_once(sat20, cfg_full=bad, qpath=qp(0)); raise SystemExit("whitelist failed")
    except PermissionError: pass
    # dry-run：入队 → 窗口核对（不点击）→ would_send，不发送
    io = FakeIO(); r = S.run_once(sat20, dry=True, io=io, qpath=qp(1), **kw)
    assert r["action"] == "DRY" and r["would_send"] == draft() and io.sent == [] and io.clicks == 0, r
    q = S.load_q(qp(1)); assert q["items"][0]["status"] == "dry" and (q.get("next_consider_at") or q.get("next_check_at"))
    # 未到 next_consider_at → WAIT（launchd 每 5 分钟敲门也不打架；忙闲下闲时 T 可能 <1min，这里显式推远）
    q["next_consider_at"] = q["next_check_at"] = (sat20 + datetime.timedelta(hours=2)).isoformat(sep=" ")
    S.save_q(q, qp(1))
    assert S.run_once(sat20 + datetime.timedelta(minutes=1), dry=True, io=FakeIO(), qpath=qp(1), **dict(kw, rng=random.Random(1)))["action"] == "WAIT"
    # 兼容旧字段 next_check_at
    qp1b = tmp / "q1b.json"
    S.save_q({"next_check_at": (sat20 + datetime.timedelta(hours=1)).isoformat(sep=" "), "items": []}, qp1b)
    assert S.run_once(sat20, dry=True, io=FakeIO(), qpath=qp1b, **kw)["action"] == "WAIT"
    # 标题不对 → HOLD（强制 ui 运输；config.transport=ilink 归聊天，本断言测窗口核对）
    kw_ui = dict(kw); kw_ui["cfg_full"] = copy.deepcopy(on); kw_ui["cfg_full"]["proactive"]["transport"] = "ui"
    io = FakeIO(title="文件传输助手"); r = S.run_once(sat20, dry=False, io=io, qpath=qp(2), **kw_ui); assert r["action"] == "HOLD" and io.sent == []
    # 屏幕最后一条是他 → HOLD
    io = FakeIO(last="them"); r = S.run_once(sat20, dry=False, io=io, qpath=qp(3), **kw_ui); assert r["action"] == "HOLD" and "回复流程" in r["reason"]
    # 去重
    r = S.run_once(sat20, dry=True, io=FakeIO(), qpath=qp(4), **dict(kw, rows=[("me", "下课啦，今天排练排到腿软！", "2026-10-24 09:00:00")]))
    assert r["action"] == "SKIP" and "重复" in r["reason"]
    # 安静时段 / 每日上限
    assert S.run_once(D(2026, 10, 24, 3, 0), dry=True, io=FakeIO(), qpath=qp(5), **kw)["action"] == "SKIP"
    r = S.run_once(sat20, dry=True, io=FakeIO(), qpath=qp(6), **dict(kw, st={"proactive_day": "2026-10-24", "proactive_count": on["proactive"].get("max_per_day", 5)})); assert "次数" in r["reason"] or "轮" in r["reason"]
    # 入队后状态变化 → DROP
    q = {"next_consider_at": None, "items": []}; S.enqueue(q, "在干嘛", "playful", sat20); S.save_q(q, qp(7))
    r = S.run_once(sat20, dry=False, io=FakeIO(), qpath=qp(7), **dict(kw_ui, rows=[("them", "我到家了", "2026-10-24 19:59:00")])); assert r["action"] == "DROP"
    # 过期作废
    q = {"next_consider_at": "2099-01-01 00:00:00", "items": []}; S.enqueue(q, "旧草稿", "x", sat20 - datetime.timedelta(hours=1)); S.save_q(q, qp(8))
    assert S.run_once(sat20, dry=True, io=FakeIO(), qpath=qp(8), **kw)["action"] == "WAIT" and S.load_q(qp(8))["items"][0]["status"] == "expired"
    # 真发路径：SENT + next_consider_at 落在 30–50min
    calls = {}
    memory.db = lambda: type("C", (), {"execute": lambda s, *a: type("R", (), {"fetchone": lambda s: (0,)})()})()
    memory.insert = lambda *a, **k: calls.setdefault("insert", a[2:4]); memory.append_diary = lambda *a: calls.setdefault("diary", a)
    memory.update_state = _bind_state(calls)
    io = FakeIO(); r = S.run_once(sat20, dry=False, io=io, qpath=qp(9), **kw_ui)
    assert r["action"] == "SENT" and io.sent == [draft()] and io.clicks == 1 and calls["state"]["proactive_count"] == 1 and calls["insert"][0] == "me"
    nca = datetime.datetime.fromisoformat(r["next_consider_at"])
    assert 1800 <= (nca - sat20).total_seconds() <= 3000, r
    # 真发不允许跳过窗口核对
    q = {"next_consider_at": None, "items": []}; S.enqueue(q, "hi", "x", sat20); S.save_q(q, qp(10))
    try: S.run_once(sat20, dry=False, ui=False, qpath=qp(10), **kw_ui); raise SystemExit("no-ui send allowed")
    except RuntimeError: pass

    # ---- ilink transport：mock send_via_ilink，不碰真微信 ----
    kw_ilink = dict(kw)
    kw_ilink["cfg_full"] = copy.deepcopy(on)
    kw_ilink["cfg_full"]["proactive"]["transport"] = "ilink"
    kw_ilink["cfg_full"]["proactive"]["primary_channel"] = "wechat_ilink"
    kw_ilink["cfg_full"]["proactive"]["channel_policy"] = "primary"
    calls_i = {}
    memory.db = lambda: type("C", (), {"execute": lambda s, *a: type("R", (), {"fetchone": lambda s: (0,)})()})()
    memory.insert = lambda *a, **k: calls_i.setdefault("insert", a[2:4])
    memory.append_diary = lambda *a: calls_i.setdefault("diary", a)
    memory.update_state = _bind_state(calls_i)
    # SENT：ilink 成功
    _orig = S.send_via_ilink
    S.send_via_ilink = lambda text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None: (True, "ilink:mockpeer")
    io = FakeIO()
    r = S.run_once(sat20, dry=False, io=io, qpath=qp(11), **kw_ilink)
    assert r["action"] == "SENT", r
    assert io.sent == [] and io.clicks == 0, "ilink 路径不应点 UI"
    assert calls_i.get("insert") and calls_i["insert"][0] == "me"
    assert calls_i.get("state", {}).get("proactive_count") == 1
    # HOLD：ilink 失败
    S.send_via_ilink = lambda text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None: (False, "ilink send fail: mocked")
    q = {"next_consider_at": None, "items": []}
    S.enqueue(q, draft(), "playful", sat20); S.save_q(q, qp(12))
    r = S.run_once(sat20, dry=False, io=FakeIO(), qpath=qp(12), **kw_ilink)
    assert r["action"] == "HOLD" and "ilink" in r["reason"], r
    assert S.load_q(qp(12))["items"][0]["status"] != "sent"
    S.send_via_ilink = _orig

    # ---- onebot transport：mock send_via_onebot ----
    kw_ob = dict(kw)
    kw_ob["cfg_full"] = copy.deepcopy(on)
    kw_ob["cfg_full"]["proactive"]["transport"] = "onebot"
    kw_ob["cfg_full"]["proactive"]["channel_policy"] = "primary"
    calls_o = {}
    memory.db = lambda: type("C", (), {"execute": lambda s, *a: type("R", (), {"fetchone": lambda s: (0,)})()})()
    memory.insert = lambda *a, **k: calls_o.setdefault("insert", a[2:4])
    memory.append_diary = lambda *a: calls_o.setdefault("diary", a)
    memory.update_state = _bind_state(calls_o)
    _orig_ob = S.send_via_onebot
    S.send_via_onebot = lambda text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None: (True, "onebot:10001:http")
    io = FakeIO()
    r = S.run_once(sat20, dry=False, io=io, qpath=qp(13), **kw_ob)
    assert r["action"] == "SENT", r
    assert io.sent == [] and io.clicks == 0
    assert calls_o.get("insert") and calls_o["insert"][0] == "me"
    # HOLD
    S.send_via_onebot = lambda text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None: (False, "onebot send fail: mocked")
    q = {"next_consider_at": None, "items": []}
    S.enqueue(q, draft(), "playful", sat20); S.save_q(q, qp(14))
    # stub outbox enqueue to avoid needing qq_outbox module side effects
    _orig_enq = None
    r = S.run_once(sat20, dry=False, io=FakeIO(), qpath=qp(14), **kw_ob)
    assert r["action"] == "HOLD" and "onebot" in r["reason"], r
    # mirror plan resolves to two hops
    plan, policy = S._resolve_send_plan({"transport": "ilink", "channel_policy": "mirror", "primary_channel": "wechat_ilink"})
    assert policy == "mirror" and plan == ["ilink", "onebot"], (plan, policy)
    plan2, pol2 = S._resolve_send_plan({"transport": "auto", "channel_policy": "primary", "primary_channel": "qq_onebot"})
    assert pol2 == "primary" and plan2[0] == "onebot", (plan2, pol2)
    plan3, pol3 = S._resolve_send_plan({"transport": "ilink", "channel_policy": "primary", "primary_channel": "qq_onebot"})
    assert pol3 == "primary" and plan3 == ["onebot"], (plan3, pol3)  # primary_channel wins; no WeChat proactive
    S.send_via_onebot = _orig_ob

    # ---- P0: prepare-failed → outbox only, UI not attempted ----
    import tempfile as _tf
    _obx = pathlib.Path(_tf.mkdtemp()) / "ilink_outbox.json"
    from channels import ilink_weixin as _iw
    from channels import outbox_store as _obs2
    _obs2.DB_PATH = pathlib.Path(_tf.mkdtemp()) / "outbox_p0.db"
    _iw.OUTBOX_FILE = _obx
    _iw._save_outbox({"items": []})
    kw_p0 = dict(kw)
    kw_p0["cfg_full"] = copy.deepcopy(on)
    kw_p0["cfg_full"]["proactive"]["transport"] = "ilink"
    kw_p0["cfg_full"]["proactive"]["primary_channel"] = "wechat_ilink"
    kw_p0["cfg_full"]["proactive"]["channel_policy"] = "primary"
    S._ilink_peer_and_ctx = lambda: ("peer_p0_test", "tok")
    ui_calls = {"n": 0}
    class CountingIO(FakeIO):
        def send_message(s, t):
            ui_calls["n"] += 1
            super().send_message(t)
    S.send_via_ilink = lambda text, *, image_path=None, link_url=None, before_send=None, on_confirmed=None: (False, "ilink prepare/ret fail ret=-2 errmsg='prepare failed'")
    q = {"next_consider_at": None, "items": []}
    S.enqueue(q, draft(), "playful", sat20); S.save_q(q, qp(15))
    r = S.run_once(sat20, dry=False, ui=True, io=CountingIO(), qpath=qp(15), **kw_p0)
    assert r["action"] == "HOLD", r
    assert "prepare failed" in (r.get("reason") or "").lower(), r
    assert ui_calls["n"] == 0, "P0: UI must not be attempted when outbox enqueued"
    ob_items = _iw._load_outbox()["items"]
    assert any(x.get("status") == "pending" for x in ob_items), ob_items
    assert any("prepare failed" in str(x.get("reason") or "") for x in ob_items), ob_items
    print("P0 outbox-only ok", r.get("outbox"), ob_items[0].get("id"))

    # ---- P1: send_via_onebot retcode!=0 with status=ok is NOT SENT ----
    assert S._onebot_http_ok({"status": "ok", "retcode": 0, "data": {"message_id": 42}}) is True
    assert S._onebot_http_ok({"status": "ok", "retcode": 0}) is False  # no delivery receipt
    assert S._onebot_http_ok({"status": "ok", "retcode": 1}) is False
    assert S._onebot_http_ok({"status": "ok"}) is False
    assert S._onebot_http_ok({"retcode": 0, "data": {"message_id": 42}}) is False  # status missing
    print("P1 onebot retcode gate ok")

    for f in ("com.xiaotian.proactive.plist", "com.xiaotian.campus.plist", "com.xiaotian.onebot.plist"):
        assert (pathlib.Path(__file__).resolve().parents[1] / "launchd" / f).exists()
    _proactive_plist = (pathlib.Path(__file__).resolve().parents[1] / "launchd" / "com.xiaotian.proactive.plist").read_text()
    assert "--no-ui" in _proactive_plist, "launchd proactive must pass --no-ui (ilink/outbox only)"

    # ---- surf_share: dry would_link separate from would_send caption ----
    _img = pathlib.Path(tempfile.mkdtemp()) / "surf.png"
    _img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    kw_surf = dict(kw)
    kw_surf["cfg_full"] = copy.deepcopy(on)
    kw_surf["cfg_full"]["proactive"]["transport"] = "onebot"
    kw_surf["cfg_full"]["proactive"]["channel_policy"] = "primary"
    kw_surf["cfg_full"]["proactive"]["primary_channel"] = "qq_onebot"
    _url = "https://www.xiaohongshu.com/explore/surf-test-url"
    _surf_text = "刷到个狠人\n滑雪住半年"
    q = {"next_consider_at": None, "items": []}
    S.enqueue(q, _surf_text, "beat:surf_share", sat20)
    S.save_q(q, qp(16))
    qq = S.load_q(qp(16))
    qq["items"][0]["meta"] = {"beat_meta": {"id": "surf_share", "material": {
        "source_url": _url, "image_path": str(_img), "title": "滑雪", "platform": "xiaohongshu",
    }}}
    qq["items"][0]["text"] = _surf_text
    qq["items"][0]["topic"] = "beat:surf_share"
    S.save_q(qq, qp(16))
    r = S.run_once(sat20, dry=True, io=FakeIO(), qpath=qp(16), **kw_surf)
    assert r["action"] == "DRY", r
    assert _url not in (r.get("would_send") or ""), ("url must not be glued into caption", r)
    assert (r.get("would_link") or "") == _url, r
    assert _surf_text in (r.get("would_send") or "") or all(
        ln in (r.get("would_send") or "") for ln in _surf_text.splitlines()
    ), r
    print("surf_share link decoupled ok", r.get("would_send"), r.get("would_link"))

    print("OK")
