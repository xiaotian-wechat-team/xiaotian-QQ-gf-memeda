"""Persona/memory files in digital-life + SQLite chat log. Only contact demo_contact touches contacts/demo_contact."""
import sqlite3, json, hashlib, datetime, pathlib, os, re, tempfile, fcntl, unicodedata, contextlib
from config import load
CFG = load()
DL = pathlib.Path(CFG["digital_life_dir"])
ALLOWED = {CFG["contact"]["id"]}

def _r(p):
    p = pathlib.Path(p); return p.read_text() if p.exists() else ""

def cdir(cid):
    if cid not in ALLOWED: raise PermissionError(cid)
    return DL / "contacts" / cid

def persona():
    return {k: _r(DL / f) for k, f in [("soul","SOUL.md"),("user","USER.md"),("memory","MEMORY.md")]}

def contact_ctx(cid):
    d = cdir(cid)
    return {"profile": _r(d/"PROFILE.md"), "memory": _r(d/"MEMORY.md"), "state": _r(d/"STATE.json")}

def append_diary(cid, line):
    d = cdir(cid) / "diary"; d.mkdir(exist_ok=True)
    f = d / f"{datetime.date.today()}.md"
    with _lock(cid), f.open("a") as fh: fh.write(f"- {datetime.datetime.now():%H:%M} {line}\n")

@contextlib.contextmanager
def _lock(cid):
    with open(cdir(cid) / ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(lf, fcntl.LOCK_UN)

def read_state(cid):
    """Parsed contacts/<cid>/STATE.json（唯一真源；根目录 digital-life/STATE.json 已废弃删除）。{} if missing. Raises ValueError if corrupt (never silently reset)."""
    f = cdir(cid) / "STATE.json"
    if not f.exists(): return {}
    return json.loads(f.read_text())

def update_state(cid, **kv):
    """Locked atomic merge into STATE.json (read-modify-write). Never replaces the whole dict.

    Only keys in kv are upserted; emotion / proactive_beats / nicknames / etc. are kept
    when callers (e.g. life_calendar.sync_to_memory) pass only life_*. Corrupt file -> raise, original kept.
    After replace, re-reads and checks kv keys survived (catches silent whole-file races).
    """
    return mutate_state(cid, lambda st: kv)

def mutate_state(cid, change):
    """Apply a computed patch under the contact lock (counters/receipts stay atomic)."""
    f = cdir(cid) / "STATE.json"
    with _lock(cid):
        st = read_state(cid)
        if not isinstance(st, dict):
            raise ValueError("STATE.json root must be object")
        kv = change(st)
        st.update(kv)
        fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=".STATE.", suffix=".tmp")
        with os.fdopen(fd, "w") as fh: json.dump(st, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, f)
        got = read_state(cid)
        for k, v in kv.items():
            if got.get(k) != v:
                raise RuntimeError(f"STATE merge lost key {k!r} after write (possible whole-file overwrite)")
        return st

def default_proactive_beats(day=""):
    return {"day": day or "", "done": {}, "last_slot_key": "", "last_activity": "",
            "shared_material_ids": [], "surf_target": None, "shares": []}

def ensure_defaults(cid, now=None):
    """Runtime seed: if emotion or proactive_beats missing, write defaults (not one-shot data only).

    Also heal last_proactive_at / beats.done from proactive_queue when a whole-file
    life-only overwrite wiped them (see heal_proactive_from_queue).
    """
    st = read_state(cid)
    kv = {}
    if now is None:
        try:
            import life_calendar as lc
            now = lc.now_local()
        except Exception:
            now = datetime.datetime.now()
    day = now.strftime("%Y-%m-%d") if hasattr(now, "strftime") else str(now)[:10]
    if not isinstance(st.get("emotion"), dict):
        import emotion
        kv["emotion"] = emotion.default()
    if not isinstance(st.get("proactive_beats"), dict):
        kv["proactive_beats"] = default_proactive_beats(day)
    if kv:
        update_state(cid, **kv)
        st = {**(st if isinstance(st, dict) else {}), **kv}
    try:
        healed = heal_proactive_from_queue(cid, now=now)
        if healed:
            st = {**st, **healed}
    except Exception as e:
        print(f"[memory] heal_proactive_from_queue skip: {e!r}")
    return st


