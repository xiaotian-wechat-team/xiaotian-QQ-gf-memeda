#!/usr/bin/env python3
"""小脚本：按需天气工具 / 方差是否挂进提示词。
用法:
  .venv/bin/python tools/weather_attach.py                  # 默认 BDA，读缓存判断
  .venv/bin/python tools/weather_attach.py --place yanyuan
  .venv/bin/python tools/weather_attach.py --refresh        # get() 再判断（受 1h 限速）
  .venv/bin/python tools/weather_attach.py --tool bda       # 打印 weather.get JSON
退出码 0=应挂载，1=平稳跳过，2=错误。
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import weather

def main():
    ap = argparse.ArgumentParser(description="按需天气工具 / 方差挂载")
    ap.add_argument("--place", default="bda", help="bda|yanyuan（默认 bda）")
    ap.add_argument("--refresh", action="store_true", help="先 weather.get 再判断")
    ap.add_argument("--tool", nargs="?", const="bda", help="只打印 get(place) JSON")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON")
    a = ap.parse_args()
    if a.tool is not None:
        place = a.tool if a.tool != "bda" or a.place == "bda" else a.place
        # --tool [place] takes precedence when provided as value
        place = a.tool if a.tool else a.place
        print(json.dumps(weather.get(place, cache_only=not a.refresh), ensure_ascii=False))
        return 0
    if a.refresh:
        r = weather.get(a.place)
        if not r.get("ok") and not (r.get("why") or "").startswith("cache"):
            print(json.dumps({"attach": False, "reason": r.get("why")}, ensure_ascii=False) if a.json else f"ERR {r.get('why')}")
            return 2
    snap = weather.current_snap(place=a.place)
    base = weather.baseline_means(place=a.place)
    ok, reason = weather.should_attach(snap=snap, baseline=base, place=a.place)
    if a.json:
        print(json.dumps({"attach": ok, "reason": reason, "place": a.place, "snap": snap, "baseline": base}, ensure_ascii=False))
    else:
        print(("ATTACH" if ok else "SKIP"), reason, f"place={a.place}")
        if snap: print("snap:", json.dumps(snap, ensure_ascii=False))
        print("baseline:", json.dumps(base, ensure_ascii=False))
        print("thresholds:", json.dumps(weather.attach_cfg(), ensure_ascii=False))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
