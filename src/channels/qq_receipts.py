"""QQ OneBot API/event receipts — short-lived local cache, NOT couple memory.

Stores API echoes + notice/meta leftovers for debug/retry inspection.
Auto-purges by TTL and row cap (from config.json qq_receipts.*). Never writes digital-life / demo_contact STATE.
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
    ROOT = Path(__file__).resolve().parents[2]

DB_PATH = ROOT / "data" / "qq_receipts.db"
TTL_SEC = 6 * 3600  # 6h default; config qq_receipts.ttl_hours wins at import
MAX_ROWS = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  echo TEXT,
  peer TEXT,
  status TEXT,
  payload TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_qr_created ON receipts(created_at);
CREATE INDEX IF NOT EXISTS ix_qr_echo ON receipts(echo);
"""


def _refresh_from_config() -> None:
    global DB_PATH, TTL_SEC, MAX_ROWS
    try:
        from config import load, ROOT as cfg_root
        cfg = load()
        qr = cfg.get("qq_receipts") or {}
        if qr.get("db"):
            p = Path(str(qr["db"])).expanduser()
            DB_PATH = p if p.is_absolute() else Path(cfg_root) / p
        if qr.get("ttl_hours") is not None:
            TTL_SEC = int(float(qr["ttl_hours"]) * 3600)
        if qr.get("max_rows") is not None:
            MAX_ROWS = int(qr["max_rows"])
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


def purge(*, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    cut = now - TTL_SEC
    with _conn() as c:
        n1 = c.execute("DELETE FROM receipts WHERE created_at<?", (cut,)).rowcount or 0
        rows = c.execute("SELECT id FROM receipts ORDER BY created_at DESC").fetchall()
        n2 = 0
        if len(rows) > MAX_ROWS:
            drop = [r[0] for r in rows[MAX_ROWS:]]
            c.executemany("DELETE FROM receipts WHERE id=?", [(i,) for i in drop])
            n2 = len(drop)
        c.commit()
    return {"deleted_ttl": n1, "deleted_cap": n2}


def record(kind: str, payload: dict, *, echo: str = "", peer: str = "", status: str = "") -> None:
    """Persist one receipt; payload truncated to keep cache small."""
    try:
        blob = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        blob = str(payload)
    if len(blob) > 4000:
        blob = blob[:4000] + "…"
    with _conn() as c:
        c.execute(
            "INSERT INTO receipts(kind,echo,peer,status,payload,created_at) VALUES(?,?,?,?,?,?)",
            (kind or "unknown", echo or "", peer or "", status or "", blob, time.time()),
        )
        c.commit()
    purge()


def stats() -> dict[str, Any]:
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        kinds = c.execute("SELECT kind, COUNT(*) FROM receipts GROUP BY kind").fetchall()
    return {"count": n, "by_kind": {r[0]: r[1] for r in kinds}, "db": str(DB_PATH), "ttl_sec": TTL_SEC}
