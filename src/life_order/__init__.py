"""life_order: gated real-world orders (Luckin / McD / DiDi link / Amap read-only).

Phases 1–6 (guards → amap → luckin → mcd → didi → brain hook). brain._reply is gated by enabled=false, dry_run=true by default.
Hard rules live in guard.py (¥50 cap, identity, expiry, proactive block).
"""
from .guard import (
    AMOUNT_CAP_YUAN,
    DEFAULT_CONFIRM_EXPIRY_SEC,
    ALLOWED_CONTACT_ID,
    ALLOWED_PEERS,
    amount_allowed,
    identity_allowed,
    proactive_blocked,
    parse_confirm_reply,
    soft_confirm_phrase,
    is_enabled,
    is_dry_run,
    load_life_order_cfg,
)
from .pending import PendingOrder, PendingStore
from .evidence import scrub_order_claims
from .record import record_success, diary_line
from .luckin import LuckinClient, DEFAULT_DEPT_ID
from .mcd import McdClient, USER_MUST_PAY_LINE
from .didi import DidiLinkClient
from .flow import handle_inbound, detect_intent
from .mcp_transport import build_luckin_transport, build_mcd_transport, live_mcp_enabled

__all__ = [
    "AMOUNT_CAP_YUAN",
    "DEFAULT_CONFIRM_EXPIRY_SEC",
    "ALLOWED_CONTACT_ID",
    "ALLOWED_PEERS",
    "amount_allowed",
    "identity_allowed",
    "proactive_blocked",
    "parse_confirm_reply",
    "soft_confirm_phrase",
    "is_enabled",
    "is_dry_run",
    "load_life_order_cfg",
    "PendingOrder",
    "PendingStore",
    "scrub_order_claims",
    "record_success",
    "diary_line",
    "LuckinClient",
    "DEFAULT_DEPT_ID",
    "McdClient",
    "USER_MUST_PAY_LINE",
    "DidiLinkClient",
    "handle_inbound",
    "detect_intent",
    "build_luckin_transport",
    "build_mcd_transport",
    "live_mcp_enabled",
]
