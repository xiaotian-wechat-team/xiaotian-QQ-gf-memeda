"""Share lane: caption via bubble_split; source_url dedicated; no glue-then-split."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from channels.share_send import peel_caption_and_link, share_send_plan, split_caption_bubbles
from bubble_split import split_bubbles


def test_peel_prefers_source_url():
    url = "https://www.douyin.com/video/7455542024306052389"
    cap, link = peel_caption_and_link(
        "学长\n刷到个滑雪的\n" + url, source_url=url
    )
    assert link == url
    assert "douyin" not in cap and "http" not in cap
    assert "滑雪" in cap


def test_plan_order_and_no_url_in_bubbles():
    url = "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef"
    plan = share_send_plan(
        "刷到个狠人\n滑雪住半年",
        source_url=url,
        image_path="/tmp/x.png",
    )
    assert plan["order"] == ["caption", "link", "image"]
    assert plan["link_url"] == url
    assert url not in plan["bubbles"]
    assert all(not (b or "").startswith("http") for b in plan["bubbles"])
    # caption-only split equals bubble_split(caption)
    assert plan["bubbles"] == split_bubbles(plan["caption"])


def test_split_caption_ignores_absent_link():
    assert split_caption_bubbles("嗯\n想你了") == split_bubbles("嗯\n想你了")


if __name__ == "__main__":
    test_peel_prefers_source_url()
    test_plan_order_and_no_url_in_bubbles()
    test_split_caption_ignores_absent_link()
    print("ALL OK")
