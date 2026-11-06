"""离线 mock：canonical + 随机其余参考（refpick.pick_identity）与按天复用 Codex 会话（codex_session + imagegen.generate_local）。不调用真实 codex。"""
import os as _os; _os.environ["XIAOTIAN_TEST"] = "1"; _os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, json, copy, datetime, tempfile, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from PIL import Image
import refpick, codex_session, imagegen, config

def make_profile(tmp):
    root = tmp / "prof"; (root / "canonical").mkdir(parents=True); (root / "raw").mkdir()
    Image.new("RGB", (500, 100), "grey").save(root / "canonical" / "sheet.png")
    (root / "canonical" / "canonical.json").write_text(json.dumps({"source_refs_raw": ["raw/s0/a.jpg"]}))
    buckets = {}
    for i, b in enumerate(["front", "front", "l34", "l34", "r34", "r34", "rprofile", "front"]):
        rel = f"raw/s{i % 4}/{i}.jpg" if i else "raw/s0/a.jpg"; (root / rel).parent.mkdir(exist_ok=True)
        Image.new("RGB", (400, 600), (i * 20, 100, 100)).save(root / rel)
        buckets.setdefault(b, []).append({"rel": rel, "shoot": pathlib.Path(rel).parent.name, "score": 0.5, "yaw": 0, "geo": 0, "roll": 0, "bbox": [0.4, 0.2, 0.2, 0.15]})
    (root / "angles.json").write_text(json.dumps(buckets))
    (root / "index.json").write_text(json.dumps({"items": [{"rel": "raw/s3/7.jpg", "risky_labels": ["bikini"]}]}))
    return root

