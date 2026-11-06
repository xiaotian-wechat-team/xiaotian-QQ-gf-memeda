"""离线 mock：姿态五视图（tools/gen_canonical_extra.py）选参考/评分/拒收/索引，refpick 按场景附姿态 sheet，imagegen 提示词。不调用真实 codex/Vision。"""
import os as _os; _os.environ["XIAOTIAN_TEST"] = "1"; _os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, json, copy, tempfile, types
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
import numpy as np
from PIL import Image
import refpick, imagegen, config
import test_canonical_session as tcs
import gen_canonical_extra as gx, gen_canonical as gc

def _cfg(root, tmp):
    cfg = copy.deepcopy(config.load()); cfg["appearance"]["profile_dir"] = str(root); cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    cfg["imagegen"]["out_dir"] = str(tmp / "out"); cfg["imagegen"]["session_state"] = str(tmp / "s.json"); return cfg

def test_extra_kind_and_pick(tmp_path=None):
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp()); root = tcs.make_profile(tmp); cfg = _cfg(root, tmp)
    # XIAOTIAN_TEST=1 → 走窄关键词；活动词不挂姿态 sheet
    assert refpick.extra_kind("夜里在天台仰头看星星") == "up" and refpick.extra_kind("图书馆低头看书") == "down"
    assert refpick.extra_kind("图书馆看书") is None and refpick.extra_kind("玩手机") is None
    assert refpick.extra_kind("走在街上回头一笑") == "back" and refpick.extra_kind("咖啡店自拍") is None
    assert refpick.extra_kind("背影，抬头看樱花") == "back"   # 多个命中：背影优先
    assert refpick.extra_kind("走在教学楼走廊里") is None
    can, oth = refpick.pick_identity("仰头看烟花", 3, cfg, seed=1); assert can == [root / "canonical" / "sheet.png"] and len(oth) == 3  # 没生成 up 时不附
    for k in ("up", "down", "back"):
        (root / "canonical" / k).mkdir(); Image.new("RGB", (500, 100)).save(root / "canonical" / k / "sheet.png")
    can, oth = refpick.pick_identity("仰头看烟花", 3, cfg, seed=1)
    assert can == [root / "canonical" / "sheet.png", root / "canonical" / "up" / "sheet.png"] and len(oth) == 3
    assert refpick.pick_identity("从背后拍的背影", 3, cfg, seed=1)[0][1].parent.name == "back"
    assert len(refpick.pick_identity("食堂吃饭", 3, cfg, seed=1)[0]) == 1
    assert len(refpick.pick_identity("图书馆看书", 3, cfg, seed=1)[0]) == 1  # 仅正视五视图
    cmd = imagegen.build_cmd("低头写作业", cfg, tmp, refs=refpick.pick_identity("低头写作业", 3, cfg, seed=2))
    ii = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-i"]; assert len(ii) == 5 and ii[0].endswith("canonical/sheet.png") and ii[1].endswith("down/sheet.png")
    # 有姿态 sheet 时才写姿态语；须传 pose_kind
    p = imagegen.build_prompt("低头写作业", "d", n_canonical=2, pose_kind="down", cfg=cfg)
    assert "低头五视图" in p and "第 1 张" in p and "身份标准" in p and "不要左右歪头" in p
    assert "任务核心是场景与活动" in p and "173" in p and "50" in p
    p1 = imagegen.build_prompt("咖啡店自拍", "d", n_canonical=1, pose_kind=None, cfg=cfg)
    assert "姿态五视图" not in p1 and "低头五视图" not in p1 and "仰头五视图" not in p1
    assert "任务核心是场景与活动" in p1 and "正视标准肖像五视图" in p1
    assert "五视图（身份标准）；第 2 张" not in imagegen.build_prompt("x", "d", n_canonical=1)
    # n_canonical>1 但无 pose_kind：不写「头部角度」诱导
    p2 = imagegen.build_prompt("x", "d", n_canonical=2, pose_kind=None); assert "头部角度" not in p2 and "画低头姿态" not in p2
    cfg["appearance"]["extra_sheets"] = False; assert len(refpick.pick_identity("仰头", 3, cfg, seed=1)[0]) == 1
    cfg["appearance"]["extra_sheets"] = True
    job = imagegen.write_pending("背影走在操场", "inner", {"text": "t"}, cfg, {}, "err"); d = json.loads(job.read_text())
    assert d["extra_sheet"].endswith("back/sheet.png") and d["canonical_refs"][1] == d["extra_sheet"] and len(d["reference_images"]) == 5
    assert "背影五视图" in d["prompt"] and "173" in d["prompt"]

def _sheet(path, n=5, h=200):
    im = Image.new("RGB", (n * 100 if n else 300, h), (200, 200, 200))
    for i in range(5): im.paste((20, 20, 20), (i * 100 + 30, 10, i * 100 + 70, 120))
    im.save(path); return path

