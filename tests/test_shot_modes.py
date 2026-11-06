"""离线：报备 50/50、用户镜头路由、hand/POV 跳过 face+body HARD。不调微信/QQ、不调 codex。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, json, copy, tempfile, random
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PIL import Image
import shot_modes as sm
import refpick, imagegen, config, chat_media, photo_scene
import test_canonical_session as tcs


def test_baobei_equal_split():
    rng = random.Random(0)
    counts = {sm.SHOT_HAND: 0, sm.SHOT_POV: 0}
    for _ in range(200):
        counts[sm.pick_baobei_shot(rng)] += 1
    assert counts[sm.SHOT_HAND] + counts[sm.SHOT_POV] == 200
    # 以 seed=0 的 200 次应大致对半分；允许 35%~65%
    assert 70 <= counts[sm.SHOT_HAND] <= 130, counts
    assert 70 <= counts[sm.SHOT_POV] <= 130, counts
    # 确定性：同 seed 序列稳定
    a = [sm.pick_baobei_shot(random.Random(42)) for _ in range(20)]
    b = [sm.pick_baobei_shot(random.Random(42)) for _ in range(20)]
    assert a == b and set(a) <= {sm.SHOT_HAND, sm.SHOT_POV}


def test_baobei_image_hint_schedule():
    slot = {"activity": "图书馆自习", "bucket": "school_library",
            "scene_hints": ["图书馆窗边"], "photo_ok": True}
    h = sm.baobei_image_hint(slot, {"photo_bias": "campus_life"}, rng=random.Random(1))
    assert h["mode"] == "want_image"
    assert h["shot_type"] in sm.BAOBEI_SHOTS
    assert h["scene"]
    assert "手" in h["scene"] or "第一视角" in h["scene"] or "视角" in h["scene"]


def test_route_user_shot_phrases():
    assert sm.route_user_shot("看看手") == sm.SHOT_HAND
    assert sm.route_user_shot("看看你的手啊") == sm.SHOT_HAND
    assert sm.route_user_shot("看看腿") == sm.SHOT_LEGS
    assert sm.route_user_shot("看看全身") == sm.SHOT_FULL_BODY
    assert sm.route_user_shot("看看你拍的") == sm.SHOT_POV
    assert sm.route_user_shot("看看你") == sm.SHOT_PORTRAIT
    # 无关闲聊
    assert sm.route_user_shot("今天吃啥") is None
    assert photo_scene.user_asks_photo("看看手")
    assert photo_scene.route_shot_request("看看你拍的风景") == sm.SHOT_POV


def test_parse_want_image_shot_prefix():
    r = chat_media.parse_image_intent("随口说一句\nWANT_IMAGE:[hand_closeup]图书馆手边特写")
    assert r["scene"] == "图书馆手边特写"
    assert r["shot_type"] == "hand_closeup"
    r = chat_media.parse_image_intent(
        '{"want_image":"操场第一视角","shot_type":"pov_first_person","caption":"风好大"}'
    )
    assert r["shot_type"] == "pov_first_person" and r["scene"] == "操场第一视角"


def test_hand_pov_skip_hard_face_body():
    assert sm.skips_hard_face_body("hand_closeup")
    assert sm.skips_hard_face_body("pov_first_person")
    assert sm.skips_hard_face_body("legs_focus")
    assert not sm.skips_hard_face_body("full_body")
    assert not sm.skips_hard_face_body(None)

    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    # body approved
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    Image.new("RGB", (200, 400), (180, 180, 180)).save(body / "front.png")
    (body / "body.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    # hands approved
    hands = root / "canonical" / "hands"
    hands.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (200, 180, 170)).save(hands / "sheet.png")
    (hands / "hands.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")

    can, oth = refpick.pick_identity("图书馆", 3, cfg, shot_type="hand_closeup")
    assert can == [hands / "sheet.png"] and oth == []
    # 默认仍 face+body
    can0, oth0 = refpick.pick_identity("图书馆", 3, cfg)
    assert (root / "canonical" / "sheet.png") in can0
    assert (body / "sheet.png") in can0

    can_p, _ = refpick.pick_identity("操场", 3, cfg, shot_type="pov_first_person")
    assert (body / "sheet.png") not in can_p  # POV 不挂 body
    assert any(p.parent.name == "canonical" or p.name == "sheet.png" for p in can_p) or any(
        p.parent.name == "hands" for p in can_p
    )

    # cap_refs: hand HARD = hands sheet；不强制 body
    srefs = [tmp / f"s{i}.jpg" for i in range(3)]
    for p in srefs:
        Image.new("RGB", (64, 64)).save(p)
    out_can, out_s, out_o = imagegen.cap_refs(
        [hands / "sheet.png", root / "canonical" / "sheet.png", body / "sheet.png"],
        srefs, [], max_refs=5, shot_type="hand_closeup",
    )
    assert out_can[0] == hands / "sheet.png"
    assert (body / "sheet.png") not in out_can[:1]
    assert len(out_s) >= 2

    # prompt 含镜头块、hand 不强制完整正脸话术
    prompt = imagegen.build_prompt(
        "图书馆手边特写", "desc", "穿搭", "", n_canonical=1, n_scene_refs=2,
        shot_type="hand_closeup",
        shot_bits={"activity": "图书馆自习", "place": "图书馆窗边"},
    )
    assert "hand_closeup" in prompt
    assert "手部" in prompt or "手" in prompt
    prompt_p = imagegen.build_prompt(
        "操场第一视角", "desc", "穿搭", "", n_canonical=1, n_scene_refs=3,
        shot_type="pov_first_person",
        shot_bits={"activity": "操场散步", "place": "操场"},
    )
    assert "pov_first_person" in prompt_p


def test_prompt_default_unchanged_third_person():
    prompt = imagegen.build_prompt("食堂吃饭", "desc", "穿搭日常", "", n_canonical=2, n_scene_refs=3, body_kind="sheet")
    assert "hand_closeup" not in prompt
    assert "正视标准肖像五视图" in prompt



def test_mirror_selfie_mode():
    assert sm.normalize_shot("mirror") == sm.SHOT_MIRROR
    assert sm.route_user_shot("对镜自拍一张") == sm.SHOT_MIRROR
    assert sm.route_user_shot("试衣镜看看") == sm.SHOT_MIRROR
    assert sm.SHOT_MIRROR in sm.ALL_SHOTS
    scene = sm.schedule_scene_for_shot(sm.SHOT_MIRROR, {
        "activity": "商场逛街", "scene_hints": ["试衣间"], "bucket": "city_fun",
    })
    assert "镜" in scene or "自拍" in scene
    block = sm.prompt_shot_block(sm.SHOT_MIRROR, scene, {"activity": "逛街", "place": "商场"})
    assert "mirror_selfie" in block
    # 对镜需要脸，不 skip face HARD
    assert not sm.skips_hard_face_body(sm.SHOT_MIRROR)

    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    (body / "body.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    hands = root / "canonical" / "hands"
    hands.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (200, 180, 170)).save(hands / "sheet.png")
    (hands / "hands.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")

    can, oth = refpick.pick_identity("试衣镜", 3, cfg, shot_type="mirror_selfie")
    assert (root / "canonical" / "sheet.png") in can
    assert (body / "sheet.png") not in can  # mirror 不挂 body
    assert oth == []

    Image.new("RGB", (64, 64)).save(tmp / "s0.jpg")
    out_can, out_s, out_o = imagegen.cap_refs(
        [root / "canonical" / "sheet.png", hands / "sheet.png", body / "sheet.png"],
        [tmp / "s0.jpg"], [], max_refs=5, shot_type="mirror_selfie",
    )
    assert out_can[0] == root / "canonical" / "sheet.png"
    assert (body / "sheet.png") not in out_can[:1]


def test_legs_refs_noop_until_approved():
    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    # 无 legs 目录 → 未批准
    assert refpick.legs_approved(cfg) is False
    assert refpick.legs_refs(cfg) == []
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    (body / "body.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    can, _ = refpick.pick_identity("看看腿", 3, cfg, shot_type="legs_focus")
    assert (body / "sheet.png") in can  # fallback body
    # candidates status 仍 no-op
    legs = root / "canonical" / "legs"
    legs.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (180, 160, 150)).save(legs / "sheet.png")
    (legs / "legs.json").write_text(json.dumps({"status": "candidates"}), encoding="utf-8")
    assert refpick.legs_approved(cfg) is False
    assert refpick.legs_refs(cfg) == []
    # approved → HARD legs
    (legs / "legs.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    assert refpick.legs_refs(cfg) == [legs / "sheet.png"]
    can2, _ = refpick.pick_identity("腿部", 3, cfg, shot_type="legs_focus")
    assert can2 == [legs / "sheet.png"]  # 仅腿模 1 槽，不软挂 face




def test_user_routes_exclusive_no_cross_card():
    """要腿/手/POV/自拍/全身必须各自命中对应卡，禁止串卡。"""
    cases = [
        ("看看腿", sm.SHOT_LEGS),
        ("给我看腿照片", sm.SHOT_LEGS),
        ("看看手", sm.SHOT_HAND),
        ("手给我看看", sm.SHOT_HAND),
        ("第一视角", sm.SHOT_POV),
        ("看看你拍的", sm.SHOT_POV),
        ("对镜自拍", sm.SHOT_MIRROR),
        ("来张自拍", sm.SHOT_PORTRAIT),
        ("看看全身", sm.SHOT_FULL_BODY),
        ("全身照", sm.SHOT_FULL_BODY),
    ]
    for text, expect in cases:
        got = sm.route_user_shot(text)
        assert got == expect, (text, got, expect)
    # 串卡回归：腿请求绝不能是手
    assert sm.route_user_shot("发张腿照片") == sm.SHOT_LEGS
    assert sm.route_user_shot("发张腿照片") != sm.SHOT_HAND


def test_legs_focus_prompt_not_face_five_view():
    """P0：legs_focus 不得把 ref#1 标成正视肖像五视图。"""
    assert sm.skips_hard_face_body(sm.SHOT_LEGS)
    prompt = imagegen.build_prompt(
        "宿舍腿部特写", "desc", "穿搭", "", n_canonical=1, n_scene_refs=2,
        shot_type="legs_focus",
        shot_bits={"activity": "宿舍", "place": "宿舍"},
    )
    assert "legs_focus" in prompt
    assert "腿" in prompt
    # 默认 face HARD 话术不得出现（否定句也不要复述该标签）
    assert "正视标准肖像五视图" not in prompt
    assert "默认必附的唯一身份标准" not in prompt
    assert "腿部五视图" in prompt


