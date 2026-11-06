"""Confirmed sends only. Persona SSOT: data/persona_dossier.json.

The websocket receive callback only signals events / enqueues late receipts.
Workers wait and persist receipts; an unknown result is never automatically retried.
"""
from __future__ import annotations

import json
import contextlib
import queue
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

from config import ROOT


class DeliveryFailed(RuntimeError):
    delivery_status = "failed"


class DeliveryUnknown(RuntimeError):
    delivery_status = "unknown"


def checked_receipt(receipt):
    if not isinstance(receipt, dict):
        raise DeliveryUnknown(f"OneBot missing receipt: {receipt!r}")
    if receipt.get("status") == "failed" or receipt.get("retcode") not in (None, 0, "0"):
        raise DeliveryFailed(f"OneBot rejected send: {receipt!r}")
    if (receipt.get("status") != "ok" or receipt.get("retcode") not in (0, "0")
            or not isinstance(receipt.get("data"), dict)
            or receipt["data"].get("message_id") is None):
        raise DeliveryUnknown(f"OneBot send not confirmed: {receipt!r}")
    return receipt


class ReceiptJournal:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "data" / "onebot_delivery.db")

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("CREATE TABLE IF NOT EXISTS receipts (operation_id TEXT PRIMARY KEY, echo TEXT, "
                  "status TEXT, receipt TEXT, updated REAL)")
        return c

    def reserve(self, operation_id, echo):
        with contextlib.closing(self.connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT status,receipt FROM receipts WHERE operation_id=?", (operation_id,)).fetchone()
            if old and old[0] in ("pending", "unknown", "confirmed"):
                return old[0], json.loads(old[1]) if old[1] else None
            c.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?)",
                      (operation_id, echo, "pending", None, time.time()))
        return "new", None

    def record(self, operation_id, status, receipt=None):
        with contextlib.closing(self.connect()) as c, c:
            # A late confirmation must never be overwritten by a racing timeout.
            c.execute("UPDATE receipts SET status=?,receipt=COALESCE(?,receipt),updated=? "
                      "WHERE operation_id=? AND status NOT IN ('confirmed','failed')",
                      (status, json.dumps(receipt) if receipt is not None else None,
                       time.time(), operation_id))


class AckTracker:
    def __init__(self, *, timeout=15, journal=None):
        self.timeout = timeout
        self.journal = journal or ReceiptJournal()
        self._lock = threading.Lock()
        self._entries = {}
        self._late = queue.Queue()
        self._worker = None

    def _finish(self, entry):
        with self._lock:
            if entry["finished"]:
                return
            entry["finished"] = True
        receipt = entry["receipt"]
        try:
            checked_receipt(receipt)
        except (DeliveryFailed, DeliveryUnknown) as exc:
            self.journal.record(entry["id"], exc.delivery_status, receipt)
            return
        self.journal.record(entry["id"], "confirmed", receipt)
        callback = entry.get("on_confirmed")
        if callback:
            try:
                callback()
            except Exception as exc:
                # Transport success is immutable; never turn bookkeeping errors into retries.
                print(f"[onebot] confirmed send bookkeeping failed id={entry['id']}: {exc!r}",
                      file=sys.stderr, flush=True)

    def _run_late(self):
        while True:
            entry = self._late.get()
            try:
                self._finish(entry)
            except Exception as exc:
                print(f"[onebot] late receipt bookkeeping failed: {exc!r}", file=sys.stderr, flush=True)
            finally:
                self._late.task_done()

    def resolve(self, receipt):
        """No disk I/O or waiting here: safe on the websocket callback thread."""
        echo = str(receipt.get("echo") or "") if isinstance(receipt, dict) else ""
        with self._lock:
            entry = self._entries.get(echo)
            if entry is None or entry["receipt"] is not None:
                return False
            entry["receipt"] = receipt
            entry["event"].set()
            if not entry["waiting"]:
                self._late.put(entry)
                if self._worker is None:
                    self._worker = threading.Thread(target=self._run_late, daemon=True,
                                                    name="onebot-late-receipts")
                    self._worker.start()
        return True

    def send(self, ws, payload, *, operation_id=None, on_confirmed=None):
        if ws is None:
            raise DeliveryFailed("OneBot websocket is disconnected")
        echo = "xiaotian:" + uuid.uuid4().hex
        operation_id = operation_id or echo
        status, old = self.journal.reserve(operation_id, echo)
        if status == "confirmed":
            if on_confirmed:
                on_confirmed()
            return old
        if status in ("pending", "unknown"):
            raise DeliveryUnknown(f"OneBot delivery unresolved; refusing resend id={operation_id}")
        entry = {"id": operation_id, "event": threading.Event(), "receipt": None,
                 "waiting": True, "finished": False, "on_confirmed": on_confirmed}
        with self._lock:
            # Keep unknown operations for late receipts; only evict terminal entries.
            if len(self._entries) >= 1024:
                self._entries = {k: v for k, v in self._entries.items() if not v["finished"]}
            self._entries[echo] = entry
        payload = {**payload, "echo": echo}
        try:
            ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            with self._lock:
                entry["waiting"] = False
                arrived = entry["receipt"] is not None
            if arrived:
                self._finish(entry)
                return checked_receipt(entry["receipt"])
            self.journal.record(operation_id, "unknown")
            raise DeliveryUnknown(f"OneBot websocket send outcome unknown id={operation_id}") from exc
        entry["event"].wait(self.timeout)
        with self._lock:
            receipt = entry["receipt"]
            entry["waiting"] = False
        if receipt is None:
            self.journal.record(operation_id, "unknown")
            raise DeliveryUnknown(f"OneBot ACK timeout; refusing automatic retry id={operation_id}")
        self._finish(entry)
        return checked_receipt(receipt)


def send_with_confirmation(send, out, callback):
    """Carry a once-only callback to WS; checked HTTP/iLink use the returned receipt."""
    journal = None
    operation_id = (out.meta or {}).get("delivery_id")
    if operation_id and (out.meta or {}).get("persist_delivery"):
        import memory
        journal = ReceiptJournal(memory.CFG["db_path"])
        operation_id = "bridge:" + operation_id
        status, old = journal.reserve(operation_id, operation_id)
        if status == "confirmed":
            return old
        if status in ("pending", "unknown"):
            raise DeliveryUnknown(f"Delivery unresolved; refusing resend id={operation_id}")
    lock = threading.Lock()
    called = False
    def once():
        nonlocal called
        with lock:
            if called:
                return
            if journal:
                journal.record(operation_id, "confirmed")
            callback()
            called = True
    out.meta = {**(out.meta or {}), "_on_confirmed": once}
    try:
        result = send(out)
    except Exception as exc:
        if journal:
            journal.record(operation_id, getattr(exc, "delivery_status", "unknown"))
        raise
    # Existing checked HTTP/iLink transports raise on failure and return on success.
    if isinstance(result, dict) and "retcode" in result:
        checked_receipt(result)
    once()
    return result
