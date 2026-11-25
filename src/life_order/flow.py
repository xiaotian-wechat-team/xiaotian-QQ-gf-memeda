"""Inbound life_order turn handler. Gated by life_order.enabled (default false)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from . import guard
from .didi import DidiLinkClient
from .evidence import scrub_order_claims
from .luckin import LuckinClient
from .mcd import McdClient, USER_MUST_PAY_LINE
from .pending import PendingStore

_LUCKIN_RE = re.compile(r"瑞幸|生椰|拿铁|美式咖啡|咖啡")
_MCD_RE = re.compile(r"麦当劳|麦麦|巨无霸|板烧|麦辣|点麦")
_DIDI_RE = re.compile(r"打车|叫车|滴滴|出租车|网约车")
_ORDER_CUE_RE = re.compile(
    r"(?:帮我|给我|替我)\s*(?:点|买|要|来)?\s*(?:一杯|一份|杯|份|个)?(?=(?:瑞幸|生椰|拿铁|美式咖啡|咖啡|"
    r"麦当劳|麦麦|巨无霸|板烧|麦辣))|"
    r"(?:帮我|给我|替我)\s*(?:叫|打)\s*(?:车|滴滴|出租车|网约车)|"
    r"想吃|想喝|想来(?:点|一杯|一份)|来一杯|来一份|下单|叫车|打车|"
    r"(?:点|买)(?:一下|一杯|一份|杯|份|个)?(?=(?:瑞幸|生椰|拿铁|美式咖啡|咖啡|"
    r"麦当劳|麦麦|巨无霸|板烧|麦辣))"
)
_NEGATED_ORDER_RE = re.compile(
    r"(?:不要|不用|别|取消|算了)\s*(?:给我|帮我|替我)?\s*(?:点|叫|打|下|买)"
    r"|(?:不要|不想|不需要|不用|别)\s*(?:喝|吃|来|要|点|叫|打|下|买)?\s*(?:瑞幸|生椰|拿铁|美式咖啡|咖啡|麦当劳|麦麦|"
    r"巨无霸|板烧|麦辣|滴滴|打车|叫车|出租车|网约车)"
)
_SHORT_MCD_ORDER_RE = re.compile(r"^(?:麦当劳|麦麦)\s*(?:巨无霸|板烧|麦辣)(?:鸡翅|汉堡|套餐)?$")


def _positive_int(value: object) -> int | None:
    """Parse an exact positive integer from deployment JSON."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        number = int(value.strip())
    else:
        return None
    return number if number > 0 else None


def detect_intent(text: str) -> str | None:
    t = text or ""
    if _NEGATED_ORDER_RE.search(t):
        return None
    has_cue = bool(_ORDER_CUE_RE.search(t))
    if _DIDI_RE.search(t) and has_cue:
        return "didi"
    if _MCD_RE.search(t) and (has_cue or _SHORT_MCD_ORDER_RE.fullmatch(t.strip())):
        return "mcd"
    if _LUCKIN_RE.search(t) and has_cue:
        return "luckin"
    return None


def _configured_luckin_products(lo: dict, *, simulation: bool) -> list[dict] | None:
    luckin = lo.get("luckin") or {}
    if simulation:
        return [{"productId": 0, "skuCode": "DEMO", "amount": 1}]
    products = luckin.get("product_list")
    if not isinstance(products, list) or not products:
        return None
    clean: list[dict] = []
    for item in products:
        if not isinstance(item, dict):
            return None
        product_id = _positive_int(item.get("productId"))
        amount = _positive_int(item.get("amount", 1))
        sku = str(item.get("skuCode") or "").strip()
        if product_id is None or amount is None or not sku or sku.upper() == "DEMO":
            return None
        clean.append({**item, "productId": product_id, "skuCode": sku, "amount": amount})
    return clean


def _configured_mcd_payload(lo: dict, *, simulation: bool) -> tuple[str, list[dict], str] | None:
    mcd = lo.get("mcd") or {}
    if simulation:
        return "DEMO", [{"productCode": "DEMO", "quantity": 1}], "1"
    store_code = str(mcd.get("store_code") or "").strip()
    items = mcd.get("items")
    take_way_code = str(mcd.get("take_way_code") or "1")
    if not store_code or store_code.upper() == "DEMO" or not isinstance(items, list) or not items:
        return None
    clean: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        code = str(item.get("productCode") or "").strip()
        quantity = _positive_int(item.get("quantity", 1))
        if not code or code.upper() == "DEMO" or quantity is None:
            return None
        clean.append({**item, "productCode": code, "quantity": quantity})
    if take_way_code != "1":
        return None
    return store_code, clean, take_way_code


def peer_key(channel: str | None, peer: str | None) -> str:
    p = (peer or "").strip()
    if not p:
        return ""
    if ":" in p and (p.startswith("wechat_ilink:") or p.startswith("qq_onebot:")):
        return p
    ch = (channel or "").strip()
    return f"{ch}:{p}" if ch else p


