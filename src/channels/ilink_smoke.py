"""iLink 微信通道冒烟（自建 Python；不装 OpenClaw，不启 OCR daemon）。

阶段：
  1) check — weixin-ilink / 凭据 / persona 是否就绪（默认，只读）
  2) plan  — 打印扫码与跑通道步骤
  3)（可选）openclaw_legacy — 仅对照，非必需路径

真正扫码：`.venv/bin/python tools/ilink_weixin_run.py login`
"""
from __future__ import annotations
import json, shutil, subprocess, sys
from pathlib import Path

try:
    from config import ROOT
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[2]

ILINK_BASE = "https://ilinkai.weixin.qq.com"
PROTOCOL_DOC = "https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md"
SDK = "weixin-ilink"
SDK_REPO = "https://github.com/zongrongjin/weixin-ilink"


def _run(argv, timeout=30) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def sdk_version() -> tuple[bool, str]:
    try:
        import weixin_ilink
        ver = getattr(weixin_ilink, "__version__", None)
        if not ver:
            # 包可能无 __version__
            from importlib.metadata import version
            ver = version("weixin-ilink")
        return True, str(ver)
    except Exception as e:
        return False, str(e)


def creds_path() -> Path:
    try:
        from .persona_map import persona_cfg
        ch = (persona_cfg().get("channels") or {}).get("wechat_ilink") or {}
        raw = ch.get("credentials_file") or "data/ilink_creds.json"
    except Exception:
        raw = "data/ilink_creds.json"
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def check() -> dict:
    ok_sdk, ver = sdk_version()
    creds = creds_path()
    creds_ok = False
    creds_meta = None
    if creds.is_file():
        try:
            info = json.loads(creds.read_text(encoding="utf-8"))
            creds_ok = bool(info.get("botToken") and info.get("baseUrl"))
            creds_meta = {
                "accountId": info.get("accountId"),
                "userId": (info.get("userId") or "")[:12] + "…" if info.get("userId") else None,
                "baseUrl": info.get("baseUrl"),
                "has_token": bool(info.get("botToken")),
            }
        except Exception as e:
            creds_meta = {"error": str(e)}
    oc = shutil.which("openclaw")  # 仅报告，不要求
    report = {
        "ok": ok_sdk,  # SDK 就绪即可开始 login；凭据可选
        "primary": "self_built_python",
        "sdk": SDK,
        "sdk_repo": SDK_REPO,
        "sdk_ok": ok_sdk,
        "sdk_version": ver,
        "ilink_api_base": ILINK_BASE,
        "protocol_doc": PROTOCOL_DOC,
        "credentials_file": str(creds),
        "credentials_ok": creds_ok,
        "credentials_meta": creds_meta,
        "openclaw_path": oc,  # 可选遗留，非必需
        "openclaw_required": False,
        "ocr_daemon": False,
        "next": [],
    }
    if not ok_sdk:
        report["next"].append(".venv/bin/pip install 'weixin-ilink[qr]'")
    if not creds_ok:
        report["next"].append(
            ".venv/bin/python tools/ilink_weixin_run.py login   # 手机微信扫码"
        )
    else:
        report["next"].extend([
            ".venv/bin/python tools/ilink_weixin_run.py --dry-run --once",
            ".venv/bin/python tools/ilink_weixin_run.py --send   # 确认后真回",
        ])
    report["next"].append("QQ 仍用：.venv/bin/python tools/onebot_qq_run.py --dry-run")
    return report


def print_install_plan():
    print(f"""# 微信 iLink 自建通道（不用 OpenClaw）— Mac mini ~/xiaotian-wechat
# 协议：{PROTOCOL_DOC}
# SDK：{SDK_REPO}（或本仓库 thin client 走同一 CGI）

硬规则：不启动 OCR/daemon.py wechat 轮询；不安装 openclaw / openclaw-weixin。

1) 依赖
   cd ~/xiaotian-wechat
   .venv/bin/pip install 'weixin-ilink[qr]'
   .venv/bin/python tools/ilink_weixin_run.py check

2) 扫码登录（必须人在场；凭据落 data/ilink_creds.json，已 gitignore）
   .venv/bin/python tools/ilink_weixin_run.py login
   # 终端 ASCII 码；不清则用打印的 URL 浏览器打开再扫
   # 若手机弹出数字配对码，按提示输入

3) 干跑（接到 brain，默认不回发）
   .venv/bin/python tools/ilink_weixin_run.py --dry-run --once
   # 用你的微信号给小甜 bot 发一条私聊

4) 真回
   .venv/bin/python tools/ilink_weixin_run.py --send

5) 共享记忆
   persona.primary_contact_id=demo_contact
   wechat_ilink:* / qq_onebot:* → 同一 contact；日程差分 schedule_memory

6) QQ（保留）
   .venv/bin/python tools/onebot_qq_run.py --self-test
   .venv/bin/python tools/onebot_qq_run.py --dry-run --once

对照：OpenClaw Node Gateway 已不是本项目路径；若本机仍有 openclaw 命令可忽略。
""")


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    cmd = argv[0] if argv else "check"
    if cmd == "plan":
        print_install_plan()
        return 0
    if cmd == "check":
        r = check()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("sdk_ok") else 1
    print("usage: ilink_smoke.py [check|plan]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
