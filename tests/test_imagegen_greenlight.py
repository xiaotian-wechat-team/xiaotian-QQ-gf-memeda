"""离线：greenlight 预检；XIAOTIAN_TEST 跳过；红灯→LocalBackendError；超时 forget 会话。"""
import os as _os
_os.environ["XIAOTIAN_TEST"] = "1"
_os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, copy, tempfile, json, subprocess as sp
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import imagegen_greenlight as gl, imagegen, codex_session, config

# test skip
assert gl.run_checks()[0]["name"] == "test_skip"
# CLI green under test
assert gl.main([]) == 0

# magpie_ok false path unit (no network assumption)
assert gl.magpie_ok("http://127.0.0.1:1/nope", timeout=0.2) is False

# generate() greenlight failure → macbook pending (mock)
_os.environ.pop("XIAOTIAN_TEST", None)  # allow greenlight path
cfg = copy.deepcopy(config.load())
cfg["imagegen"]["out_dir"] = tempfile.mkdtemp()
cfg["imagegen"]["ssh_host"] = ""
cfg["imagegen"]["session_state"] = str(pathlib.Path(tempfile.mkdtemp()) / "s.json")
calls = []
real_gl = imagegen.ensure_magpie
def boom(cfg=None):
    raise imagegen.LocalBackendError("Magpie 未启动（test）")
imagegen.ensure_magpie = boom
try:
    try:
        imagegen.generate("x", cfg=cfg); assert False
    except RuntimeError as e:
        assert "ssh_host 为空" in str(e) or "Magpie" in str(e)
finally:
    imagegen.ensure_magpie = real_gl
    _os.environ["XIAOTIAN_TEST"] = "1"

# timeout → forget sticky session
_os.environ["XIAOTIAN_TEST"] = "1"
cfg2 = copy.deepcopy(config.load())
tmp = pathlib.Path(tempfile.mkdtemp())
cfg2["imagegen"]["out_dir"] = str(tmp / "out")
cfg2["imagegen"]["session_state"] = str(tmp / "s.json")
cfg2["imagegen"]["ssh_host"] = ""
cfg2["imagegen"]["timeout"] = 1
codex_session.save(cfg2, "STICKY")
assert codex_session.current(cfg2) == "STICKY"
orig = imagegen.subprocess.run
mb = []
def to_run(cmd, **kw):
    if cmd and pathlib.Path(str(cmd[0])).name == "codex":
        raise sp.TimeoutExpired(cmd, 1)
    return orig(cmd, **kw)
real_mb = imagegen.generate_macbook
imagegen.generate_macbook = lambda *a, **k: (mb.append(1), (["mb"], []))[1]
imagegen.subprocess.run = to_run
try:
    # ensure_magpie skipped under XIAOTIAN_TEST via greenlight
    imagegen.generate("x", cfg=cfg2)
finally:
    imagegen.subprocess.run = orig
    imagegen.generate_macbook = real_mb
assert mb and codex_session.current(cfg2) is None  # forgotten after timeout

# reasoning_effort in dry cmd
_os.environ["XIAOTIAN_TEST"] = "1"
cfg3 = copy.deepcopy(config.load()); cfg3["imagegen"]["reasoning_effort"] = "low"
cmd, _ = imagegen.generate("窗边", dry=True, cfg=cfg3)
assert "-c" in cmd and any("model_reasoning_effort=low" in x for x in cmd)

# max_session_count
cfg4 = {"imagegen": {"session_state": str(tmp / "s2.json"), "max_session_count": 2}}
codex_session.save(cfg4, "A"); codex_session.save(cfg4, "A")
assert codex_session.load_state(cfg4)["count"] == 2
assert codex_session.current(cfg4) is None  # hit max → new session

print("OK")
