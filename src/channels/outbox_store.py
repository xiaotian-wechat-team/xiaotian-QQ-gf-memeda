"""Shared SQLite outbox for iLink / OneBot (volume-controlled).

Replaces data/*_outbox.json. Pending kept; terminal rows short-retained and capped.
Never writes digital-life / couple MEMORY.
"""
from __future__ import annotations

import json
import sqlite3
import time
import copy
import contextlib
import fcntl
import functools
import threading
from pathlib import Path
from typing import Any, Optional

try:
    from config import ROOT as _CFG_ROOT
    ROOT = Path(_CFG_ROOT)
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[2]

DB_PATH = ROOT / "data" / "outbox.db"
# Terminal (sent/dropped/cleared) retention + hard caps (config outbox.* overrides at import)
TERMINAL_TTL_SEC = 48 * 3600
MAX_TERMINAL_ROWS = 200
MAX_PENDING_ROWS = 80
LEGACY_JSON = {
    "ilink": ROOT / "data" / "ilink_outbox.json",
    "onebot": ROOT / "data" / "qq_outbox.json",
    "qq": ROOT / "data" / "qq_outbox.json",
}

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


@contextlib.contextmanager
def channel_lock(channel: str):
    """Serialize flushes and writes across threads/processes, without a SQL lock over I/O."""
    key = (str(DB_PATH.resolve()), _norm_channel(channel))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_HELD, "keys", set())
        if key in held:
            yield
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        path = DB_PATH.with_name(DB_PATH.name + "." + key[1] + ".lock")
        with path.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            _HELD.keys = held | {key}
            try:
                yield
            finally:
                _HELD.keys = held
                fcntl.flock(fh, fcntl.LOCK_UN)


def serialized(channel: str):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with channel_lock(channel):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


class OutboxConflict(RuntimeError):
    """A caller tried to change a row using an obsolete snapshot."""


def _refresh_from_config() -> None:
    """Wire outbox path / TTLs / caps from config.json outbox.*."""
    global DB_PATH, TERMINAL_TTL_SEC, MAX_TERMINAL_ROWS, MAX_PENDING_ROWS
    try:
        from config import load, ROOT as cfg_root
        cfg = load()
        ob = cfg.get("outbox") or {}
        if ob.get("db"):
            path = Path(str(ob["db"])).expanduser()
            DB_PATH = path if path.is_absolute() else Path(cfg_root) / path
        if ob.get("terminal_ttl_hours") is not None:
            TERMINAL_TTL_SEC = int(float(ob["terminal_ttl_hours"]) * 3600)
        if ob.get("max_terminal") is not None:
            MAX_TERMINAL_ROWS = int(ob["max_terminal"])
        if ob.get("max_pending") is not None:
            MAX_PENDING_ROWS = int(ob["max_pending"])
    except Exception:
        pass


_refresh_from_config()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox(
  channel TEXT NOT NULL,
  id TEXT NOT NULL,
  peer TEXT,
  contact TEXT,
  text TEXT,
  image_path TEXT,
  status TEXT,
  reason TEXT,
  created TEXT,
  note TEXT,
  dropped_at TEXT,
  extra TEXT DEFAULT '{}',
  updated_at REAL NOT NULL,
  PRIMARY KEY(channel, id)
);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON outbox(channel, status, updated_at);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _norm_channel(channel: str) -> str:
    ch = (channel or "").strip().lower()
    if ch in ("qq", "qq_onebot", "onebot"):
        return "onebot"
    if ch in ("ilink", "wechat_ilink"):
        return "ilink"
    return ch or "unknown"


def _migrate_legacy(channel: str) -> None:
    path = LEGACY_JSON.get(channel)
    if not path or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM outbox WHERE channel=?", (channel,)).fetchone()[0]
        if n:
            return
        now = time.time()
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            _upsert_cur(c, channel, it, now=now)
        c.commit()
    try:
        bak = path.with_suffix(path.suffix + ".migrated")
        path.replace(bak)
    except Exception:
        pass


