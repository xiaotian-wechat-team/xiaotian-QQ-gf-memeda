#!/usr/bin/env python3
"""Standalone sniff-test: imagegen unit → WeChat chat (小熊 only).

Bot / human detection sniff: generate one scene photo via the modular imagegen
API, then optionally paste-send it into the Mac WeChat thread for contact
remark「小熊」(id demo_contact). Does NOT start the WeChat reply daemon.

Usage (from repo root, venv python):
  .venv/bin/python tools/try_chat_imagegen.py --scene "图书馆窗边看书"          # dry: no send
  .venv/bin/python tools/try_chat_imagegen.py --scene "..." --dry               # same
  .venv/bin/python tools/try_chat_imagegen.py --no-generate PATH.png --dry      # dry path check
  .venv/bin/python tools/try_chat_imagegen.py --scene "..." --live              # REAL send
  .venv/bin/python tools/try_chat_imagegen.py --no-generate PATH.png --live

Flags:
  --scene TEXT       scene for imagegen.generate (required unless --no-generate)
  --no-generate PATH send an existing image file (skip Codex)
  --caption TEXT     optional text bubble before the image
  --dry              default: plan only, never send / never generate unless --generate-dry-run
  --live             required to actually send (and generate if needed)
  --generate-on-dry  with --dry, still run imagegen (write files) but do not send

Leave com.xiaotian.wechat stopped. This script only uses mac_wechat_io + chat_media.
"""
from __future__ import annotations
import argparse, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chat_media
from config import load


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", help="imagegen scene text")
    ap.add_argument("--no-generate", metavar="PATH", help="existing image path; skip generate")
    ap.add_argument("--caption", default="", help="optional caption before image")
    ap.add_argument("--dry", action="store_true", help="no send (default if --live omitted)")
    ap.add_argument("--live", action="store_true", help="REAL send to WeChat whitelist contact")
    ap.add_argument("--generate-on-dry", action="store_true", help="run generate even in dry mode")
    a = ap.parse_args()

    if a.live and a.dry:
        ap.error("use either --live or --dry, not both")
    dry = not a.live
    if not a.scene and not a.no_generate:
        ap.error("need --scene or --no-generate PATH")

    cfg = load()
    c = chat_media.assert_allowed_contact(cfg)
    print(f"[try_chat_imagegen] contact id={c['id']} remark={c.get('remark')!r} dry={dry}")

    src = a.no_generate or a.scene
    if dry and not a.generate_on_dry:
        r = chat_media.send_generated_image(
            src, caption=a.caption or None, dry=True, no_generate=bool(a.no_generate), cfg=cfg
        )
        print("[dry]", json_dumps(r))
        print("Re-run with --live to send (and generate if needed). Daemon left stopped.")
        return 0

    # live, or dry+generate-on-dry
    if dry and a.generate_on_dry:
        path = chat_media.resolve_source(src, no_generate=bool(a.no_generate))
        print(f"[dry+generate] path={path}")
        print("Not sending (--live required).")
        return 0

    r = chat_media.send_generated_image(
        src,
        caption=a.caption or None,
        dry=False,
        no_generate=bool(a.no_generate),
        open_chat_first=True,
        cfg=cfg,
    )
    print("[live]", json_dumps(r))
    return 0


def json_dumps(d):
    import json
    return json.dumps(d, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PermissionError as e:
        print(f"REFUSED: {e}", file=sys.stderr); raise SystemExit(2)
