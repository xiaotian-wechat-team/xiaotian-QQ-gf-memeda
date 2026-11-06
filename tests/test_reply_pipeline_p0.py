"""P0: outbox drop, nick merge, sleep tighten, cancel sleep helper."""
import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import tempfile
from channels import outbox_store as _obs
_obs.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "outbox_reply_p0.db"

from bubble_split import merge_sole_nick_bubbles, split_bubbles
import sleep_chat as sc
from channels import bridge
from channels import ilink_weixin as iw


def test_merge_sole_nick():
    assert merge_sole_nick_bubbles(["小熊", "睡吧"], "小熊") == ["小熊，睡吧"]
    assert merge_sole_nick_bubbles(["嗯", "小熊"], "小熊") == ["嗯小熊"]
    out = merge_sole_nick_bubbles(["小熊", "晚安", "小熊"], "小熊")
    assert all(b != "小熊" for b in out)
    assert any("小熊" in b for b in out)
    print("ok nick", out)


def test_goodnight_tighten_no_itinerary():
    raw = "还活动了？\n明天爬长城别忘了\n晚安"
    out = sc.tighten_text(raw, "pre_sleep")
    assert "长城" not in out and "明天" not in out
    assert "晚安" in out or "活动" in out
    bubbles = split_bubbles(out, max_bubbles=3)
    assert len(bubbles) <= 3
    print("ok tighten", out)


def test_outbox_drop_stale_goodnight_night():
    iw._save_outbox({"items": []})
    data = {
        "items": [{
            "id": "t-test-gn",
            "peer": "demo@im.wechat",
            "text": "我先睡了\n晚安笨熊",
            "status": "pending",
            "created": "2026-10-26 00:01:00",
        }]
    }
    iw._save_outbox(data)
    class StubBot:
        class client:
            opts = object()
            @staticmethod
            def _generate_client_id():
                return "c"
    now = dt.datetime(2026, 10, 26, 0, 30, 0)
    res = iw.flush_ilink_outbox(StubBot(), "demo@im.wechat", "tok", after_inbound_text="还在吗", now=now)
    assert res and res[0].get("dropped") is True
    saved = iw._load_outbox()
    assert saved["items"][0]["status"] == "dropped"
    print("ok outbox drop", saved["items"][0]["note"][:60])


def test_interruptible_sleep_cancel():
    flag = {"n": 0}
    def cancel():
        flag["n"] += 1
        return flag["n"] >= 2
    assert bridge._interruptible_sleep(2.0, cancel) is True
    print("ok cancel sleep")


if __name__ == "__main__":
    test_merge_sole_nick()
    test_goodnight_tighten_no_itinerary()
    test_outbox_drop_stale_goodnight_night()
    test_interruptible_sleep_cancel()
    print("ALL OK")
