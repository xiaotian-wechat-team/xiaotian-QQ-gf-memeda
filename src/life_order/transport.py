"""Injectable MCP-shaped transports. Real createOrder must never run under dry_run."""
from __future__ import annotations

from typing import Any, Callable, Protocol


class LuckinTransport(Protocol):
    def query_shop_list(self, *, longitude: float, latitude: float, dept_name: str | None = None) -> Any: ...
    def search_product(self, *, dept_id: int, query: str) -> Any: ...
    def switch_product(self, **kwargs: Any) -> Any: ...
    def preview_order(self, *, dept_id: int, product_list: list[dict]) -> Any: ...
    def create_order(self, **kwargs: Any) -> Any: ...


class McdTransport(Protocol):
    def query_nearby_stores(self, **kwargs: Any) -> Any: ...
    def query_meals(self, **kwargs: Any) -> Any: ...
    def calculate_price(self, **kwargs: Any) -> Any: ...
    def create_order(self, **kwargs: Any) -> Any: ...


class CallableTransport:
    """Map method names to callables; missing → status=unavailable."""

    def __init__(self, handlers: dict[str, Callable[..., Any]] | None = None):
        self.handlers = dict(handlers or {})
        self.calls: list[tuple[str, dict]] = []

    def _call(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, dict(kwargs)))
        fn = self.handlers.get(name)
        if fn is None:
            return {"status": "unavailable", "ok": False, "info": f"no handler for {name}"}
        return fn(**kwargs)

    # Luckin
    def query_shop_list(self, *, longitude: float, latitude: float, dept_name: str | None = None):
        return self._call("query_shop_list", longitude=longitude, latitude=latitude, dept_name=dept_name)

    def search_product(self, *, dept_id: int, query: str):
        return self._call("search_product", dept_id=dept_id, query=query)

    def switch_product(self, **kwargs: Any):
        return self._call("switch_product", **kwargs)

    def preview_order(self, *, dept_id: int, product_list: list[dict]):
        return self._call("preview_order", dept_id=dept_id, product_list=product_list)

    def create_order(self, **kwargs: Any):
        return self._call("create_order", **kwargs)

    # McD
    def query_nearby_stores(self, **kwargs: Any):
        return self._call("query_nearby_stores", **kwargs)

    def query_meals(self, **kwargs: Any):
        return self._call("query_meals", **kwargs)

    def calculate_price(self, **kwargs: Any):
        return self._call("calculate_price", **kwargs)
