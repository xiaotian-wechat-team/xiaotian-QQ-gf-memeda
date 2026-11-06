"""bridge want_image → Outbound.meta.image_path（ilink/onebot 共用；无真 LLM/无真发）。"""
from __future__ import annotations
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import brain
from channels.bridge import handle_inbound
from channels.types import Inbound, Outbound
from channels import onebot_qq


def test_bridge_sets_image_path_for_ilink_and_onebot(tmp_path, monkeypatch):
    img = tmp_path / "fake.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    # stub chat_media：不生图、不发 hold_on 真逻辑以外的重依赖
    cm = types.ModuleType("chat_media")

    def hold_on_line(rng=None, **kwargs):
        return "等一下哈"

    def plan_chat_images(scene, *, caption=None, **kw):
        assert scene == "操场散步"
        return {
            "scene": scene,
            "n_images": 1,
            "paths": [img],
            "texts": [caption or "给你看"],
            "dry": False,
        }

    cm.hold_on_line = hold_on_line
    cm.plan_chat_images = plan_chat_images
    monkeypatch.setitem(sys.modules, "chat_media", cm)
    monkeypatch.setenv("IMAGEGEN_HOLD_ON", "1")

    # mem.release 廉价 mock
    mem_m = types.ModuleType("mem")
    released = {"n": 0}

    def release():
        released["n"] += 1

    mem_m.release = release
    monkeypatch.setitem(sys.modules, "mem", mem_m)

    def reply_fn(cid, hist):
        brain.LAST = {"want_image": "操场散步", "t_ms": 1}
        return "下课啦看这"

    couple_peer = {
        "wechat_ilink": "demo@im.wechat",
        "qq_onebot": "10001",
    }
    for channel in ("wechat_ilink", "qq_onebot"):
        sent: list[Outbound] = []
        brain.LAST = {}
        released["n"] = 0
        r = handle_inbound(
            Inbound(channel=channel, peer_id=couple_peer[channel], text="发张图"),
            dry=False,
            send=sent.append,
            reply_fn=reply_fn,
            persist=False,
        )
        assert r["want_image"] == "操场散步", r
        assert r.get("n_images") == 1
        assert released["n"] == 1, "mem.release after reply"
        # hold_on 文本 + 独立文字气泡 + 图（无图注糊一条）
        assert len(sent) >= 2, sent
        assert sent[0].text == "等一下哈" and not (sent[0].meta or {}).get("image_path")
        img_outs = [o for o in sent if (o.meta or {}).get("image_path")]
        assert len(img_outs) == 1
        assert img_outs[0].meta["image_path"] == str(img)
        # image Outbound must not pack caption text
        assert not (img_outs[0].text or "").strip(), img_outs[0].text
        text_outs = [
            o for o in sent
            if (o.text or "").strip() and not (o.meta or {}).get("image_path")
        ]
        assert any("下课" in (o.text or "") for o in text_outs), sent
        first_img_i = next(i for i, o in enumerate(sent) if (o.meta or {}).get("image_path"))
        assert all(not (sent[j].meta or {}).get("image_path") for j in range(first_img_i))
        assert img_outs[0].channel == channel


def test_onebot_ws_send_image_payload(tmp_path, monkeypatch):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg-bytes")

    from channels.send_receipts import AckTracker, ReceiptJournal
    tracker = AckTracker(timeout=.01, journal=ReceiptJournal(tmp_path / "acks.db"))
    class FakeWS:
        def __init__(self):
            self.frames = []

        def send(self, s):
            self.frames.append(json.loads(s))
            tracker.resolve({"echo": self.frames[-1]["echo"], "status":"ok", "retcode":0, "data":{"message_id":1}})

    ws = FakeWS()
    onebot_qq.send_private_image_ws(ws, "10001", str(img), caption="看", tracker=tracker)
    assert len(ws.frames) == 1
    fr = ws.frames[0]
    assert fr["action"] == "send_private_msg"
    assert fr["params"]["user_id"] == 10001
    segs = fr["params"]["message"]
    assert segs[0] == {"type": "text", "data": {"text": "看"}}
    assert segs[1]["type"] == "image"
    assert segs[1]["data"]["file"].startswith("base64://")

    # Runner._send 路径：构造最小 runner 调内部逻辑
    captured = []

    class R:
        _ws = type("W", (), {"send": lambda self, s: captured.append(json.loads(s))})()

    # 复用模块级函数即可；再测 _send 分支通过直接调用 send_private_image_ws 已覆盖。
    # 额外：Outbound meta → 与 bridge 一致
    out = Outbound(channel="qq_onebot", peer_id="10001", text="配文", meta={"image_path": str(img)})
    onebot_qq.send_private_image_ws(ws, out.peer_id, out.meta["image_path"], caption=out.text, tracker=tracker)
    assert ws.frames[-1]["params"]["message"][1]["type"] == "image"



