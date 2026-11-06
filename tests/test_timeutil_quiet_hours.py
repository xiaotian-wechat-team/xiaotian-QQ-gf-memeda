"""TZ + quiet_hours shared helpers (Asia/Shanghai; config-driven night window)."""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import timeutil as tu
from channels import onebot_qq as ob
from channels import ilink_weixin as iw


def test_now_local_ignores_machine_tz_uses_shanghai():
    """Even if the process thinks it is UTC (or US Pacific), now_local is 东八区."""
    fixed_utc = dt.datetime(2026, 11, 5, 16, 0, 0, tzinfo=dt.UTC)  # → 00:00 Shanghai

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                # naive "machine local" pretending to be UTC wall
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    cfg = {"timezone": "Asia/Shanghai"}
    with mock.patch.object(tu.dt, "datetime", _FixedDateTime):
        n = tu.now_local(cfg)
    assert n.tzinfo is None
    # 16:00 UTC → 00:00 Asia/Shanghai next calendar day
    assert n == dt.datetime(2026, 11, 6, 0, 0, 0)


def test_now_local_respects_config_timezone_non_shanghai():
    fixed_utc = dt.datetime(2026, 11, 5, 16, 0, 0, tzinfo=dt.UTC)

    class _FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    cfg = {"timezone": "America/Los_Angeles"}
    with mock.patch.object(tu.dt, "datetime", _FixedDateTime):
        n = tu.now_local(cfg)
    expect = fixed_utc.astimezone(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None)
    assert n == expect


def test_quiet_hours_fallback_22_7_when_unset():
    cfg = {"proactive": {}}  # no quiet_hours
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 22, 0), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 23, 30), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 6, 59), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 7, 0), cfg) is False
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 12, 0), cfg) is False
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 21, 59), cfg) is False


def test_quiet_hours_config_respected():
    # Mon 2026-10-26 — weekday; quiet 01:00–08:00 (wraps? no, start<end)
    cfg = {"proactive": {"quiet_hours": ["01:00", "08:00"]}}
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 0, 30), cfg) is False
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 1, 0), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 7, 59), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 8, 0), cfg) is False
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 23, 0), cfg) is False


def test_quiet_hours_wrap_midnight_from_config():
    cfg = {"proactive": {"quiet_hours": ["22:00", "07:00"]}}
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 21, 59), cfg) is False
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 22, 0), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 23, 30), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 0, 0), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 6, 59), cfg) is True
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 7, 0), cfg) is False
    assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 12, 0), cfg) is False


def test_channels_is_night_hour_uses_quiet_hours(monkeypatch):
    cfg = {"proactive": {"quiet_hours": ["01:00", "08:00"]}}
    monkeypatch.setattr("config.load", lambda: cfg)
    # 23:00 was hard-coded night before; now must follow config → False
    assert ob._is_night_hour(dt.datetime(2026, 10, 26, 23, 0)) is False
    assert iw._is_night_hour(dt.datetime(2026, 10, 26, 23, 0)) is False
    assert ob._is_night_hour(dt.datetime(2026, 10, 26, 2, 0)) is True
    assert iw._is_night_hour(dt.datetime(2026, 10, 26, 2, 0)) is True


def test_channels_is_night_hour_fallback_without_quiet_hours(monkeypatch):
    monkeypatch.setattr("config.load", lambda: {"proactive": {}})
    assert ob._is_night_hour(dt.datetime(2026, 10, 26, 22, 15)) is True
    assert iw._is_night_hour(dt.datetime(2026, 10, 26, 6, 0)) is True
    assert ob._is_night_hour(dt.datetime(2026, 10, 26, 12, 0)) is False

def test_invalid_timezone_falls_back_to_shanghai(caplog):
    import logging

    cfg = {"timezone": "Not/A/Real/Zone"}
    with caplog.at_level(logging.WARNING):
        z = tu.zone(cfg)
        n = tu.now_local(cfg)
    assert str(z) == tu.DEFAULT_TZ_NAME
    assert n.tzinfo is None
    assert any("falling back" in rec.getMessage() for rec in caplog.records)
    assert any("Not/A/Real/Zone" in rec.getMessage() for rec in caplog.records)


