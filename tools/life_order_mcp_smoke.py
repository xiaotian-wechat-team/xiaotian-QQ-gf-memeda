#!/usr/bin/env python3
"""Read-only live smoke for Luckin / McD MCP. NEVER create/cancel.

Run with worktree venv (has `mcp` package):
  ~/xiaotian-wechat-life-order/.venv-lo/bin/python tools/life_order_mcp_smoke.py

Keys: config/life_order_mcp.env (gitignored). Never prints secrets.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from life_order.mcp_env import secret_status, LUCKIN_MCP_URL, MCD_MCP_URL
from life_order.mcp_http import (
    luckin_list_tools, mcd_list_tools, luckin_call, mcd_call, is_write_tool, WRITE_DENY,
)
from life_order.mcp_transport import LiveLuckinTransport, LiveMcdTransport

# 北京理工燕园附近
LIANGXIANG = {"longitude": 116.31, "latitude": 39.9925}
DEFAULT_DEPT = 605488


def _summarize(obj, limit=800):
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = repr(obj)
    if len(s) > limit:
        return s[:limit] + f"... <truncated len={len(s)}>"
    return s


def main():
    st = secret_status()
    print("=== life_order MCP read-only smoke ===")
    print("endpoints:", st["luckin_url"], "|", st["mcd_url"])
    print("keys: luckin_set=%s len=%s | mcd_set=%s len=%s" % (
        st["luckin_set"], st["luckin_len"], st["mcd_set"], st["mcd_len"]))
    print("write_deny_count:", len(WRITE_DENY))
    assert is_write_tool("createOrder") and is_write_tool("create-order")

    # Prove create is blocked without network
    blocked = LiveLuckinTransport(call_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))).create_order()
    print("local createOrder block:", blocked["status"])

    if not st["luckin_set"]:
        print("SKIP luckin: no key")
    else:
        print("--- Luckin tools/list ---")
        tools = luckin_list_tools()
        print("ok=%s count=%s via=%s" % (tools.get("ok"), tools.get("count"), tools.get("via") or tools.get("fallback_from")))
        names = [t.get("name") for t in (tools.get("tools") or [])]
        print("tool_names:", names[:20])
        print("--- Luckin queryShopList near BIT Liangxiang ---")
        shops = luckin_call("queryShopList", {**LIANGXIANG})
        print(_summarize(shops))
        print("--- Luckin searchProductForMcp 拿铁 @605488 ---")
        products = luckin_call("searchProductForMcp", {"deptId": DEFAULT_DEPT, "query": "拿铁"})
        print(_summarize(products))
        # previewOrder only if we can extract a product safely from search — still read/price only
        parsed = products.get("parsed") or products.get("structured") or products.get("raw")
        product_list = None
        # try common shapes
        candidates = []
        if isinstance(parsed, dict):
            for key in ("productList", "products", "list", "data"):
                v = parsed.get(key)
                if isinstance(v, list):
                    candidates = v
                    break
            if not candidates and isinstance(parsed.get("data"), dict):
                for key in ("productList", "products", "list"):
                    v = parsed["data"].get(key)
                    if isinstance(v, list):
                        candidates = v
                        break
        if isinstance(products.get("text"), str):
            try:
                j = json.loads(products["text"])
                if isinstance(j, dict):
                    for key in ("productList", "products", "list", "data"):
                        v = j.get(key) if key != "data" or not isinstance(j.get("data"), dict) else None
                        if isinstance(v, list):
                            candidates = v
                            break
            except Exception:
                pass
        for c in candidates[:3]:
            if not isinstance(c, dict):
                continue
            pid = c.get("productId") or c.get("product_id")
            sku = c.get("skuCode") or c.get("sku_code") or c.get("sku")
            if pid and sku:
                product_list = [{"productId": int(pid), "skuCode": str(sku), "amount": 1}]
                break
        if product_list:
            print("--- Luckin previewOrder (price only) ---")
            prev = luckin_call("previewOrder", {"deptId": DEFAULT_DEPT, "productList": product_list})
            print(_summarize(prev))
        else:
            print("SKIP previewOrder: could not extract productId/skuCode from search (read-only still OK)")
        # hard check we never call create
        print("assert createOrder denied:", LiveLuckinTransport().create_order()["status"])

    if not st["mcd_set"]:
        print("SKIP mcd: no key")
    else:
        print("--- McD tools/list ---")
        tools = mcd_list_tools()
        print("ok=%s count=%s" % (tools.get("ok"), tools.get("count")))
        names = [t.get("name") for t in (tools.get("tools") or [])]
        print("tool_names_sample:", names[:15], "... total", len(names))
        print("--- McD query-nearby-stores (beType=1 pickup) ---")
        # Schema often wants lat/lng as strings — try common fields
        stores = mcd_call("query-nearby-stores", {
            "beType": 1,
            "latitude": str(LIANGXIANG["latitude"]),
            "longitude": str(LIANGXIANG["longitude"]),
        })
        print(_summarize(stores))
        if not stores.get("ok"):
            # alternate arg names
            stores2 = mcd_call("query-nearby-stores", {
                "beType": 1,
                "lat": LIANGXIANG["latitude"],
                "lng": LIANGXIANG["longitude"],
            })
            print("alt args:", _summarize(stores2))
        print("assert create-order denied:", LiveMcdTransport().create_order()["status"])

    print("=== smoke done (no write tools invoked) ===")


if __name__ == "__main__":
    main()
