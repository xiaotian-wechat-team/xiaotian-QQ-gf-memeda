"""Unit tests for Magpie inbound image caption (no network; no QQ/WeChat send)."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import image_caption as ic
from channels.types import Inbound


def _tiny_jpeg(path: pathlib.Path):
    from PIL import Image
    Image.new("RGB", (64, 48), (200, 100, 50)).save(path, format="JPEG", quality=85)


def test_format_and_cache(tmp_path, monkeypatch):
    cfg = {
        "enabled": True,
        "magpie_base": "http://127.0.0.1:3425/v1",
        "model": "auto/gpt-6-luna",
        "cache_dir": str(tmp_path / "cache"),
        "timeout": 5,
    }
    assert ic.format_tag_with_caption("[图片]", "空教室阳光") == "[图片：空教室阳光]"
    assert ic.format_tag_with_caption("[图片×2]", "两张合影") == "[图片×2：两张合影]"

    img = tmp_path / "a.jpg"
    _tiny_jpeg(img)
    sha = ic.file_hash(img)
    ic.cache_put(sha, "缓存描述", cfg=cfg)
    assert ic.cache_get(sha, cfg) == "缓存描述"

    # cache hit skips network
    called = []
    def boom(*a, **k):
        called.append(1)
        raise AssertionError("should not call network")
    monkeypatch.setattr(ic, "_magpie_key", lambda: "k")
    # if cache works, caption_image returns without prep/network after hit
    assert ic.caption_image(img, cfg=cfg, use_cache=True) == "缓存描述"


def test_enrich_fail_soft_and_success(tmp_path, monkeypatch):
    cfg = {
        "enabled": True,
        "magpie_base": "http://127.0.0.1:3425/v1",
        "model": "auto/gpt-6-luna",
        "cache_dir": str(tmp_path / "cache"),
    }
    # no path → bare tag
    t, info = ic.enrich_text("[图片]", [], cfg=cfg)
    assert t == "[图片]" and info["error"] == "no_local_image"

    img = tmp_path / "b.jpg"
    _tiny_jpeg(img)

    monkeypatch.setattr(ic, "caption_image", lambda *a, **k: "木课桌朝向黑板")
    t2, info2 = ic.enrich_text("看看 [图片]", [str(img)], cfg=cfg)
    assert t2 == "看看 [图片：木课桌朝向黑板]"
    assert info2["ok"] and info2["caption"] == "木课桌朝向黑板"
    monkeypatch.undo()

    # already enriched
    t3, info3 = ic.enrich_text("[图片：已有描述]", [str(img)], cfg=cfg)
    assert t3 == "[图片：已有描述]" and info3.get("error") == "already_enriched"

    # refuse kimi production base (real caption_image, no network)
    monkeypatch.setattr(ic, "_magpie_key", lambda: "k")
    monkeypatch.setattr(ic, "prep_jpeg_b64", lambda *a, **k: ("xx", (1, 1), 2))
    bad = {**cfg, "magpie_base": "https://api.kimi.com/coding/v1", "cache_dir": str(tmp_path / "cache2")}
    assert ic.caption_image(img, cfg=bad, use_cache=False) is None


def test_enrich_inbound_before_brain_shape(tmp_path, monkeypatch):
    img = tmp_path / "c.jpg"
    _tiny_jpeg(img)
    monkeypatch.setattr(ic, "caption_image", lambda *a, **k: "拼图乐高")
    msg = Inbound(
        channel="qq_onebot",
        peer_id="424242",
        text="[图片]",
        msg_type="image",
        raw={"_xiaotian_media": {"image_paths": [str(img)], "image_count": 1}},
    )
    msg2, info = ic.enrich_inbound(msg, cfg={"enabled": True, "cache_dir": str(tmp_path / "c")})
    assert msg2.text == "[图片：拼图乐高]"
    assert info["ok"]
    assert msg2.raw.get("_xiaotian_caption", {}).get("ok")


def test_brain_type_note_allows_bracket_desc():
    import brain
    tn = brain.type_note_for([("them", "[图片：空教室]")])
    assert "图片" in tn
    assert "短描述" in tn or "冒号" in tn
    assert "看不到内容" not in tn


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
