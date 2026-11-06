"""Isolated classmate memory — NEVER touch digital-life / contacts/demo_contact couple store.

Local SQLite under data/classmate_memory.db (path overridable via
persona.classmate.memory in config.json). Short retention to control volume.
Materializes DB + parent dirs on first use / ensure().
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

try:
    from config import ROOT as _CFG_ROOT
    ROOT = Path(_CFG_ROOT)
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[1]

DB_PATH = ROOT / "data" / "classmate_memory.db"
TTL_SEC = 14 * 86400  # 14d chat retention (override via persona.classmate.ttl_days)
MAX_ROWS_PER_CONTACT = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  contact TEXT NOT NULL,
  sender TEXT NOT NULL,
  text TEXT NOT NULL,
  ts REAL NOT NULL,
  msg_id TEXT,
  msg_type TEXT
);
CREATE INDEX IF NOT EXISTS ix_cm_contact_ts ON messages(contact, ts);
CREATE TABLE IF NOT EXISTS profiles(
  contact TEXT PRIMARY KEY,
  display_name TEXT,
  address_as TEXT,
  notes TEXT DEFAULT '',
  updated_at REAL NOT NULL
);
"""


def _refresh_from_config() -> None:
    """Wire DB path / TTL from config.json (defaults kept if absent)."""
    global DB_PATH, TTL_SEC, MAX_ROWS_PER_CONTACT
    try:
        from config import load, ROOT as cfg_root
        cfg = load()
        cm = ((cfg.get("persona") or {}).get("classmate") or {})
        mem = cm.get("memory") or cm.get("db")
        if mem:
            p = Path(str(mem)).expanduser()
            DB_PATH = p if p.is_absolute() else Path(cfg_root) / p
        if cm.get("ttl_days") is not None:
            TTL_SEC = int(float(cm["ttl_days"]) * 86400)
        if cm.get("max_rows_per_contact") is not None:
            MAX_ROWS_PER_CONTACT = int(cm["max_rows_per_contact"])
    except Exception:
        pass


_refresh_from_config()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def ensure() -> Path:
    """Materialize classmate_memory.db + schema on first use / startup."""
    _refresh_from_config()
    with _conn() as c:
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    return DB_PATH


def purge(contact: Optional[str] = None, *, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    cut = now - TTL_SEC
    with _conn() as c:
        if contact:
            n1 = c.execute("DELETE FROM messages WHERE contact=? AND ts<?", (contact, cut)).rowcount or 0
            rows = c.execute(
                "SELECT id FROM messages WHERE contact=? ORDER BY ts DESC", (contact,)
            ).fetchall()
            n2 = 0
            if len(rows) > MAX_ROWS_PER_CONTACT:
                drop = [r[0] for r in rows[MAX_ROWS_PER_CONTACT:]]
                c.executemany("DELETE FROM messages WHERE id=?", [(i,) for i in drop])
                n2 = len(drop)
            c.commit()
            return {"deleted_ttl": n1, "deleted_cap": n2}
        n1 = c.execute("DELETE FROM messages WHERE ts<?", (cut,)).rowcount or 0
        c.commit()
        return {"deleted_ttl": n1, "deleted_cap": 0}


def upsert_profile(contact: str, *, display_name: str = "", address_as: str = "") -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            """INSERT INTO profiles(contact, display_name, address_as, notes, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(contact) DO UPDATE SET
                 display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE profiles.display_name END,
                 address_as=CASE WHEN excluded.address_as!='' THEN excluded.address_as ELSE profiles.address_as END,
                 updated_at=excluded.updated_at
            """,
            (contact, display_name or "", address_as or "", "", now),
        )
        c.commit()


def profile(contact: str) -> dict[str, Any]:
    with _conn() as c:
        r = c.execute("SELECT * FROM profiles WHERE contact=?", (contact,)).fetchone()
    if not r:
        return {}
    return dict(r)


def insert(contact: str, sender: str, text: str, *, msg_id: str = "", msg_type: str = "text") -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO messages(contact,sender,text,ts,msg_id,msg_type) VALUES(?,?,?,?,?,?)",
            (contact, sender, text or "", time.time(), msg_id or "", msg_type or "text"),
        )
        c.commit()
    purge(contact)


def history(contact: str, n: int = 20) -> list[tuple[str, str]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT sender,text FROM messages WHERE contact=? ORDER BY id DESC LIMIT ?",
            (contact, n),
        ).fetchall()
    return [(r["sender"], r["text"]) for r in reversed(rows)]
