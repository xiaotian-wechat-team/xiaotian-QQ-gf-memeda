"""OneBot → bridge smoke（无网、不写 demo_contact 共享库、不调 LLM）。"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from channels.onebot_qq import (
    normalize_event,
    make_private_event,
    inject_text,
    send_private_text_http,
)
from channels.bridge import handle_inbound
from channels.types import Inbound, Outbound

# normalize
ev = make_private_event("10001", "桥接测试")
m = normalize_event(ev)
assert m and m.channel == "qq_onebot" and m.peer_id == "10001" and m.text == "桥接测试"

# inject + stub → 情侣白名单 peer 共享 demo_contact；陌生人走 classmate
r = inject_text("在吗", peer="10001", stub_reply="嗯我在", persist=False)
assert r["contact_id"] == "demo_contact"
assert r["reply"] == "嗯我在"
assert r["persist"] is False
assert r["dry"] is True

# 非白名单 peer：normalize 丢弃；bridge 对 classmate contact 硬拦截（仅 demo_contact 运行时）
assert normalize_event(make_private_event("10002", "在吗")) is None
r_s = handle_inbound(
    Inbound(channel="qq_onebot", peer_id="10002", text="在吗"),
    dry=True,
    reply_fn=lambda _cid, _hist: "同学占位",
    persist=False,
)
assert r_s["contact_id"].startswith("classmate:")
assert r_s.get("blocked") is True
assert r_s.get("reason") == "contact_not_allowed"
assert r_s.get("reply") == ""

# handle_inbound persist=False + send 仍可调用 send（dry=False 路径）情侣
sent = []
def _send(out: Outbound):
    sent.append(out)

msg = Inbound(channel="qq_onebot", peer_id="10001", text="ping")
r2 = handle_inbound(
    msg,
    dry=False,
    send=_send,
    reply_fn=lambda cid, hist: f"pong:{cid}",
    persist=False,
)
assert r2["reply"] == "pong:demo_contact"
assert len(sent) == 1 and sent[0].text == "pong:demo_contact" and sent[0].peer_id == "10001"


# inbound image / record → brain-facing text
ev_img = {
    "post_type": "message",
    "message_type": "private",
    "user_id": 10001,
    "message": [{"type": "image", "data": {"file": "file://x.jpg"}}],
    "message_id": 9,
    "time": 1,
}
mi = normalize_event(ev_img)
assert mi and mi.msg_type == "image" and "[图片]" in mi.text, mi

ev_voice = {
    "post_type": "message",
    "message_type": "private",
    "user_id": 10001,
    "message": [{"type": "record", "data": {"file": "file://x.silk"}}],
    "message_id": 10,
    "time": 1,
}
mv = normalize_event(ev_voice)
assert mv and mv.msg_type == "record" and "[语音]" in mv.text, mv

# outbox enqueue / load (SQLite)
from channels import onebot_qq as ob
from channels import outbox_store as _obs
import tempfile, os
_ob_dir = pathlib.Path(tempfile.mkdtemp())
_obs.DB_PATH = _ob_dir / "outbox.db"
_obs.LEGACY_JSON = {"onebot": _ob_dir / "nope.json", "ilink": _ob_dir / "nope2.json"}
import memory
memory.CFG = {**memory.CFG, "db_path": str(_ob_dir / "chat.db")}
oid = ob.enqueue_outbox(peer="10001", text="补发测试", reason="unit")
data = ob._load_outbox()
assert any(x.get("id") == oid and x.get("status") == "pending" for x in data["items"])
flushed = []
ob.flush_qq_outbox(lambda peer, txt: flushed.append((peer, txt)), peer_id="10001", after_inbound_text="hi")
assert flushed == [("10001", "补发测试")]
assert ob._load_outbox()["items"][0]["status"] == "sent"

# want_voice bridge path (mock TTS; no network)
import brain
sent_v = []
def _send_v(out: Outbound):
    sent_v.append(out)
brain.LAST = {"want_voice": True, "nickname": None}
import channels.bridge as bridge
_orig_pv = bridge._plan_want_voice
bridge._plan_want_voice = lambda text, *, channel: "/tmp/fake_voice.mp3"
msg = Inbound(channel="qq_onebot", peer_id="10001", text="说一句")
r3 = handle_inbound(
    msg, dry=False, send=_send_v,
    reply_fn=lambda cid, hist: "嗯嗯",
    persist=False,
)
assert r3.get("want_voice") is True
assert any((o.meta or {}).get("voice_path") == "/tmp/fake_voice.mp3" for o in sent_v), sent_v
bridge._plan_want_voice = _orig_pv
brain.LAST = {}

# QQ outbox: stale goodnight dropped (align iLink)
import datetime as _dt
_ob_dir2 = pathlib.Path(tempfile.mkdtemp())
_obs.DB_PATH = _ob_dir2 / "outbox.db"
ob._save_outbox({"items": [{
    "id": "gn-qq",
    "peer": "10001",
    "text": "我先睡了\n晚安笨熊",
    "status": "pending",
}]})
flushed2 = []
res_gn = ob.flush_qq_outbox(
    lambda peer, txt: flushed2.append((peer, txt)),
    peer_id="10001",
    after_inbound_text="还在吗",
    now=_dt.datetime(2026, 10, 26, 0, 30, 0),
)
assert res_gn and res_gn[0].get("dropped") is True, res_gn
assert flushed2 == []
assert next(it for it in ob._load_outbox()["items"] if it["id"] == "gn-qq")["status"] == "dropped"

# require_media: image missing → drop, never text-only
ob._save_outbox({"items": [{
    "id": "surf1",
    "peer": "10001",
    "text": "看这个",
    "image_path": "/nonexistent/surf.png",
    "require_media": True,
    "status": "pending",
}]})
flushed3 = []
res_s = ob.flush_qq_outbox(
    lambda peer, txt: flushed3.append(txt),
    lambda peer, path, caption=None: flushed3.append(("img", path)),
    peer_id="10001",
    after_inbound_text="hi",
)
assert res_s and res_s[0].get("dropped") is True
assert flushed3 == []
assert next(it for it in ob._load_outbox()["items"] if it["id"] == "surf1")["status"] == "dropped"
print("OK")
