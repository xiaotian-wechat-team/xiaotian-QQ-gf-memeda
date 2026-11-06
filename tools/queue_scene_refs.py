#!/usr/bin/env python3
"""场景参考图补齐队列：每新增活动/场景目标 ≥4 张真实参考图。

开发优先定活动+时间表；图库可闲时/未开发时排队回填。
不启动 wechat daemon。可 resume。

用法（仓库根）:
  .venv/bin/python tools/queue_scene_refs.py status
  .venv/bin/python tools/queue_scene_refs.py enqueue --activity-ids ski_chongli travel_hemu
  .venv/bin/python tools/queue_scene_refs.py enqueue --scene-ids trip-049 country-001
  .venv/bin/python tools/queue_scene_refs.py enqueue --from-pool          # 活动池 scene_match → outfit
  .venv/bin/python tools/queue_scene_refs.py enqueue --missing            # 所有 <4 张的 outfit 场景
  .venv/bin/python tools/queue_scene_refs.py process --limit 8            # 闲时处理队列头
  .venv/bin/python tools/queue_scene_refs.py process --dry-run

队列文件：data/scene_refs_queue.json
实际抓取复用 tools/build_scene_refs.py（Bing/Wikimedia；可选 collectors）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

QUEUE = ROOT / "data" / "scene_refs_queue.json"
OUTFIT = ROOT / "data" / "outfit_scenes.json"
POOL = ROOT / "data" / "activity_pool.json"
ASSETS = Path(os.path.expanduser(os.environ.get("SCENE_REFS_DIR", "~/xiaotian-assets/scene_refs")))
PER_SCENE = 4


def _load_q() -> dict:
    if QUEUE.exists():
        return json.loads(QUEUE.read_text())
    return {"version": 1, "per_scene": PER_SCENE, "pending": [], "done": [], "updated": None}


def _save_q(doc: dict) -> None:
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def _outfit_scenes() -> list[dict]:
    return json.loads(OUTFIT.read_text()).get("scenes") or []


def _counts() -> dict[str, int]:
    db = ASSETS / "scene_refs.sqlite"
    out: dict[str, int] = {}
    if not db.exists():
        return out
    con = sqlite3.connect(str(db))
    for sid, n in con.execute("SELECT scene_id, COUNT(*) FROM images GROUP BY scene_id"):
        out[sid] = int(n)
    con.close()
    return out


def _match_scene_ids(keywords: list[str]) -> list[str]:
    keys = [k for k in keywords if k]
    hits = []
    for s in _outfit_scenes():
        blob = s.get("name", "") + " " + " ".join(s.get("aliases") or []) + " " + (s.get("hint") or "")
        if any(k in blob for k in keys):
            hits.append(s["id"])
    return hits


def enqueue_scene_ids(scene_ids: list[str], *, reason: str = "") -> dict:
    doc = _load_q()
    counts = _counts()
    pending_ids = {x["scene_id"] for x in doc.get("pending") or []}
    added = []
    for sid in scene_ids:
        have = counts.get(sid, 0)
        need = max(0, int(doc.get("per_scene") or PER_SCENE) - have)
        if need <= 0:
            continue
        if sid in pending_ids:
            continue
        item = {
            "scene_id": sid,
            "have": have,
            "need": need,
            "reason": reason,
            "queued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        doc.setdefault("pending", []).append(item)
        pending_ids.add(sid)
        added.append(item)
    _save_q(doc)
    return {"added": len(added), "pending": len(doc["pending"]), "items": added}


def enqueue_for_activities(activity_ids: list[str]) -> dict:
    pool = json.loads(POOL.read_text()) if POOL.exists() else {"activities": []}
    by = {a["id"]: a for a in pool.get("activities") or []}
    sids: list[str] = []
    for aid in activity_ids:
        act = by.get(aid)
        if not act:
            continue
        sids.extend(_match_scene_ids(list(act.get("scene_match") or []) + list(act.get("aliases") or [])))
    # unique preserve order
    seen = set()
    uniq = []
    for s in sids:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return enqueue_scene_ids(uniq, reason="activity:" + ",".join(activity_ids))


def enqueue_missing() -> dict:
    counts = _counts()
    per = PER_SCENE
    missing = [s["id"] for s in _outfit_scenes() if counts.get(s["id"], 0) < per]
    return enqueue_scene_ids(missing, reason="coverage<4")


def status() -> dict:
    doc = _load_q()
    counts = _counts()
    scenes = _outfit_scenes()
    under = sum(1 for s in scenes if counts.get(s["id"], 0) < PER_SCENE)
    total_img = sum(counts.values())
    return {
        "outfit_scenes": len(scenes),
        "scene_dirs_with_images": len(counts),
        "images": total_img,
        "under_4": under,
        "queue_pending": len(doc.get("pending") or []),
        "queue_done": len(doc.get("done") or []),
        "queue_path": str(QUEUE),
        "assets": str(ASSETS),
        "per_scene_target": PER_SCENE,
    }


def process_queue(*, limit: int = 8, dry_run: bool = False, collectors: bool = False) -> dict:
    """处理队列头：调用 build_scene_refs 逻辑只补指定 scene_id。"""
    import build_scene_refs as bsr

    doc = _load_q()
    pending = list(doc.get("pending") or [])
    if not pending:
        return {"processed": 0, "msg": "queue empty"}
    batch = pending[: max(1, limit)]
    if dry_run:
        return {"processed": 0, "dry_run": batch}

    os.environ.setdefault("SCENE_REFS_BUILD", "1")
    con = bsr.ensure()
    model = None
    try:
        # optional embed model — build_scene_refs may lazy-load
        pass
    except Exception:
        model = None

    scenes_by_id = {s["id"]: s for s in bsr.load_scenes()}
    progress = bsr.load_progress()
    done_items = []
    for item in batch:
        sid = item["scene_id"]
        sc = scenes_by_id.get(sid)
        if not sc:
            item["error"] = "unknown_scene"
            done_items.append(item)
            continue
        before = con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (sid,)).fetchone()[0]
        bsr.process_scene(con, sc, model, PER_SCENE, (3.0, 8.0), progress)
        after = con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (sid,)).fetchone()[0]
        item["have_before"] = before
        item["have_after"] = after
        item["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        done_items.append(item)
        print(f"processed {sid} {before}→{after}", flush=True)

    # drop processed from pending (even if still <4 — will re-enqueue via --missing)
    done_ids = {x["scene_id"] for x in done_items}
    doc["pending"] = [x for x in pending if x["scene_id"] not in done_ids]
    doc.setdefault("done", []).extend(done_items)
    # keep done tail short
    doc["done"] = doc["done"][-500:]
    bsr.save_progress(progress)
    _save_q(doc)

    # optional CN collectors pass for still-short
    if collectors:
        still = [scenes_by_id[i["scene_id"]] for i in done_items
                 if i["scene_id"] in scenes_by_id and
                 con.execute("SELECT COUNT(*) FROM images WHERE scene_id=?", (i["scene_id"],)).fetchone()[0] < PER_SCENE]
        if still:
            bsr.collect_cn_pass(con, still, model, PER_SCENE, progress)

    return {"processed": len(done_items), "pending": len(doc["pending"])}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status")
    p.set_defaults(func=lambda a: print(json.dumps(status(), ensure_ascii=False, indent=2)) or 0)

    p = sub.add_parser("enqueue")
    p.add_argument("--activity-ids", nargs="*", default=[])
    p.add_argument("--scene-ids", nargs="*", default=[])
    p.add_argument("--from-pool", action="store_true")
    p.add_argument("--missing", action="store_true")
    def _enq(a):
        total = {"added": 0}
        if a.missing:
            total = enqueue_missing()
        if a.from_pool:
            pool = json.loads(POOL.read_text())
            ids = [x["id"] for x in pool.get("activities") or []]
            total = enqueue_for_activities(ids)
        if a.activity_ids:
            total = enqueue_for_activities(a.activity_ids)
        if a.scene_ids:
            total = enqueue_scene_ids(a.scene_ids, reason="manual")
        print(json.dumps(total, ensure_ascii=False, indent=2))
        return 0
    p.set_defaults(func=_enq)

    p = sub.add_parser("process")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--collectors", action="store_true")
    p.set_defaults(func=lambda a: print(json.dumps(
        process_queue(limit=a.limit, dry_run=a.dry_run, collectors=a.collectors),
        ensure_ascii=False, indent=2)) or 0)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
