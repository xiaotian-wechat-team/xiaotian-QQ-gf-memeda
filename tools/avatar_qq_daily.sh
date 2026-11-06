#!/bin/bash
# launchd 日跑：source_poll quiet 结束后处理 QQ 换头队列，再偶发相册（mood+互斥）。
# 不碰 iLink / 微信 WDA。日志由 launchd 重定向。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT" || exit 1
export TZ=Asia/Shanghai
# 1) 兑现已到期队列（小红书路径）
"$PY" "$ROOT/tools/avatar_qq.py" --process-queue
# 2) 偶发相册：门控/无人脸/last_change.lock 均在脚本内；失败不拖垮 job
"$PY" "$ROOT/tools/avatar_qq.py" --album || true