def default_store(cfg: dict | None = None) -> PendingStore:
    from config import ROOT
    path = Path((guard.load_life_order_cfg(cfg).get("pending_path"))
                or (ROOT / "data" / "life_order_pending.json"))
    return PendingStore(path)


_DRY_STORE = PendingStore()


def _ride_coords_from_text(text: str) -> dict | None:
    match = re.search(
        r"从\s*(?P<from>.+?)\s*(?:打车|叫车|滴滴)?\s*(?:到|去)\s*(?P<to>[^，,。！？!?]+)",
        text or "",
    )
    if not match:
        return None
    names = [match.group("from").strip(), match.group("to").strip()]
    names[1] = re.sub(r"(?:打车|叫车|滴滴)$", "", names[1]).strip()
    if not all(names):
        return None
    from . import amap
    points = []
    import math
    for name in names:
        result = amap.geocode(name)
        if not result.get("ok"):
            return None
        candidates = (result.get("data") or {}).get("geocodes") or []
        location = candidates[0].get("location") if candidates and isinstance(candidates[0], dict) else None
        try:
            lng, lat = (float(v) for v in str(location or "").split(",", 1))
        except (TypeError, ValueError):
            return None
        if (not math.isfinite(lng) or not math.isfinite(lat)
                or not -180 <= lng <= 180 or not -90 <= lat <= 90):
            return None
        points.append((lat, lng))
    return {
        "from_lat": points[0][0], "from_lng": points[0][1], "from_name": names[0],
        "to_lat": points[1][0], "to_lng": points[1][1], "to_name": names[1],
    }


def handle_inbound(
    contact_id: str,
    text: str,
    *,
    peer: str | None = None,
    channel: str | None = None,
    is_proactive: bool = False,
    cfg: dict | None = None,
    store: PendingStore | None = None,
    luckin: LuckinClient | None = None,
    mcd: McdClient | None = None,
    didi: DidiLinkClient | None = None,
    mem_add: Callable[..., Any] | None = None,
    now: float | None = None,
    evidence_sink: dict | None = None,
    dry: bool = False,
) -> str | None:
    """Return reply text if life_order handled this turn; else None (fall through to LLM)."""
    if not guard.is_enabled(cfg):
        return None
    blocked, _ = guard.proactive_blocked(is_proactive)
    if blocked:
        return None

    pk = peer_key(channel, peer)
    ok_id, _ = guard.identity_allowed(contact_id, pk, cfg=cfg)
    if not ok_id:
        return None
    now = time.time() if now is None else float(now)
    if store is None:
        store = _DRY_STORE if dry else default_store(cfg)
    if luckin is None:
        from .mcp_transport import build_luckin_transport
        luckin = LuckinClient(build_luckin_transport(cfg), cfg=cfg)
    if mcd is None:
        from .mcp_transport import build_mcd_transport
        mcd = McdClient(build_mcd_transport(cfg), cfg=cfg)
    didi = didi or DidiLinkClient(cfg=cfg)

    pending = store.get(contact_id, now=now)
    if pending is not None:
        if pending.peer != pk:
            return None
        decision = guard.parse_confirm_reply(text, cfg=cfg)
        if decision == "cancel":
            store.clear(contact_id, peer=pk)
            return "行，那先不下了"
        if decision == "confirm":
            return _execute_pending(
                pending, contact_id=contact_id, peer=pk, store=store,
                luckin=luckin, mcd=mcd, mem_add=mem_add, now=now,
                cfg=cfg, evidence_sink=evidence_sink, dry=dry,
            )
        soft = guard.soft_confirm_phrase(0, cfg=cfg)
        return f"还等你拍板呢～{soft}"

    intent = detect_intent(text)
    if not intent:
        return None

    if intent == "didi":
        lo = guard.load_life_order_cfg(cfg)
        simulation = dry or guard.is_dry_run(cfg)
        demo = (lo.get("didi") or {}).get("demo_coords") if simulation else None
        if demo is None and not simulation:
            demo = _ride_coords_from_text(text)
        if not demo:
            return "告诉我从哪里出发、到哪里，地址具体一点，我再给你备链接"
        out = didi.generate_links(
            contact_id=contact_id, peer=pk, is_proactive=False, **demo,
        )
        if evidence_sink is not None:
            evidence_sink.update({"tool": "didi_link", "status": out.get("status"), "dry_run": simulation})
        if not out.get("ok"):
            return "这会儿打车链接备不了"
        reply = out.get("reply_hint") or "链接好了，你自己点进去叫车哦"
        if simulation:
            reply = "（演练）" + reply
        return scrub_order_claims(reply)

    if intent == "luckin":
        lo = guard.load_life_order_cfg(cfg)
        simulation = dry or guard.is_dry_run(cfg)
        amount_raw = (lo.get("luckin") or {}).get("demo_amount_yuan", 28)
        ok_amt, reason = guard.amount_allowed(amount_raw, cfg=cfg)
        if not ok_amt:
            return f"超预算啦，{reason}"
        amount = float(amount_raw)
        summary = "瑞幸（预览）"
        product_list = _configured_luckin_products(lo, simulation=simulation)
        if product_list is None:
            return "我还没拿到具体的瑞幸商品，先不下单哦"
        # Dry-run is local-only. A live preview is allowed only after the
        # deployment has supplied a concrete, non-DEMO product payload.
        if not simulation:
            try:
                prev = luckin.preview(product_list)
                if prev.get("amount_yuan") is not None and not prev.get("amount_blocked"):
                    amount = float(prev["amount_yuan"])
                    summary = f"瑞幸约¥{amount:g}"
                elif prev.get("amount_blocked"):
                    return f"超预算啦，{prev.get('amount_reason')}"
                elif not prev.get("ok"):
                    return "瑞幸价格暂时核不准，先不下单哦"
            except Exception:
                return "瑞幸价格暂时核不准，先不下单哦"
        soft = guard.soft_confirm_phrase(0, cfg=cfg)
        try:
            store.create(
                contact_id=contact_id, peer=pk, service="luckin",
                amount_yuan=amount, summary=summary,
                payload={"product_list": product_list, "dept_id": luckin.default_dept_id},
                soft_phrase=soft, cfg=cfg, now=now,
            )
        except ValueError:
            return "还有一单在等你确认呢～"
        return f"{summary}\n{soft}"

    if intent == "mcd":
        lo = guard.load_life_order_cfg(cfg)
        simulation = dry or guard.is_dry_run(cfg)
        amount_raw = (lo.get("mcd") or {}).get("demo_amount_yuan", 35)
        ok_amt, reason = guard.amount_allowed(amount_raw, cfg=cfg)
        if not ok_amt:
            return f"超预算啦，{reason}"
        amount = float(amount_raw)
        mcd_payload = _configured_mcd_payload(lo, simulation=simulation)
        if mcd_payload is None:
            return "我还没拿到具体的麦当劳门店和餐品，先不下单哦"
        store_code, items, take_way_code = mcd_payload
        soft = guard.soft_confirm_phrase(0, cfg=cfg)
        summary = f"麦当劳自取约¥{amount:g}"
        try:
            store.create(
                contact_id=contact_id, peer=pk, service="mcd",
                amount_yuan=amount, summary=summary,
                payload={"store_code": store_code, "items": items,
                         "take_way_code": take_way_code},
                soft_phrase=soft, cfg=cfg, now=now,
            )
        except ValueError:
            return "还有一单在等你确认呢～"
        return f"{summary}\n{USER_MUST_PAY_LINE}\n{soft}"

    return None


