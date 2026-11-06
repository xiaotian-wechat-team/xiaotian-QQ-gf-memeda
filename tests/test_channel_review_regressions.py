"""Review regressions: temporary storage, fake model/transports, no live services."""
import concurrent.futures
import datetime as dt
import io
import json
import multiprocessing
import pathlib
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import brain
import memory
from channels import bridge, ilink_weixin as iw, onebot_qq as qq, outbox_store as ob
from channels.types import Inbound

PEER = "10001"
NOW = dt.datetime(2026, 10, 27, 12, 0)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import classmate_memory, image_caption, voice_asr, mem, schedule_fidelity
    monkeypatch.setattr(memory, "CFG", {**memory.CFG, "db_path": str(tmp_path / "chat.db")})
    monkeypatch.setattr(memory, "DL", tmp_path / "life")
    (memory.DL / "contacts" / "demo_contact").mkdir(parents=True)
    monkeypatch.setattr(classmate_memory, "DB_PATH", tmp_path / "classmate.db")
    monkeypatch.setattr(ob, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(ob, "LEGACY_JSON", {})
    monkeypatch.setattr(iw, "CTX_FILE", tmp_path / "ctx.json")
    monkeypatch.setattr(brain, "LAST", {})
    monkeypatch.setattr(bridge, "_mem_release", lambda: None)
    monkeypatch.setattr(bridge, "_human_wait_for_send", lambda *a, **k: 0)
    monkeypatch.setattr(bridge, "_sleep_split_gap", lambda *a: ({}, {}))
    monkeypatch.setattr(image_caption, "enrich_inbound", lambda m: (m, {}))
    monkeypatch.setattr(voice_asr, "enrich_inbound", lambda m: (m, {}))
    monkeypatch.setattr(mem, "add", Mock())
    monkeypatch.setattr(schedule_fidelity, "resolve_slot", lambda *a, **k: {})
    monkeypatch.setattr(schedule_fidelity, "outbox_item_stale", lambda *a, **k: (False, ""))
    monkeypatch.setattr(qq.urllib.request, "urlopen", Mock(side_effect=AssertionError("live network forbidden")))


def item(oid):
    return {"id": oid, "peer": PEER, "text": "收到", "status": "pending"}


def rows():
    return {it["id"]: it for it in ob.load("onebot")["items"]}


def test_stale_snapshot_keeps_concurrent_enqueue_and_sent_status():
    ob.save("onebot", {"items": [item("a")]})
    enqueue_snapshot = ob.load("onebot")
    flush_snapshot = ob.load("onebot")
    flush_snapshot["items"][0]["status"] = "sent"
    ob.save("onebot", flush_snapshot)
    enqueue_snapshot["items"].append(item("b"))
    ob.save("onebot", enqueue_snapshot)
    # Stale pending A must not resurrect; B must not disappear on another save.
    ob.save("onebot", flush_snapshot)
    assert rows()["a"]["status"] == "sent"
    assert rows()["b"]["status"] == "pending"


def test_changed_stale_row_rejected_atomically():
    ob.save("onebot", {"items": [item("a")]})
    stale = ob.load("onebot")
    latest = ob.load("onebot")
    latest["items"][0]["status"] = "sent"
    ob.save("onebot", latest)
    stale["items"].insert(0, item("b"))
    stale["items"][1]["status"] = "hold"
    with pytest.raises(ob.OutboxConflict):
        ob.save("onebot", stale)
    assert set(rows()) == {"a"}
    assert rows()["a"]["status"] == "sent"


def test_absent_ids_and_duplicate_enqueue_are_not_deletions_or_resets():
    ob.save("onebot", {"items": [{**item("a"), "status": "sent"}]})
    ob.save("onebot", {"items": [item("a"), item("b")]})
    ob.save("onebot", {"items": []})
    assert set(rows()) == {"a", "b"}
    assert rows()["a"]["status"] == "sent"


def test_concurrent_flush_sends_once_and_keeps_new_enqueue():
    ob.save("onebot", {"items": [item("a")]})
    pending_enqueue = ob.load("onebot")
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    sent = []

    def send(peer, text):
        sent.append(text)
        entered.set()
        assert release.wait(5)

    def flush(second=False):
        if second:
            second_started.set()
        return qq.flush_qq_outbox(send, peer_id=PEER, now=NOW)

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(flush)
        assert entered.wait(5)
        second = pool.submit(flush, True)
        try:
            assert second_started.wait(5)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    pending_enqueue["items"].append(item("b"))
    ob.save("onebot", pending_enqueue)
    assert sent == ["收到"]
    assert rows()["a"]["status"] == "sent"
    assert rows()["b"]["status"] == "pending"


def _process_flush(db_path, entered, release, sent):
    """Child worker: real queue storage and lock, fake transport and memory."""
    import channels.outbox_send as out_send
    import schedule_fidelity
    ob.DB_PATH = pathlib.Path(db_path)
    ob.LEGACY_JSON = {}
    out_send.persist_outbound_bubble = lambda *a, **k: True
    schedule_fidelity.resolve_slot = lambda *a, **k: {}
    schedule_fidelity.outbox_item_stale = lambda *a, **k: (False, "")

    def send(peer, text):
        sent.put(text)
        entered.set()
        assert release.wait(10)

    qq.flush_qq_outbox(send, peer_id=PEER, now=NOW)


def test_flush_lock_also_prevents_duplicate_delivery_across_processes():
    ob.save("onebot", {"items": [item("a")]})
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    sent = ctx.Queue()
    child = ctx.Process(target=_process_flush, args=(str(ob.DB_PATH), entered, release, sent))
    child.start()
    parent_sent = []
    attempted, finished = threading.Event(), threading.Event()

    def parent_flush():
        attempted.set()
        try:
            return qq.flush_qq_outbox(lambda p, t: parent_sent.append(t), peer_id=PEER, now=NOW)
        finally:
            finished.set()

    try:
        assert entered.wait(10)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            pending = pool.submit(parent_flush)
            try:
                assert attempted.wait(5)
                assert not finished.wait(0.1), "second process bypassed the flush lock"
            finally:
                release.set()
            pending.result(timeout=10)
        child.join(10)
        assert child.exitcode == 0
        assert sent.get(timeout=5) == "收到"
        assert parent_sent == []
        assert rows()["a"]["status"] == "sent"
    finally:
        release.set()
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join()
        sent.close()


def test_ilink_third_cancel_preserves_next_batch(monkeypatch):
    runner = iw.ILinkWeixinRunner(dry=False, typing=False)
    runner._ensure_inbox()
    processed = []
    other = Inbound("wechat_ilink", "other-test-peer", "other")
    messages = [Inbound("wechat_ilink", "demo@im.wechat", f"message-{i}", msg_id=str(i)) for i in range(4)]
    monkeypatch.setattr(runner, "_typing_set", lambda *a, **k: None)

    def cancel(msg, **kwargs):
        processed.append(msg.text)
        if len(processed) <= 3:
            runner._inbox.put(messages[len(processed)])
            assert kwargs["should_cancel"]()
            return {"cancelled": True}
        return {"reply": "done"}

    monkeypatch.setattr(bridge, "handle_inbound", cancel)
    runner._inbox.put(other)
    assert runner.handle_one(messages[0])["cancelled"]
    assert processed == ["message-0", "message-1", "message-2"]
    assert runner._inbox.get_nowait() is other
    last = runner._inbox.get_nowait()
    assert last is messages[3]
    assert runner.handle_one(last)["reply"] == "done"
    assert processed[-1] == "message-3"


@pytest.mark.parametrize("sender", [qq.send_private_text_http, qq.send_private_image_http, qq.send_private_record_http])
@pytest.mark.parametrize("response", [
    '{"status":"failed","retcode":100,"data":null}',
    '{"status":"ok","retcode":1,"data":{"message_id":1}}',
    '{"status":"async","retcode":1}',
    '{"status":"ok","retcode":0,"data":{}}',
    '<html>bad gateway</html>',
    '[]',
])
def test_http_rejects_unconfirmed_delivery(sender, response, monkeypatch):
    monkeypatch.setattr(qq.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(response.encode()))
    with pytest.raises(RuntimeError):
        sender("http://unused.invalid", PEER, "hello")


@pytest.mark.parametrize("sender", [qq.send_private_text_http, qq.send_private_image_http, qq.send_private_record_http])
def test_http_accepts_delivery_receipt(sender, monkeypatch):
    response = {"status": "ok", "retcode": 0, "data": {"message_id": 42}}
    monkeypatch.setattr(qq.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(response).encode()))
    assert sender("http://unused.invalid", PEER, "hello") == response


