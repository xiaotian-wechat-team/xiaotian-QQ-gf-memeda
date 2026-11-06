#!/usr/bin/env python3
"""Minimal stdio MCP client for LCU (read-only evaluation helper).
Usage: lcu_client.py 'JS code' [more JS ...]  -> prints JSON results; each call 30s timeout."""
import json, os, subprocess, sys, threading, time, queue
LCU_BIN = os.path.expanduser("~/.local/share/lcu/current/bin/lcu")

class LCU:
    def __init__(self):
        env = dict(os.environ, TMPDIR=os.path.expanduser("~/xiaotian-wechat/data"))
        self.p = subprocess.Popen([LCU_BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.q = queue.Queue(); self.id = 0; self.server_requests = []
        threading.Thread(target=self._rd, daemon=True).start()
        self.req("initialize", {"protocolVersion": "2024-11-05", "capabilities": {"elicitation": {}},
                                "clientInfo": {"name": "xiaotian-eval", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    def _rd(self):
        for line in self.p.stdout:
            try: self.q.put(json.loads(line))
            except Exception: pass
    def _send(self, m): self.p.stdin.write(json.dumps(m) + "\n"); self.p.stdin.flush()
    def req(self, method, params, timeout=30):
        self.id += 1; i = self.id
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        end = time.time() + timeout
        while time.time() < end:
            try: m = self.q.get(timeout=end - time.time())
            except queue.Empty: break
            if "method" in m and "id" in m:   # server->client request (e.g. elicitation/create)
                self.server_requests.append(m)
                self._send({"jsonrpc": "2.0", "id": m["id"], "result": self.on_request(m)}); continue
            if m.get("id") == i: return m
        raise TimeoutError(method)
    def js(self, code, timeout=30):
        return self.req("tools/call", {"name": "js", "arguments": {"code": code}}, timeout)
    def on_request(self, m):
        """Default: decline every elicitation (read-only use). Override to accept."""
        return {"action": "decline"}
    def close(self): self.p.terminate()

if __name__ == "__main__":
    c = LCU()
    try:
        for code in sys.argv[1:]:
            t = time.time(); r = c.js(code)
            print(json.dumps({"elapsed_s": round(time.time() - t, 2), "resp": r}, ensure_ascii=False))
    finally: c.close()
