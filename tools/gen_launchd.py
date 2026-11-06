#!/usr/bin/env python3
"""生成 launchd/*.plist（安装路径固定指向主仓库 ~/xiaotian-wechat，见 docs/launchd.md）。
python3 tools/gen_launchd.py  → 重写 launchd/ 下 plist。改作息先改这里再重新生成。"""
import os, plistlib, pathlib
HOME = os.path.expanduser("~"); R = f"{HOME}/xiaotian-wechat"; PY = f"{R}/.venv/bin/python"; LOG = f"{HOME}/Library/Logs/xiaotian"
OUT = pathlib.Path(__file__).resolve().parents[1] / "launchd"
ENV = {"PATH": f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": HOME, "LANG": "zh_CN.UTF-8",
       "LC_ALL": "zh_CN.UTF-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "MEM0_TELEMETRY": "False",
       "TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TZ": "Asia/Shanghai"}

def jitter(max_s, argv):
    """launchd 没有 RandomDelay 键（会被静默忽略）；用 bash 先随机睡 0~max_s 秒再 exec。"""
    q = " ".join("'" + a.replace("'", "'\\''") + "'" for a in argv)
    return ["/bin/bash", "-c", f"sleep $((RANDOM % {max_s})); exec {q}"]

def job(name, args, wd=f"{R}/src", **kw):
    d = {"Label": f"com.xiaotian.{name}", "ProgramArguments": args, "WorkingDirectory": wd, "EnvironmentVariables": ENV,
         "StandardOutPath": f"{LOG}/{name}.log", "StandardErrorPath": f"{LOG}/{name}.err",
         "LimitLoadToSessionType": "Aqua", "RunAtLoad": False, "ProcessType": "Background", "LowPriorityIO": True, "Nice": 5}
    d.update(kw)
    with open(OUT / f"com.xiaotian.{name}.plist", "wb") as f: plistlib.dump(d, f, sort_keys=False)

# 1) OCR mac 微信 daemon（对照/应急）。默认不 RunAtLoad：真发走 iLink（tools/ilink_weixin_run.py）。
#    需显式 `xiaotian_ctl.sh start wechat` 且 config ocr_daemon_enabled=true 才会发送。
job("wechat", [PY, f"{R}/src/daemon.py"], RunAtLoad=False, KeepAlive={"SuccessfulExit": False}, ThrottleInterval=30,
    ProcessType="Interactive", LowPriorityIO=False, Nice=0, ExitTimeOut=20)
# 1b) 微信 iLink 长驻（真发主路径）。KeepAlive；WorkingDirectory=仓库根（与手动
#     `python tools/ilink_weixin_run.py --send` 一致）。OCR 仍禁用。
#     若已有手动 PID，先 install；采纳时再 stop/start ilink（勿乱杀现网进程）。
job("ilink", [PY, f"{R}/tools/ilink_weixin_run.py", "--send"], wd=R, RunAtLoad=True,
    KeepAlive={"SuccessfulExit": False}, ThrottleInterval=30,
    ProcessType="Background", LowPriorityIO=True, Nice=5, ExitTimeOut=20)

# 1b2) QQ OneBot 长驻（NapCat 正向 WS → brain）。KeepAlive；与 iLink 并行。
#     WorkingDirectory=仓库根。若已有手动 onebot_qq_run.py --send，ctl start 会跳过以免双开。
job("onebot", [PY, f"{R}/tools/onebot_qq_run.py", "--send"], wd=R, RunAtLoad=True,
    KeepAlive={"SuccessfulExit": False}, ThrottleInterval=30,
    ProcessType="Background", LowPriorityIO=True, Nice=5, ExitTimeOut=20)

# 1c) Magpie（本机 Codex/gpt-6-astra 网关 :3425）。生图依赖它；挂了会 TimeoutExpired×2。
#     KeepAlive 守护；与 imagegen.ensure_magpie 互补（代码侧也会 open 拉起）。
_magpie = f"{HOME}/Applications/magpie.app/Contents/MacOS/magpie"
job("magpie", [_magpie], wd=f"{HOME}/Applications/magpie.app/Contents/MacOS", RunAtLoad=True,
    KeepAlive=True, ThrottleInterval=10, ProcessType="Interactive", LowPriorityIO=False, Nice=0, ExitTimeOut=20)

# 2) 主动消息：全天每 5 分钟扫描节拍（:02/:07/...），包括 00:30 后。
#    普通闲聊的 next_consider_at 不阻止新节拍；真实发送服从睡眠/暂停/轻量额度。
cal = [{"Hour": h, "Minute": m} for h in range(24) for m in range(2, 60, 5)]
#    --no-ui: transport=ilink 时纯 ilink/outbox，避免 prepare-failed 时 outbox+UI 双发
job("proactive", [PY, f"{R}/src/proactive_sender.py", "--once", "--no-ui"], StartCalendarInterval=cal, ExitTimeOut=120)
# 3) 校园新闻：早饭后/晚饭时，各加 0~10 分钟抖动
job("campus", jitter(600, [PY, f"{R}/src/campus.py", "--fetch"]),
    StartCalendarInterval=[{"Hour": 9, "Minute": 17}, {"Hour": 18, "Minute": 43}])
# 4) 素材采集：偶数点 :05 起 + 0~30 分钟抖动 → 实际 08:05~00:35，整段避开 source_poll quiet 01:00~07:30；source_poll 再限速
job("collect", jitter(1800, [PY, "-m", "collectors.pipeline"]),
    StartCalendarInterval=[{"Hour": h, "Minute": 5} for h in (8, 10, 12, 14, 16, 18, 20, 22, 0)])
# 5) 素材清理：她睡得最沉的 04:23
job("materials-purge", [PY, "-m", "src.material_store", "purge"], wd=R, StartCalendarInterval={"Hour": 4, "Minute": 23})
# 6) 每晚反思：03:50 + 0~15 分钟（熬夜最晚到 3 点，之后整理「昨天」）
job("reflection", ["/bin/bash", "-c", f"sleep $((RANDOM % 900)); exec '{PY}' '{R}/src/reflect.py' --date \"$(date -v-1d +%F)\""],
    StartCalendarInterval={"Hour": 3, "Minute": 50}, ExitTimeOut=600)

# 7) 每周日 20:00：下月行程 apply --inject-research --freeze-after && sync
#    与聊天服务分离；只做本地校验，不调用独立 review Agent
#    Weekday 0 = Sunday（launchd）。默认 --month=下月。
_env_sched = dict(ENV)
job(
    "schedule-month",
    ["/bin/bash", "-c",
     f"'{PY}' '{R}/tools/update_month_schedule.py' apply --inject-research --freeze-after"
     f" && '{PY}' '{R}/tools/update_month_schedule.py' sync"],
    wd=R,
    EnvironmentVariables=_env_sched,
    StartCalendarInterval={"Weekday": 0, "Hour": 20, "Minute": 0},
    ExitTimeOut=900,
)

# 8) QQ 换头队列：每天 source_poll quiet（≈01:00–07:30）结束后跑 process-queue，再偶发 --album
#    与 proactive quiet_hours 无关；不碰 iLink / wechat daemon。07:40 + 0~15 分钟抖动。
job(
    "avatar-qq",
    jitter(900, ["/bin/bash", f"{R}/tools/avatar_qq_daily.sh"]),
    wd=R,
    StartCalendarInterval={"Hour": 7, "Minute": 40},
    ExitTimeOut=600,
)