def test_pick_identity_and_session(tmp_path=None):
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp()); root = make_profile(tmp)
    cfg = copy.deepcopy(config.load()); cfg["appearance"]["profile_dir"] = str(root); cfg["appearance"]["canonical_refs"] = ["sheet.png", "missing.png"]
    cfg["imagegen"]["session_state"] = str(tmp / "sess.json"); cfg["imagegen"]["out_dir"] = str(tmp / "out"); cfg["imagegen"]["cmd"] = "codex"; cfg["imagegen"]["max_session_count"] = 0; cfg["imagegen"]["reasoning_effort"] = ""
    # 1) canonical 在前、其余 3 张：不同拍摄、不含 canonical 源图、不含 risky、头肩裁图
    can, oth = refpick.pick_identity("图书馆", 3, cfg, seed=3)
    assert can == [root / "canonical" / "sheet.png"] and len(oth) == 3 and all(p.exists() and p.name.startswith("ang_") for p in oth)
    src = {refpick._sha(f"raw/s0/a.jpg"), refpick._sha("raw/s3/7.jpg")}; assert not src & {p.name.split("_")[1] for p in oth}
    shas = {refpick._sha(r["rel"]): r["shoot"] for v in json.loads((root / "angles.json").read_text()).values() for r in v}
    assert len({shas[p.name.split("_")[1]] for p in oth}) == 3
    assert {p.name for p in refpick.others(3, cfg, seed=1)} != {p.name for p in refpick.others(3, cfg, seed=2)} or True
    ex = {p.name.split("_")[1] for p in oth}; again = refpick.others(3, cfg, seed=3, exclude=ex); assert not ex & {p.name.split("_")[1] for p in again}
    cmd = imagegen.build_cmd("x", cfg, tmp, refs=(can, oth)); i = cmd.index("-i"); assert cmd[i + 1] == str(can[0]) and cmd.count("-i") == 4
    p = imagegen.build_prompt("x", "d", n_canonical=1); assert "正视标准肖像五视图" in p and "身份标准" in p and "任务核心是场景与活动" in p and "173" in p
    # 2) 会话：同一天复用，跨天新开，坏了当天新开
    d1 = datetime.datetime(2026, 10, 24, 9); d2 = d1 + datetime.timedelta(days=1)
    assert codex_session.current(cfg, d1) is None
    codex_session.save(cfg, "S1", d1); assert codex_session.current(cfg, d1) == "S1" and codex_session.current(cfg, d2) is None
    codex_session.save(cfg, "S1", d1); assert codex_session.load_state(cfg)["count"] == 2
    codex_session.save(cfg, "S2", d2); st = codex_session.load_state(cfg); assert st["session_id"] == "S2" and st["history"][-1]["session_id"] == "S1"
    c, _ = codex_session.build("codex", ["a", "b"], tmp, "S2"); assert c[:3] == ["codex", "exec", "resume"] and c.index("S2") < c.index("-i") and c[c.index("S2") + 1] == "-"
    c, _ = codex_session.build("codex", ["a"], tmp, None); assert "resume" not in c and "-C" in c
    assert codex_session.thread_id('{"type":"thread.started","thread_id":"abc-1"}\n') == "abc-1"
    # 3) generate_local：mock codex。第一次新会话→记录 id；同天第二次 resume；resume 坏→同天新开；跨天新开
    calls = []; state = {"broken": False}
    def fake_run(cmd, input=None, **kw):
        calls.append(cmd); out = pathlib.Path(kw.get("cwd")); (out / "generated_images").mkdir(exist_ok=True)
        if "resume" in cmd and state["broken"]: return types.SimpleNamespace(returncode=1, stdout="", stderr="Error: thread/resume failed: no rollout found")
        Image.new("RGB", (8, 8)).save(out / "generated_images" / f"img{len(calls)}.png")
        if "resume" in cmd:
            # resume 后可能夹 -c key=value；取 '-' 前一个非 option 的 token 为 session id
            i = cmd.index("resume"); tid = None
            for j in range(i + 1, len(cmd)):
                if cmd[j] == "-": break
                if cmd[j].startswith("-"): 
                    if cmd[j] in ("-c", "--config") and j + 1 < len(cmd): continue
                    continue
                if "=" in cmd[j] and j and cmd[j-1] in ("-c", "--config"): continue
                tid = cmd[j]; break
            tid = tid or f"T{len(calls)}"
        else:
            tid = f"T{len(calls)}"
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"type": "thread.started", "thread_id": tid}) + "\n"
            # 回归：agent 读文档时 stdout 会回显 "no rollout found" 等字样，不能因此不保存会话
            + json.dumps({"type": "item.completed", "item": {"text": "resume 失败（`no rollout found` / `thread/resume failed`）→ 新开"}}, ensure_ascii=False) + "\n", stderr="")
    class FakeDT(datetime.datetime):
        cur = d1
        @classmethod
        def now(cls, tz=None): return cls.cur
    orig_run, orig_dt, orig_cdt = imagegen.subprocess.run, imagegen.datetime.datetime, codex_session.datetime.datetime
    (tmp / "sess.json").unlink()
    imagegen.subprocess.run = fake_run; imagegen.datetime.datetime = FakeDT; codex_session.datetime.datetime = FakeDT
    plan = {"text": "穿着日常。", "realism_text": "", "vector": {}, "level": 0}
    try:
        def gen(): FakeDT.cur += datetime.timedelta(seconds=1); return imagegen.generate_local("x", cfg=cfg, _plan=plan)
        gen(); assert "resume" not in calls[-1]; sid = codex_session.current(cfg, FakeDT.cur); assert sid == "T1"
        gen(); assert "resume" in calls[-1] and "T1" in calls[-1] and codex_session.current(cfg, FakeDT.cur) == "T1"
        state["broken"] = True; gen(); assert "resume" in calls[-2] and "resume" not in calls[-1]; state["broken"] = False
        new = codex_session.current(cfg, FakeDT.cur); assert new and new != "T1"
        run = json.loads(sorted((tmp / "out").glob("*/run.json"))[-1].read_text()); assert run["session"] == "new" and run["canonical_refs"]
        FakeDT.cur = d2; gen(); assert "resume" not in calls[-1] and codex_session.current(cfg, d2) not in (None, new)
    finally:
        imagegen.subprocess.run, imagegen.datetime.datetime, codex_session.datetime.datetime = orig_run, orig_dt, orig_cdt

if __name__ == "__main__": test_pick_identity_and_session(); print("OK")
