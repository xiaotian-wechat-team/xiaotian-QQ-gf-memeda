"""Shared wall-clock helpers: Asia/Shanghai (东八区) by default.

All proactive / bridge / outbox / night-window scheduling should call
``now_local()`` and ``in_quiet_hours()`` instead of bare ``datetime.now()``.

Config keys (optional):
  - ``timezone`` / ``tz`` / ``life_calendar.tz`` → IANA; default Asia/Shanghai
  - ``proactive.quiet_hours`` → [start, end] ``HH:MM`` (wraps midnight)
  - ``proactive.quiet_hours_weekend`` → weekend override
  Fallback when quiet_hours missing or malformed: ``["22:00", "07:00"]``.
  Invalid timezone names fall back to Asia/Shanghai (warning logged).
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DEFAULT_TZ_NAME = "Asia/Shanghai"
# Legacy outbox goodnight night window when config omits quiet_hours.
DEFAULT_QUIET_HOURS = ("22:00", "07:00")
DEFAULT_QUIET_HOURS_WEEKEND = ("22:00", "07:00")


def tz_name(cfg: dict | None = None) -> str:
    if cfg is None:
        try:
            from config import load
            cfg = load()
        except Exception:
            cfg = {}
    cfg = cfg or {}
    name = (
        cfg.get("timezone")
        or cfg.get("tz")
        or (cfg.get("life_calendar") or {}).get("tz")
        or DEFAULT_TZ_NAME
    )
    return str(name).strip() or DEFAULT_TZ_NAME


def zone(cfg: dict | None = None) -> ZoneInfo:
    name = tz_name(cfg)
    try:
        return ZoneInfo(name)
    except Exception as exc:
        log.warning(
            "invalid timezone %r (%s); falling back to %s",
            name,
            exc,
            DEFAULT_TZ_NAME,
        )
        return ZoneInfo(DEFAULT_TZ_NAME)


def now_local(cfg: dict | None = None) -> dt.datetime:
    """Current wall clock in configured TZ, returned as naive local datetime."""
    return dt.datetime.now(zone(cfg)).replace(tzinfo=None)


def _as_local(now: dt.datetime, cfg: dict | None = None) -> dt.datetime:
    """Convert aware datetimes to the configured local zone.

    Naive values are treated as already-local wall clocks (existing API).
    """
    if now.tzinfo is not None:
        return now.astimezone(zone(cfg))
    return now


def _hm_to_min(s: str) -> int:
    parts = str(s).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"malformed HH:MM: {s!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"out-of-range HH:MM: {s!r}")
    return h * 60 + m


def _valid_pair(pair) -> tuple[str, str] | None:
    try:
        seq = list(pair)
    except TypeError:
        return None
    if len(seq) < 2:
        return None
    try:
        a, b = str(seq[0]), str(seq[1])
        _hm_to_min(a)
        _hm_to_min(b)
        return a, b
    except Exception:
        return None


def quiet_hours_pair(
    now: dt.datetime | None = None,
    cfg: dict | None = None,
) -> tuple[str, str]:
    """Return (start, end) HH:MM for the quiet/night window.

    Reads ``proactive.quiet_hours`` (weekend: ``quiet_hours_weekend``).
    Fallback ``22:00``–``07:00`` when unset or malformed — same legacy
    window previously hard-coded in outbox goodnight drop.
    """
    if cfg is None:
        try:
            from config import load
            cfg = load()
        except Exception:
            cfg = {}
    cfg = cfg or {}
    now = _as_local(now, cfg) if now is not None else now_local(cfg)
    proactive = cfg.get("proactive") or {}
    if now.weekday() >= 5:
        pair = (
            proactive.get("quiet_hours_weekend")
            or proactive.get("quiet_hours")
            or DEFAULT_QUIET_HOURS_WEEKEND
        )
    else:
        pair = proactive.get("quiet_hours") or DEFAULT_QUIET_HOURS
    parsed = _valid_pair(pair)
    if parsed is None:
        log.warning(
            "malformed quiet_hours %r; falling back to %s–%s",
            pair,
            DEFAULT_QUIET_HOURS[0],
            DEFAULT_QUIET_HOURS[1],
        )
        return DEFAULT_QUIET_HOURS
    return parsed


def in_quiet_hours(
    now: dt.datetime | None = None,
    cfg: dict | None = None,
) -> bool:
    """True if ``now`` falls in the configured quiet/night window (wrap-midnight)."""
    if cfg is None:
        try:
            from config import load
            cfg = load()
        except Exception:
            cfg = {}
    now = _as_local(now, cfg) if now is not None else now_local(cfg)
    qs, qe = quiet_hours_pair(now, cfg)
    t = now.hour * 60 + now.minute
    a, b = _hm_to_min(qs), _hm_to_min(qe)
    if a == b:
        return True  # degenerate: whole day
    if a < b:
        return a <= t < b
    # wraps midnight, e.g. 22:00 → 07:00
    return t >= a or t < b


# Alias used by outbox goodnight drop (same predicate).
is_night_window = in_quiet_hours