@pytest.mark.parametrize("business_failure", [False, True])
def test_http_handler_failure_never_records_sent_or_marks_replied(monkeypatch, business_failure):
    event = qq.make_private_event(PEER, "在吗")
    handler = qq._HttpEventHandler.__new__(qq._HttpEventHandler)
    payload = json.dumps(event).encode()
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    handler.server = SimpleNamespace(runner=SimpleNamespace(
        dry=False, api_base="http://unused.invalid", token="", got=0,
        reply_fn=lambda *a: "在的",
    ))
    handler.send_response = Mock()
    handler.end_headers = Mock()
    if business_failure:
        monkeypatch.setattr(qq.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(
            b'{"status":"failed","retcode":100,"data":null}'))
    else:
        monkeypatch.setattr(qq.urllib.request, "urlopen", Mock(side_effect=ConnectionError("offline")))
    handler.do_POST()
    with memory.db() as conn:
        assert conn.execute("SELECT sender,replied FROM messages").fetchall() == [("them", 0)]
    assert not memory.read_state("demo_contact").get("last_reply_at")
    handler.send_response.assert_called_once_with(204)


@pytest.mark.parametrize("text", ["在吗", "下周陪我出去玩", "明天上午上课"])
def test_brain_dry_reply_has_no_schedule_or_memory_writes(tmp_path, monkeypatch, text):
    import mem, life_calendar, schedule_pipeline
    monkeypatch.setattr(schedule_pipeline, "OVERRIDE_PENDING", tmp_path / "override.json")
    monkeypatch.setattr(brain, "CFG", {**brain.CFG, "llm": {"backend": "test_stub"},
                                     "life_calendar": {"sync_on_reply": True}})
    monkeypatch.setitem(brain.BACKENDS, "test_stub", lambda prompt: "好呀")
    monkeypatch.setattr(brain, "build_prompt", lambda *a, **k: "test prompt")
    monkeypatch.setattr(brain, "_nick", lambda *a, **k: None)
    memory.update_state("demo_contact", user_day_curiosity={"pending_ask": True})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert brain.reply("demo_contact", [("them", text)], dry=True, now=NOW)
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    mem.add.assert_not_called()


