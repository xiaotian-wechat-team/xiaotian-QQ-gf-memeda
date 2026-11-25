"""Pending life_order confirmation state.

Mirrors schedule_fidelity.pending_ask field style (pending / asked_at / question),
but stays in a dedicated store so STATE.json of the live bot is never touched.
In-memory by default; optional JSON path for tests / later persistence.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .guard import confirm_expiry_sec, load_life_order_cfg


def _now_ts() -> float:
    return time.time()


@dataclass
class PendingOrder:
    """One awaiting-confirmation order draft."""
    id: str
    contact_id: str
    peer: str
    service: str  # luckin | mcd | didi | other
    amount_yuan: float
    summary: str = ""
    payload: dict = field(default_factory=dict)
    created_at: float = 0.0
    expiry_sec: int = 180
    soft_phrase: str = ""
    # schedule_fidelity-like flags
    pending_ask: bool = True
    pending_question: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "PendingOrder":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        data = {k: v for k, v in (raw or {}).items() if k in known}
        if "payload" in data and not isinstance(data["payload"], dict):
            data["payload"] = {}
        return cls(**data)

    def is_expired(self, now: float | None = None) -> bool:
        # Confirmation TTL only applies to drafts. A claimed order may already
        # exist at the provider and must survive until explicitly reconciled.
        if not self.pending_ask:
            return False
        now = _now_ts() if now is None else float(now)
        return now >= float(self.created_at) + int(self.expiry_sec)


class PendingStore:
    """Contact-scoped pending order with cross-process atomic confirmation."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._by_cid: dict[str, PendingOrder] = {}
        self._mutex = threading.RLock()
        if self.path:
            self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            self._by_cid = {}
            return
        raw = json.loads(self.path.read_text())
        items = raw.get("pending") if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            raise ValueError("invalid life_order pending store")
        loaded = {}
        for cid, blob in items.items():
            if not isinstance(blob, dict):
                raise ValueError("invalid life_order pending entry")
            loaded[str(cid)] = PendingOrder.from_dict(blob)
        self._by_cid = loaded

    @contextmanager
    def _locked(self):
        with self._mutex:
            if not self.path:
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = Path(str(self.path) + ".lock")
            with lock_path.open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    self._load()
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self) -> None:
        if not self.path:
            return
        payload = {"pending": {cid: p.to_dict() for cid, p in self._by_cid.items()}}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".life-order-pending-")
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(payload, out, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def create(
        self,
        *,
        contact_id: str,
        peer: str,
        service: str,
        amount_yuan: float,
        summary: str = "",
        payload: dict | None = None,
        soft_phrase: str = "",
        cfg: dict | None = None,
        now: float | None = None,
        order_id: str | None = None,
    ) -> PendingOrder:
        now = _now_ts() if now is None else float(now)
        expiry = confirm_expiry_sec(cfg)
        question = (summary or f"{service} ¥{amount_yuan:g}")[:80]
        pending = PendingOrder(
            id=order_id or uuid.uuid4().hex[:12],
            contact_id=str(contact_id),
            peer=str(peer),
            service=str(service),
            amount_yuan=float(amount_yuan),
            summary=summary or "",
            payload=dict(payload or {}),
            created_at=now,
            expiry_sec=expiry,
            soft_phrase=soft_phrase or "",
            pending_ask=True,
            pending_question=question,
        )
        with self._locked():
            existing = self._by_cid.get(str(contact_id))
            if existing and not existing.is_expired(now):
                raise ValueError("an active or processing pending order already exists")
            self._by_cid[str(contact_id)] = pending
            self._save()
        return pending

    def get(self, contact_id: str, *, now: float | None = None) -> PendingOrder | None:
        with self._locked():
            p = self._by_cid.get(str(contact_id))
            if not p:
                return None
            if p.is_expired(now):
                self._by_cid.pop(str(contact_id), None)
                self._save()
                return None
            if not p.pending_ask:
                return None
            return p

    def claim(
        self,
        contact_id: str,
        *,
        peer: str,
        service: str,
        now: float | None = None,
    ) -> PendingOrder | None:
        with self._locked():
            p = self._by_cid.get(str(contact_id))
            if not p or p.is_expired(now) or not p.pending_ask:
                return None
            if p.peer != str(peer) or p.service != str(service):
                return None
            p.pending_ask = False
            p.pending_question = ""
            self._save()
            return p

    def clear(self, contact_id: str, *, peer: str | None = None) -> bool:
        with self._locked():
            cid = str(contact_id)
            p = self._by_cid.get(cid)
            if p and peer is not None and p.peer != str(peer):
                return False
            removed = self._by_cid.pop(cid, None) is not None
            if removed:
                self._save()
            return removed

    def mark_resolved(
        self,
        contact_id: str,
        *,
        accepted: bool,
        peer: str | None = None,
        service: str | None = None,
    ) -> PendingOrder | None:
        with self._locked():
            p = self._by_cid.get(str(contact_id))
            if not p:
                return None
            if peer is not None and p.peer != str(peer):
                return None
            if service is not None and p.service != str(service):
                return None
            p.pending_ask = False
            p.pending_question = ""
            self._by_cid.pop(str(contact_id), None)
            self._save()
            return p

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            return {cid: p.to_dict() for cid, p in self._by_cid.items()}
