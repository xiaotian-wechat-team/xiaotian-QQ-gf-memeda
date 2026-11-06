"""离线：mock HTTP 和 codex，验证 relay webhook 应急中继。不生图、不联网。"""
import os as _os; _os.environ["XIAOTIAN_TEST"] = "1"; _os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")  # 测试绝不读真实 relay.env / 联网
import sys, pathlib, copy, json, tempfile, os, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "src"))
import imagegen, relay, config
KEY = "test-relay-placeholder"
ENV = {"RELAY_WEBHOOK_URL": "https://relay.example/hook", "RELAY_WEBHOOK_KEY": KEY}
sent = []
class Resp:
    def __init__(s, st): s.status = st
    def __enter__(s): return s
    def __exit__(s, *a): return False
def fake_urlopen_factory(fail_times):
    n = {"i": 0}
    def f(req, timeout):
        assert timeout == 10
        sent.append(req); n["i"] += 1
        if n["i"] <= fail_times: raise OSError("boom")
        return Resp(200)
    return f
# 1) post: headers + 重试 2 次 + 不泄露 key
orig_open = relay.urllib.request.urlopen
import io, contextlib
err = io.StringIO()
relay.urllib.request.urlopen = fake_urlopen_factory(2)
with contextlib.redirect_stderr(err): ok, info = relay.post({"job_id": "x"}, ENV, sleep=lambda s: None)
assert ok and len(sent) == 3
h = dict(sent[0].header_items()); assert h["Authorization"] == f"Bearer {KEY}" and h["X-webhook-key"] == KEY
assert KEY not in err.getvalue()
sent.clear(); relay.urllib.request.urlopen = fake_urlopen_factory(9)
with contextlib.redirect_stderr(err): ok, info = relay.post({"job_id": "x"}, ENV, sleep=lambda s: None)
assert not ok and len(sent) == 3 and KEY not in info and KEY not in err.getvalue()
assert relay.post({}, {}) == (False, "not configured") and not relay.configured({})
# 2) load_env
tf = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False); tf.write("# c\nRELAY_WEBHOOK_URL=https://a/b\nRELAY_WEBHOOK_KEY='k'\n"); tf.close()
assert relay.load_env(tf.name) == {"RELAY_WEBHOOK_URL": "https://a/b", "RELAY_WEBHOOK_KEY": "k"}
# 3) 端到端：本机 codex 两次失败 → macbook(ssh_host 空) → pending + webhook → relayed_pending
cfg = config.load(); c = copy.deepcopy(cfg); c["imagegen"]["out_dir"] = tempfile.mkdtemp(); c["imagegen"]["ssh_host"] = ""
orig_run = imagegen.subprocess.run
def down_run(cmd, **kw):
    if cmd[0] != "codex": return orig_run(cmd, **kw)
    return type("R", (), {"stdout": "", "stderr": "connection refused", "returncode": 1})()
os.environ["RELAY_ENV_FILE"] = tf.name + ".none"   # 未配置 → 旧行为
imagegen.subprocess.run = down_run; sent.clear(); relay.urllib.request.urlopen = fake_urlopen_factory(0)
try:
    try: imagegen.generate("在咖啡店", cfg=c); assert False
    except RuntimeError as e: assert "在 MacBook 上跑" in str(e)
    assert not sent
    envf = pathlib.Path(tempfile.mkdtemp()) / "relay.env"; envf.write_text(f"RELAY_WEBHOOK_URL=https://r/h\nRELAY_WEBHOOK_KEY={KEY}\n")
    os.environ["RELAY_ENV_FILE"] = str(envf)
    with contextlib.redirect_stderr(err):
        try: imagegen.generate("在咖啡店", cfg=c); assert False
        except RuntimeError as e: assert "relayed_pending" in str(e) and KEY not in str(e)
finally:
    imagegen.subprocess.run = orig_run; os.environ["RELAY_ENV_FILE"] = "/nonexistent/xiaotian-test-relay.env"
assert len(sent) == 1
p = json.loads(sent[0].data)
for k in ("job_id", "pending_json", "result_path", "prompt", "outfit_text", "realism_text", "reference_images", "created_at", "error"): assert k in p, k
assert pathlib.Path(p["pending_json"]).is_absolute() and all(pathlib.Path(r).is_absolute() for r in p["reference_images"]) and p["reference_images"]
assert "connection refused" in p["error"] and "$imagegen" in p["prompt"]
job = json.loads(pathlib.Path(p["pending_json"]).read_text()); assert job["status"] == "relayed_pending" and KEY not in json.dumps(job)
# 4) --relay-done
img = pathlib.Path(tempfile.mkdtemp()) / "x.png"; img.write_bytes(b"\x89PNG fake")
dst = imagegen.relay_done(p["job_id"], img, cfg=c)
assert str(dst) == p["result_path"] and dst.read_bytes() == img.read_bytes()
assert json.loads(pathlib.Path(p["pending_json"]).read_text())["status"] == "done" and json.loads((dst.parent / "run.json").read_text())["relayed"]
relay.urllib.request.urlopen = orig_open
print("OK")
