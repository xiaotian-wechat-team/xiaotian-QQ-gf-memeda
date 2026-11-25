"""Persona-scoped console overrides; facts: data/persona_dossier.json.

Week templates and date overrides share one contract with the calendar.
Legacy unscoped weekday files belong exclusively to xiaotian.
"""
from __future__ import annotations

import datetime as dt
import copy
import fcntl
import json
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "data" / "ui_schedule_overrides.json"
DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
# Reusable slot semantics only; dated outing IDs, probe paths and provenance
# must not turn into recurring facts. Values come from the server's schedule.
WEEK_METADATA = ("location", "scene_hints", "bucket", "leg", "photo_ok",
                 "delay", "energy", "city", "from_city", "to_city", "execution_status")


def _context(persona_id=None):
    from config import load as config_load
    from location import selected_persona
    cfg = config_load()
    selected = selected_persona(cfg)
    persona_id = persona_id or selected
    scope = cfg.get("identity_runtime") or {}
    path = OVERRIDES
    if scope and persona_id == selected:
        root = scope.get("persona_runtime_root") or scope["runtime_root"]
        path = pathlib.Path(root).expanduser() / "runtime" / "ui_schedule_overrides.json"
    elif isinstance(persona_id, str) and persona_id.startswith("companion_"):
        from persona_onboarding import runtime_root_for
        path = runtime_root_for(persona_id) / "runtime" / "ui_schedule_overrides.json"
    return persona_id, path


def _read(path):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(doc, dict):
        raise ValueError("invalid schedule document")
    return doc


def _persona(doc, persona_id):
    if "personas" in doc:
        if not isinstance(doc["personas"], dict):
            raise ValueError("invalid schedule personas")
        return doc["personas"].get(persona_id, {})
    return {"week": doc, "dates": {}} if persona_id == "xiaotian" else {}


def read(persona_id=None):
    persona_id, path = _context(persona_id)
    raw = _persona(_read(path), persona_id)
    if (not isinstance(raw, dict)
            or not isinstance(raw.get("week", {}), dict)
            or not isinstance(raw.get("dates", {}), dict)):
        raise ValueError("invalid persona schedule")
    return {"week": raw.get("week", {}), "dates": raw.get("dates", {})}


def load(persona_id=None):
    return read(persona_id)["week"]


def day_for(date, persona_id=None):
    raw = read(persona_id)
    blocks = raw["dates"].get(date.isoformat(), raw["week"].get(DAYS[date.weekday()]))
    if blocks is None:
        return None
    validate_blocks(blocks)
    return [{**block, "delay": block.get("delay", [5, 40]), "energy": block.get("energy", 0),
             "source": "persona_console"} for block in blocks]


def current_for(date, persona_id=None):
    return day_for(date, persona_id) or []


def slot_for(now, persona_id=None):
    from location import _minutes
    minute = now.hour * 60 + now.minute
    # Today's explicit slots win over a previous day's overnight continuation.
    for delta in (0, -1):
        date = now.date() + dt.timedelta(days=delta)
        offset, previous_start, previous_end = 0, 0, 0
        for slot in day_for(date, persona_id) or []:
            start, end = _minutes(slot["start"]), _minutes(slot["end"])
            if previous_end > 1440 and start + offset < previous_start:
                offset = 1440
            start, end = start + offset, end + offset
            if end < start:
                end += 1440
            previous_start, previous_end = start, end
            point = minute - delta * 1440
            if start <= point < end:
                return {**slot, "date": date.isoformat()}
    return None


def validate_blocks(blocks):
    from location import _minutes
    # Accept the four-column editor; only validated schedule fields are written.
    if not isinstance(blocks, list) or len(blocks) > 48:
        raise ValueError("invalid schedule")
    clean = []
    previous_end = 0
    previous_start = 0
    offset = 0
    for block in blocks:
        if isinstance(block, list) and len(block) == 4:
            block = dict(zip(("start", "end", "activity", "tone"), block))
        if not isinstance(block, dict):
            raise ValueError("invalid block")
        start, end = block.get("start"), block.get("end")
        a, b = _minutes(start), _minutes(end)
        if previous_end > 1440 and a + offset < previous_start:
            offset = 1440
        a, b = a + offset, b + offset
        if b < a:
            b += 1440
        activity, tone = block.get("activity"), block.get("tone", "")
        if (start == "24:00" or a >= b or a < previous_end or not isinstance(activity, str)
                or not activity.strip() or len(activity) > 120
                or not isinstance(tone, str) or len(tone) > 240):
            raise ValueError("invalid or overlapping block")
        # Only schedule fields cross this boundary; no runtime paths or commands.
        item = {"start": start, "end": end, "activity": activity.strip(), "tone": tone}
        clean.append(item)
        previous_start = a
        previous_end = b
    return clean


def _retain_metadata(clean, original, *, weekly=False):
    for index, item in enumerate(clean):
        matches = [slot for slot in original if isinstance(slot, dict)
                   and all(slot.get(key) == item[key] for key in ("start", "end", "activity"))]
        if len(matches) == 1:
            metadata = matches[0]
            if weekly:
                metadata = {key: metadata[key] for key in WEEK_METADATA if key in metadata}
            clean[index] = {**copy.deepcopy(metadata), **item}
    return clean


def save(persona_id, *, day=None, date=None, blocks=None, reset=False, reference_date=None):
    if (day is None) == (date is None) or (day is not None and day not in DAYS):
        raise ValueError("specify a weekday or date")
    if date is not None:
        date = dt.date.fromisoformat(date).isoformat()
    if reference_date is not None:
        if day is None or reset:
            raise ValueError("a reference date is only valid when saving a week template")
        reference_date = dt.date.fromisoformat(reference_date)
        if DAYS[reference_date.weekday()] != day:
            raise ValueError("reference date must match the template weekday")
    clean = [] if reset else validate_blocks(blocks)
    persona_id, path = _context(persona_id)
    reference_slots = []
    if not reset and (date is not None or reference_date is not None):
        # Reading the reference must not bind image probes or mutate runtime state.
        from location import schedule_day
        from preview_mode import preview
        with preview():
            source_date = dt.date.fromisoformat(date) if date is not None else reference_date
            reference_slots = schedule_day(persona_id, source_date).get("slots") or []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = _read(path)
        if "personas" not in doc:
            doc = {"version": 2, "personas": {"xiaotian": {"week": doc, "dates": {}}}}
        raw = doc["personas"].setdefault(persona_id, {"week": {}, "dates": {}})
        target, key = (raw.setdefault("dates", {}), date) if date else (raw.setdefault("week", {}), day)
        if reset:
            target.pop(key, None)
        else:
            # Existing templates supply their own metadata on later edits. New
            # copies use the original reference date, even after week navigation.
            # Changed time/activity pairs deliberately resolve their place anew.
            original = reference_slots if date is not None else target.get(key, reference_slots)
            _retain_metadata(clean, original, weekly=day is not None)
            target[key] = clean
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as out:
            json.dump(doc, out, ensure_ascii=False, indent=2)
            out.write("\n")
        os.replace(out.name, path)
    return clean
