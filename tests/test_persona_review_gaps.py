"""Offline regressions: receipt configuration and strict demo_contact media boundaries."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import sys, pathlib
import pytest
from unittest.mock import Mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_classmate_memory_ensure_and_config_path(tmp_path, monkeypatch):
    import classmate_memory as cm
    monkeypatch.setattr(cm, "DB_PATH", tmp_path / "nested" / "cm.db")
    p = cm.ensure()
    assert p.is_file()
    assert p.parent.is_dir()
    cm.insert("classmate:qq_onebot:1", "them", "hi")
    assert cm.history("classmate:qq_onebot:1", 5)[-1][1] == "hi"


def test_qq_receipts_reads_config_defaults():
    import channels.qq_receipts as qr
    from config import load
    cfg = load().get("qq_receipts") or {}
    assert qr.TTL_SEC == int(float(cfg.get("ttl_hours", 6)) * 3600)
    assert qr.MAX_ROWS == int(cfg.get("max_rows", 2000))
    assert str(qr.DB_PATH).endswith("qq_receipts.db")


def test_outbox_reads_config_ttl():
    import channels.outbox_store as ob
    from config import load
    cfg = load().get("outbox") or {}
    assert ob.TERMINAL_TTL_SEC == int(float(cfg.get("terminal_ttl_hours", 48)) * 3600)
    assert ob.MAX_PENDING_ROWS == int(cfg.get("max_pending", 80))
    assert ob.MAX_TERMINAL_ROWS == int(cfg.get("max_terminal", 200))


def test_outbound_media_gate_cannot_be_bypassed_by_classmate_flags():
    from channels import onebot_qq as ob
    assert not ob._is_allowed_peer("555555")
    assert ob._outbound_media_permitted("555555", "image") is False
    assert ob._outbound_media_permitted("555555", "voice") is False
    allow = next(iter(ob._qq_allow_ids()))
    assert ob._outbound_media_permitted(allow, "image") is True
    assert ob._outbound_media_permitted(allow, "voice") is True
    cfg = {"contact": {"id": "demo_contact"}, "persona": {
        "primary_contact_id": "demo_contact", "contact_aliases": {f"qq_onebot:{allow}": "demo_contact"},
        "channels": {"qq": {"allow_user_ids": [allow]}},
        "classmate": {"enabled": True, "allow_image": True, "allow_voice": True},
    }}
    for kind in ("image", "voice"):
        assert ob._outbound_media_permitted("555555", kind, cfg) is False
        assert ob._outbound_media_permitted(allow, kind, cfg) is True


def test_scrub_couple_nicknames():
    from channels.classmate import scrub_couple_nicknames
    s = scrub_couple_nicknames("宝贝，在吗 honey", "阿强同学")
    assert "宝贝" not in s and "honey" not in s.lower()
    assert "阿强同学" in s
    s2 = scrub_couple_nicknames("小熊别闹", "李同学")
    assert "小熊" not in s2 and "李同学" in s2
    s3 = scrub_couple_nicknames("嗨🐻想你了", "王同学")
    assert "🐻" not in s3 and "想你了" not in s3
    assert "王同学" in s3
    s4 = scrub_couple_nicknames("好想你，想你啦，想我想，想我了", "周同学")
    for frag in ("好想你", "想你啦", "想我想", "想我了"):
        assert frag not in s4


def test_qq_send_layer_rejects_stranger_without_caption_fallback():
    from channels import onebot_qq as ob
    ws, tracker = Mock(), Mock()
    with pytest.raises(PermissionError):
        ob.send_private_image_ws(ws, "999001", "/nonexistent/image.jpg", caption="嗯", tracker=tracker)
    ws.send.assert_not_called()
    tracker.send.assert_not_called()



def test_ilink_outbound_media_gate():
    from channels import ilink_weixin as iw
    stranger = "stranger@im.wechat"
    couple = "demo@im.wechat"
    assert iw._is_couple_peer(stranger) is False
    assert iw._outbound_media_permitted(stranger, "image") is False
    assert iw._outbound_media_permitted(stranger, "voice") is False
    assert iw._is_couple_peer(couple) is True
    assert iw._outbound_media_permitted(couple, "image") is True
    assert iw._outbound_media_permitted(couple, "voice") is True
    cfg = {"contact": {"id": "demo_contact"}, "persona": {
        "primary_contact_id": "demo_contact", "contact_aliases": {f"wechat_ilink:{couple}": "demo_contact"},
        "channels": {}, "classmate": {"enabled": True, "allow_image": True, "allow_voice": True},
    }}
    for kind in ("image", "voice"):
        assert iw._outbound_media_permitted(stranger, kind, cfg) is False
        assert iw._outbound_media_permitted(couple, kind, cfg) is True


def test_ilink_send_rejects_media_for_stranger(monkeypatch):
    from channels import ilink_weixin as iw
    from channels.types import Outbound
    sent = []
    runner = iw.ILinkWeixinRunner.__new__(iw.ILinkWeixinRunner)
    runner._bot = object()
    runner._ctx = {}
    monkeypatch.setattr(runner, "_refresh_ctx", lambda peer: "tok")
    monkeypatch.setattr(
        runner, "_send_text_checked", lambda peer, text, ctx: sent.append(("text", text))
    )
    monkeypatch.setattr(
        runner, "_send_image_checked",
        lambda peer, image, ctx, caption=None: sent.append(("image", image)),
    )
    monkeypatch.setattr(
        runner, "_send_audio_file",
        lambda peer, path, ctx, caption=None: sent.append(("voice", path)),
    )
    for meta in ({"image_path": "/tmp/x.jpg"}, {"voice_path": "/tmp/x.wav"}):
        with pytest.raises(PermissionError):
            runner._send(Outbound(channel="wechat_ilink", peer_id="stranger@im.wechat",
                                 text="旁白也不能误发", meta=meta))
    assert sent == []
    couple = "demo@im.wechat"
    runner._send(Outbound(channel="wechat_ilink", peer_id=couple, text="看看",
                         meta={"image_path": "/tmp/x.jpg"}))
    assert sent == [("text", "看看"), ("image", "/tmp/x.jpg")]
    sent.clear()
    runner._send(Outbound(channel="wechat_ilink", peer_id=couple, text="语音旁白",
                         meta={"voice_path": "/tmp/x.wav"}))
    assert sent == [("voice", "/tmp/x.wav")]

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
