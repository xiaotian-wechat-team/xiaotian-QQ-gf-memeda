#!/usr/bin/env python3
"""Simulate a full life_order conversation offline (no network, no real orders).

Usage (from worktree):
  ~/xiaotian-wechat/.venv/bin/python tools/life_order_dry_run.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from life_order.flow import handle_inbound
from life_order.pending import PendingStore
from life_order.luckin import LuckinClient
from life_order.transport import CallableTransport

PEER = "qq_onebot:10001"
CFG = {
    "life_order": {
        "enabled": True,
        "dry_run": True,
        "amount_cap_yuan": 50,
        "confirm_expiry_sec": 180,
        "allowed_contact_id": "demo_contact",
        "allowed_peers": [PEER],
        "confirm_words": ["好", "求你", "嗯", "下吧"],
        "cancel_words": ["算了", "不要"],
        "soft_confirm_phrases": ["你求求我嘛～", "那我给你下单了哦"],
        "luckin": {"default_dept_id": 605488, "demo_amount_yuan": 28},
    }
}


def main():
    mem = []
    created = []

    def mem_add(cid, fact, **kw):
        mem.append({"cid": cid, "fact": fact, **kw})
        return {"ok": True}

    tr = CallableTransport({
        "preview_order": lambda **kw: {"payAmount": 2800, "status": "ok"},
        "create_order": lambda **kw: created.append(kw) or {"ok": True, "orderNo": "SHOULD_NOT"},
    })
    with tempfile.TemporaryDirectory() as td:
        store = PendingStore(pathlib.Path(td) / "pending.json")
        luckin = LuckinClient(tr, cfg=CFG)
        turns = [
            ("他", "帮我点杯瑞幸生椰拿铁"),
            ("小甜", None),  # filled
            ("他", "求你"),
            ("小甜", None),
        ]
        print("=== life_order dry-run (no network) ===")
        r1 = handle_inbound(
            "demo_contact", turns[0][1], peer=PEER, cfg=CFG, store=store, luckin=luckin,
        )
        print(f"他: {turns[0][1]}")
        print(f"小甜: {r1}")
        r2 = handle_inbound(
            "demo_contact", "求你", peer=PEER, cfg=CFG, store=store, luckin=luckin,
            mem_add=mem_add,
            now=(store.snapshot().get("demo_contact") or {}).get("created_at", 0) + 1,
        )
        # pending may already be cleared; get created_at before confirm
        print(f"他: 求你")
        print(f"小甜: {r2}")
        print("---")
        print("create_order calls:", created)
        print("memory records:")
        for m in mem:
            print(" ", m)
        assert created == [], "dry_run must not createOrder"
        assert mem and mem[0]["kind"] == "life_order"
        assert r1 and "求求我" in r1
        assert r2 and "DRY-LK-" in r2
        print("OK")


if __name__ == "__main__":
    main()