@pytest.mark.parametrize("persist", [None, True, False])
def test_bridge_dry_real_reply_path_never_opens_database(persist, monkeypatch):
    import image_caption, voice_asr
    db = Mock(side_effect=AssertionError("dry opened chat.db"))
    monkeypatch.setattr(memory, "db", db)
    reply = Mock(return_value="在的")
    monkeypatch.setattr(brain, "reply", reply)
    caption, asr = Mock(), Mock()
    monkeypatch.setattr(image_caption, "enrich_inbound", caption)
    monkeypatch.setattr(voice_asr, "enrich_inbound", asr)
    send = Mock()
    result = bridge.handle_inbound(Inbound("qq_onebot", PEER, "在吗"), dry=True, persist=persist, send=send)
    assert result["persist"] is False
    assert reply.call_args.kwargs["dry"] is True
    db.assert_not_called()
    send.assert_not_called()
    caption.assert_not_called()
    asr.assert_not_called()


def test_classmate_preview_does_not_create_database(tmp_path):
    result = bridge.handle_inbound(Inbound("qq_onebot", "999-test", "hello"), dry=True,
                                   persist=True, reply_fn=lambda *a: "同学你好")
    assert result["persist"] is False
    assert not (tmp_path / "classmate.db").exists()


def test_partial_send_records_only_confirmed_bubbles(monkeypatch):
    import bubble_split
    monkeypatch.setattr(bubble_split, "split_bubbles", lambda *a, **k: ["第一句", "第二句"])
    monkeypatch.setattr(bubble_split, "bubble_gap_sec", lambda *a, **k: 0)
    sent = []

    def send(out):
        if sent:
            raise ConnectionError("second bubble failed")
        sent.append(out.text)

    result = bridge.handle_inbound(Inbound("qq_onebot", PEER, "在吗"), dry=False, send=send,
                                  reply_fn=lambda *a: "第一句\n第二句")
    assert result["delivery_status"] == "unknown"
    with memory.db() as conn:
        assert conn.execute("SELECT text FROM messages WHERE sender='me'").fetchall() == [("第一句",)]
        assert conn.execute("SELECT replied FROM messages WHERE sender='them'").fetchone() == (0,)
    # Exception must release the turn lock for the next request.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        future = pool.submit(bridge.handle_inbound, Inbound("qq_onebot", PEER, "再来"),
                             dry=True, reply_fn=lambda *a: "好的")
        assert future.result(timeout=5)["reply"] == "好的"


