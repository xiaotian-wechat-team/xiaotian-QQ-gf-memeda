"""离线：只构造命令、挑参考图，不调用 codex / ssh。"""
import os as _os; _os.environ["XIAOTIAN_TEST"] = "1"; _os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")  # 测试绝不读真实 relay.env / 联网
import sys, pathlib, copy, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import imagegen, config, refpick
cfg = config.load(); assert cfg["appearance"]["profile"] == "zhuzhuxiaoye"
desc, refs = imagegen.appearance(cfg); assert "黑色" in desc and 8 <= len(refs) <= 12 and all(r.exists() for r in refs)
# 参考库：raw/ 全部入索引
root = refpick.profile_root(cfg); idx = json.loads((root / "index.json").read_text())
assert idx["count"] >= 451 and idx["usable"] >= 150
assert len(refpick.candidates(idx)) == idx["usable"]   # 不再按穿着过滤，全部人脸清晰的都可选
assert refpick.scene_tags("在图书馆看书") == {"campus"} and "cafe" in refpick.scene_tags("喝奶茶")
picks = refpick.pick("在图书馆看书", 3, cfg, seed=1)
assert len(picks) == 3 and all(p.exists() and p.name.endswith("_face.jpg") for p in picks)
sh1 = {p.name.split("_")[0] for p in picks}
again = refpick.pick("在图书馆看书", 3, cfg, seed=1, exclude=sh1); assert not sh1 & {p.name.split("_")[0] for p in again}
groups = {i["group"] for i in idx["items"] if any(p.name.startswith(i["sha1"]) for p in picks)}; assert len(groups) == 3
it = next(i for i in refpick.candidates(idx)); x, y, w, h = refpick.crop_box(it); assert 0 <= x and 0 <= y and x + w <= it["w"] and y + h <= it["h"]
# 默认 local：本机 codex，提示词走 stdin，命令末尾是 -i <ref>
assert cfg["imagegen"]["backend"] == "local"
can, oth = imagegen.pick_refs("在咖啡店窗边自拍", cfg)  # face sheet + body/sheet HARD；有 body 时 others=[]
cmd, imgs = imagegen.generate("在咖啡店窗边自拍", dry=True)
assert imgs == [] and cmd[:2] == ["codex", "exec"]
assert can and can[0].name == "sheet.png" and can[0].parent.name == "canonical"
if len(can) >= 2 and can[1].parent.name == "body":
    assert can[1].name == "sheet.png" and oth == []
    # XIAOTIAN_TEST=1 → 无 scene_refs；dry -i = 两张必附五视图
    assert cmd.count("-i") == len(can) == 2
else:
    assert cmd.count("-i") == len(can) + len(oth) and len(oth) == cfg["imagegen"].get("other_refs", 3) and "/cache/" in cmd[-1]
p = imagegen.build_prompt("x", desc); assert "$imagegen" in p and "同一个女生" in p and "不要生成另一个长相" in p and "保守" in p
# 夜槽：build_prompt 强制室内夜灯/窗外黑夜（不只靠 caption）
assert imagegen._is_night_hour(hour=21) and imagegen._is_night_hour(hour=3) and not imagegen._is_night_hour(hour=15)
p_n = imagegen.build_prompt("宿舍闲聊", desc, night=True)
assert "室内夜灯" in p_n and "窗外是黑夜" in p_n and "禁止白天日光" in p_n
p_d = imagegen.build_prompt("宿舍闲聊", desc, night=False)
assert "室内夜灯" not in p_d
# 审核拦截 -> 换一组参考图重试一次（mock codex，不真调用）
# 关掉 body 必附，才能测 other 头肩轮换（有 body 时 others=[]）
import subprocess as sp, tempfile, os as _os
_os.environ["BODY_ORBIT_REFS"] = "0"
calls, prompts = [], []
class R:
    def __init__(s, out): s.stdout, s.stderr, s.returncode = out, "", 1
def fake_run(cmd, **kw):
    if cmd[0] != "codex": return orig(cmd, **kw)
    calls.append({pathlib.Path(c).stem.removeprefix("ang_").split("_")[0] for c in cmd[cmd.index("-i") + 1::2] if "/cache/" in c});  # 只看随机照片（canonical sheet 每次都带）
    prompts.append(kw.get("input", "")); return R("Your request was rejected by the safety system (moderation)")
c3 = copy.deepcopy(cfg); c3["imagegen"]["out_dir"] = tempfile.mkdtemp()
orig = imagegen.subprocess.run; imagegen.subprocess.run = fake_run
try:
    imagegen.generate("在咖啡店", cfg=c3, outfit_scene="约会"); assert False
except RuntimeError: pass
finally: imagegen.subprocess.run = orig
# 约会=3 级：拦截 -> 换参考图(3级) -> 降到 2 -> 1 -> 0，共 5 次，之后报错
assert len(calls) == 5 and len(calls[0]) == 3 and not calls[0] & calls[1], calls
import outfit
assert outfit.EXPOSURE_TEXT[3] in prompts[0] and outfit.EXPOSURE_TEXT[3] in prompts[1]
assert [any(outfit.EXPOSURE_TEXT[l] in pr for l in [lv]) for pr, lv in zip(prompts[2:], (2, 1, 0))] == [True] * 3
assert all("穿着日常、得体、保守" not in pr for pr in prompts)
# 正式场合 0 级：换一次参考图后不能再降，只调 2 次
calls.clear(); prompts.clear(); imagegen.subprocess.run = fake_run
try:
    imagegen.generate("x", cfg=c3, outfit_scene="正式"); assert False