def test_evaluate_and_reject(tmp_path=None, monkey=None):
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp()); can = tmp / "canonical"; can.mkdir()
    for v in gc.VIEWS: Image.new("RGB", (100, 200), (200, 200, 200)).save(can / f"{v}.png")
    refs = [tmp / "r1.jpg", tmp / "r2.jpg"]; [Image.new("RGB", (10, 10)).save(r) for r in refs]
    face = lambda g, p=0.0: {"geo": g, "pitch": p, "eyeL": 0.3, "eyeR": 0.3}
    plan = {}
    orig = (gc.vision_face, gc.proportions, gc.featureprint, gc.fp_dist)
    gc.vision_face = lambda p: plan.get(pathlib.Path(p).stem)
    gc.proportions = lambda p: np.array([0.4, 0.3, 1.0, 0.6, 0.3]); gc.featureprint = lambda p: p; gc.fp_dist = lambda a, b: 0.6
    try:
        d = tmp / "c1"; d.mkdir(); _sheet(d / "sheet.png"); views = gx.split(d / "sheet.png", d, gx.SETS["up"]["views"])
        plan.update({v: face(g, 0.3) for v, g in zip(gx.SETS["up"]["views"], [-0.9, -0.4, 0, 0.4, 0.9])})
        ev = gx.evaluate("up", views, refs, can); assert ev["reject"] == [] and abs(ev["total"] - 0.18) < 1e-6 and ev["pitch_delta"] == 0.3
        plan.update({"up_l34": None, "up_r34": face(-0.95)})   # 漏脸 + 顺序颠倒
        ev2 = gx.evaluate("up", views, refs, can); assert ev2["reject"] and ev2["total"] > ev["total"] + 0.3
        d2 = tmp / "c2"; d2.mkdir(); Image.new("RGB", (300, 200)).save(d2 / "sheet.png")   # 方图 = 格数不对
        ev3 = gx.evaluate("up", gx.split(d2 / "sheet.png", d2, gx.SETS["up"]["views"]), refs, can); assert "比例不是横排 5 格" in ev3["reject"]
        d3 = tmp / "c3"; d3.mkdir(); _sheet(d3 / "sheet.png"); bv = gx.split(d3 / "sheet.png", d3, gx.SETS["back"]["views"])
        plan.update({"back": None, "back_l34": None, "back_r34": None, "over_lshoulder": face(-0.7), "over_rshoulder": face(0.7)})
        eb = gx.evaluate("back", bv, refs, can); assert eb["reject"] == [] and eb["faces_detected"] == 2 and "hair_dist" in eb
        plan["back"] = face(0.0); eb2 = gx.evaluate("back", bv, refs, can); assert eb2["reject"] and eb2["total"] > eb["total"]
        # 选优：有拒收理由的候选排后；索引写入 extra_sheets
        (can / "back").mkdir(); root = tmp
        m = gx.write_set("back", can, [(1, d3, eb2), (2, d3, eb)], {"3": {"blocked": True}}, refs, root)
        assert m["chosen_candidate"] == 2 and (can / "back" / "sheet.png").exists() and (can / "back" / "over_rshoulder.png").exists()
        (can / "canonical.json").write_text(json.dumps({"sheet": "sheet.png"})); gx.update_index(can, {"back": m})
        idx = json.loads((can / "canonical.json").read_text()); assert idx["sheet"] == "sheet.png" and idx["extra_sheets"]["back"]["sheet"] == "back/sheet.png"
    finally:
        gc.vision_face, gc.proportions, gc.featureprint, gc.fp_dist = orig

def test_run_codex_by_thread(tmp_path=None):
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp()); ch = tmp / "codex"; _os.environ["CODEX_HOME"] = str(ch)
    try:
        def fake(cmd, input=None, **kw):
            assert "--json" in cmd and cmd.count("-i") == 2 and "$imagegen" in input
            for t in ("T9", "OTHER"): (ch / "generated_images" / t).mkdir(parents=True, exist_ok=True); Image.new("RGB", (5, 1)).save(ch / "generated_images" / t / f"{t}.png")
            return types.SimpleNamespace(returncode=0, stdout='{"type":"thread.started","thread_id":"T9"}\n', stderr="")
        orig = gx.subprocess.run; gx.subprocess.run = fake
        try: imgs, r, tid = gx.run_codex("codex", "p $imagegen", ["a", "b"], tmp)
        finally: gx.subprocess.run = orig
        assert tid == "T9" and [p.name for p in imgs] == ["T9.png"]
    finally: _os.environ.pop("CODEX_HOME", None)

def test_pick_refs_sharp(tmp_path=None):
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp()); (tmp / "frontal").mkdir(); items = []
    rng = np.random.default_rng(0)
    for i, sharp in enumerate([True, False, True, True, True]):
        a = rng.integers(0, 255, (200, 160)) if sharp else np.full((200, 160), 128)
        Image.fromarray(a.astype("uint8")).save(tmp / "frontal" / f"{i}.jpg"); items.append({"file": f"frontal/{i}.jpg", "score": 1 - i / 10, "shoot": "s"})
    (tmp / "frontal" / "frontal_index.json").write_text(json.dumps({"items": items}))
    got = [p.name for p, _, _ in gx.pick_refs(tmp, 3)]; assert got == ["0.jpg", "2.jpg", "3.jpg"]


def test_vector_pose_confidence(tmp_path=None):
    """非测试模式下向量门槛：日常活动不附姿态；明确仰头/低头/背影自信命中。嵌入不可用则跳过。"""
    import os
    if os.environ.get("POSE_EMBED", "1") in ("0", "false", "no"): return
    # temporarily allow embed path
    old = os.environ.pop("XIAOTIAN_TEST", None)
    try:
        import outfit
        if not outfit._load_model(): return
        # clear cache so live path runs
        refpick._pose_emb = None
        assert refpick.extra_kind("图书馆看书", use_embed=True) is None
        assert refpick.extra_kind("走在教学楼走廊里", use_embed=True) is None
        assert refpick.extra_kind("仰头看烟花", use_embed=True) == "up"
        assert refpick.extra_kind("低头写作业", use_embed=True) == "down"
        assert refpick.extra_kind("背影走在操场", use_embed=True) == "back"
    finally:
        if old is not None: os.environ["XIAOTIAN_TEST"] = old
        else: os.environ["XIAOTIAN_TEST"] = "1"
        refpick._pose_emb = None

if __name__ == "__main__":
    test_extra_kind_and_pick(); test_evaluate_and_reject(); test_run_codex_by_thread(); test_pick_refs_sharp(); test_vector_pose_confidence(); print("OK")
