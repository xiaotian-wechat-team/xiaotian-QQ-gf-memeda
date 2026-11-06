"""cursor-agent 聊天会话按天复用：同一天 brain 回复走同一个 chatId（`--resume <id>`），
跨天或 resume 坏了就 `create-chat` 开新会话，避免每次 TemporaryDirectory 冷启动。
状态：llm.session_state（默认 data/llm_session.json，不入库）。
工具向调用（complete_json / 抽事实）不应复用本会话，以免污染人设上下文。"""
import datetime, json, os, pathlib, re, subprocess

RESUME_BROKEN = (
    "no rollout found", "thread/resume failed", "session not found",
    "could not find session", "no such session", "chat not found",
    "unable to resume", "failed to resume",
)

def state_path(cfg_llm=None):
    cfg_llm = cfg_llm or {}
    p = cfg_llm.get("session_state")
    if p:
        path = pathlib.Path(os.path.expanduser(p))
        if not path.is_absolute():
            path = pathlib.Path(__file__).resolve().parents[1] / path
        return path
    if os.environ.get("XIAOTIAN_TEST"):
        import tempfile
        return pathlib.Path(tempfile.gettempdir()) / f"xiaotian-test-llm-session-{os.getpid()}.json"
    return pathlib.Path(__file__).resolve().parents[1] / "data" / "llm_session.json"

def today(now=None):
    return (now or datetime.datetime.now()).date().isoformat()

def load_state(cfg_llm=None):
    try:
        return json.loads(state_path(cfg_llm).read_text())
    except (OSError, ValueError):
        return {}

def current(cfg_llm=None, now=None):
    s = load_state(cfg_llm)
    return s.get("session_id") if s.get("date") == today(now) and s.get("session_id") else None

def save(cfg_llm, session_id, now=None):
    s = load_state(cfg_llm)
    d = today(now)
    p = state_path(cfg_llm)
    if s.get("date") != d or s.get("session_id") != session_id:
        hist = list(s.get("history") or [])
        if s.get("session_id") and s.get("session_id") != session_id:
            hist = (hist + [{"date": s.get("date"), "session_id": s["session_id"], "count": s.get("count", 0)}])[-14:]
        s = {"date": d, "session_id": session_id, "count": 0, "history": hist}
    s["count"] = s.get("count", 0) + 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    return s

def forget(cfg_llm=None, now=None):
    s = load_state(cfg_llm)
    if s.get("session_id"):
        s["history"] = ((s.get("history") or []) + [{"date": s.get("date"), "session_id": s["session_id"], "broken": True}])[-14:]
    s["session_id"] = None
    p = state_path(cfg_llm)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=1))

def resume_broken(log):
    l = (log or "").lower()
    return any(w in l for w in RESUME_BROKEN)

def create_chat(cmd):
    """`cursor-agent create-chat` → chat id（stdout 一行）。"""
    r = subprocess.run([cmd, "create-chat"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "create-chat failed")[-500:])
    sid = (r.stdout or "").strip().splitlines()[-1].strip() if (r.stdout or "").strip() else ""
    if not sid or " " in sid:
        raise RuntimeError(f"bad create-chat id: {r.stdout!r} {r.stderr!r}"[-500:])
    return sid

def ensure(cfg_llm, cmd, now=None):
    sid = current(cfg_llm, now)
    if sid:
        return sid
    sid = create_chat(cmd)
    save(cfg_llm, sid, now)
    return sid

def workspace(cfg_llm=None):
    """固定空工作区：--trust 只覆盖这里，避免整仓 AGENTS.md 进上下文；人设靠 ~/.cursor/skills/xiaotian-chat。"""
    cfg_llm = cfg_llm or {}
    p = cfg_llm.get("workspace") or "data/llm_ws"
    path = pathlib.Path(os.path.expanduser(p))
    if not path.is_absolute():
        path = pathlib.Path(__file__).resolve().parents[1] / path
    path.mkdir(parents=True, exist_ok=True)
    return path
