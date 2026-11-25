"""Amap (高德) Web Service — read-only helpers for life_order phase 2.

Uses REST with AMAP_KEY from the environment. Does not install or import `mcp`.
Never writes files containing the key. All network I/O goes through urlopen
so tests can monkeypatch it.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://restapi.amap.com/v3"
ENV_KEY = "AMAP_KEY"


def get_key() -> str | None:
    key = (os.environ.get(ENV_KEY) or "").strip()
    return key or None


def _get(path: str, params: dict[str, Any], *, timeout: float = 8.0) -> dict:
    key = get_key()
    if not key:
        return {"status": "no_key", "info": f"missing env {ENV_KEY}", "ok": False}
    q = dict(params)
    q["key"] = key
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "xiaotian-life-order/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"status": "http_error", "ok": False, "info": str(e.code), "raw": ""}
    except Exception as e:
        return {"status": "network_error", "ok": False, "info": type(e).__name__, "raw": ""}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"status": "bad_json", "ok": False, "info": "invalid json", "raw": body[:200]}
    # Amap uses status "1" for OK
    ok = str(data.get("status")) == "1"
    return {
        "status": "ok" if ok else "amap_error",
        "ok": ok,
        "info": data.get("info") or data.get("infocode") or "",
        "data": data,
    }


def geocode(address: str, *, city: str | None = None) -> dict:
    """Read-only: address → location."""
    params: dict[str, Any] = {"address": address}
    if city:
        params["city"] = city
    return _get("/geocode/geo", params)


def regeocode(location: str) -> dict:
    """Read-only: lng,lat → address."""
    return _get("/geocode/regeo", {"location": location})


def around(
    location: str,
    keywords: str = "",
    *,
    radius: int = 1000,
    types: str = "",
) -> dict:
    """Read-only: nearby POI search."""
    params: dict[str, Any] = {
        "location": location,
        "radius": int(radius),
        "offset": 10,
        "page": 1,
        "extensions": "base",
    }
    if keywords:
        params["keywords"] = keywords
    if types:
        params["types"] = types
    return _get("/place/around", params)


def walking_route(origin: str, destination: str) -> dict:
    """Read-only walking direction."""
    return _get("/direction/walking", {"origin": origin, "destination": destination})


def driving_route(origin: str, destination: str) -> dict:
    """Read-only driving direction (plan only; no dispatch)."""
    return _get("/direction/driving", {"origin": origin, "destination": destination})
