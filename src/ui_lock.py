"""微信 UI 锁（data/wechat_ui.lock，flock）+ 回复优先。
- 回复 daemon：ui_lock("reply") 阻塞拿锁；发现聊天区变化后用 reply_priority() 挂起 data/wechat_reply_pending，
  直到回复发完才撤掉。
- 主动发送：ui_lock("proactive") 只做非阻塞尝试；有回复挂起 / 锁被占 → 抛 LockBusy，交给下次 5 分钟检查。
  拿到锁后发送前还要再看一次 reply_pending()，有就让路。主动发送单次持锁只有几秒，回复最坏等这几秒。"""
import contextlib, fcntl, os, pathlib, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
UI_LOCK = ROOT / "data" / "wechat_ui.lock"
PENDING = ROOT / "data" / "wechat_reply_pending"
STALE_SEC = 900   # daemon 崩了留下的挂起标记，超时后视为无效

_HELD = 0

class LockBusy(RuntimeError): pass

def _alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True

def reply_pending(path=None, now=None):
    p = pathlib.Path(path or PENDING)
    try: st = p.stat(); pid = int(p.read_text().strip() or 0)
    except (FileNotFoundError, ValueError): return False
    if (now or time.time()) - st.st_mtime > STALE_SEC: return False
    return _alive(pid) if pid else True

@contextlib.contextmanager
def reply_priority(path=None):
    p = pathlib.Path(path or PENDING); p.parent.mkdir(exist_ok=True)
    p.write_text(str(os.getpid()))
    try: yield
    finally:
        with contextlib.suppress(FileNotFoundError): p.unlink()

@contextlib.contextmanager
def ui_lock(role="reply", path=None, pending=None):
    if role not in ("reply", "proactive"): raise ValueError(role)
    global _HELD
    if _HELD:   # 同进程重入（如 daemon 持锁时 read_chat 内部的复制读取）：flock 对同进程的新 fd 也会互斥，直接放行
        yield; return
    p = pathlib.Path(path or UI_LOCK); p.parent.mkdir(exist_ok=True)
    if role == "proactive" and reply_pending(pending): raise LockBusy("回复优先：有待回复消息")
    with open(p, "a") as f:
        if role == "reply": fcntl.flock(f, fcntl.LOCK_EX)
        else:
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise LockBusy("UI 锁被回复流程占用")
            if reply_pending(pending): fcntl.flock(f, fcntl.LOCK_UN); raise LockBusy("回复优先：有待回复消息")
        _HELD += 1
        try: yield
        finally: _HELD -= 1; fcntl.flock(f, fcntl.LOCK_UN)
