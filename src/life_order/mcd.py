"""McDonald's in-store pickup (orderType=1). User must pay; dry_run never create-order."""
from __future__ import annotations

import math

from typing import Any

from . import guard
from .pending import PendingStore
from .record import record_success
from .transport import CallableTransport, McdTransport

USER_MUST_PAY_LINE = "这单要你自己付款哦，我只能帮你建到店自取单"


def _fen_to_yuan(val: Any) -> float | None:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return n
    # McD calculate-price docs: amounts in 分
    if abs(n) >= 1 and n == int(n) and abs(n) > 50:
        return n / 100.0
    return n


def _extract_amount_yuan(raw: dict | None) -> float | None:
    if not isinstance(raw, dict):
        return None
    for key in ("totalAmount", "payAmount", "actualAmount", "price", "amount", "totalPrice"):
        if key in raw and raw[key] is not None:
            amount = _fen_to_yuan(raw[key])
            if amount is not None:
                return amount
    for key in ("data", "parsed", "structured", "result"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            amount = _extract_amount_yuan(nested)
            if amount is not None:
                return amount
    return None


def _extract_order_no(raw: dict | None) -> str | None:
    """Require a provider-issued order identifier before claiming success."""
    if not isinstance(raw, dict):
        return None
    for key in ("orderId", "order_id", "orderNo", "order_no"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    for key in ("parsed", "structured", "data", "result"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            value = _extract_order_no(nested)
            if value:
                return value
    return None


class McdClient:
    """Pickup-only (beType=1, orderType=1). Delivery paths are rejected."""

    def __init__(self, transport: McdTransport | None = None, *, cfg: dict | None = None):
        self.transport = transport or CallableTransport()
        self.cfg = cfg
        mcd = (guard.load_life_order_cfg(cfg).get("mcd") or {})
        self.order_type = int(mcd.get("order_type", 1))
        self.be_type = int(mcd.get("be_type", 1))
        self.user_must_pay = True

    def pay_reminder(self) -> str:
        return USER_MUST_PAY_LINE

    def query_stores(self, **kwargs: Any) -> Any:
        kwargs.setdefault("beType", self.be_type)
        return self.transport.query_nearby_stores(**kwargs)

    def query_meals(self, **kwargs: Any) -> Any:
        kwargs.setdefault("orderType", self.order_type)
        kwargs.setdefault("beType", self.be_type)
        return self.transport.query_meals(**kwargs)

    def calculate_price(self, *, store_code: str, items: list[dict], **kwargs: Any) -> dict:
        if self.order_type != 1 or self.be_type != 1:
            return {"status": "blocked", "ok": False, "info": "仅支持到店自取"}
        raw = self.transport.calculate_price(
            storeCode=store_code,
            orderType=1,
            beType=1,
            items=items,
            **kwargs,
        )
        valid = isinstance(raw, dict) and raw.get("ok") not in (False, 0, "false", "no") and str(
            raw.get("status") or "ok"
        ).lower() not in {"error", "failed", "tool_error", "blocked", "unavailable", "empty", "rpc_error", "bad_response", "http_error"}
        amount = _extract_amount_yuan(raw) if valid else None
        ok_amt, reason = (True, "") if amount is None else guard.amount_allowed(amount, cfg=self.cfg)
        return {
            "status": "ok" if valid else "error",
            "ok": bool(valid and ok_amt),
            "amount_yuan": amount,
            "amount_blocked": not ok_amt,
            "amount_reason": reason,
            "store_code": store_code,
            "items": items,
            "raw": raw,
            "service": "mcd",
            "order_type": 1,
            "be_type": 1,
            "user_must_pay": self.user_must_pay,
            "pay_reminder": self.pay_reminder(),
        }

    def place(
        self,
        *,
        contact_id: str,
        peer: str,
        store_code: str,
        items: list[dict],
        amount_yuan: float,
        take_way_code: str,
        pending_store: PendingStore,
        confirmed: bool,
        is_proactive: bool = False,
        mem_add=None,
        now: float | None = None,
        force_dry: bool = False,
        **create_kwargs: Any,
    ) -> dict:
        dry = guard.is_dry_run(self.cfg) or force_dry
        blocked, why = guard.proactive_blocked(is_proactive)
        if blocked:
            return {"status": "blocked", "ok": False, "info": why, "dry_run": dry}
        ok_id, why = guard.identity_allowed(contact_id, peer, cfg=self.cfg)
        if not ok_id:
            return {"status": "blocked", "ok": False, "info": why}
        ok_amt, why = guard.amount_allowed(amount_yuan, cfg=self.cfg)
        if not ok_amt:
            return {"status": "blocked", "ok": False, "info": why}
        if self.order_type != 1 or self.be_type != 1 or str(take_way_code) != "1":
            return {"status": "blocked", "ok": False, "info": "仅支持到店自取"}
        if not confirmed:
            return {"status": "need_confirm", "ok": False, "info": "等待确认"}
        if not dry and not guard.is_enabled(self.cfg):
            return {"status": "blocked", "ok": False, "info": "life_order.enabled=false"}

        pending = pending_store.claim(contact_id, peer=peer, service="mcd", now=now)
        if pending is None:
            return {"status": "expired", "ok": False, "info": "当前会话没有可确认的麦当劳订单"}
        expected_amount = float(pending.amount_yuan)
        expected = pending.payload or {}
        mismatch = (
            not math.isclose(float(amount_yuan), expected_amount, rel_tol=0, abs_tol=0.001)
            or ("store_code" in expected and str(expected["store_code"]) != str(store_code))
            or ("items" in expected and list(expected["items"]) != list(items))
            or ("take_way_code" in expected and str(expected["take_way_code"]) != str(take_way_code))
        )
        if mismatch:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="mcd")
            return {"status": "failed", "ok": False, "info": "确认内容与订单不一致"}

        if dry:
            order_no = f"DRY-MCD-{pending.id}"
            pending_store.mark_resolved(contact_id, accepted=True, peer=peer, service="mcd")
            result = {
                "status": "ok", "ok": True, "dry_run": True,
                "order_no": order_no, "amount_yuan": expected_amount,
                "service": "mcd", "order_type": 1,
                "user_must_pay": True, "pay_reminder": self.pay_reminder(),
            }
            if mem_add is not None:
                try:
                    record_success(
                        contact_id, service="麦当劳自取", amount_yuan=expected_amount,
                        order_no=order_no, mem_add=mem_add, extra="dry_run；需用户自行支付",
                    )
                except Exception as exc:
                    result["memory_error"] = type(exc).__name__
            return result

        try:
            quote = self.calculate_price(store_code=store_code, items=items)
        except Exception:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="mcd")
            return {"status": "failed", "ok": False, "info": "最终价格查询失败", "dry_run": False}
        actual_amount = quote.get("amount_yuan")
        if actual_amount is None:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="mcd")
            return {"status": "failed", "ok": False, "info": "无法核实最终金额", "dry_run": False}
        ok_amt, why = guard.amount_allowed(actual_amount, cfg=self.cfg)
        if not ok_amt:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="mcd")
            return {"status": "blocked", "ok": False, "info": why, "dry_run": False}
        if not math.isclose(float(actual_amount), expected_amount, rel_tol=0, abs_tol=0.001):
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="mcd")
            return {"status": "failed", "ok": False, "info": "价格已变化，请重新确认", "dry_run": False}

        try:
            raw = self.transport.create_order(
                storeCode=store_code, orderType=1, beType=1,
                takeWayCode=take_way_code, items=items, **create_kwargs,
            )
        except Exception as exc:
            return {
                "status": "unknown", "ok": False, "dry_run": False,
                "info": "下单结果未确认", "error": type(exc).__name__,
                "pay_reminder": self.pay_reminder(),
            }
        ok = isinstance(raw, dict) and (
            raw.get("ok") is True or raw.get("status") in ("ok", "success", True, 1, "1")
        )
        if not ok:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="mcd")
            return {
                "status": "failed", "ok": False, "raw": raw,
                "dry_run": False, "pay_reminder": self.pay_reminder(),
            }
        order_no = _extract_order_no(raw)
        if not order_no:
            return {
                "status": "unknown", "ok": False, "dry_run": False,
                "info": "下单结果未包含订单号", "raw": raw,
                "pay_reminder": self.pay_reminder(),
            }
        pending_store.mark_resolved(contact_id, accepted=True, peer=peer, service="mcd")
        result = {
            "status": "ok", "ok": True, "dry_run": False,
            "order_no": order_no, "amount_yuan": float(actual_amount),
            "service": "mcd", "user_must_pay": True,
            "pay_reminder": self.pay_reminder(), "raw": raw,
        }
        try:
            record_success(
                contact_id, service="麦当劳自取", amount_yuan=float(actual_amount),
                order_no=order_no, mem_add=mem_add, extra="需用户自行支付",
            )
        except Exception as exc:
            result["memory_error"] = type(exc).__name__
        return result