def test_preview_skips_indirect_caches_and_vector_store(tmp_path, monkeypatch):
    import mem, life_calendar, weather
    from preview_mode import preview, is_preview
    retrieve = Mock(side_effect=AssertionError("preview opened vector store"))
    monkeypatch.setattr(mem, "retrieve_block", retrieve)
    probes = Mock()
    monkeypatch.setitem(sys.modules, "scene_refs", SimpleNamespace(ensure_day_probes=probes))
    monkeypatch.setattr(weather, "CACHE_FILE", tmp_path / "weather.json")
    monkeypatch.setattr(weather, "BASELINE_FILE", tmp_path / "baseline.json")
    with preview():
        brain.memory_block("demo_contact", "还记得吗")
        life_calendar._bind_day_scene_probes({"date": "2099-01-01"})
        weather._write_disk("bda", 1, "晴")
        weather._write_baseline_raw({"sample": 1})
        fetch = Mock(side_effect=AssertionError("preview fetched weather"))
        assert weather.get("bda", fetch=fetch)["why"] == "no-cache"
        fetch.assert_not_called()
    assert not is_preview()
    retrieve.assert_not_called()
    probes.assert_not_called()
    assert not (tmp_path / "weather.json").exists()
    assert not (tmp_path / "baseline.json").exists()


def test_preview_llm_does_not_resume_or_create_daily_session(monkeypatch):
    import llm
    from preview_mode import preview, is_preview
    session = Mock(side_effect=AssertionError("preview touched real session"))
    monkeypatch.setattr(llm.llm_session, "ensure", session)
    monkeypatch.setattr(llm.llm_session, "workspace", session)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="好呀", stderr=""))
    monkeypatch.setattr(llm.subprocess, "run", run)
    with preview():
        assert llm.complete("preview", reuse_session=True) == "好呀"
    session.assert_not_called()
    assert "--resume" not in run.call_args.args[0]
    assert not is_preview()
    with pytest.raises(ValueError):
        with preview():
            raise ValueError("failed preview")
    assert not is_preview()


def test_concurrent_turn_cannot_overwrite_metadata_during_media_plan(monkeypatch):
    first_planning, release, second_attempted, second_replied = [threading.Event() for _ in range(4)]
    seen = []

    def reply(cid, hist):
        name = hist[-1][1]
        if name == "B":
            second_replied.set()
        brain.LAST = {"want_image": name, "nickname": name, "shot_type": name}
        return name

    def plan(scene, text, **kwargs):
        if scene == "A":
            first_planning.set()
            assert release.wait(5)
        seen.append((scene, brain.LAST["shot_type"]))
        return {"paths": [scene + ".png"], "texts": []}

    import photo_scene
    monkeypatch.setattr(photo_scene, "allow_inbound_image_gen", lambda *a: True)
    monkeypatch.setattr(bridge, "_plan_want_image", plan)

    def handle(name):
        if name == "B":
            second_attempted.set()
        return bridge.handle_inbound(Inbound("qq_onebot", PEER, name), dry=False,
                                      persist=False, send=lambda out: None, reply_fn=reply)

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(handle, "A")
        assert first_planning.wait(5)
        second = pool.submit(handle, "B")
        try:
            assert second_attempted.wait(5)
            assert not second_replied.wait(0.1)
        finally:
            release.set()
        assert first.result(timeout=5)["image_paths"] == ["A.png"]
        assert second.result(timeout=5)["image_paths"] == ["B.png"]
    assert seen == [("A", "A"), ("B", "B")]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
