"""生图 Codex 会话按天轮换：同一天所有生图复用同一个 Codex 会话（`codex exec resume <id>`），日期一变就开全新会话，
避免上下文无限增长；当天记录的会话丢失/坏掉（resume 报 no rollout found / thread/resume failed）就为当天新开一个。
状态文件：imagegen.session_state（默认 data/codex_session.json，不入库）= {"date": "YYYY-MM-DD", "session_id": ..., "count": n, "history": [...]}；
history 只留最近 14 天的 id 便于排查，不会再被 resume。MacBook 应急脚本用同样规则（~/.xiaotian/codex_session_<date>）。"""
import datetime, json, os, pathlib, re

RESUME_BROKEN = ("no rollout found", "thread/resume failed", "session not found", "could not find session", "no such session")
THREAD_RE = re.compile(r'"type"\s*:\s*"thread\.started"[^}]*"thread_id"\s*:\s*"([^"]+)"')

def state_path(cfg):
    p = (cfg.get("imagegen") or {}).get("session_state")
    if p: return pathlib.Path(os.path.expanduser(p))
    if os.environ.get("XIAOTIAN_TEST"):   # 测试绝不碰真实会话状态
        import tempfile; return pathlib.Path(tempfile.gettempdir()) / f"xiaotian-test-codex-session-{os.getpid()}.json"
    return pathlib.Path(__file__).resolve().parents[1] / "data" / "codex_session.json"

def today(now=None): return (now or datetime.datetime.now()).date().isoformat()

def load_state(cfg):
    try: return json.loads(state_path(cfg).read_text())
    except (OSError, ValueError): return {}

def current(cfg, now=None):
    """今天可复用的 session_id；不是今天的（或没有）返回 None —— 调用方就开新会话。
    count ≥ imagegen.max_session_count（默认 6）也返回 None，避免 sticky resume 上下文膨胀后空等超时。"""
    s = load_state(cfg)
    if s.get("date") != today(now) or not s.get("session_id"):
        return None
    try:
        max_c = int((cfg.get("imagegen") or {}).get("max_session_count") or 0)
    except (TypeError, ValueError):
        max_c = 0
    if max_c > 0 and int(s.get("count") or 0) >= max_c:
        return None
    return s.get("session_id")

def save(cfg, session_id, now=None):
    s = load_state(cfg); d = today(now); p = state_path(cfg)
    if s.get("date") != d or s.get("session_id") != session_id:
        hist = (s.get("history") or [])
        if s.get("session_id") and s.get("session_id") != session_id: hist = (hist + [{"date": s.get("date"), "session_id": s["session_id"], "count": s.get("count", 0)}])[-14:]
        s = {"date": d, "session_id": session_id, "count": 0, "history": hist}
    s["count"] = s.get("count", 0) + 1
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    return s

def forget(cfg, now=None):
    """当天会话坏了：清掉 session_id（保留历史），下次 build 开新会话。"""
    s = load_state(cfg)
    if s.get("session_id"):
        s["history"] = ((s.get("history") or []) + [{"date": s.get("date"), "session_id": s["session_id"], "broken": True}])[-14:]
    s["session_id"] = None; p = state_path(cfg); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(s, ensure_ascii=False, indent=1))

def build(codex, refs, out_dir, session_id=None, extra_args=None):
    """返回 (cmd, cwd)。新会话：codex exec --json ... -C out_dir -i...（提示词走 stdin）；
    复用：codex exec resume --json <id> - -i ...（resume 不支持 -C/--sandbox，沿用会话设置；位置参数放在 -i 前面，-i 是多值参数会吞后面的参数）。
    extra_args：插在 -i 之前（如 -c model_reasoning_effort=low），生图用低推理避免 ultra 空转。"""
    extra = list(extra_args or [])
    if session_id:
        cmd = [codex, "exec", "resume", "--json", "--skip-git-repo-check", *extra, session_id, "-"]
    else:
        cmd = [codex, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(out_dir), *extra]
    for r in refs: cmd += ["-i", str(r)]
    return cmd, str(out_dir)

def thread_id(stdout):
    m = THREAD_RE.search(stdout or ""); return m.group(1) if m else None

def resume_broken(log): l = (log or "").lower(); return any(w in l for w in RESUME_BROKEN)