def test_bridge_casual_chat_no_hold_on_or_image(monkeypatch):
    """「干嘛呢」类闲聊：即使 reply_fn 塞了 want_image，也不得 hold_on / 生图。"""
    import types
    cm = types.ModuleType("chat_media")
    called = {"hold": 0, "plan": 0}

    def hold_on_line(rng=None, **kwargs):
        called["hold"] += 1
        return "急什么 马上发你"

    def plan_chat_images(scene, *, caption=None, **kw):
        called["plan"] += 1
        return {"scene": scene, "n_images": 1, "paths": ["/tmp/x.png"], "texts": [caption or ""], "dry": False}

    cm.hold_on_line = hold_on_line
    cm.plan_chat_images = plan_chat_images
    monkeypatch.setitem(sys.modules, "chat_media", cm)
    monkeypatch.setenv("IMAGEGEN_HOLD_ON", "1")

    mem_m = types.ModuleType("mem")
    mem_m.release = lambda: None
    monkeypatch.setitem(sys.modules, "mem", mem_m)

    def reply_fn(cid, hist):
        brain.LAST = {"want_image": "操场散步", "t_ms": 1}
        return "刷短视频呢"

    sent = []
    brain.LAST = {}
    r = handle_inbound(
        Inbound(channel="qq_onebot", peer_id="10001", text="干嘛呢"),
        dry=False,
        send=sent.append,
        reply_fn=reply_fn,
        persist=False,
    )
    assert r.get("want_image") in (None, ""), r
    assert called["hold"] == 0 and called["plan"] == 0, called
    assert all(not (o.meta or {}).get("image_path") for o in sent), sent
    assert any("刷短视频" in (o.text or "") for o in sent), sent
    assert not any("马上发你" in (o.text or "") or "急什么" in (o.text or "") for o in sent), sent


def test_bridge_dry_exposes_want_image_without_send(monkeypatch):
    mem_m = types.ModuleType("mem")
    mem_m.release = lambda: None
    monkeypatch.setitem(sys.modules, "mem", mem_m)

    def reply_fn(cid, hist):
        brain.LAST = {"want_image": "图书馆"}
        return "书堆旁边"

    sent = []
    r = handle_inbound(
        Inbound(channel="wechat_ilink", peer_id="demo@im.wechat", text="？"),
        dry=True,
        send=sent.append,
        reply_fn=reply_fn,
        persist=False,
    )
    assert r["want_image"] == "图书馆"
    assert r["reply"] == "书堆旁边"
    assert sent == []


if __name__ == "__main__":
    # 无 pytest 时的最小跑法
    import tempfile
    from unittest.mock import MagicMock

    class MP:
        def __init__(self):
            self._env = {}
            self._mods = {}

        def setitem(self, d, k, v):
            self._mods[(id(d), k)] = d.get(k, None) if isinstance(d, dict) else None
            d[k] = v

        def setenv(self, k, v):
            import os
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

        def undo(self):
            import os
            for k, old in self._env.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    with tempfile.TemporaryDirectory() as td:
        mp = MP()
        try:
            test_bridge_sets_image_path_for_ilink_and_onebot(pathlib.Path(td), mp)
            test_onebot_ws_send_image_payload(pathlib.Path(td), mp)
            test_bridge_casual_chat_no_hold_on_or_image(mp)
            test_bridge_dry_exposes_want_image_without_send(mp)
        finally:
            mp.undo()
    print("OK")
