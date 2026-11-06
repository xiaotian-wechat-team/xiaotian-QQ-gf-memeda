"""离线：日程槽 → shot + prompt 模板；meal/activity_end 分流。不调微信/QQ。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, random
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import schedule_shot as ss
import shot_modes as sm
import proactive_beats as pb


def test_baobei_still_hand_pov_only():
    rng = random.Random(0)
    shots = {ss.pick_shot_for_slot({"bucket": "city_fun", "activity": "逛街"},
                                   beat_id="schedule_brief", rng=random.Random(i))
             for i in range(40)}
    assert shots <= {sm.SHOT_HAND, sm.SHOT_POV}


def test_meal_prefers_hand_for_food():
    slot = {"activity": "吃晚饭", "bucket": "campus_life",
            "scene_hints": ["食堂"], "photo_ok": True}
    h = ss.image_hint_for_beat("meal", slot, rng=random.Random(1))
    assert h["mode"] == "want_image"
    assert h["shot_type"] == sm.SHOT_HAND
    assert h["scene"]
    assert "prompt_shot" in h and "hand_closeup" in (h.get("prompt_shot") or "")
    assert h["ref_policy"]["hard"] == ["hands/sheet"]


def test_mirror_bias_home_or_fitting():
    slot = {"activity": "宿舍化妆", "bucket": "weekend_home",
            "scene_hints": ["化妆镜"], "photo_ok": True}
    h = ss.image_hint_for_beat("daily_chat", slot, rng=random.Random(2))
    assert h["shot_type"] == sm.SHOT_MIRROR
    slot2 = {"activity": "商场试衣", "bucket": "city_fun",
             "scene_hints": ["试衣间"], "photo_ok": True}
    assert ss.pick_shot_for_slot(slot2, beat_id="activity_end", rng=random.Random(3)) == sm.SHOT_MIRROR


def test_bucket_weights_distribution():
    slot = {"activity": "操场散步", "bucket": "campus_fun",
            "scene_hints": ["操场"], "photo_ok": True}
    counts = {}
    for i in range(200):
        s = ss.pick_shot_for_slot(slot, beat_id="activity_end", rng=random.Random(i))
        counts[s] = counts.get(s, 0) + 1
    assert counts.get(sm.SHOT_POV, 0) >= 40  # outdoor-ish bias via keyword → POV forced
    # 关键词「操场」走 _ACT_FORCE → POV
    assert ss.pick_shot_for_slot(slot, beat_id="activity_end", rng=random.Random(0)) == sm.SHOT_POV


def test_meal_image_hint_uses_schedule_shot():
    slot = {"activity": "吃午饭", "bucket": "campus_life",
            "scene_hints": ["食堂窗口"], "photo_ok": True}
    # 强制无 material：本机可能有真实 materials DB
    _orig = pb.pick_surf_item
    pb.pick_surf_item = lambda *a, **k: None
    try:
        h = pb.meal_image_hint(slot, {"photo_bias": "campus_life"}, rng=random.Random(5))
    finally:
        pb.pick_surf_item = _orig
    assert h.get("mode") == "want_image"
    assert h.get("shot_type") in sm.ALL_SHOTS
    assert h.get("source", "").startswith("schedule_shot")
    task = pb.build_beat_task({
        "id": "meal", "slot": slot, "today": {}, "image": h,
    })
    assert f"WANT_IMAGE:[{h['shot_type']}]" in task


def test_activity_end_task_optional_want_image():
    ended = {"activity": "攀岩", "bucket": "city_fun", "scene_hints": ["攀岩馆"],
             "photo_ok": True, "tone": "嗨", "start": "13:00"}
    img = ss.image_hint_for_beat("activity_end", ended, rng=random.Random(7))
    task = pb.build_beat_task({
        "id": "activity_end", "slot": ended, "today": {}, "ended": ended, "image": img,
    })
    assert "WANT_IMAGE:[" in task
    assert img["shot_type"] in task


def test_cli_describe():
    d = ss.describe_mapping(
        {"activity": "图书馆自习", "bucket": "school_library", "scene_hints": ["窗边"]},
        beat_id="meal", seed=0,
    )
    assert d["hint"]["shot_type"]
    assert d["prompt_shot_preview"]




def test_proactive_single_is_one_sop_path():
    """主动单张：恰好一条 SOP，不是混合。"""
    slot = {"activity": "课间休息", "bucket": "campus_life",
            "scene_hints": ["走廊"], "photo_ok": True}
    # 避开 _ACT_FORCE 关键词
    shots = {ss.pick_shot_for_slot(slot, beat_id="daily_chat", rng=random.Random(i))
             for i in range(30)}
    assert shots <= set(sm.ALL_SHOTS)
    h = ss.image_hint_for_beat("daily_chat", slot, rng=random.Random(11))
    assert h["shot_type"] in sm.ALL_SHOTS
    assert h.get("prompt_shot")  # 单一镜头块
    # 不应同时塞入手+腿话术
    ps = h.get("prompt_shot") or ""
    hits = sum(1 for k in ("hand_closeup", "legs_focus", "pov_first_person", "mirror_selfie",
                           "full_body", "portrait") if k in ps)
    assert hits == 1, (h["shot_type"], ps, hits)


def test_n2_independent_draws_from_weights():
    """N=2：独立有放回抽样；用户锁则全同卡。"""
    slot = {"activity": "课间休息", "bucket": "campus_life",
            "scene_hints": ["走廊"], "photo_ok": True}
    types = ss.sample_shot_types_for_send(
        2, slot, beat_id="daily_chat", rng=random.Random(42), locked_shot=None,
    )
    assert len(types) == 2
    assert all(t in sm.ALL_SHOTS for t in types)
    # 多次抽样应能看到多样化（有放回独立抽）
    pairs = [
        tuple(ss.sample_shot_types_for_send(
            2, slot, beat_id="daily_chat", rng=random.Random(i), locked_shot=None,
        ))
        for i in range(80)
    ]
    assert len(set(pairs)) >= 5  # 不是永远同一对
    # 权重底座含 full_body 0.40
    w = ss._body_part_weights()
    assert abs(w[sm.SHOT_FULL_BODY] - 0.40) < 1e-9
    for name in ss._PART_EQUAL:
        assert abs(w[name] - 0.12) < 1e-9
    locked = ss.sample_shot_types_for_send(
        2, slot, beat_id="daily_chat", rng=random.Random(0), locked_shot=sm.SHOT_HAND,
    )
    assert locked == [sm.SHOT_HAND, sm.SHOT_HAND]


if __name__ == "__main__":
    test_baobei_still_hand_pov_only()
    test_meal_prefers_hand_for_food()
    test_mirror_bias_home_or_fitting()
    test_bucket_weights_distribution()
    test_meal_image_hint_uses_schedule_shot()
    test_activity_end_task_optional_want_image()
    test_cli_describe()
    test_proactive_single_is_one_sop_path()
    test_n2_independent_draws_from_weights()
    print("OK")
