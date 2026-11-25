"""Streamable-HTTP MCP client for Luckin / McD.

WRITE tools are hard-denied in this module (create/cancel/order placement).
Uses the `mcp` Python package when available; falls back to raw JSON-RPC+SSE.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .mcp_env import LUCKIN_MCP_URL, MCD_MCP_URL, luckin_token, mcd_token

# Absolute deny — never call these from life_order live transport.
WRITE_DENY = frozenset({
    "createOrder", "cancelOrder",
    "create-order", "cancel-order",
    "mall-create-order", "party-order-create",
    "delivery-create-address",
    "auto-bind-coupons", "draw-lottery",
    "taxi_create_order", "taxi_cancel_order",
})
READ_ALLOW = frozenset({
    "queryShopList", "searchProductForMcp", "previewOrder",
    "queryProductDetailInfo", "queryOrderDetailInfo", "query-order",
    "query-nearby-stores", "query-meals", "calculate-price",
    "order-list", "mall-order-list", "mall-order-detail",
})

_WRITE_NAME_RE = re.compile(
    r"(^create)|(^cancel)|(create.?order)|(cancel.?order)|(^draw-)|(^auto-bind)",
    re.I,
)


def is_write_tool(name: str) -> bool:
    n = (name or "").strip()
    if n in WRITE_DENY:
        return True
    # previewOrder / calculate-price / query-* stay allowed
    if n in ("previewOrder", "calculate-price", "queryProductDetailInfo",
             "queryOrderDetailInfo", "query-order", "order-list", "mall-order-list",
             "mall-order-detail"):
        # query order is read-only; create is denied above
        if n.startswith("query") or n in ("previewOrder", "calculate-price",
                                          "order-list", "mall-order-list", "mall-order-detail"):
            return False
    if _WRITE_NAME_RE.search(n) and "preview" not in n.lower() and not n.lower().startswith("query"):
        # conservative: block create*/cancel*/draw*/auto-bind*
        if n.lower().startswith(("create", "cancel", "draw", "auto-bind")):
            return True
    return n in WRITE_DENY


def _parse_sse_or_json(body: str) -> Any:
    body = (body or "").strip()
    if not body:
        return None
    if body.startswith("{") or body.startswith("["):
        return json.loads(body)
    # SSE: collect last data: JSON payload
    last = None
    for line in body.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                last = json.loads(raw)
            except json.JSONDecodeError:
                continue
    return last


def _result_to_dict(result: Any) -> dict:
    if result is None:
        return {"status": "empty", "ok": False}
    if isinstance(result, dict):
        out = dict(result)
        raw = result
    else:
        out = {"ok": True, "status": "ok"}
        try:
            if hasattr(result, "model_dump"):
                out["raw"] = result.model_dump(mode="json")
            elif hasattr(result, "dict"):
                out["raw"] = result.dict()
            else:
                out["raw"] = str(result)
        except Exception:
            out["raw"] = repr(result)
        raw = out.get("raw")
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text") or "")
            if texts:
                joined = "\n".join(texts)
                out["text"] = joined
                try:
                    out["parsed"] = json.loads(joined)
                except Exception:
                    pass
        if raw.get("structuredContent") is not None:
            out["structured"] = raw["structuredContent"]
        if raw.get("isError"):
            out["ok"] = False
            out["status"] = "tool_error"
    if "ok" not in out:
        out["ok"] = str(out.get("status") or "ok").lower() not in {
            "error", "failed", "tool_error", "blocked", "unavailable", "empty",
        }
    out.setdefault("status", "ok" if out["ok"] else "tool_error")
    return out


async def _call_via_sdk(url: str, token: str, method: str, name: str | None = None,
                        arguments: dict | None = None) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    headers = {"Authorization": f"Bearer {token}"}
    http = create_mcp_http_client(headers=headers)
    async with http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                if method == "tools/list":
                    res = await session.list_tools()
                    tools = []
                    for t in getattr(res, "tools", []) or []:
                        tools.append({
                            "name": getattr(t, "name", None),
                            "description": (getattr(t, "description", None) or "")[:160],
                        })
                    return {"ok": True, "status": "ok", "tools": tools, "count": len(tools)}
                if method == "tools/call":
                    assert name
                    res = await session.call_tool(name, arguments or {})
                    return _result_to_dict(res)
                return {"ok": False, "status": "unsupported", "info": method}


async def _call_via_raw(url: str, token: str, method: str, name: str | None = None,
                        arguments: dict | None = None) -> dict:
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if method == "tools/list":
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    elif method == "tools/call":
        payload = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    else:
        return {"ok": False, "status": "unsupported", "info": method}

    # Some servers want initialize first with session id — try direct tools call first.
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # initialize handshake (best-effort)
        init = {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "xiaotian-life-order", "version": "0.1"},
            },
        }
        session_headers = dict(headers)
        try:
            r0 = await client.post(url, headers=headers, json=init)
            sid = r0.headers.get("mcp-session-id") or r0.headers.get("Mcp-Session-Id")
            if sid:
                session_headers["mcp-session-id"] = sid
            # notifications/initialized
            await client.post(
                url, headers=session_headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
        except Exception:
            pass
        r = await client.post(url, headers=session_headers, json=payload)
        body = r.text
        parsed = _parse_sse_or_json(body)
        if r.status_code >= 400:
            return {
                "ok": False,
                "status": "http_error",
                "http_status": r.status_code,
                "info": f"http {r.status_code}",
                # never include Authorization; body may be large — truncate, scrub bearer-looking strings
                "body_preview": _scrub(body[:300]),
            }
        if isinstance(parsed, dict) and parsed.get("error") is not None:
            error = parsed.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            return {
                "ok": False, "status": "rpc_error",
                "info": _scrub(str(message or "JSON-RPC error"))[:200], "via": "raw",
            }
        if method == "tools/list":
            result = parsed.get("result") if isinstance(parsed, dict) else None
            tools_raw = result.get("tools") if isinstance(result, dict) else None
            if not isinstance(tools_raw, list):
                return {"ok": False, "status": "bad_response", "raw": parsed, "via": "raw"}
            tools = []
            for t in tools_raw:
                if isinstance(t, dict):
                    tools.append({
                        "name": t.get("name"),
                        "description": (t.get("description") or "")[:160],
                    })
            return {"ok": True, "status": "ok", "tools": tools, "count": len(tools), "via": "raw"}
        if isinstance(parsed, dict) and "result" in parsed:
            return _result_to_dict(parsed["result"]) | {"via": "raw"}
        return {"ok": False, "status": "bad_response", "raw": parsed, "via": "raw"}


def _scrub(text: str) -> str:
    return re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer ***", text or "")


async def mcp_request(
    *,
    url: str,
    token: str,
    method: str,
    name: str | None = None,
    arguments: dict | None = None,
    prefer_sdk: bool = True,
) -> dict:
    if method == "tools/call":
        if not name or is_write_tool(name):
            return {
                "ok": False, "status": "blocked",
                "info": f"write tool denied: {name}", "tool": name,
            }
        if name not in READ_ALLOW:
            return {
                "ok": False, "status": "blocked",
                "info": f"tool not in read-only allowlist: {name}", "tool": name,
            }
    errors = []
    if prefer_sdk:
        try:
            return await _call_via_sdk(url, token, method, name, arguments)
        except Exception as e:
            errors.append(f"sdk:{type(e).__name__}")
    try:
        out = await _call_via_raw(url, token, method, name, arguments)
        if errors:
            out["fallback_from"] = errors
        return out
    except Exception as e:
        return {
            "ok": False,
            "status": "error",
            "info": type(e).__name__,
            "errors": errors + [f"raw:{type(e).__name__}"],
        }


def call_sync(**kwargs) -> dict:
    return asyncio.run(mcp_request(**kwargs))


def list_tools_sync(url: str, token: str) -> dict:
    return call_sync(url=url, token=token, method="tools/list")


def call_tool_sync(url: str, token: str, name: str, arguments: dict | None = None) -> dict:
    return call_sync(url=url, token=token, method="tools/call", name=name, arguments=arguments or {})


def luckin_call(name: str, arguments: dict | None = None) -> dict:
    tok = luckin_token()
    if not tok:
        return {"ok": False, "status": "no_key", "info": "missing LUCKIN_MCP_KEY"}
    return call_tool_sync(LUCKIN_MCP_URL, tok, name, arguments)


def mcd_call(name: str, arguments: dict | None = None) -> dict:
    tok = mcd_token()
    if not tok:
        return {"ok": False, "status": "no_key", "info": "missing MCD_MCP_KEY"}
    return call_tool_sync(MCD_MCP_URL, tok, name, arguments)


def luckin_list_tools() -> dict:
    tok = luckin_token()
    if not tok:
        return {"ok": False, "status": "no_key", "info": "missing LUCKIN_MCP_KEY"}
    return list_tools_sync(LUCKIN_MCP_URL, tok)


def mcd_list_tools() -> dict:
    tok = mcd_token()
    if not tok:
        return {"ok": False, "status": "no_key", "info": "missing MCD_MCP_KEY"}
    return list_tools_sync(MCD_MCP_URL, tok)
