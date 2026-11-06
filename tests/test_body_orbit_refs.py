"""离线：脸五视图 + 身体五视图必附；其余 3 槽给 scene。不调 codex。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
os.environ.pop("BODY_ORBIT_REFS", None)
import sys, pathlib, json, copy, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PIL import Image
import refpick, imagegen, config
import test_canonical_session as tcs


def _cfg(root, tmp):
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    cfg["appearance"]["extra_sheets"] = True
    cfg["imagegen"]["out_dir"] = str(tmp / "out")
    cfg["imagegen"]["session_state"] = str(tmp / "s.json")
    return cfg


def _add_body(root, status="approved", front=True, sheet=True):
    body = root / "canonical" / "body"
    body.mkdir(parents=True, exist_ok=True)
    if front:
        Image.new("RGB", (200, 400), (180, 180, 180)).save(body / "front.png")
    if sheet:
        Image.new("RGB", (500, 200), (160, 160, 160)).save(body / "sheet.png")
    (body / "body.json").write_text(json.dumps({
        "status": status, "kind": "sop_promoted", "source": "艺考模卡",
    }, ensure_ascii=False), encoding="utf-8")
    return body


def test_body_orbit_pick_and_cap():
    tmp = pathlib.Path(tempfile.mkdtemp()); root = tcs.make_profile(tmp); cfg = _cfg(root, tmp)

    assert refpick.body_orbit_refs(cfg) == []
    can, oth = refpick.pick_identity("食堂吃饭", 3, cfg, seed=1)
    assert can == [root / "canonical" / "sheet.png"] and len(oth) == 3

    body = _add_body(root, status="orbit_generated_pending_user_review")
    assert refpick.body_orbit_refs(cfg) == []

    (body / "body.json").write_text(json.dumps({"status": "approved", "kind": "sop_promoted"}), encoding="utf-8")
    # MUST be body/sheet.png — never prefer front
    assert refpick.body_orbit_refs(cfg) == [body / "sheet.png"]
    assert (body / "front.png").exists()  # front may exist but must not be picked

    can, oth = refpick.pick_identity("食堂吃饭", 3, cfg, seed=1)
    assert can == [root / "canonical" / "sheet.png", body / "sheet.png"]
    assert oth == []  # remaining slots reserved for scene_refs

    # with body: pose sheet must NOT be attached (scene needs the 3 slots)
    for k in ("up", "down", "back"):
        (root / "canonical" / k).mkdir(exist_ok=True)
        Image.new("RGB", (500, 100)).save(root / "canonical" / k / "sheet.png")
    can, oth = refpick.pick_identity("仰头看烟花", 3, cfg, seed=1)
    assert can == [root / "canonical" / "sheet.png", body / "sheet.png"]
    assert oth == []

    # cap_refs: face+body HARD; remaining 3 → scene; drop others
    sheet, bsheet = can
    s1, s2, s3 = [pathlib.Path(f"/tmp/s{i}.jpg") for i in range(1, 4)]
    o1, o2, o3 = [pathlib.Path(f"/tmp/o{i}.jpg") for i in range(1, 4)]
    pose = root / "canonical" / "up" / "sheet.png"
    c_out, s_out, o_out = imagegen.cap_refs([sheet, bsheet, pose], [s1, s2, s3], [o1, o2, o3], max_refs=5)
    assert c_out[:2] == [sheet, bsheet]
    assert s_out == [s1, s2, s3]
    assert o_out == []
    assert pose not in c_out  # scene wins remaining
    assert len(c_out) + len(s_out) + len(o_out) == 5

    # only front, no sheet → cannot attach body orbit
    (body / "sheet.png").unlink()
    assert refpick.body_orbit_refs(cfg) == []

    Image.new("RGB", (500, 200)).save(body / "sheet.png")
    os.environ["BODY_ORBIT_REFS"] = "0"
    try:
        assert refpick.body_orbit_refs(cfg) == []
    finally:
        os.environ.pop("BODY_ORBIT_REFS", None)

    refs = refpick.pick_identity("图书馆看书", 3, cfg, seed=2)
    s_fake = [tmp / f"sc{i}.jpg" for i in range(3)]
    for p in s_fake: p.write_bytes(b"x")
    cmd = imagegen.build_cmd("图书馆看书", cfg, tmp, refs=refs, scene_refs=s_fake)
    ii = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-i"]
    assert len(ii) == 5
    assert ii[0].endswith("canonical/sheet.png")
    assert ii[1].replace("\\", "/").endswith("canonical/body/sheet.png")
    assert all(str(p) in ii for p in s_fake)

    p = imagegen.build_prompt("图书馆", "d", n_canonical=2, body_kind="sheet", n_scene_refs=3, cfg=cfg)
    assert "全身环视五视图（体型）" in p and "canonical/body/sheet" in p
    assert "第 2 张" in p and imagegen._body_kind_from_refs(refs[0]) == "sheet"

    job = imagegen.write_pending("食堂", "inner", {"text": "t"}, cfg, {}, "err")
    d = json.loads(job.read_text(encoding="utf-8"))
    assert d.get("body_ref") and d["body_ref"].endswith("body/sheet.png")
    assert d["canonical_refs"][0].endswith("sheet.png")
    assert d["canonical_refs"][1].endswith("body/sheet.png")
    assert d["other_refs"] == []


def test_live_profile_body_sheet_hard():
    cfg = config.load()
    root = refpick.profile_root(cfg)
    body_json = root / "canonical" / "body" / "body.json"
    if not body_json.exists():
        print("skip live: no body.json"); return
    st = json.loads(body_json.read_text(encoding="utf-8")).get("status")
    if st != "approved":
        print(f"skip live: status={st!r}"); return
    can, oth = imagegen.pick_refs("在图书馆看书", cfg)
    assert can[0].name == "sheet.png" and can[0].parent.name == "canonical"
    assert can[1].name == "sheet.png" and can[1].parent.name == "body"
    assert oth == []
    s_fake = [pathlib.Path(f"/tmp/live_sc{i}.jpg") for i in range(3)]
    c2, s2, o2 = imagegen.cap_refs(can, s_fake, [pathlib.Path("/tmp/o.jpg")], 5)
    assert c2 == can[:2] and s2 == s_fake and o2 == []


if __name__ == "__main__":
    test_body_orbit_pick_and_cap()
    test_live_profile_body_sheet_hard()
    print("OK")

def test_build_prompt_outfit_not_model_card():
    p = imagegen.build_prompt(
        "798看展", "黑长直", outfit_text="穿搭：针织衫长裙",
        n_canonical=2, n_scene_refs=3, body_kind="sheet",
    )
    assert "identity" in p and "proportion" in p
    assert "not grey tee" in p and "model-card" in p
    assert "灰上衣" in p or "模卡" in p
    assert "Outfit MUST change" in p or "必须按" in p
    assert "不是衣服" in p or "not a clothing" in p or "绝不是衣服" in p