except RuntimeError: pass
finally: imagegen.subprocess.run = orig
assert len(calls) == 2 and "规矩得体" in prompts[0]
_os.environ.pop("BODY_ORBIT_REFS", None)
assert imagegen.blocked("content policy violation") and not imagegen.blocked("timeout")
# 备用 macbook 后端
c2 = copy.deepcopy(cfg); c2["imagegen"]["backend"] = "macbook"; c2["imagegen"]["ssh_host"] = ""
cmd, _ = imagegen.generate("在咖啡店窗边自拍", dry=True, cfg=c2); assert "imagegen_macbook.sh" in cmd[-1] and "咖啡店" in cmd[-1]
cmd, _ = imagegen.generate("在三里屯逛街", dry=True, cfg=c2, outfit_scene="见老师"); assert "OUTFIT_TEXT=" in cmd[-1] and "规矩得体" in cmd[-1]
c2["imagegen"]["ssh_host"] = "you@macbook"
cmd, _ = imagegen.generate("x y", dry=True, cfg=c2); assert cmd[0] == "ssh" and cmd[3] == "you@macbook" and "'x y'" in cmd[-1]
sh = (pathlib.Path(__file__).resolve().parents[1] / "tools/imagegen_macbook.sh").read_text()
assert "device-auth" not in sh.replace("不发设备码", "") and "printf '%s' \"$PROMPT\" |" in sh and "${SCENE}" in sh
sk = (pathlib.Path(__file__).resolve().parents[1] / "tools/codex-skill/xiaotian-imagegen/SKILL.md").read_text()
assert sk.startswith("---\nname: xiaotian-imagegen\n") and "raw/" in sk and "refpick.py" in sk and "outfit.py" in sk and "--outfit-scene" in sk
print("OK")
# 后端选择：默认 local；IMAGEGEN_BACKEND=macbook 强制
import os
os.environ.pop("IMAGEGEN_BACKEND", None)
assert imagegen.resolve_backend(cfg) == ("local", False)
os.environ["IMAGEGEN_BACKEND"] = "macbook"
try:
    assert imagegen.resolve_backend(cfg) == ("macbook", True)
    c4 = copy.deepcopy(cfg); c4["imagegen"]["ssh_host"] = ""
    cmd, _ = imagegen.generate("在咖啡店", dry=True, cfg=c4); assert "imagegen_macbook.sh" in cmd[-1]
finally: os.environ.pop("IMAGEGEN_BACKEND")
# 本机非审核失败（如 Magpie 挂了）：重试 1 次后切 MacBook，pending 任务里记 backend=macbook + fallback_reason
c5 = copy.deepcopy(cfg); c5["imagegen"]["out_dir"] = tempfile.mkdtemp(); c5["imagegen"]["ssh_host"] = ""
lc = []
def down_run(cmd, **kw):
    if cmd[0] != "codex": return orig(cmd, **kw)
    lc.append(1); return type("R", (), {"stdout": "", "stderr": "error: connection refused 127.0.0.1:3425", "returncode": 1})()
imagegen.subprocess.run = down_run
try:
    imagegen.generate("在咖啡店", cfg=c5); assert False
except RuntimeError as e: assert "ssh_host 为空" in str(e)
finally: imagegen.subprocess.run = orig
assert len(lc) == 2
job = json.loads(next((pathlib.Path(c5["imagegen"]["out_dir"]) / "pending").glob("*.json")).read_text())
assert job["backend"] == "macbook" and not job["forced"] and "3425" in job["fallback_reason"] and len(job["local_errors"]) == 2
runs = [json.loads(p.read_text()) for p in pathlib.Path(c5["imagegen"]["out_dir"]).glob("*/run.json")]
assert runs and all(r["backend"] == "local" and r["status"] == "failed" for r in runs)
# 超时也算非审核失败 -> 切 MacBook
lc.clear()
def to_run(cmd, **kw):
    if cmd[0] != "codex": return orig(cmd, **kw)
    lc.append(1); raise sp.TimeoutExpired(cmd, 1)
mb = []
real_mb = imagegen.generate_macbook; imagegen.generate_macbook = lambda *a, **k: (mb.append(k["_meta"]), (["mb"], []))[1]
imagegen.subprocess.run = to_run
try: assert imagegen.generate("x", cfg=c5) == (["mb"], [])
finally: imagegen.subprocess.run = orig
assert len(lc) == 2 and "TimeoutExpired" in mb[0]["fallback_reason"]
# 审核拦截不切 MacBook
mb.clear(); imagegen.subprocess.run = fake_run
try:
    imagegen.generate("x", cfg=c3, outfit_scene="正式"); assert False
except imagegen.ModerationBlocked: pass
finally: imagegen.subprocess.run = orig; imagegen.generate_macbook = real_mb
assert mb == []
# 本机第 2 次成功：不切，run.json 记 local
lc.clear(); fake_png = pathlib.Path(tempfile.mkdtemp()) / "a.png"; fake_png.write_bytes(b"x")
def flaky(cmd, **kw):
    if cmd[0] != "codex": return orig(cmd, **kw)
    lc.append(1)
    if len(lc) == 2: d = pathlib.Path(cmd[cmd.index("-C") + 1]) / "generated_images"; d.mkdir(parents=True); shutil.copy(fake_png, d)
    return type("R", (), {"stdout": "", "stderr": "401 unauthorized", "returncode": 1 if len(lc) == 1 else 0})()
import shutil
imagegen.subprocess.run = flaky
try: cmd, imgs = imagegen.generate("x", cfg=c5)
finally: imagegen.subprocess.run = orig
assert imgs and len(lc) == 2
m = json.loads((imgs[0].parents[1] / "run.json").read_text()) if (imgs[0].parents[1] / "run.json").exists() else json.loads((imgs[0].parent / "run.json").read_text())
assert m["backend"] == "local" and m["status"] == "ok" and m["local_try"] == 2 and "401" in m["local_errors"][0]
print("ok")
