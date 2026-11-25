"""Link one MacBook relay job to one proactive send. Persona: data/persona_dossier.json.

Only pending files created with resume_protocol 1 and a send_intent can be
waited on or delivered. Older files are not read for delivery and are not
rewritten. This module does not send, does not generate, and does not invent
schedule or appearance facts.
"""
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import threading

PROTOCOL = 1
OPEN = {"pending", "relayed_pending", "relay_failed"}
CLOSED = {"sent", "handed_off", "delivery_unknown", "blocked_stale", "send_claimed"}
_SLOT_KEYS = (
    "start", "end", "activity", "location", "scene_hints", "scene_id",
    "photo_ok", "bucket", "execution_status", "leg", "city", "lat", "lon",
)
_TRANSITIONS = {
    "pending": {"blocked_stale"},
    "relayed_pending": {"blocked_stale"},
    "relay_failed": {"blocked_stale"},
    "done": {"send_claimed", "blocked_stale"},
    "send_claimed": {"sent", "handed_off", "delivery_unknown", "blocked_stale", "done"},
    "delivery_unknown": {"sent", "handed_off", "blocked_stale"},
    "handed_off": set(),
    "sent": set(),
    "blocked_stale": set(),
}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def slot_signature(slot):
    slot = slot or {}
    return {k: slot[k] for k in _SLOT_KEYS if k in slot}


def make_intent(*, contact, beat_id, done_key, date, slot, scene, queue_id, caption):
    """Stable opportunity id. Random shot wording is not part of the id."""
    ident = {
        "contact": str(contact or ""),
        "source": "proactive",
        "beat_id": str(beat_id or ""),
        "done_key": str(done_key or ""),
        "date": str(date or ""),
        "slot": slot_signature(slot),
    }
    intent_id = hashlib.sha256(
        json.dumps(ident, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    return {
        "resume_protocol": PROTOCOL,
        "send_intent": {**ident, "intent_id": intent_id, "scene": str(scene or ""),
                        "queue_id": str(queue_id or ""), "caption": str(caption or "")},
    }


def protocol_fields(resume):
    """Fields to store on a new pending file. None leaves the file legacy."""
    if not isinstance(resume, dict) or resume.get("resume_protocol") != PROTOCOL:
        return None
    intent = resume.get("send_intent")
    if not isinstance(intent, dict):
        return None
    need = ("intent_id", "contact", "source", "done_key", "date")
    if any(not str(intent.get(k) or "").strip() for k in need):
        return None
    if intent.get("source") != "proactive" or not isinstance(intent.get("slot"), dict):
        return None
    return {
        "resume_protocol": PROTOCOL,
        "send_intent": {
            "intent_id": str(intent["intent_id"]),
            "contact": str(intent["contact"]),
            "source": "proactive",
            "beat_id": str(intent.get("beat_id") or ""),
            "done_key": str(intent["done_key"]),
            "date": str(intent["date"]),
            "slot": intent["slot"],
            "scene": str(intent.get("scene") or ""),
            "queue_id": str(intent.get("queue_id") or ""),
            "caption": str(intent.get("caption") or ""),
        },
    }


def assess(data, intent_id):
    if not isinstance(data, dict) or data.get("resume_protocol") != PROTOCOL:
        return "legacy"
    if str((data.get("send_intent") or {}).get("intent_id") or "") != str(intent_id or ""):
        return "mismatch"
    status = data.get("status")
    if status in OPEN:
        return "open"
    if status == "done":
        return "ready" if _result_ok(data.get("result_path")) else "broken"
    if status in {"failed", "error"}:
        return "failed"
    if status in CLOSED:
        return "closed"
    return "unknown"


def find_reusable(directory, intent_id):
    """Oldest protocol job for this intent. Legacy files are skipped and not opened for write."""
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        return None
    ready = opened = None
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        kind = assess(data, intent_id)
        if kind == "ready" and ready is None:
            ready = (path, data)
        elif kind == "open" and opened is None:
            opened = (path, data)
    return ready or opened


def claim(path, intent_id):
    def mutate(data):
        if data.get("status") != "done" or not _result_ok(data.get("result_path")):
            return None
        data = dict(data)
        data["status"] = "send_claimed"
        data["claimed_at"] = _now()
        return data
    return _update(path, intent_id, mutate) is not None


def block(path, intent_id, reason):
    return transition(path, intent_id, "blocked_stale", reason=reason)


def rollback_unsent(path, intent_id):
    """Return a claimed job to done only when this process has not been told it sent."""
    return transition(path, intent_id, "done", reason="unsent rollback")


def transition(path, intent_id, target, reason=None):
    def mutate(data):
        if target not in _TRANSITIONS.get(data.get("status"), ()):
            return None
        data = dict(data)
        data["status"] = target
        if reason:
            data["delivery_note"] = str(reason)[:300]
        data["delivery_updated_at"] = _now()
        return data
    return _update(path, intent_id, mutate) is not None


def delivery_target(queue_status):
    return {
        "unknown": "delivery_unknown",
        "partial": "delivery_unknown",
        "hold": "handed_off",
        "sent": "sent",
        "dropped": "blocked_stale",
        "expired": "blocked_stale",
        "image_isolated": "blocked_stale",
    }.get(queue_status)


def _result_ok(path):
    if not path:
        return False
    file = pathlib.Path(path)
    try:
        return file.is_file() and file.stat().st_size > 0
    except OSError:
        return False


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _thread_lock(key):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _update(path, intent_id, mutator):
    """Rewrite a protocol job only. A legacy file returns None and is not modified."""
    path = pathlib.Path(path)
    if not path.is_file():
        return None
    key = str(path.resolve())
    with _thread_lock(key):
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    return None
                if data.get("resume_protocol") != PROTOCOL:
                    return None
                if str((data.get("send_intent") or {}).get("intent_id") or "") != str(intent_id or ""):
                    return None
                updated = mutator(dict(data))
                if updated is None:
                    return None
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=1))
                os.replace(tmp, path)
                return updated
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