def heal_proactive_from_queue(cid, now=None, queue_path=None):
    """If STATE lost last_proactive_at / today's beats.done but queue has sent beats, restore.

    Guards against unlocked whole-file STATE rewrites that leave only life_* (seen with
    life_plan_meta.synced_at frozen to a fixture clock). Merge-only; never deletes keys.
    Returns dict of keys written, or {} if nothing to heal.
    """
    if now is None:
        now = datetime.datetime.now()
    day = now.strftime("%Y-%m-%d") if hasattr(now, "strftime") else str(now)[:10]
    st = read_state(cid)
    beats = st.get("proactive_beats") if isinstance(st.get("proactive_beats"), dict) else None
    done = dict((beats or {}).get("done") or {}) if beats and beats.get("day") == day else {}
    last_pro = st.get("last_proactive_at") or ""
    need_last = not last_pro or str(last_pro).startswith("TEST-MARKER")
    need_done = not done
    if not need_last and not need_done:
        return {}
    qpath = pathlib.Path(queue_path) if queue_path else pathlib.Path(CFG["db_path"]).parent / "proactive_queue.json"
    if not qpath.exists():
        return {}
    try:
        q = json.loads(qpath.read_text())
    except Exception:
        return {}
    q_done = {}
    last_sa = None
    cnt = 0
    for it in q.get("items") or []:
        if it.get("status") != "sent" or it.get("accounted") is False:
            # New delivery journals are recovered by the idempotent receipt handler.
            continue
        sa = str(it.get("sent_at") or "")
        if not sa.startswith(day):
            continue
        cnt += 1
        if last_sa is None or sa > last_sa:
            last_sa = sa
        meta = it.get("meta") or {}
        dk = meta.get("beat_done_key") or (meta.get("beat_meta") or {}).get("done_key") or (meta.get("beat_meta") or {}).get("id")
        if not dk:
            topic = str(it.get("topic") or "")
            if topic.startswith("beat:"):
                dk = topic.split(":", 1)[1]
        if dk:
            try:
                t = datetime.datetime.strptime(sa, "%Y-%m-%d %H:%M:%S").strftime("%H:%M:%S")
            except Exception:
                t = sa[11:19] if len(sa) >= 19 else sa
            q_done[dk] = t
            for merged in (meta.get("beat_meta") or {}).get("merged_keys") or []:
                q_done[merged] = t
    if not last_sa and not q_done:
        return {}
    kv = {}
    if need_last and last_sa:
        kv["last_proactive_at"] = last_sa
        kv["proactive_day"] = day
        if not st.get("proactive_count"):
            kv["proactive_count"] = cnt
    if need_done and q_done:
        base = dict(beats) if beats and beats.get("day") == day else default_proactive_beats(day)
        base["day"] = day
        base["done"] = {**dict(base.get("done") or {}), **q_done}
        kv["proactive_beats"] = base
    if not kv:
        return {}
    update_state(cid, **kv)
    return kv

def db():
    p = pathlib.Path(CFG["db_path"]); p.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, contact TEXT, sender TEXT,
      text TEXT, fp TEXT UNIQUE, ts TEXT, replied INTEGER DEFAULT 0)""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    for col in ("type", "sent_at"):   # 2026-10-24：消息类型 / 微信时间戳解析出的发送时间（可空）
        if col not in cols: c.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT")
    return c

class Msg(tuple):
    """(sender, text) 二元组，可照旧解包；额外带 .sent_at / .type（来自 chat.db 或屏幕）。"""
    def __new__(cls, sender, text, sent_at=None, type=None):
        o = super().__new__(cls, (sender, text)); o.sent_at, o.type = sent_at, type; return o

def norm(t):
    """OCR-tolerant normal form for comparison (not storage)."""
    t = unicodedata.normalize("NFKC", t or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))
    return re.sub(r"[\s，。,.!！?？~～…、\"'“”‘’]+", "", t).lower()

def fingerprint(cid, sender, text, prev_text, occ):
    return hashlib.sha1(f"{cid}|{sender}|{text}|{prev_text}|{occ}".encode()).hexdigest()

def insert(c, cid, sender, text, fp, commit=True, type=None, sent_at=None):
    try:
        c.execute("INSERT INTO messages(contact,sender,text,fp,ts,type,sent_at) VALUES(?,?,?,?,?,?,?)",
                  (cid, sender, text, fp, datetime.datetime.now().isoformat(timespec="seconds"), type, sent_at))
        if commit: c.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def tail(c, cid, n=50):
    rows = c.execute("SELECT id,sender,text FROM messages WHERE contact=? ORDER BY id DESC LIMIT ?", (cid, n)).fetchall()
    return rows[::-1]

def new_suffix(db_tail, visible):
    """Sequence alignment: db_tail/visible are [(sender,text)]. Return the visible messages not yet in DB.
    Finds the longest k such that the last k DB rows == a contiguous run in visible ending at index j;
    everything after j is new. Prefers matches nearest the end of the screen. No overlap -> all visible new
    (only safe when DB is empty or the screen fully scrolled; caller logs it)."""
    key = lambda m: (m[0], norm(m[1]))
    D, V = [key(m) for m in db_tail], [key(m) for m in visible]
    if not D: return list(visible), True
    best_j, best_k = -1, 0
    for j in range(len(V) - 1, -1, -1):
        k = 0
        while k <= j and k < len(D) and V[j - k] == D[-1 - k]: k += 1
        if k > best_k:
            best_k, best_j = k, j
        if k == min(len(D), j + 1) and k >= 1: break  # full match of available overlap
    if best_k == 0: return list(visible), False
    return list(visible[best_j + 1:]), True

def history(c, cid, n=20):
    rows = c.execute("SELECT sender,text,sent_at,type FROM messages WHERE contact=? ORDER BY id DESC LIMIT ?", (cid, n)).fetchall()
    return [Msg(*r) for r in rows[::-1]]

def mark_replied(c, cid):
    c.execute("UPDATE messages SET replied=1 WHERE contact=? AND sender='them' AND replied=0", (cid,)); c.commit()
