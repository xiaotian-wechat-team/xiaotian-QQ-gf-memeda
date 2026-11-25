"""Live MCP transports for Luckin / McD — read/preview only; writes denied in mcp_http."""
from __future__ import annotations

from typing import Any, Callable

from . import guard
from .mcp_env import LUCKIN_MCP_URL, MCD_MCP_URL, luckin_token, mcd_token, secret_status
from .mcp_http import luckin_call, mcd_call, is_write_tool
from .transport import CallableTransport


def live_mcp_enabled(cfg: dict | None = None) -> bool:
    """Config flag: life_order.mcp.enabled (default False). Independent of life_order.enabled."""
    lo = guard.load_life_order_cfg(cfg)
    mcp = lo.get("mcp") or {}
    return bool(mcp.get("enabled"))


class LiveLuckinTransport:
    """Maps LuckinTransport methods → MCP tools. create_order always blocked here too."""

    def __init__(self, call_fn: Callable[..., dict] | None = None):
        self._call = call_fn or luckin_call
        self.calls: list[tuple[str, dict]] = []

    def _invoke(self, tool: str, **kwargs: Any) -> Any:
        self.calls.append((tool, dict(kwargs)))
        if is_write_tool(tool):
            return {"ok": False, "status": "blocked", "info": f"write tool denied: {tool}"}
        return self._call(tool, kwargs)

    def query_shop_list(self, *, longitude: float, latitude: float, dept_name: str | None = None):
        args: dict[str, Any] = {"longitude": float(longitude), "latitude": float(latitude)}
        if dept_name:
            args["deptName"] = dept_name
        return self._invoke("queryShopList", **args)

    def search_product(self, *, dept_id: int, query: str):
        return self._invoke("searchProductForMcp", deptId=int(dept_id), query=query)

    def switch_product(self, **kwargs: Any):
        # remap common snake → MCP camel if needed
        args = dict(kwargs)
        if "dept_id" in args and "deptId" not in args:
            args["deptId"] = args.pop("dept_id")
        if "product_id" in args and "productId" not in args:
            args["productId"] = args.pop("product_id")
        return self._invoke("switchProduct", **args)

    def preview_order(self, *, dept_id: int, product_list: list[dict]):
        return self._invoke("previewOrder", deptId=int(dept_id), productList=product_list)

    def create_order(self, **kwargs: Any):
        self.calls.append(("createOrder", dict(kwargs)))
        return {"ok": False, "status": "blocked", "info": "write tool denied: createOrder"}


class LiveMcdTransport:
    def __init__(self, call_fn: Callable[..., dict] | None = None):
        self._call = call_fn or mcd_call
        self.calls: list[tuple[str, dict]] = []

    def _invoke(self, tool: str, **kwargs: Any) -> Any:
        self.calls.append((tool, dict(kwargs)))
        if is_write_tool(tool):
            return {"ok": False, "status": "blocked", "info": f"write tool denied: {tool}"}
        return self._call(tool, kwargs)

    def query_nearby_stores(self, **kwargs: Any):
        return self._invoke("query-nearby-stores", **kwargs)

    def query_meals(self, **kwargs: Any):
        return self._invoke("query-meals", **kwargs)

    def calculate_price(self, **kwargs: Any):
        return self._invoke("calculate-price", **kwargs)

    def create_order(self, **kwargs: Any):
        self.calls.append(("create-order", dict(kwargs)))
        return {"ok": False, "status": "blocked", "info": "write tool denied: create-order"}


def build_luckin_transport(cfg: dict | None = None):
    """Return live MCP transport when mcp.enabled, else empty CallableTransport."""
    if live_mcp_enabled(cfg) and luckin_token():
        return LiveLuckinTransport()
    return CallableTransport()


def build_mcd_transport(cfg: dict | None = None):
    if live_mcp_enabled(cfg) and mcd_token():
        return LiveMcdTransport()
    return CallableTransport()


def endpoints_info() -> dict:
    st = secret_status()
    return {
        **st,
        "mcp_flag_default": False,
        "write_deny_sample": sorted(list(
            {"createOrder", "cancelOrder", "create-order", "cancel-order"}
        )),
    }