def test_malformed_quiet_hours_fallback_22_7():
    samples = [
        {"proactive": {"quiet_hours": ["nope"]}},
        {"proactive": {"quiet_hours": ["foo", "bar"]}},
        {"proactive": {"quiet_hours": "22:00-07:00"}},
        {"proactive": {"quiet_hours": ["25:00", "07:00"]}},
        {"proactive": {"quiet_hours": ["22:00", "60:00"]}},
        {"proactive": {"quiet_hours": [None, None]}},
        {"proactive": {"quiet_hours": ["22", "7"]}},
        {"proactive": {"quiet_hours": []}},
    ]
    weekday = dt.datetime(2026, 10, 26, 22, 0)  # Monday
    noon = dt.datetime(2026, 10, 26, 12, 0)
    for cfg in samples:
        assert tu.quiet_hours_pair(weekday, cfg) == ("22:00", "07:00")
        assert tu.in_quiet_hours(weekday, cfg) is True
        assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 6, 59), cfg) is True
        assert tu.in_quiet_hours(dt.datetime(2026, 10, 26, 7, 0), cfg) is False
        assert tu.in_quiet_hours(noon, cfg) is False


def test_aware_datetime_converted_to_local_zone_before_hour():
    """16:00 UTC is 00:00 Asia/Shanghai — use local hour, not the UTC hour."""
    cfg = {"timezone": "Asia/Shanghai", "proactive": {"quiet_hours": ["22:00", "07:00"]}}
    utc_1600 = dt.datetime(2026, 11, 5, 16, 0, 0, tzinfo=dt.UTC)  # 00:00 CST next day
    utc_1500 = dt.datetime(2026, 11, 5, 15, 0, 0, tzinfo=dt.UTC)  # 23:00 CST
    utc_0100 = dt.datetime(2026, 11, 5, 1, 0, 0, tzinfo=dt.UTC)   # 09:00 CST
    # If .hour were read as UTC, 16:00 would be outside 22–7; locally it is inside.
    assert tu.in_quiet_hours(utc_1600, cfg) is True
    assert tu.in_quiet_hours(utc_1500, cfg) is True
    assert tu.in_quiet_hours(utc_0100, cfg) is False

    cfg2 = {"timezone": "Asia/Shanghai", "proactive": {"quiet_hours": ["01:00", "08:00"]}}
    # 16:00 UTC → 00:00 CST, outside 01–08
    assert tu.in_quiet_hours(utc_1600, cfg2) is False
    utc_1730 = dt.datetime(2026, 11, 5, 17, 30, 0, tzinfo=dt.UTC)  # 01:30 CST
    assert tu.in_quiet_hours(utc_1730, cfg2) is True
    # weekday/weekend selection must also use the converted local date
    # 2026-11-07 16:00 UTC = Sunday 00:00 CST
    weekend_cfg = {
        "timezone": "Asia/Shanghai",
        "proactive": {
            "quiet_hours": ["01:00", "08:00"],
            "quiet_hours_weekend": ["21:00", "10:00"],
        },
    }
    sunday_utc = dt.datetime(2026, 11, 7, 16, 0, 0, tzinfo=dt.UTC)  # Sun 00:00 CST
    # weekend window 21–10 includes 00:00; weekday 01–08 does not
    assert tu.quiet_hours_pair(sunday_utc, weekend_cfg) == ("21:00", "10:00")
    assert tu.in_quiet_hours(sunday_utc, weekend_cfg) is True


def test_outbox_created_uses_shared_wall_clock(monkeypatch, tmp_path):
    from channels import outbox_store as store

    fixed = dt.datetime(2026, 11, 6, 13, 30, 0)
    expected = "2026-11-06 13:30:00"
    monkeypatch.setattr(tu, "now_local", lambda: fixed)

    data = {"items": []}
    monkeypatch.setattr(ob, "require_allowed_peer", lambda *args: None)
    monkeypatch.setattr(ob, "_load_outbox", lambda: data)
    monkeypatch.setattr(ob, "_save_outbox", lambda value: None)
    ob.enqueue_outbox(peer="10001", text="hello", item_id="tz-test")
    assert data["items"][0]["created"] == expected

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(store, "LEGACY_JSON", {})
    store.save("onebot", {"items": [{"id": "tz-store", "text": "hello"}]})
    assert store.load("onebot")["items"][0]["created"] == expected
