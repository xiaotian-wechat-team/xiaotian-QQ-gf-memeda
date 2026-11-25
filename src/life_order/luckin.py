"""Luckin (瑞幸) client — preview/search allowed; createOrder gated + dry_run safe."""
from __future__ import annotations

import math

from typing import Any

from . import guard
from .pending import PendingStore
from .record import record_success
from .transport import CallableTransport, LuckinTransport

DEFAULT_DEPT_ID = 605488


def _extract_amount_yuan(preview: dict | None) -> float | None:
    if not isinstance(preview, dict):
        return None
    for key in ("payAmount", "pay_amount", "actualAmount", "totalAmount", "amount", "price"):
        if key in preview and preview[key] is not None:
            try:
                val = float(preview[key])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(val):
                return val
            # Luckin MCP often returns fen
            if val > 200 and val == int(val):
                return val / 100.0
            return val
    for key in ("data", "parsed", "structured", "result"):
        nested = preview.get(key)
        if isinstance(nested, dict):
            amount = _extract_amount_yuan(nested)
            if amount is not None:
                return amount
    return None


def _extract_order_no(raw: dict | None) -> str | None:
    """Require a provider-issued order identifier before claiming success."""
    if not isinstance(raw, dict):
        return None
    for key in ("orderNo", "order_no", "orderId", "order_id"):
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


class LuckinClient:
    def __init__(
        self,
        transport: LuckinTransport | None = None,
        *,
        cfg: dict | None = None,
        default_dept_id: int = DEFAULT_DEPT_ID,
    ):
        self.transport = transport or CallableTransport()
        self.cfg = cfg
        self.default_dept_id = int(
            (guard.load_life_order_cfg(cfg).get("luckin") or {}).get("default_dept_id", default_dept_id)
        )

    def query_shops(self, *, longitude: float, latitude: float, dept_name: str | None = None) -> Any:
        return self.transport.query_shop_list(longitude=longitude, latitude=latitude, dept_name=dept_name)

    def search(self, query: str, *, dept_id: int | None = None) -> Any:
        return self.transport.search_product(dept_id=int(dept_id or self.default_dept_id), query=query)

    def switch_product(self, **kwargs: Any) -> Any:
        kwargs.setdefault("deptId", self.default_dept_id)
        return self.transport.switch_product(**kwargs)

    def preview(self, product_list: list[dict], *, dept_id: int | None = None) -> dict:
        raw = self.transport.preview_order(dept_id=int(dept_id or self.default_dept_id), product_list=product_list)
        valid = isinstance(raw, dict) and raw.get("ok") not in (False, 0, "false", "no") and str(
            raw.get("status") or "ok"
        ).lower() not in {"error", "failed", "tool_error", "blocked", "unavailable", "empty", "rpc_error", "bad_response", "http_error"}
        amount = _extract_amount_yuan(raw) if valid else None
        ok_amt, reason = (True, "") if amount is None else guard.amount_allowed(amount, cfg=self.cfg)
        return {
            "status": "ok" if valid else "error",
            "ok": bool(valid and ok_amt),
            "amount_yuan": amount,
            "amount_blocked": (not ok_amt),
            "amount_reason": reason,
            "dept_id": int(dept_id or self.default_dept_id),
            "product_list": product_list,
            "raw": raw,
            "service": "luckin",
        }

    def place(
        self,
        *,
        contact_id: str,
        peer: str,
        product_list: list[dict],
        amount_yuan: float,
        longitude: float,
        latitude: float,
        pending_store: PendingStore,
        confirmed: bool,
        is_proactive: bool = False,
        dept_id: int | None = None,
        coupon_code_list: list[str] | None = None,
        remark: str = "",
        mem_add=None,
        now: float | None = None,
        force_dry: bool = False,
    ) -> dict:
        """Create only an atomically claimed, freshly priced confirmation."""
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
        if not confirmed:
            return {"status": "need_confirm", "ok": False, "info": "等待确认"}
        if not dry and not guard.is_enabled(self.cfg):
            return {"status": "blocked", "ok": False, "info": "life_order.enabled=false"}

        pending = pending_store.claim(contact_id, peer=peer, service="luckin", now=now)
        if pending is None:
            return {"status": "expired", "ok": False, "info": "当前会话没有可确认的瑞幸订单"}
        expected_amount = float(pending.amount_yuan)
        if not math.isclose(float(amount_yuan), expected_amount, rel_tol=0, abs_tol=0.001):
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "failed", "ok": False, "info": "确认金额与订单不一致"}
        expected_products = (pending.payload or {}).get("product_list")
        if expected_products is not None and list(expected_products) != list(product_list):
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "failed", "ok": False, "info": "确认商品与订单不一致"}

        dept = int(dept_id or self.default_dept_id)
        if dry:
            order_no = f"DRY-LK-{pending.id}"
            pending_store.mark_resolved(contact_id, accepted=True, peer=peer, service="luckin")
            result = {
                "status": "ok", "ok": True, "dry_run": True,
                "order_no": order_no, "amount_yuan": expected_amount,
                "service": "luckin", "dept_id": dept,
            }
            if mem_add is not None:
                try:
                    record_success(
                        contact_id, service="瑞幸", amount_yuan=expected_amount,
                        order_no=order_no, mem_add=mem_add, extra="dry_run",
                    )
                except Exception as exc:
                    result["memory_error"] = type(exc).__name__
            return result

        try:
            quote = self.preview(product_list, dept_id=dept)
        except Exception:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "failed", "ok": False, "info": "最终价格查询失败", "dry_run": False}
        actual_amount = quote.get("amount_yuan")
        if actual_amount is None:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "failed", "ok": False, "info": "无法核实最终金额", "dry_run": False}
        ok_amt, why = guard.amount_allowed(actual_amount, cfg=self.cfg)
        if not ok_amt:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "blocked", "ok": False, "info": why, "dry_run": False}
        if not math.isclose(float(actual_amount), expected_amount, rel_tol=0, abs_tol=0.001):
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "failed", "ok": False, "info": "价格已变化，请重新确认", "dry_run": False}

        try:
            raw = self.transport.create_order(
                deptId=dept, productList=product_list,
                longitude=longitude, latitude=latitude,
                couponCodeList=coupon_code_list or [], remark=remark or "",
            )
        except Exception as exc:
            return {
                "status": "unknown", "ok": False, "dry_run": False,
                "info": "下单结果未确认", "error": type(exc).__name__,
            }
        ok = isinstance(raw, dict) and (
            raw.get("ok") is True or raw.get("status") in ("ok", "1", 1, True, "success")
        )
        if not ok:
            pending_store.mark_resolved(contact_id, accepted=False, peer=peer, service="luckin")
            return {"status": "failed", "ok": False, "raw": raw, "dry_run": False}
        order_no = _extract_order_no(raw)
        if not order_no:
            return {
                "status": "unknown", "ok": False, "dry_run": False,
                "info": "下单结果未包含订单号", "raw": raw,
            }
        pending_store.mark_resolved(contact_id, accepted=True, peer=peer, service="luckin")
        result = {
            "status": "ok", "ok": True, "dry_run": False,
            "order_no": order_no, "amount_yuan": float(actual_amount),
            "service": "luckin", "raw": raw,
        }
        try:
            record_success(
                contact_id, service="瑞幸", amount_yuan=float(actual_amount),
                order_no=order_no, mem_add=mem_add,
            )
        except Exception as exc:
            result["memory_error"] = type(exc).__name__
        return result