def test_legs_approved_primary_ref_is_legs_card():
    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    hands = root / "canonical" / "hands"
    hands.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (200, 180, 170)).save(hands / "sheet.png")
    (hands / "hands.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    legs = root / "canonical" / "legs"
    legs.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (180, 160, 150)).save(legs / "sheet.png")
    (legs / "legs.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    can, _ = refpick.pick_identity("看看腿", 3, cfg, shot_type="legs_focus")
    assert can == [legs / "sheet.png"]
    # 要腿时 primary 绝不能是手卡，也不软挂 face
    assert can[0] != hands / "sheet.png"
    assert (root / "canonical" / "sheet.png") not in can
    out_can, _, _ = imagegen.cap_refs(can, [], [], max_refs=5, shot_type="legs_focus")
    assert out_can == [legs / "sheet.png"]


def test_user_lock_multi_keeps_same_card():
    """用户硬锁时 N>1 仍全是同一卡，不串到别的 SOP。"""
    import schedule_shot as ss
    types = ss.sample_shot_types_for_send(
        3, {"bucket": "campus_life", "activity": "散步"},
        beat_id="daily_chat", rng=random.Random(0), locked_shot=sm.SHOT_LEGS,
    )
    assert types == [sm.SHOT_LEGS, sm.SHOT_LEGS, sm.SHOT_LEGS]
    plan = chat_media.plan_chat_images(
        "腿部特写", caption="看", n_images=2, rng=random.Random(1),
        dry=True, shot_type=sm.SHOT_LEGS, shot_lock=True,
    )
    assert plan["shot_types"] == [sm.SHOT_LEGS, sm.SHOT_LEGS]



def test_legs_only_one_legs_ref_k_scene_four():
    """GPT max 5：legs approved → 仅 1 腿模卡，身份槽=1 → k_scene=4。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    face = root / "canonical" / "sheet.png"
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    (body / "body.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    legs = root / "canonical" / "legs"
    legs.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (180, 160, 150)).save(legs / "sheet.png")
    (legs / "legs.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    can, oth = refpick.pick_identity("宿舍腿", 3, cfg, shot_type="legs_focus")
    assert can == [legs / "sheet.png"] and oth == []
    assert face not in can and (body / "sheet.png") not in can
    max_refs = 5
    k_scene = max_refs - len(can)
    assert k_scene == 4
    srefs = [tmp / f"s{i}.jpg" for i in range(4)]
    for pth in srefs:
        Image.new("RGB", (64, 64)).save(pth)
    out_can, out_s, out_o = imagegen.cap_refs(can, srefs, [], max_refs=max_refs, shot_type="legs_focus")
    assert out_can == [legs / "sheet.png"]
    assert len(out_s) == 4
    import schedule_shot as ss
    pol = ss.ref_policy(sm.SHOT_LEGS)
    assert "face/sheet" not in pol.get("soft", [])


def test_hand_only_one_hands_ref():
    """GPT max 5：hand_closeup → 仅 1 手模卡。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    face = root / "canonical" / "sheet.png"
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    (body / "body.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    hands = root / "canonical" / "hands"
    hands.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (200, 180, 170)).save(hands / "sheet.png")
    (hands / "hands.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    can, oth = refpick.pick_identity("图书馆手", 3, cfg, shot_type="hand_closeup")
    assert can == [hands / "sheet.png"] and oth == []
    assert face not in can and (body / "sheet.png") not in can
    assert max(0, 5 - len(can)) == 4


def test_hand_legs_prompt_forbids_third_person():
    """hand_closeup / legs_focus 必须主观第一视角，禁止第三人称旁拍。"""
    h = sm.prompt_shot_block(sm.SHOT_HAND, "图书馆", {"activity": "写字", "place": "图书馆"})
    assert "第一人称" in h and "拍自己的手" in h
    assert "右手或左手举手机，拍当前正在做的事。结合当前活动与场景。" in h
    assert "第三人称" not in h
    prompt_h = imagegen.build_prompt(
        "手边特写", "desc", "穿搭", "", n_canonical=1, n_scene_refs=4,
        shot_type="hand_closeup",
        shot_bits={"activity": "写字", "place": "图书馆"},
    )
    assert "hand_closeup" in prompt_h
    assert "第一人称" in prompt_h or "主观" in prompt_h

    g = sm.prompt_shot_block(sm.SHOT_LEGS, "宿舍", {"activity": "宿舍", "place": "宿舍"})
    assert "第一人称拍自己的腿" in g and "结合当前活动与场景。" in g
    assert "第三人称" not in g
    prompt_g = imagegen.build_prompt(
        "腿部特写", "desc", "穿搭", "", n_canonical=1, n_scene_refs=4,
        shot_type="legs_focus",
        shot_bits={"activity": "宿舍", "place": "宿舍"},
    )
    assert "legs_focus" in prompt_g
    assert "第一人称" in prompt_g or "主观" in prompt_g


def test_full_body_still_two_identity_refs():
    """full_body 仍 face+body 共 2 身份槽。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    face = root / "canonical" / "sheet.png"
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    (body / "body.json").write_text(json.dumps({"status": "approved"}), encoding="utf-8")
    can, oth = refpick.pick_identity("操场全身", 3, cfg, shot_type="full_body")
    assert face in can and (body / "sheet.png") in can
    assert len(can) == 2 and oth == []
    Image.new("RGB", (64, 64)).save(tmp / "s0.jpg")
    out_can, out_s, _ = imagegen.cap_refs(
        can, [tmp / "s0.jpg"], [], max_refs=5, shot_type="full_body",
    )
    assert face in out_can and (body / "sheet.png") in out_can
    assert len(out_can) == 2
    assert not sm.skips_hard_face_body("full_body")
    # portrait = 仅 1 face
    can_p, _ = refpick.pick_identity("自拍", 3, cfg, shot_type="portrait")
    assert can_p == [face]


if __name__ == "__main__":
    test_baobei_equal_split()
    test_baobei_image_hint_schedule()
    test_route_user_shot_phrases()
    test_parse_want_image_shot_prefix()
    test_hand_pov_skip_hard_face_body()
    test_prompt_default_unchanged_third_person()
    test_mirror_selfie_mode()
    test_legs_refs_noop_until_approved()
    test_user_routes_exclusive_no_cross_card()
    test_legs_focus_prompt_not_face_five_view()
    test_legs_approved_primary_ref_is_legs_card()
    test_user_lock_multi_keeps_same_card()
    test_legs_only_one_legs_ref_k_scene_four()
    test_hand_only_one_hands_ref()
    test_hand_legs_prompt_forbids_third_person()
    test_full_body_still_two_identity_refs()
    print("OK")
