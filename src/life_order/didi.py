"""DiDi / Amap ride links only — never taxi_create_order / ride request."""
from __future__ import annotations

import urllib.parse
from typing import Any, Callable

from . import guard

# Explicit denylist — place() must not exist for rides.
FORBIDDEN_ACTIONS = frozenset({
    "taxi_create_order",
    "create_order",
    "ride_request",
    "taxi_cancel_order",
})


def build_amap_nav_link(
    *,
    to_lat: float | str,
    to_lng: float | str,
    to_name: str = "目的地",
    from_lat: float | str | None = None,
    from_lng: float | str | None = None,
    mode: str = "car",
) -> str:
    """Amap URI scheme for the user to open navigation themselves."""
    params = {
        "sourceApplication": "xiaotian-life-order",
        "dlat": str(to_lat),
        "dlon": str(to_lng),
        "dname": to_name,
        "dev": "0",
        "t": "0" if mode == "car" else "2",
    }
    if from_lat is not None and from_lng is not None:
        params["slat"] = str(from_lat)
        params["slon"] = str(from_lng)
    return "amapuri://route/plan/?" + urllib.parse.urlencode(params)


def build_didi_web_link(
    *,
    from_lat: float | str,
    from_lng: float | str,
    to_lat: float | str,
    to_lng: float | str,
    from_name: str = "出发地",
    to_name: str = "目的地",
) -> str:
    """HTTPS landing page the user taps to open DiDi themselves (no server-side order)."""
    q = urllib.parse.urlencode({
        "fromlat": str(from_lat),
        "fromlng": str(from_lng),
        "tolat": str(to_lat),
        "tolng": str(to_lng),
        "fromname": from_name,
        "toname": to_name,
    })
    return f"https://v.didi.cn/p/taxi?{q}"


class DidiLinkClient:
    """Link-only client. create/cancel/ride-request are hard-refused."""

    def __init__(
        self,
        *,
        cfg: dict | None = None,
        link_transport: Callable[..., Any] | None = None,
    ):
        self.cfg = cfg
        self.link_transport = link_transport  # optional taxi_generate_ride_app_link
        mode = (guard.load_life_order_cfg(cfg).get("didi") or {}).get("mode", "link_only")
        self.mode = mode

    def generate_links(
        self,
        *,
        from_lat: float | str,
        from_lng: float | str,
        to_lat: float | str,
        to_lng: float | str,
        from_name: str = "出发地",
        to_name: str = "目的地",
        contact_id: str,
        peer: str,
        is_proactive: bool = False,
    ) -> dict:
        blocked, why = guard.proactive_blocked(is_proactive)
        if blocked:
            return {"status": "blocked", "ok": False, "info": why}
        ok_id, why = guard.identity_allowed(contact_id, peer, cfg=self.cfg)
        if not ok_id:
            return {"status": "blocked", "ok": False, "info": why}
        if self.mode != "link_only":
            return {"status": "blocked", "ok": False, "info": "didi_mode must be link_only"}

        amap = build_amap_nav_link(
            to_lat=to_lat, to_lng=to_lng, to_name=to_name,
            from_lat=from_lat, from_lng=from_lng,
        )
        didi = build_didi_web_link(
            from_lat=from_lat, from_lng=from_lng,
            to_lat=to_lat, to_lng=to_lng,
            from_name=from_name, to_name=to_name,
        )
        extra = None
        if self.link_transport is not None:
            extra = self.link_transport(
                from_lat=str(from_lat), from_lng=str(from_lng),
                to_lat=str(to_lat), to_lng=str(to_lng),
            )
        return {
            "status": "ok",
            "ok": True,
            "service": "didi",
            "mode": "link_only",
            "amap_link": amap,
            "didi_link": didi,
            "app_link": extra,
            "reply_hint": f"我帮你备好打车链接了，自己点进去叫车哦\n{didi}",
        }

    def create_order(self, *args: Any, **kwargs: Any) -> dict:
        """Hard refuse — phase 5 is link-only."""
        return {"status": "blocked", "ok": False, "info": "滴滴阶段仅出链接，禁止代下单"}

    def taxi_create_order(self, *args: Any, **kwargs: Any) -> dict:
        return self.create_order(*args, **kwargs)
