#!/usr/bin/env python3
"""刷新小甜两地天气预报（北京时间）。

  .venv/bin/python tools/weather_refresh.py           # 全量：当前实况 + 未来 3 天
  .venv/bin/python tools/weather_refresh.py --current # 只更新当前实况，保留已有逐日
  .venv/bin/python tools/weather_refresh.py --auto    # 12 点前全量；午后只刷实况。预报过期则仍全量

失败写到 stderr，并追加 data/logs/weather_refresh.log。
不覆盖上次有效的 data/weather_forecast.json。
不改 config、聊天记录、STATE、队列、素材、日程，不发 QQ/微信。

launchd 模板：launchd/com.xiaotian.weather-refresh.plist
（07:40 全量、16:40 实况，均为北京时间）。模板只放在仓库里，不会自动安装。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import weather

LOG = ROOT / "data" / "logs" / "weather_refresh.log"


def main(argv=None):
    ap = argparse.ArgumentParser(description="刷新 bda / 燕园 天气预报")
    ap.add_argument("--current", action="store_true", help="只刷新当前实况")
    ap.add_argument("--auto", action="store_true", help="按北京时间决定全量或只刷实况")
    ap.add_argument("--place", action="append", help="只刷一地，可重复。默认两地")
    args = ap.parse_args(argv)
    if args.auto and args.current:
        ap.error("--auto 与 --current 不能同时用")
    if args.auto:
        current_only = weather.refresh_kind() == "current"
    else:
        current_only = bool(args.current)
    result = weather.refresh(
        places=args.place,
        current_only=current_only,
        log_path=LOG,
        force=True,
    )
    print(
        f"{result.get('why')} updated={result.get('updated')} "
        f"current_only={current_only} errors={result.get('errors')}"
    )
    if result.get("ok"):
        return 0
    if result.get("why") == "partial":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
