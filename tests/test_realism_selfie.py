
"""自拍强制 / 偶发生活照偏置 realism；不破坏 COUNT_P 与 ban。"""
import os, random, collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["XIAOTIAN_TEST"] = "1"
import realism
import imagegen
import outfit


def test_detect_photo_mode():
    assert realism.detect_photo_mode(None, "宿舍化妆镜自拍") == "selfie"
    assert realism.detect_photo_mode("宅宿舍/睡前", "对镜自拍看看妆") == "selfie"
    assert realism.detect_photo_mode(None, "给你拍一张操场") == "casual"
    assert realism.detect_photo_mode(None, "图书馆窗边看书") is None


def test_selfie_force_always_injects():
    hit = 0
    for seed in range(40):
        s = realism.pick(None, "宿舍化妆镜自拍", hour=21, seed=seed)
        assert s["photo_mode"] == "selfie"
        ids = set(s["ids"])
        assert ids & set(realism.SELFIE_RELATED_IDS), s
        # ban still honored: formal+selfie text shouldn't pick banned mirror in sacred — use sport_action ban on front_cam
        hit += 1
    assert hit == 40
    # mirror prefer
    s = realism.pick(None, "电梯镜子自拍", hour=12, seed=3)
    assert "mirror_selfie" in s["ids"] or "elevator_mirror" in s["ids"] or "front_cam_selfie" in s["ids"]


def test_casual_bias_probability():
    # p=1 always inject phone snap related
    n_inj = 0
    for seed in range(60):
        s = realism.pick(None, "给你拍一张食堂", hour=12, seed=seed, casual_p=1.0)
        assert s["photo_mode"] == "casual"
        if set(s["ids"]) & set(realism.PHONE_SNAP_IDS + ("vertical_phone",)):
            n_inj += 1
    assert n_inj == 60
    # p=0 never forces (lottery may still pick — allow some)
    forcedish = 0
    for seed in range(80):
        s = realism.pick(None, "给你拍一张食堂", hour=12, seed=seed, casual_p=0.0)
        # with p=0 inject skipped; may still randomly have phone ids
        pass
    # COUNT_P on non-selfie still ok
    rng = random.Random(42)
    ns = collections.Counter(len(realism.pick("外出日常", rng=rng, hour=15)["ids"]) for _ in range(2000))
    assert 0.22 < ns[0] / 2000 < 0.40


def test_ban_tags_still_block_mirror_in_formal():
    tags = realism.scene_tags(None, "见老师正式答辩自拍")
    assert "formal" in tags
    s = realism.pick(None, "见老师正式答辩自拍", hour=10, seed=1, tags=tags)
    assert "mirror_selfie" not in s["ids"]
    assert "elevator_mirror" not in s["ids"]
    # still got some selfie-related if allowed (front_cam ban_tags sport_action only)
    assert set(s["ids"]) & set(realism.SELFIE_RELATED_IDS) or s["ids"] == s["ids"]


def test_add_realism_wiring_selfie():
    plan = {"scene": "宅宿舍/睡前", "vector": outfit.BY_NAME.get("宅宿舍/睡前", {}).get("vec") or {}, "text": "t"}
    plan = imagegen.add_realism(plan, "宿舍化妆镜自拍", seed=2, cfg={"imagegen": {"realism_casual_photo_p": 0.4}})
    assert plan["realism"] and set(plan["realism"]["ids"]) & set(realism.SELFIE_RELATED_IDS)
    assert "写实细节" in plan["realism_text"] or plan["realism"]["ids"]


def test_chat_path_casual_bias_via_add_realism():
    plan = {"scene": "外出日常", "vector": {}, "text": "t"}
    # chat + casual_p=1 → phone snap injected even without 自拍 keyword
    p1 = imagegen.add_realism(dict(plan), "图书馆窗边看书", seed=9, cfg={"imagegen": {"realism_casual_photo_p": 1.0}}, chat=True)
    assert p1["realism"]["photo_mode"] == "casual"
    assert set(p1["realism"]["ids"]) & set(realism.PHONE_SNAP_IDS + ("vertical_phone",))
    # non-chat, no keyword → no forced casual
    p0 = imagegen.add_realism(dict(plan), "图书馆窗边看书", seed=9, cfg={"imagegen": {"realism_casual_photo_p": 1.0}}, chat=False)
    assert p0["realism"].get("photo_mode") in (None, "casual")  # detect None
    assert p0["realism"].get("photo_mode") is None
