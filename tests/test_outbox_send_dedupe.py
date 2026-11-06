"""Outbox anti-dupe: pollution drop, suffix resume, chat.db persist, temp DB only."""
import sys, pathlib, tempfile, datetime as dt
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from channels import outbox_store as _obs

from channels.outbox_send import (
    already_sent_count,
    bubbles_suffix,
    is_test_pollution,
    record_sent_bubble,
    split_outbound_bubbles,
)
from channels import ilink_weixin as iw
from channels import onebot_qq as ob


@pytest.fixture(autouse=True)
def isolated_delivery(tmp_path, monkeypatch):
    import memory
    from channels import persona_map
    monkeypatch.setattr(_obs, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(memory, "DL", tmp_path / "life")
    (memory.DL / "contacts/demo_contact").mkdir(parents=True)
    monkeypatch.setattr(memory, "CFG", {**memory.CFG, "db_path": str(tmp_path / "chat.db")})
    # Both synthetic transport peers represent the only authorized contact in this fixture.
    monkeypatch.setattr(persona_map, "load", lambda: {
        "contact": {"id": "demo_contact"}, "persona": {"primary_contact_id": "demo_contact",
        "contact_aliases": {"wechat_ilink:peerResume": "demo_contact", "qq_onebot:peer_p0_zz": "demo_contact"},
        "channels": {"qq": {"allow_user_ids": ["peer_p0_zz"]}}}})


def test_pollution():
    dirty, why = is_test_pollution({"peer": "peer_p0_test", "reason": "x"})
    assert dirty and why == "test_peer"
    dirty, why = is_test_pollution({"peer": "real", "reason": "proactive: mocked fail"})
    assert dirty and why == "mocked"
    dirty, why = is_test_pollution({"peer": "real", "reason": "prepare failed"})
    assert not dirty
    print("ok pollution")


def test_suffix_and_record():
    text = "第一句\n第二句\n第三句"
    # split may keep as fewer bubbles depending on splitter; force via record
    it = {"text": text, "orig_text": text}
    # manually set bubbles via split
    bubs = split_outbound_bubbles(text)
    assert bubs
    record_sent_bubble(it, bubs[0])
    assert already_sent_count(it) == 1
    assert it["text"] == bubbles_suffix(text, 1)
    # empty suffix when all sent
    for b in bubs[1:]:
        record_sent_bubble(it, b)
    assert it["text"] == ""
    print("ok suffix", len(bubs))


def test_flush_skips_sent_prefix():
    import channels.outbox_send as osnd
    _orig_split = osnd.split_outbound_bubbles
    osnd.split_outbound_bubbles = lambda t: [x for x in (t or "").split("\n") if x]
    import weixin_ilink.api as api
    _o = api.send_message
    import schedule_fidelity as sf
    _stale = sf.outbox_item_stale
    sf.outbox_item_stale = lambda *a, **k: (False, "")
    import memory
    _mdb, _mtail, _mfp, _mins = memory.db, memory.tail, memory.fingerprint, memory.insert
    memory.db = lambda: type("C", (), {"execute": lambda *a, **k: None, "commit": lambda *a: None})()
    memory.tail = lambda *a, **k: [(0, "", "")]
    memory.fingerprint = lambda *a, **k: "fp"
    memory.insert = lambda *a, **k: True
    sent = []
    def _cap(opts, msg, *a, **k):
        txt = msg["item_list"][0]["text_item"]["text"]
        sent.append(txt)
        return {"ret": 0, "message_id": 100 + len(sent)}
    api.send_message = _cap
    try:
        iw._save_outbox({"items": [{
            "id": "resume-1",
            "peer": "peerResume",
            "contact": "demo_contact",
            "text": "第二句",
            "orig_text": "第一句\n第二句",
            "sent_bubbles": ["第一句"],
            "sent_bubble_count": 1,
            "status": "pending",
            "created": "2026-10-26 12:00:00",
        }]})
        class StubBot:
            class client:
                opts = object()
                @staticmethod
                def _generate_client_id():
                    return "c"
        now = dt.datetime(2026, 10, 26, 12, 30, 0)
        res = iw.flush_ilink_outbox(StubBot(), "peerResume", "tok", now=now)
        assert res and res[0].get("ok") is True, res
        assert sent == ["第二句"], sent
        assert iw._load_outbox()["items"][0]["status"] == "sent"
        print("ok resume skip prefix", sent)
    finally:
        api.send_message = _o
        sf.outbox_item_stale = _stale
        osnd.split_outbound_bubbles = _orig_split
        memory.db, memory.tail, memory.fingerprint, memory.insert = _mdb, _mtail, _mfp, _mins


def test_qq_flush_pollution_drop():
    ob._save_outbox({"items": [{
        "id": "poll-1",
        "peer": "peer_p0_zz",
        "text": "腿软",
        "status": "pending",
        "reason": "unit",
        "created": "2026-10-26 12:00:00",
    }]})
    sent = []
    res = ob.flush_qq_outbox(lambda p, t: sent.append(t), peer_id="peer_p0_zz",
                             after_inbound_text="hi",
                             now=dt.datetime(2026, 10, 26, 12, 0))
    assert res and res[0].get("dropped"), res
    assert sent == []
    assert ob._load_outbox()["items"][0]["status"] == "dropped"
    print("ok qq pollution drop")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
