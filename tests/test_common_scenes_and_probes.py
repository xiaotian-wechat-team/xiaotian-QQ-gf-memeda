"""离线：常用场景映射、同日探针绑定、全身加权分布。不调微信/QQ、不强制外网。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import json, pathlib, random, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import common_scenes as cs
import scene_refs as sr
import schedule_shot as ss
import shot_modes as sm
import photo_scene


def test_resolve_pack_from_schedule_keywords():
    assert cs.resolve_pack("宿舍刷小红书") == "dorm"
    assert cs.resolve_pack_from_slot({"activity": "食堂午饭", "scene_hints": ["食堂"]}) == "canteen"
    assert cs.resolve_pack("期末复习·图书馆") == "library"
    assert cs.resolve_pack("操场散步") == "stadium"
    assert cs.resolve_pack("编导技法课 教室") == "classroom"
    assert cs.resolve_pack("现代舞基训 练功房") == "studio"
    assert cs.resolve_pack("798逛展") is None


def test_scene_key_prefers_common():
    assert cs.scene_key_for_slot({"activity": "图书馆自习", "scene_hints": ["图书馆"]}) == "common:library"
    assert cs.scene_key_for_slot({"activity": "神秘活动", "scene_hints": ["木屋"]}).startswith("hint:")


def test_ensure_day_probes_persists(tmp_path, monkeypatch):
    # isolate probes file
    probes_path = tmp_path / "scene_ref_day_probes.json"
    monkeypatch.setattr(sr, "DAY_PROBES_PATH", probes_path)
    # empty common dir
    common_root = tmp_path / "common"
    monkeypatch.setattr(cs, "DEFAULT_COMMON_DIR", common_root)
    day = {
        "date": "2026-10-26",
        "slots": [
            {"start": "08:00", "end": "12:00", "activity": "食堂午饭",
             "scene_hints": ["食堂"], "photo_ok": True, "bucket": "campus_life"},
            {"start": "12:00", "end": "18:00", "activity": "图书馆自习",
             "scene_hints": ["图书馆"], "photo_ok": True, "bucket": "school_library"},
            {"start": "18:00", "end": "19:00", "activity": "食堂午饭再来",
             "scene_hints": ["食堂"], "photo_ok": True, "bucket": "campus_life"},
        ],
    }
    # stub pick to avoid needing real sqlite hits
    def fake_pick(scene, k=2, cfg=None, people_free_only=True, min_quality=0.0):
        p1 = tmp_path / f"{scene}-a.jpg"
        p2 = tmp_path / f"{scene}-b.jpg"
        p1.write_bytes(b"x"); p2.write_bytes(b"y")
        return [(p1, {"source": "stub"}), (p2, {"source": "stub"})]
    monkeypatch.setattr(sr, "pick", fake_pick)
    probes1 = sr.ensure_day_probes(day, k=2)
    assert "common:canteen" in probes1
    assert "common:library" in probes1
    paths_canteen = list(probes1["common:canteen"]["paths"])
    # second call identical bind
    probes2 = sr.ensure_day_probes(day, k=2)
    assert probes2["common:canteen"]["paths"] == paths_canteen
    doc = json.loads(probes_path.read_text())
    assert "2026-10-26" in doc["days"]


def test_full_body_weight_higher_equal_parts():
    w = ss._weights_for_bucket("campus_life")
    assert w[sm.SHOT_FULL_BODY] >= 0.34
    parts = [sm.SHOT_HAND, sm.SHOT_POV, sm.SHOT_MIRROR, sm.SHOT_LEGS, sm.SHOT_PORTRAIT]
    vals = [w[p] for p in parts]
    # roughly equal: max/min < 1.8 after light bias
    assert max(vals) / max(min(vals), 1e-6) < 1.8
    # monte carlo
    slot = {"activity": "校园散步", "bucket": "campus_life", "scene_hints": ["林荫道"], "photo_ok": True}
    counts = {}
    for i in range(500):
        s = ss.pick_shot_for_slot(slot, beat_id="daily_chat", rng=random.Random(i))
        counts[s] = counts.get(s, 0) + 1
    assert counts.get(sm.SHOT_FULL_BODY, 0) >= 140  # ~40% of 500
    # each other part gets some mass
    for p in parts:
        assert counts.get(p, 0) >= 20, counts


def test_user_prompt_wake_hardened():
    assert sm.route_user_shot("全身呢") == sm.SHOT_FULL_BODY
    assert sm.route_user_shot("发张全身") == sm.SHOT_FULL_BODY
    assert sm.route_user_shot("对镜") == sm.SHOT_MIRROR
    assert sm.route_user_shot("镜子里看看") == sm.SHOT_MIRROR
    assert sm.route_user_shot("自拍一张") == sm.SHOT_PORTRAIT
    assert sm.route_user_shot("手呢") == sm.SHOT_HAND
    assert sm.route_user_shot("腿呢") == sm.SHOT_LEGS
    assert sm.route_user_shot("你拍的那张") == sm.SHOT_POV
    assert photo_scene.user_asks_photo("全身呢")
    assert photo_scene.user_asks_photo("对镜")
    # explicit override still wins over schedule default
    assert sm.route_user_shot("看看手") == sm.SHOT_HAND


def test_baobei_still_equal_hand_pov():
    shots = {ss.pick_shot_for_slot({"bucket": "campus_life"}, beat_id="schedule_brief",
                                   rng=random.Random(i)) for i in range(30)}
    assert shots <= {sm.SHOT_HAND, sm.SHOT_POV}


def test_campus_is_bda_not_bit():
    assert "舞蹈" in cs.CAMPUS_NAME
    assert "理工" not in cs.CAMPUS_NAME


def test_packs_queries_force_campus():
    for pid, meta in cs.PACKS.items():
        assert meta["queries"], pid
        blob = " ".join(meta["queries"])
        assert (
            ("北京舞蹈学院" in blob)
            or ("Beijing Dance Academy" in blob)
            or ("北舞" in blob)
            or ("BDA" in blob)
        )


def test_cross_pack_dedupe_keeps_best_pack(tmp_path, monkeypatch):
    """Same sha256 in two packs → keep in better cosine pack, reject the other."""
    import hashlib
    import numpy as np
    from PIL import Image

    common_root = tmp_path / "common"
    monkeypatch.setattr(cs, "DEFAULT_COMMON_DIR", common_root)

    img_path_tmp = tmp_path / "seed.jpg"
    Image.new("RGB", (400, 400), (200, 100, 50)).save(img_path_tmp, "JPEG")
    raw = img_path_tmp.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    fname = f"{sha[:16]}.jpg"

    for pid in ("dorm", "library"):
        img_dir = common_root / pid / "images"
        rej = common_root / pid / "rejected"
        img_dir.mkdir(parents=True)
        rej.mkdir(parents=True)
        dest = img_dir / fname
        dest.write_bytes(raw)
        uniq = img_dir / f"uniq_{pid}.jpg"
        Image.new("RGB", (420, 420), (10, 20, 30) if pid == "dorm" else (30, 40, 50)).save(uniq, "JPEG")
        uniq_sha = hashlib.sha256(uniq.read_bytes()).hexdigest()
        lines = [
            {
                "file": fname,
                "query": f"北京舞蹈学院 {cs.PACKS[pid]['zh']} 空景",
                "family": "bing",
                "sha256": sha,
                "phash": "a" * 16,
            },
            {
                "file": uniq.name,
                "query": f"北京舞蹈学院 {cs.PACKS[pid]['zh']} 实景",
                "family": "bing",
                "sha256": uniq_sha,
                "phash": "b" * 16,
            },
        ]
        (common_root / pid / "candidates.jsonl").write_text(
            "\n".join(json.dumps(o, ensure_ascii=False) for o in lines) + "\n",
            encoding="utf-8",
        )
        (common_root / pid / "pack.json").write_text(
            json.dumps(
                {
                    "pack_id": pid,
                    "zh": cs.PACKS[pid]["zh"],
                    "campus": cs.CAMPUS_NAME,
                    "source_family": "bing",
                    "queries": list(cs.PACKS[pid]["queries"]),
                    "kept_files": [fname, uniq.name],
                    "n_kept": 2,
                    "n_rejected": 0,
                    "rejected": [],
                    "centroid": None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    import build_common_scenes as bcs

    class FakeModel:
        pass

    def fake_embed(model, texts):
        out = []
        for t in texts:
            if "宿舍" in t or "dorm" in t.lower():
                out.append([1.0, 0.0, 0.0])
            elif "图书" in t or "library" in t.lower():
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.5, 0.5, 0.0])
        arr = np.asarray(out, dtype="float32")
        arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)
        return arr

    monkeypatch.setattr(bcs, "embed_texts", fake_embed)
    monkeypatch.setattr(bcs.cs, "DEFAULT_COMMON_DIR", common_root)

    result = bcs.cross_pack_dedupe(FakeModel(), packs=["dorm", "library"])
    assert result["conflicts"] == 1
    dorm_meta = json.loads((common_root / "dorm" / "pack.json").read_text())
    lib_meta = json.loads((common_root / "library" / "pack.json").read_text())
    in_dorm = fname in dorm_meta["kept_files"]
    in_lib = fname in lib_meta["kept_files"]
    assert in_dorm != in_lib
    if in_dorm:
        assert not (common_root / "library" / "images" / fname).exists()
        assert (common_root / "library" / "rejected" / fname).exists()
    else:
        assert not (common_root / "dorm" / "images" / fname).exists()
        assert (common_root / "dorm" / "rejected" / fname).exists()


def test_is_outfit_scene_id():
    assert sr.is_outfit_scene_id("campus-007")
    assert sr.is_outfit_scene_id("sport-005")
    assert not sr.is_outfit_scene_id("宿舍刷小红书")
    assert not sr.is_outfit_scene_id("食堂手边特写·握杯")
    assert not sr.is_outfit_scene_id(None)


def test_pick_prefers_slot_common_pack_over_outfit_id(tmp_path, monkeypatch):
    """Schedule slot maps to dorm; outfit id must NOT steal pack resolve."""
    common_root = tmp_path / "common"
    dorm_img = common_root / "dorm" / "images" / "d1.jpg"
    dorm_img.parent.mkdir(parents=True)
    dorm_img.write_bytes(b"dorm")
    monkeypatch.setattr(cs, "DEFAULT_COMMON_DIR", common_root)

    called = {"pick": []}

    def fake_pick(scene, k=2, cfg=None, people_free_only=True, min_quality=0.0):
        called["pick"].append(scene)
        g = tmp_path / f"generic-{scene}.jpg"
        g.write_bytes(b"g")
        return [(g, {"source": "generic"})]

    monkeypatch.setattr(sr, "pick", fake_pick)
    monkeypatch.setattr(sr, "get_day_probe", lambda *a, **k: [])

    slot = {"activity": "宿舍刷小红书", "scene_hints": ["宿舍"], "bucket": "campus_life"}
    hit = sr.pick_with_common_and_probes(
        "宿舍对镜自拍", k=1, cfg={},
        slot=slot, pick_key="campus-007", pack_scene="宿舍对镜自拍",
    )
    assert hit and hit[0][1].get("pack_id") == "dorm"
    assert hit[0][1].get("source") == "common:dorm"
    assert called["pick"] == []  # common pack satisfied; no generic


def test_pick_falls_back_generic_when_slot_unmapped(tmp_path, monkeypatch):
    """Unmapped slot (798) → generic scene_refs, not empty."""
    monkeypatch.setattr(cs, "DEFAULT_COMMON_DIR", tmp_path / "empty-common")
    monkeypatch.setattr(sr, "get_day_probe", lambda *a, **k: [])

    def fake_pick(scene, k=2, cfg=None, people_free_only=True, min_quality=0.0):
        g = tmp_path / "generic-798.jpg"
        g.write_bytes(b"g")
        return [(g, {"source": "generic", "q": scene})]

    monkeypatch.setattr(sr, "pick", fake_pick)
    slot = {"activity": "798逛展", "scene_hints": ["798"], "bucket": "outing"}
    assert cs.resolve_pack_from_slot(slot) is None
    hit = sr.pick_with_common_and_probes(
        "798逛展随手拍", k=1, cfg={},
        slot=slot, pick_key="fun-001", pack_scene="798逛展随手拍",
    )
    assert len(hit) == 1
    assert hit[0][1].get("source") == "generic"
    assert hit[0][1].get("q") == "fun-001"


def test_plan_chat_images_forwards_slot_to_generate(monkeypatch):
    import chat_media
    seen = {}

    def fake_gen(scene, **kw):
        seen.update(kw)
        seen["scene"] = scene
        p = pathlib.Path(tempfile.mkdtemp()) / "x.jpg"
        p.write_bytes(b"x")
        return ["c"], [p]

    slot = {"activity": "图书馆自习", "scene_hints": ["图书馆"]}
    plan = chat_media.plan_chat_images(
        "图书馆自习随手拍", caption="看", n_images=1, rng=random.Random(0),
        generate_fn=fake_gen, slot=slot, today={"date": "2026-10-26"},
        shot_type="full_body", shot_lock=True,
    )
    assert seen.get("slot") == slot
    assert seen.get("date") == "2026-10-26"
    assert plan.get("slot") == slot
