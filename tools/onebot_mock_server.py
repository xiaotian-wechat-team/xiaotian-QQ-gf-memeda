#!/usr/bin/env python3
"""本地假 OneBot v11 正向 WS（无真 QQ）——给 adapter --probe / --dry-run 冒烟。

用法（仓库根）：
  .venv/bin/python tools/onebot_mock_server.py --port 3001
  # 另开终端：
  .venv/bin/python tools/onebot_qq_run.py --probe --ws ws://127.0.0.1:3001
  .venv/bin/python tools/onebot_qq_run.py --dry-run --once --ws ws://127.0.0.1:3001
  # 向已连接客户端推一条私聊：
  curl -s -X POST http://127.0.0.1:3002/inject -H 'content-type: application/json' \
    -d '{"user_id":10001,"text":"mock 在吗"}'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Set

try:
    import websockets
    from websockets.asyncio.server import serve
except ImportError:
    print("需要 websockets：.venv/bin/pip install websockets", file=sys.stderr)
    raise SystemExit(2)

CLIENTS: Set = set()


async def ws_handler(ws):
    CLIENTS.add(ws)
    peer = getattr(ws, "remote_address", None)
    print(f"[mock] client + {peer} total={len(CLIENTS)}", flush=True)
    try:
        await ws.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "time": int(time.time()),
                }
            )
        )
        async for raw in ws:
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                continue
            action = req.get("action")
            echo = req.get("echo")
            if action == "get_login_info":
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"user_id": 900001, "nickname": "mock-xiaotian-qq"},
                            "echo": echo,
                        }
                    )
                )
            elif action == "send_private_msg":
                params = req.get("params") or {}
                print(
                    f"[mock] SEND private → {params.get('user_id')}: {params.get('message')!r}",
                    flush=True,
                )
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": int(time.time()) % 10_000_000},
                            "echo": echo,
                        }
                    )
                )
            else:
                await ws.send(
                    json.dumps(
                        {"status": "failed", "retcode": 1404, "data": None, "echo": echo, "msg": f"no {action}"}
                    )
                )
    finally:
        CLIENTS.discard(ws)
        print(f"[mock] client - {peer} total={len(CLIENTS)}", flush=True)


async def broadcast_event(ev: dict):
    dead = []
    raw = json.dumps(ev, ensure_ascii=False)
    for ws in list(CLIENTS):
        try:
            await ws.send(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


async def http_inject(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """极简 HTTP：POST /inject {"user_id":…,"text":…} 或 GET /health。"""
    try:
        req_line = await reader.readline()
        if not req_line:
            writer.close()
            return
        parts = req_line.decode("latin1", errors="replace").split()
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"
        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if b":" in line:
                k, _, v = line.decode("latin1", errors="replace").partition(":")
                headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length") or 0)
        body = await reader.readexactly(length) if length else b""

        status = 200
        resp_body = b"ok\n"
        if path.startswith("/health"):
            resp_body = json.dumps({"ok": True, "clients": len(CLIENTS)}).encode() + b"\n"
        elif method == "POST" and path.startswith("/inject"):
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                status = 400
                resp_body = b'{"error":"bad json"}\n'
            else:
                uid = data.get("user_id") or 10001
                text = data.get("text") or "hello"
                ev = {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": int(uid) if str(uid).isdigit() else uid,
                    "message": [{"type": "text", "data": {"text": text}}],
                    "raw_message": text,
                    "message_id": int(time.time()) % 10_000_000,
                    "time": int(time.time()),
                    "sender": {"user_id": int(uid) if str(uid).isdigit() else uid},
                }
                await broadcast_event(ev)
                print(f"[mock] inject → {len(CLIENTS)} client(s): {text!r}", flush=True)
                resp_body = json.dumps({"ok": True, "clients": len(CLIENTS)}).encode() + b"\n"
        else:
            status = 404
            resp_body = b"not found\n"

        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(resp_body)}\r\nConnection: close\r\n\r\n".encode()
            + resp_body
        )
        await writer.drain()
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main_async(ws_port: int, http_port: int):
    print(f"[mock] OneBot WS ws://127.0.0.1:{ws_port}", flush=True)
    print(f"[mock] inject  HTTP http://127.0.0.1:{http_port}/inject", flush=True)
    async with serve(ws_handler, "127.0.0.1", ws_port):
        await asyncio.start_server(http_inject, "127.0.0.1", http_port)
        await asyncio.Future()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mock OneBot v11 WS for xiaotian smoke")
    ap.add_argument("--port", type=int, default=3001, help="正向 WS 端口")
    ap.add_argument("--http-port", type=int, default=3002, help="inject HTTP 端口")
    args = ap.parse_args(argv)
    try:
        asyncio.run(main_async(args.port, args.http_port))
    except KeyboardInterrupt:
        print("[mock] stop", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
