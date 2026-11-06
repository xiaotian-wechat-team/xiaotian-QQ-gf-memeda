"""MacBook 应急中继：Mac mini 无法直接调 MacBook（ssh_host 为空或 ssh 失败）时，把 pending 任务 POST 到开发助手的
relay webhook，由助手转给 MacBook 生图后再用 `python -m src.imagegen --relay-done <job_id> <image>` 回填。
配置：config/relay.env（RELAY_WEBHOOK_URL / RELAY_WEBHOOK_KEY；gitignore，chmod 600）。未配置则不发，保持旧行为。
密钥绝不写日志。"""
import datetime, json, os, pathlib, sys, time, urllib.request

ENV_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "relay.env"
TIMEOUT, RETRIES = 10, 2

_REAL_URLOPEN = urllib.request.urlopen

def _testing():
    return os.environ.get("XIAOTIAN_TEST") == "1"

def load_env(path=None):
    """优先 path，其次 RELAY_ENV_FILE；测试模式（XIAOTIAN_TEST=1）下绝不读真实 config/relay.env。"""
    src = path or os.environ.get("RELAY_ENV_FILE")
    if not src:
        if _testing(): return {}
        src = ENV_PATH
    p = pathlib.Path(src); out = {}
    if not p.exists(): return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def configured(env=None):
    env = load_env() if env is None else env
    return bool(env.get("RELAY_WEBHOOK_URL") and env.get("RELAY_WEBHOOK_KEY"))

def build_payload(job_path, job):
    return {"type": "xiaotian_imagegen_relay", "job_id": job["job_id"], "pending_json": str(pathlib.Path(job_path).resolve()),
            "result_path": job["result_path"], "prompt": job.get("prompt", ""), "scene": job.get("scene", ""),
            "outfit_text": job.get("outfit_text", ""), "realism_text": job.get("realism_text", ""),
            "reference_images": job.get("reference_images", []), "macbook_cmd": job.get("macbook_cmd", ""),
            "created_at": job.get("created_at"), "error": job.get("error", "")[:1000],
            "done_cmd": f"python -m src.imagegen --relay-done {job['job_id']} <image_path>"}

def post(payload, env=None, sleep=time.sleep):
    """POST JSON；超时 10s，失败重试 2 次。返回 (ok, info)。info 不含密钥。"""
    env = load_env() if env is None else env
    url, key = env.get("RELAY_WEBHOOK_URL"), env.get("RELAY_WEBHOOK_KEY")
    if not (url and key): return False, "not configured"
    if _testing() and urllib.request.urlopen is _REAL_URLOPEN:
        raise RuntimeError("XIAOTIAN_TEST=1：拒绝真实 relay HTTP 请求（请 mock urlopen）")
    data = json.dumps(payload, ensure_ascii=False).encode()
    last = ""
    for i in range(RETRIES + 1):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}", "X-Webhook-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                if 200 <= r.status < 300: return True, f"HTTP {r.status}"
                last = f"HTTP {r.status}"
        except Exception as e:
            last = f"{type(e).__name__}: {getattr(e, 'code', '') or getattr(e, 'reason', '') or ''}".replace(key, "***")
        print(f"[relay] webhook 第 {i+1} 次失败：{last}", file=sys.stderr)
        if i < RETRIES: sleep(2 * (i + 1))
    return False, last

def relay_job(job_path, env=None, sleep=time.sleep):
    """已写好的 pending json → 发 webhook，成功则 status=relayed_pending。返回 ok。"""
    env = load_env() if env is None else env
    if not configured(env): return False
    job_path = pathlib.Path(job_path); job = json.loads(job_path.read_text())
    ok, info = post(build_payload(job_path, job), env, sleep)
    job.update(status="relayed_pending" if ok else "relay_failed", relay_info=info,
               relayed_at=datetime.datetime.now().isoformat(timespec="seconds"))
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1)); return ok
