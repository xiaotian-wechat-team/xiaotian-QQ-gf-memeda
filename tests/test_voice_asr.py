"""Unit tests for inbound voice ASR (no QQ/WeChat send; network only if model already cached)."""
import pathlib
import sys
import wave
import struct
import math

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import voice_asr as va
from channels.types import Inbound


def _tiny_wav(path: pathlib.Path, seconds: float = 0.3):
    sr = 16000
    n = int(sr * seconds)
    pcm = b"".join(struct.pack("<h", int(1000 * math.sin(2 * math.pi * 440 * i / sr))) for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def test_format_and_cache(tmp_path, monkeypatch):
    cfg = {
        "enabled": True,
        "backend": "mlx_whisper",
        "model": "mlx-community/whisper-small-mlx",
        "cache_dir": str(tmp_path / "cache"),
    }
    assert va.format_tag_with_transcript("[语音]", "刚下课") == "[语音：刚下课]"
    assert va.format_tag_with_transcript("[语音 3秒]", "在吗") == "[语音 3秒：在吗]"

    wav = tmp_path / "a.wav"
    _tiny_wav(wav)
    sha = va.file_hash(wav)
    va.cache_put(sha, "缓存转写", cfg=cfg)
    assert va.cache_get(sha, cfg) == "缓存转写"
    assert va.transcribe_file(wav, cfg=cfg, use_cache=True) == "缓存转写"


def test_enrich_fail_soft_and_success(tmp_path, monkeypatch):
    cfg = {
        "enabled": True,
        "backend": "mlx_whisper",
        "cache_dir": str(tmp_path / "cache"),
    }
    t, info = va.enrich_text("[语音]", [], cfg=cfg)
    assert t == "[语音]" and info["error"] == "no_local_voice"

    wav = tmp_path / "b.wav"
    _tiny_wav(wav)
    monkeypatch.setattr(va, "transcribe_file", lambda *a, **k: "宝宝你在干嘛")
    t2, info2 = va.enrich_text("听下 [语音]", [str(wav)], cfg=cfg)
    assert t2 == "听下 [语音：宝宝你在干嘛]"
    assert info2["ok"] and info2["text"] == "宝宝你在干嘛"

    t3, info3 = va.enrich_text("[语音：已有]", [str(wav)], cfg=cfg)
    assert t3 == "[语音：已有]" and info3.get("error") == "already_enriched"


def test_enrich_inbound_shape(tmp_path, monkeypatch):
    wav = tmp_path / "c.wav"
    _tiny_wav(wav)
    monkeypatch.setattr(va, "transcribe_file", lambda *a, **k: "我到宿舍了")
    msg = Inbound(
        channel="qq_onebot",
        peer_id="10001",
        text="[语音]",
        msg_type="record",
        raw={"_xiaotian_media": {"voice_paths": [str(wav)], "voice_count": 1}},
    )
    msg2, info = va.enrich_inbound(msg, cfg={"enabled": True, "cache_dir": str(tmp_path / "c")})
    assert msg2.text == "[语音：我到宿舍了]"
    assert info["ok"]
    assert msg2.raw.get("_xiaotian_asr", {}).get("ok")


def test_brain_type_note_for_voice_transcript():
    import brain
    tn = brain.type_note_for([("them", "[语音：刚下课]")])
    assert "语音" in tn
    assert "转写" in tn or "冒号" in tn
    assert "暂时听不了" not in tn




def test_prefer_qq_inline_skips_mlx(tmp_path, monkeypatch):
    called = {"mlx": 0}

    def _boom(*a, **k):
        called["mlx"] += 1
        return "不应走到"

    monkeypatch.setattr(va, "transcribe_file", _boom)
    cfg = {"enabled": True, "prefer_qq_stt": True, "backend": "mlx_whisper", "cache_dir": str(tmp_path / "c")}
    t, info = va.enrich_text("[语音]", [], cfg=cfg, qq_transcript="刚下课啦")
    assert t == "[语音：刚下课啦]"
    assert info["ok"] and info["source"] == "qq_inline"
    assert called["mlx"] == 0


def test_prefer_qq_fetch_then_mlx_fallback(tmp_path, monkeypatch):
    cfg = {
        "enabled": True,
        "prefer_qq_stt": True,
        "qq_stt_action": "fetch_ptt_text",
        "backend": "mlx_whisper",
        "cache_dir": str(tmp_path / "c"),
    }
    monkeypatch.setattr(va, "qq_stt_fetch", lambda *a, **k: "QQ转写成功")
    monkeypatch.setattr(va, "transcribe_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError("mlx")))
    t, info = va.enrich_text("[语音]", [], cfg=cfg, qq_message_id=12345)
    assert t == "[语音：QQ转写成功]"
    assert info["source"] == "qq_fetch_ptt_text"

    wav = tmp_path / "f.wav"
    _tiny_wav(wav)
    monkeypatch.setattr(va, "qq_stt_fetch", lambda *a, **k: None)
    monkeypatch.setattr(va, "transcribe_file", lambda *a, **k: "本地回退")
    t2, info2 = va.enrich_text("[语音]", [str(wav)], cfg=cfg, qq_message_id=99)
    assert t2 == "[语音：本地回退]"
    assert info2["source"] == "mlx_whisper"
    assert info2.get("qq_stt_error")


def test_extract_inline_from_record_segment():
    raw = {
        "message_id": 7,
        "message": [{"type": "record", "data": {"file": "a.silk", "text": "内嵌转写"}}],
    }
    assert va.extract_inline_qq_transcript(raw) == "内嵌转写"


def test_normalize_record_stores_message_id_and_transcript():
    from channels.onebot_qq import normalize_event

    ev = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 10001,
        "message_id": 4242,
        "time": 1,
        "message": [
            {
                "type": "record",
                "data": {"file": "file://dummy.silk", "text": "内嵌", "duration": 3},
            }
        ],
    }
    m = normalize_event(ev)
    assert m and m.msg_type == "record"
    assert m.msg_id == "4242"
    media = m.raw.get("_xiaotian_media") or {}
    assert media.get("message_id") == 4242
    assert media.get("voice_transcript") == "内嵌"
    assert "[语音" in m.text and "内嵌" in m.text

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
