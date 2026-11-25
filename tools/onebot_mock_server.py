#!/usr/bin/env python3
"""Isolated OneBot v11 simulator. Never forwards events to QQ or NapCat."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import time
from typing import Set

try:
    from websockets.asyncio.server import serve
except ImportError:
    serve = None

CLIENTS: Set = set()
EVENTS: list[dict] = []
ACK_MODE = "confirmed"
BOT_ID = 9000000002
PEER_ID = 9000000001
EVIDENCE: pathlib.Path | None = None
LLM_REPLY: str | None = None


def record(direction: str, payload: dict) -> None:
    row = {"ts": time.time(), "direction": direction, "payload": payload}
    EVENTS.append(row)
    if EVIDENCE:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        with EVIDENCE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def private_event(data: dict) -> dict:
    text = str(data.get("text") or "")
    uid = data.get("user_id") or PEER_ID
    bot = data.get("self_id") or BOT_ID
    mid = data.get("message_id") or int(time.time() * 1000)
    event = dict(data.get("event") or {})
    event.update({
        "post_type": event.get("post_type") or "message",
        "message_type": event.get("message_type") or data.get("message_type") or "private",
        "self_id": bot,
        "user_id": uid,
        "message": event.get("message") or [{"type": "text", "data": {"text": text}}],
        "raw_message": event.get("raw_message") or text,
        "message_id": event.get("message_id") or mid,
        "time": event.get("time") or data.get("time") or int(time.time()),
        "sender": event.get("sender") or {"user_id": uid},
    })
    return event


def acknowledgement(req: dict, mode: str) -> dict | None:
    if mode == "unknown":
        return None
    if mode == "failed":
        return {"status": "failed", "retcode": 100, "data": None,
                "echo": req.get("echo"), "msg": "simulated failure"}
    return {"status": "ok", "retcode": 0,
            "data": {"message_id": int(time.time() * 1000) % 2_000_000_000},
            "echo": req.get("echo")}


async def ws_handler(ws):
    CLIENTS.add(ws)
    record("client_connected", {"count": len(CLIENTS)})
    try:
        async for raw in ws:
            req = json.loads(raw)
            action = req.get("action")
            if action == "get_login_info":
                response = {"status": "ok", "retcode": 0,
                            "data": {"user_id": BOT_ID, "nickname": "fake-onebot"},
                            "echo": req.get("echo")}
            elif action == "send_private_msg":
                params = req.get("params") or {}
                record("outbound", req)
                if str(params.get("user_id")) != str(PEER_ID):
                    response = {"status": "failed", "retcode": 100, "data": None,
                                "echo": req.get("echo"), "msg": "unexpected test peer"}
                else:
                    mode = str(req.get("mock_ack") or ACK_MODE)
                    if mode == "delayed":
                        await asyncio.sleep(1)
                    response = acknowledgement(req, mode)
            else:
                response = {"status": "failed", "retcode": 1404, "data": None,
                            "echo": req.get("echo"), "msg": f"unsupported {action}"}
            if response is not None:
                record("ack", response)
                await ws.send(json.dumps(response, ensure_ascii=False))
    finally:
        CLIENTS.discard(ws)
        record("client_disconnected", {"count": len(CLIENTS)})


async def broadcast_event(event: dict) -> None:
    record("inbound", event)
    raw = json.dumps(event, ensure_ascii=False)
    await asyncio.gather(*(ws.send(raw) for ws in list(CLIENTS)), return_exceptions=True)


def http_response(status: int, payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode() + b"\n"
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


async def http_control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    global ACK_MODE
    try:
        line = await reader.readline()
        parts = line.decode("latin1", errors="replace").split()
        method, path = parts[0], parts[1]
        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, value = line.decode("latin1").partition(":")
            headers[key.lower()] = value.strip()
        length = min(int(headers.get("content-length") or 0), 1_000_000)
        body = await reader.readexactly(length) if length else b""
        if path == "/health":
            status, payload = 200, {"ok": True, "clients": len(CLIENTS), "ack_mode": ACK_MODE,
                                    "bot_id": BOT_ID, "peer_id": PEER_ID}
        elif path == "/events":
            status, payload = 200, {"events": EVENTS[-200:]}
        elif method == "POST" and path == "/inject":
            data = json.loads(body or b"{}")
            event = private_event(data)
            await broadcast_event(event)
            status, payload = 200, {"ok": True, "clients": len(CLIENTS), "event": event}
        elif method == "POST" and path == "/ack":
            data = json.loads(body or b"{}")
            mode = data.get("mode")
            if mode not in ("confirmed", "failed", "unknown", "delayed"):
                raise ValueError("invalid ACK mode")
            ACK_MODE = mode
            status, payload = 200, {"ok": True, "ack_mode": mode}
        elif method == "POST" and path == "/v1/chat/completions" and LLM_REPLY is not None:
            data = json.loads(body or b"{}")
            if not isinstance(data.get("messages"), list):
                raise ValueError("messages required")
            record("model_request", data)
            status, payload = 200, {"choices": [{"message": {"role": "assistant", "content": LLM_REPLY}}]}
        elif method == "POST" and path == "/reset":
            EVENTS.clear()
            if EVIDENCE:
                EVIDENCE.write_text("")
            status, payload = 200, {"ok": True}
        else:
            status, payload = 404, {"error": "not found"}
        writer.write(http_response(status, payload))
        await writer.drain()
    except (ValueError, json.JSONDecodeError) as exc:
        writer.write(http_response(400, {"error": str(exc)}))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main_async(host: str, ws_port: int, http_port: int):
    async with serve(ws_handler, host, ws_port):
        await asyncio.start_server(http_control, host, http_port)
        print(f"[fake-onebot] ws={host}:{ws_port} control={host}:{http_port}", flush=True)
        await asyncio.Future()


def main(argv=None):
    global ACK_MODE, BOT_ID, PEER_ID, EVIDENCE, LLM_REPLY
    ap = argparse.ArgumentParser(description="Fake OneBot v11 with no QQ forwarding")
    ap.add_argument("--host", default=os.environ.get("FAKE_ONEBOT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FAKE_ONEBOT_PORT", "3001")))
    ap.add_argument("--http-port", type=int, default=int(os.environ.get("FAKE_ONEBOT_HTTP_PORT", "3002")))
    ap.add_argument("--bot-id", default=os.environ.get("FAKE_ONEBOT_BOT_ID", str(BOT_ID)))
    ap.add_argument("--peer-id", default=os.environ.get("FAKE_ONEBOT_PEER_ID", str(PEER_ID)))
    ap.add_argument("--ack", choices=("confirmed", "failed", "unknown", "delayed"),
                    default=os.environ.get("FAKE_ONEBOT_ACK", "confirmed"))
    ap.add_argument("--evidence", default=os.environ.get("FAKE_ONEBOT_EVIDENCE", ""))
    ap.add_argument("--llm-reply", default=None, help="enable local fake model endpoint; never calls an external model")
    args = ap.parse_args(argv)
    if serve is None:
        raise RuntimeError("fake OneBot requires the 'websockets' package")
    ACK_MODE, BOT_ID, PEER_ID = args.ack, args.bot_id, args.peer_id
    EVIDENCE = pathlib.Path(args.evidence) if args.evidence else None
    LLM_REPLY = args.llm_reply
    asyncio.run(main_async(args.host, args.port, args.http_port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
