"""One persona-scoped location view for the console, chat and image gate.

Persona facts: data/persona_dossier.json. Unknown places stay unknown.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "persona_locations.json"
TZ = ZoneInfo("Asia/Shanghai")
EMPTY = {"nodes": {}, "activity_location_map": {}, "routes": []}


def selected_persona(cfg=None):
    if cfg is None:
        from config import load as config_load
        cfg = config_load()
    return str((cfg.get("identity_runtime") or {}).get("persona_id")
               or (cfg.get("persona") or {}).get("id") or "xiaotian")


def local_time(now=None):
    now = now or dt.datetime.now(TZ)
    return now.astimezone(TZ).replace(tzinfo=None) if now.tzinfo else now


def load(persona_id="xiaotian"):
    try:
        doc = json.loads(config_path(persona_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        doc = {}
    if not isinstance(doc, dict):
        raise ValueError("invalid location document")
    raw = doc.get(persona_id, {})
    if (not isinstance(raw, dict)
            or any(not isinstance(raw.get(key, {}), dict)
                   for key in ("nodes", "activity_location_map", "location_aliases"))
            or not isinstance(raw.get("routes", []), list)
            or any(not isinstance(route, dict) for route in raw.get("routes", []))):
        raise ValueError("invalid persona location configuration")
    # Never merge another persona's nodes, activity map or routes.
    return {**EMPTY, **raw}


def config_path(persona_id):
    from config import load as config_load
    cfg = config_load()
    scope = cfg.get("identity_runtime") or {}
    if scope and persona_id == selected_persona(cfg):
        root = scope.get("persona_runtime_root") or scope["runtime_root"]
        return pathlib.Path(root).expanduser() / "runtime" / "persona_locations.json"
    if isinstance(persona_id, str) and persona_id.startswith("companion_"):
        from persona_onboarding import runtime_root_for
        return runtime_root_for(persona_id) / "runtime" / "persona_locations.json"
    return CONFIG


def schedule_day(persona_id, date):
    """Read the selected worker's schedule, or an explicit console override.

    A console request cannot change the worker's global identity. Other
    personas have no generated calendar unless they have their own override.
    """
    from config import load as config_load
    import ui_schedule
    cfg = config_load()
    override = ui_schedule.day_for(date, persona_id)
    if override is not None:
        return {"date": date.isoformat(), "slots": override, "source": "persona_console"}
    if persona_id != selected_persona(cfg) or persona_id.startswith("companion_"):
        return {"date": date.isoformat(), "slots": [], "source": "unconfigured"}
    if cfg.get("schedule"):
        return {"date": date.isoformat(), "slots": cfg["schedule"], "source": "fixed"}
    import life_calendar
    day = life_calendar.get_day(date) or {}
    return {**day, "slots": day.get("slots") or [], "date": date.isoformat(),
            "source": day.get("source", "life_calendar")}


def _minutes(value):
    if value == "24:00":
        return 1440
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("invalid schedule time")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _key(value):
    return re.sub(r"\s+", "", str(value or ""))


def _node_for(slot, cfg):
    explicit = str(slot.get("location") or "").strip()
    if explicit:
        if explicit in {"未知", "待定", "位置待确认", "未确定", "不详"}:
            return None
        alias = cfg.get("location_aliases", {}).get(explicit)
        if alias in cfg["nodes"]:
            return alias
        return next((key for key, label in cfg["nodes"].items()
                     if explicit in (key, label)), explicit)
    # A recorded city is more authoritative than a habitual campus activity.
    # E.g. sleeping in Tianjin must not resolve to the Beijing dormitory.
    if isinstance(slot.get("city"), str) and slot["city"].strip():
        return slot["city"].strip()
    # Scene hints describe confirmed places more precisely than an activity alias.
    hints = slot.get("scene_hints") or []
    hints = [hints] if isinstance(hints, str) else hints
    matches = {key for key, label in cfg["nodes"].items() for hint in hints
               if str(label) and str(label) in str(hint)}
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        return None
    mapping = {_key(activity): node for activity, node in cfg["activity_location_map"].items()}
    return mapping.get(_key(slot.get("activity")))


def _route_between(cfg, source, target):
    if not source or not target or source == target:
        return None
    for route in cfg["routes"]:
        if (route.get("from") in (source, cfg["nodes"].get(source, source))
                and route.get("to") in (target, cfg["nodes"].get(target, target))):
            return route
    return None


def timeline(persona_id="xiaotian", date=None, blocks=None):
    """Expand the same schedule into nodes and feasible static commutes."""
    date = date or local_time().date()
    cfg = load(persona_id)
    day = schedule_day(persona_id, date) if blocks is None else {"slots": blocks, "source": "provided"}
    nodes, errors = [], []
    offset = 0
    for item in day.get("slots") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 4:
            item = dict(zip(("start", "end", "activity", "tone"), item))
        if not isinstance(item, dict):
            errors.append("invalid_slot")
            continue
        try:
            start, end = _minutes(item.get("start")), _minutes(item.get("end"))
            if start == 1440 or start == end:
                raise ValueError("empty slot")
        except ValueError:
            errors.append("invalid_time")
            continue
        # Only a slot that explicitly crosses midnight can advance the day.
        if nodes and nodes[-1]["end_minute"] > 1440 and start + offset < nodes[-1]["start_minute"]:
            offset = 1440
        start += offset
        end += offset
        if end < start:
            end += 1440
        from schedule_execution import effective
        slot = effective(item)
        node = _node_for(slot, cfg)
        transit = (slot.get("leg") in ("outbound", "return", "transit")
                   or slot.get("bucket") in ("travel", "transit"))
        place_context = {key: str(slot[key]).strip() for key in ("city", "from_city", "to_city")
                         if isinstance(slot.get(key), str) and slot[key].strip()}
        nodes.append({
            "type": "node", "node_id": node,
            "location": cfg["nodes"].get(node, node) if node else "位置待确认",
            "activity": str(slot.get("activity") or ""), "tone": slot.get("tone"),
            "enter": item["start"], "exit": item["end"],
            "start_minute": start, "end_minute": end,
            "status": "in_transit" if transit else ("stationary" if node else "unknown"),
            "known": bool(node), "photo_ok": bool(node) and not transit and slot.get("photo_ok") is True,
            **place_context,
        })
    if blocks is None and (not nodes or nodes[0]["start_minute"] > 0):
        # Carry only explicitly recorded overnight slots; never synthesize a trip.
        previous_date = date - dt.timedelta(days=1)
        previous = timeline(persona_id, previous_date, blocks=schedule_day(persona_id, previous_date).get("slots", []))
        boundary = nodes[0]["start_minute"] if nodes else 1440
        carry = [{**node, "start_minute": node["start_minute"] - 1440,
                  "end_minute": node["end_minute"] - 1440, "enter": "昨日 " + node["enter"]}
                 for node in previous["nodes"] if 1440 < node["end_minute"]
                 and node["end_minute"] - 1440 <= boundary]
        if not previous["errors"]:
            nodes = carry + nodes
    transfers = []
    for previous, following in zip(nodes, nodes[1:]):
        gap_start, arrive = previous["end_minute"], following["start_minute"]
        gap = arrive - gap_start
        cities_differ = (previous.get("city") and following.get("city")
                         and _key(previous["city"]).removesuffix("市") != _key(following["city"]).removesuffix("市"))
        same_location = (bool(previous["node_id"])
                         and previous["node_id"] == following["node_id"]
                         and not cities_differ
                         and previous["status"] == following["status"] == "stationary")
        # Named campus stops/routes are scoped to the configured city. A hotel
        # dorm or classroom in another city cannot borrow a Beijing walking time.
        outside_scope = cfg.get("city") and any(
            node["node_id"] in cfg["nodes"] and node.get("city")
            and _key(node["city"]).removesuffix("市") != _key(cfg["city"]).removesuffix("市")
            for node in (previous, following))
        route = None if same_location or outside_scope else _route_between(cfg, previous["node_id"], following["node_id"])
        duration = 0 if same_location else (route or {}).get("minutes")
        known = same_location or (type(duration) is int and 0 < duration <= gap)
        depart = arrive - duration if known else gap_start
        if gap < 0:
            errors.append("overlapping_slots")
        transfers.append({
            "type": "transfer", "from": previous["location"], "to": following["location"],
            "depart": f"{depart // 60 % 24:02d}:{depart % 60:02d}", "arrive": following["enter"],
            "start_minute": depart, "end_minute": arrive,
            "minutes": duration if known else max(0, gap), "gap_minutes": max(0, gap),
            "mode": "无需通勤" if same_location else (route or {}).get("mode", "路线待匹配"),
            "route_minutes": duration, "same_location": same_location,
            "known": known, "photo_ok": False,
        })
    return {"persona_id": persona_id, "date": date.isoformat(), "nodes": nodes,
            "transfers": transfers, "errors": errors, "source": day.get("source")}


def current(persona_id="xiaotian", now=None, *, view=None):
    now = local_time(now)
    if view is None:
        # Shared consumers use the same schedule-versioned plan, without starting
        # quota-consuming work on a chat/image-gate read.
        from location_agent import view as agent_view
        view, _ = agent_view(persona_id, now.date(), generate_plan=False)
    minute = now.hour * 60 + now.minute
    unknown = {"status": "unknown", "photo_ok": False, "source": view.get("source")}
    if view.get("errors"):
        return {**unknown, "reason": "invalid_schedule"}
    matches = [node for node in view["nodes"] if node["start_minute"] <= minute < node["end_minute"]]
    if len(matches) == 1:
        node = matches[0]
        if node["status"] == "unknown":
            return {**unknown, "activity": node["activity"]}
        result = {**node, "source": view.get("source")}
        if node["status"] == "in_transit":
            result["eta"] = node["exit"]
        return result
    for transfer in view["transfers"]:
        if (not transfer["same_location"] and transfer["known"]
                and transfer["start_minute"] <= minute < transfer["end_minute"]):
            return {**transfer, "status": "in_transit", "location": f"前往{transfer['to']}途中",
                    "eta": transfer["arrive"], "source": "static_route"}
    return unknown


def prompt_line(value):
    if value.get("status") == "in_transit":
        return f"位置：{value.get('location') or '路上'}，预计{value.get('eta')}到达；尚未到达，路上暂不发照片。"
    if value.get("status") == "stationary":
        place = value['location']
        if value.get("city") and value['city'] not in place:
            place = f"{value['city']} · {place}"
        suffix = "（Agent安排的计划地点，不代表定位实测）" if value.get("assignment") == "agent" else ""
        return f"位置：{place}{suffix}。"
    return "位置待确认，不编造已到达的地点。"
