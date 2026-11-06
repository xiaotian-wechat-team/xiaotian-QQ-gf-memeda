"""离线：bubble_split 分行/截断；比例预算+抖动；daemon/proactive/bridge 调用点存在。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from bubble_split import (
    split_bubbles,
    is_url_bubble,
    extract_urls,
    bubble_gap_sec,
    MAX_BUBBLES,
    MAX_BUBBLE_CHARS,
    count_user_bubbles,
    reply_bubble_range,
    reply_bubble_budget,
    pack_bubbles,
)


def test_empty():
    assert split_bubbles(None) == []
    assert split_bubbles("") == []
    assert split_bubbles("   \n  ") == []


def test_lines_as_bubbles():
    assert split_bubbles("嗯\n想你了") == ["嗯", "想你了"]


def test_strip_want_image():
    text = "先回你一句\nWANT_IMAGE: dorm_night\n再补一句"
    assert split_bubbles(text) == ["先回你一句", "再补一句"]


def test_soft_split_long_line():
    long = "啊" * (MAX_BUBBLE_CHARS + 5)
    out = split_bubbles(long)
    assert out
    assert "".join(out) == long


def test_max_bubbles_cap():
    lines = "\n".join(f"第{i}句呀" for i in range(12))
    out = split_bubbles(lines)
    assert len(out) <= MAX_BUBBLES


def test_dedupe_adjacent():
    assert split_bubbles("嗯\n嗯\n好") == ["嗯", "好"]


def test_call_sites_exist():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("src/daemon.py", "src/proactive_sender.py", "src/channels/bridge.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "split_bubbles" in src, rel
        assert "bubble_split" in src, rel


def test_bubble_gap_sec_natural_range():
    """Inter-bubble pause must breathe (~0.85–2.6s); old 0.35–0.9s looked piled."""
    class _R:
        def uniform(self, a, b):
            return (a + b) / 2
    for text in ("嗯", "热乎的", "别光吃生的 加点热的", "x" * 18):
        g = bubble_gap_sec(text, rng=_R())
        assert 0.85 <= g <= 2.6, (text, g)
    # longer text ≥ shorter (same rng mid-jitter)
    assert bubble_gap_sec("别光吃生的 加点热的", rng=_R()) >= bubble_gap_sec("嗯", rng=_R())


def test_call_sites_use_bubble_gap():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("src/daemon.py", "src/proactive_sender.py", "src/channels/bridge.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "bubble_gap_sec" in src, rel
        assert "0.35 + min(0.55" not in src, rel


def test_count_user_bubbles_trailing():
    assert count_user_bubbles([("me", "嗨"), ("them", "a"), ("them", "b")]) == 2
    assert count_user_bubbles([("them", "单条")]) == 1
    assert count_user_bubbles([("them", "1"), ("them", "2"), ("them", "3"), ("them", "4"), ("them", "5")]) == 5
    assert count_user_bubbles([("them", "旧"), ("me", "回"), ("them", "新")]) == 1
    assert count_user_bubbles([]) == 1


def test_reply_bubble_range_anchors():
    assert reply_bubble_range(1) == (1, 3)
    assert reply_bubble_range(5) == (3, 7)
    lo, hi = reply_bubble_range(10)
    assert lo <= hi <= MAX_BUBBLES
    assert hi <= 8


def test_reply_bubble_budget_variance():
    class _R:
        def __init__(self, v):
            self.v = v
        def triangular(self, lo, hi, mode):
            return self.v
        def uniform(self, lo, hi):
            return self.v
    assert reply_bubble_budget(1, rng=_R(1.0), hard_min=1, hard_max=8) == 1
    assert reply_bubble_budget(1, rng=_R(3.0), hard_min=1, hard_max=8) == 3
    assert reply_bubble_budget(5, rng=_R(3.0), hard_min=1, hard_max=8) == 3
    assert reply_bubble_budget(5, rng=_R(7.0), hard_min=1, hard_max=8) == 7
    # never flood: 1→ not 10
    for _ in range(40):
        b = reply_bubble_budget(1, hard_min=1, hard_max=8)
        assert 1 <= b <= 3


def test_pack_bubbles_merges_short():
    many = ["嗯", "好", "行", "哦", "啊", "哈", "嘿", "嗨", "呀"]
    out = pack_bubbles(many, 3)
    assert len(out) == 3
    assert "".join(out).replace(" ", "") == "".join(many)


def test_split_respects_budget_via_max():
    lines = "\n".join(f"句子{i}内容不错" for i in range(8))
    out = split_bubbles(lines, max_bubbles=2)
    assert len(out) <= 2
    # packing keeps content rather than hard-drop
    assert "句子0" in out[0]


def test_bridge_uses_budget_and_image_unit(monkeypatch):
    """Budget limits captions; each image remains its own outbound after the text."""
    import brain, image_caption, voice_asr, photo_scene
    from channels import bridge
    from channels.types import Inbound
    monkeypatch.setattr(brain, "LAST", {})
    monkeypatch.setattr(image_caption, "enrich_inbound", lambda msg: (msg, {}))
    monkeypatch.setattr(voice_asr, "enrich_inbound", lambda msg: (msg, {}))
    monkeypatch.setattr(photo_scene, "allow_inbound_image_gen", lambda text: True)
    monkeypatch.setattr(bridge, "_mem_release", lambda: None)
    monkeypatch.setattr(bridge, "_sleep_split_gap", lambda style: ({}, {}))
    monkeypatch.setattr(bridge, "_interruptible_sleep", lambda *a: False)
    monkeypatch.setattr(bridge, "_plan_want_image", lambda *a, **k: {
        "paths": ["first.png", "second.png"], "texts": ["第一张", "第二张", "看看"],
    })

    def reply(*args):
        brain.LAST.update(want_image="操场", reply_bubble_budget=1)
        return "看看"

    sent = []
    bridge.handle_inbound(Inbound("qq_onebot", "10001", "发张图"),
                          dry=False, persist=False, reply_fn=reply, send=sent.append)
    assert len(sent) == 3
    assert sent[0].text and not (sent[0].meta or {}).get("image_path")
    assert [out.meta["image_path"] for out in sent[1:]] == ["first.png", "second.png"]
    assert all(not out.text for out in sent[1:])




def test_url_stays_whole_bubble():
    """surf_share / long Douyin URL must not char-chop into /v fragments."""
    url = "https://www.douyin.com/video/7455542024306052389"
    text = "学长\n刷到个滑雪的\n崇礼雪如意\n寒假人得挤爆吧\n" + url
    out = split_bubbles(text)
    assert url in out, out
    assert out.count(url) == 1
    assert is_url_bubble(url)
    # no path fragments
    bad = [b for b in out if b != url and ("douyin" in b.lower() or b.startswith("/"))]
    assert bad == [], bad
    # URL is its own final bubble after captions
    assert out[-1] == url
    # mixed caption+url on one line → peel URL intact
    mixed = split_bubbles("学长刷到这个 " + url)
    assert url in mixed and is_url_bubble(mixed[-1])
    rest, urls = extract_urls("看 " + url + " 呀")
    assert urls == [url] and "看" in rest
    # pack must not glue URL onto Chinese even under tight budget
    from bubble_split import pack_bubbles
    packed = pack_bubbles(
        ["学长", "刷到个滑雪的", "崇礼雪如意", "寒假人得挤爆吧", url],
        max_bubbles=2,
        max_chars=22,
    )
    assert url in packed
    assert any(is_url_bubble(b) for b in packed)
    print("ok url whole", out)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
