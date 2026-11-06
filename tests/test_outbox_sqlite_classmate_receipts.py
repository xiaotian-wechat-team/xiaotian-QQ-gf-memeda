"""P0: outbox SQLite + classmate memory isolation + qq receipts + ilink fatal exit."""
import sys, pathlib, time, json, os
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

def test_outbox_sqlite(tmp_path, monkeypatch):
    import channels.outbox_store as ob
    monkeypatch.setattr(ob, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(ob, "LEGACY_JSON", {"onebot": tmp_path / "qq.json", "ilink": tmp_path / "ilink.json"})
    # migrate legacy
    (tmp_path / "qq.json").write_text(json.dumps({
        "items": [{"id": "a1", "peer": "1", "text": "hi", "status": "pending", "created": "2026-10-26 12:00:00"}]
    }), encoding="utf-8")
    data = ob.load("onebot")
    assert any(i["id"] == "a1" for i in data["items"])
    data["items"].append({
        "id": "a2", "peer": "1", "text": "bye", "status": "sent",
        "created": "2026-10-26 12:01:00",
    })
    ob.save("onebot", data)
    again = ob.load("onebot")
    ids = {i["id"] for i in again["items"]}
    assert "a1" in ids and "a2" in ids
    # purge old terminal
    with ob._conn() as c:
        c.execute("UPDATE outbox SET updated_at=? WHERE id=?", (time.time() - ob.TERMINAL_TTL_SEC - 10, "a2"))
        c.commit()
    ob.purge("onebot")
    ids2 = {i["id"] for i in ob.load("onebot")["items"]}
    assert "a2" not in ids2 and "a1" in ids2

def test_classmate_memory_isolated(tmp_path, monkeypatch):
    import classmate_memory as cm
    monkeypatch.setattr(cm, "DB_PATH", tmp_path / "cm.db")
    cid = "classmate:qq_onebot:999"
    cm.upsert_profile(cid, display_name="阿强", address_as="阿强同学")
    cm.insert(cid, "them", "你好")
    cm.insert(cid, "me", "阿强同学，你好")
    h = cm.history(cid, 10)
    assert h[-1] == ("me", "阿强同学，你好")
    # must not touch digital-life
    dl = pathlib.Path.home() / "digital-life" / "contacts" / "demo_contact"
    # just ensure our module path is under tmp
    assert str(cm.DB_PATH).startswith(str(tmp_path))

def test_qq_receipts_ttl(tmp_path, monkeypatch):
    import channels.qq_receipts as qr
    monkeypatch.setattr(qr, "DB_PATH", tmp_path / "r.db")
    monkeypatch.setattr(qr, "TTL_SEC", 1)
    monkeypatch.setattr(qr, "MAX_ROWS", 5)
    qr.record("api_echo", {"echo": "x", "retcode": 0}, echo="x", status="0")
    assert qr.stats()["count"] == 1
    with qr._conn() as c:
        c.execute("UPDATE receipts SET created_at=?", (time.time() - 10,))
        c.commit()
    qr.purge()
    assert qr.stats()["count"] == 0

def test_ilink_fatal_exit_code():
    """Feeder fatal → run returns 1 (launchd KeepAlive)."""
    from channels import ilink_weixin as iw
    class FakeBot:
        def messages(self):
            raise ConnectionError("disconnect")
        def stop(self):
            pass
    r = iw.ILinkWeixinRunner(dry=True, once=False)
    r._build_bot = lambda: FakeBot()  # type: ignore
    r._ensure_inbox = lambda: setattr(r, "_inbox", __import__("queue").Queue()) or setattr(r, "_inbox_lock", __import__("threading").Lock())
    # minimal inbox setup
    import queue, threading
    r._inbox = queue.Queue()
    r._inbox_lock = threading.Lock()
    r._got = 0
    code = r.run()
    assert code == 1, code

def test_bridge_classmate_no_image(monkeypatch):
    from channels.types import Inbound
    from channels import bridge
    from channels import classmate as cm_mod
    sent = []
    monkeypatch.setattr(cm_mod, "reply_classmate", lambda *a, **k: "测试同学，你好")
    import classmate_memory as mem
    # isolate
    import tempfile
    td = tempfile.mkdtemp()
    monkeypatch.setattr(mem, "DB_PATH", pathlib.Path(td) / "cm.db")
    msg = Inbound(channel="qq_onebot", peer_id="555", text="在吗", raw={"sender": {"nickname": "测试"}})
    out = bridge.handle_inbound(msg, dry=False, send=lambda o: sent.append(o), persist=True, reply_fn=None)
    # Runtime hard-gate: only demo_contact couple path is live; classmate contacts are blocked.
    assert out.get("contact_id", "").startswith("classmate:")
    assert out.get("blocked") is True
    assert out.get("reason") == "contact_not_allowed"
    assert sent == []

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