def _execute_pending(
    pending,
    *,
    contact_id: str,
    peer: str,
    store: PendingStore,
    luckin: LuckinClient,
    mcd: McdClient,
    mem_add,
    now: float,
    cfg: dict | None,
    evidence_sink: dict | None,
    dry: bool,
) -> str:
    soft_done = guard.soft_confirm_phrase(1, cfg=cfg)
    payload = pending.payload or {}
    if pending.service == "luckin":
        result = luckin.place(
            contact_id=contact_id, peer=peer,
            product_list=list(payload.get("product_list") or []),
            amount_yuan=pending.amount_yuan,
            longitude=float(payload.get("longitude") or 116.31),
            latitude=float(payload.get("latitude") or 39.9925),
            pending_store=store, confirmed=True, now=now, mem_add=mem_add,
            dept_id=payload.get("dept_id"), force_dry=dry,
        )
    elif pending.service == "mcd":
        result = mcd.place(
            contact_id=contact_id, peer=peer,
            store_code=str(payload.get("store_code") or "DEMO"),
            items=list(payload.get("items") or []),
            amount_yuan=pending.amount_yuan,
            take_way_code=str(payload.get("take_way_code") or "1"),
            pending_store=store, confirmed=True, now=now, mem_add=mem_add,
            force_dry=dry,
        )
    else:
        store.clear(contact_id, peer=peer)
        return "这单类型我还不会下"

    if evidence_sink is not None:
        evidence_sink.update(result)

    if not result.get("ok"):
        if result.get("status") == "unknown":
            return "下单结果还没确认，先别重复下单"
        return scrub_order_claims(f"没成，{result.get('info') or result.get('status')}")

    service = {"luckin": "瑞幸", "mcd": "麦当劳自取"}.get(pending.service, pending.service)
    prefix = "（演练）" if result.get("dry_run") else ""
    amount = result.get("amount_yuan", pending.amount_yuan)
    line = f"{soft_done}\n{prefix}{service} ¥{amount:g}，单号{result.get('order_no')}"
    if pending.service == "mcd":
        line += f"\n{USER_MUST_PAY_LINE}"
    if result.get("dry_run"):
        return scrub_order_claims(line, {"status": "ok", "dry_run": True})
    return scrub_order_claims(line, result)