def _upsert_cur(c: sqlite3.Connection, channel: str, it: dict, now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    known = {"id", "peer", "contact", "text", "image_path", "status", "reason",
             "created", "note", "dropped_at", "channel", "_version"}
    extra = {k: v for k, v in it.items() if k not in known}
    c.execute(
        """INSERT INTO outbox(channel,id,peer,contact,text,image_path,status,reason,
           created,note,dropped_at,extra,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(channel,id) DO UPDATE SET
             peer=excluded.peer, contact=excluded.contact, text=excluded.text,
             image_path=excluded.image_path, status=excluded.status, reason=excluded.reason,
             created=excluded.created, note=excluded.note, dropped_at=excluded.dropped_at,
             extra=excluded.extra, updated_at=excluded.updated_at
        """,
        (
            channel,
            str(it.get("id")),
            it.get("peer"),
            it.get("contact"),
            it.get("text") or "",
            it.get("image_path"),
            it.get("status") or "pending",
            it.get("reason") or "",
            it.get("created") or __import__("timeutil").now_local().strftime("%Y-%m-%d %H:%M:%S"),
            it.get("note"),
            it.get("dropped_at"),
            json.dumps(extra, ensure_ascii=False),
            now,
        ),
    )


def purge(channel: Optional[str] = None, *, now: Optional[float] = None) -> dict:
    """Drop old terminal rows; cap terminal + pending volume."""
    now = now if now is not None else time.time()
    cut = now - TERMINAL_TTL_SEC
    terminal = ("sent", "dropped", "cleared", "done", "failed")
    with _conn() as c:
        chs = [channel] if channel else [
            r[0] for r in c.execute("SELECT DISTINCT channel FROM outbox").fetchall()
        ]
        stats = {"deleted_ttl": 0, "deleted_cap": 0, "pending_trimmed": 0}
        for ch in chs:
            if not ch:
                continue
            cur = c.execute(
                f"DELETE FROM outbox WHERE channel=? AND status IN ({','.join('?'*len(terminal))}) "
                f"AND updated_at<?",
                (ch, *terminal, cut),
            )
            stats["deleted_ttl"] += cur.rowcount or 0
            # cap terminal
            rows = c.execute(
                f"SELECT id FROM outbox WHERE channel=? AND status IN ({','.join('?'*len(terminal))}) "
                f"ORDER BY updated_at DESC",
                (ch, *terminal),
            ).fetchall()
            if len(rows) > MAX_TERMINAL_ROWS:
                drop_ids = [r[0] for r in rows[MAX_TERMINAL_ROWS:]]
                c.executemany(
                    "DELETE FROM outbox WHERE channel=? AND id=?",
                    [(ch, i) for i in drop_ids],
                )
                stats["deleted_cap"] += len(drop_ids)
            # pending cap: keep newest
            pend = c.execute(
                "SELECT id FROM outbox WHERE channel=? AND status IN ('pending','hold','补发') "
                "ORDER BY updated_at DESC",
                (ch,),
            ).fetchall()
            if len(pend) > MAX_PENDING_ROWS:
                drop_ids = [r[0] for r in pend[MAX_PENDING_ROWS:]]
                c.executemany(
                    "DELETE FROM outbox WHERE channel=? AND id=?",
                    [(ch, i) for i in drop_ids],
                )
                stats["pending_trimmed"] += len(drop_ids)
        c.commit()
    return stats


def load(channel: str) -> dict:
    ch = _norm_channel(channel)
    _migrate_legacy(ch)
    purge(ch)
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM outbox WHERE channel=? ORDER BY updated_at ASC", (ch,)
        ).fetchall()
    items = []
    for r in rows:
        it = {
            "id": r["id"],
            "peer": r["peer"],
            "contact": r["contact"],
            "text": r["text"] or "",
            "status": r["status"],
            "reason": r["reason"] or "",
            "created": r["created"],
            "note": r["note"],
            "dropped_at": r["dropped_at"],
            "channel": r["channel"],
        }
        if r["image_path"]:
            it["image_path"] = r["image_path"]
        try:
            extra = json.loads(r["extra"] or "{}")
            if isinstance(extra, dict):
                it.update(extra)
        except Exception:
            pass
        it["_version"] = r["updated_at"]
        items.append(it)
    return {"items": items, "_original": {it["id"]: copy.deepcopy(it) for it in items}}


def save(channel: str, data: dict) -> None:
    """Merge changed rows only; absent IDs never mean delete. Reject stale edits."""
    ch = _norm_channel(channel)
    items = [it for it in ((data or {}).get("items") or [])
             if isinstance(it, dict) and it.get("id")]
    original = (data or {}).get("_original") or {}
    versions = {}
    with channel_lock(ch), _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        for it in items:
            oid = str(it["id"])
            if it == original.get(oid):
                continue
            row = c.execute("SELECT updated_at FROM outbox WHERE channel=? AND id=?", (ch, oid)).fetchone()
            expected = it.get("_version")
            if expected is None and row is not None:
                # Idempotent enqueue, never overwrite an existing delivery.
                continue
            if expected is not None and (row is None or row[0] != expected):
                raise OutboxConflict(f"outbox {ch}/{oid} changed; reload before editing")
            version = max(time.time(), (row[0] + 0.000001) if row else 0)
            _upsert_cur(c, ch, it, now=version)
            versions[oid] = version
        c.commit()
    for it in items:
        if str(it.get("id")) in versions:
            it["_version"] = versions[str(it["id"])]
    data["_original"] = {str(it["id"]): copy.deepcopy(it) for it in items if it.get("id")}
    purge(ch)


def stats(channel: Optional[str] = None) -> dict[str, Any]:
    with _conn() as c:
        if channel:
            ch = _norm_channel(channel)
            rows = c.execute(
                "SELECT status, COUNT(*) FROM outbox WHERE channel=? GROUP BY status", (ch,)
            ).fetchall()
            return {"channel": ch, "by_status": {r[0]: r[1] for r in rows}, "db": str(DB_PATH)}
        rows = c.execute(
            "SELECT channel, status, COUNT(*) FROM outbox GROUP BY channel, status"
        ).fetchall()
        out: dict = {"db": str(DB_PATH), "channels": {}}
        for ch, st, n in rows:
            out["channels"].setdefault(ch, {})[st] = n
        return out
