# launchd 自启（2026-10-24 用户批准安装并加载）

> **2026-10-25**：微信真发 = iLink KeepAlive（`com.xiaotian.ilink`）；`com.xiaotian.wechat`（OCR）默认不加载且 `RunAtLoad=false`。`tools/xiaotian_ctl.sh` 省略 job 时 **含 ilink、不含 wechat**；`disable-ocr` 可 bootout+disable OCR。
>
> **2026-10-26**：QQ 真回 = OneBot KeepAlive（`com.xiaotian.onebot` → `tools/onebot_qq_run.py --send`）。DEFAULT **含 onebot**；手动 `--send` 时 `start onebot` 跳过以免双开。


plist 由 `tools/gen_launchd.py` 生成（改作息改这里再生成），安装/启停用 `tools/xiaotian_ctl.sh {status|install|start|stop|restart|logs} [job…]`。
日志统一在 `~/Library/Logs/xiaotian/<job>.log|.err`。时间均为北京时间（Asia/Shanghai）。

## 安装路径：主仓库 `~/xiaotian-wechat`（不另建部署目录）

`config.py`/`ui_lock.py` 都按代码所在仓库根（ROOT）找 `config.json`、`data/chat.db`、`data/wechat_ui.lock`、`data/mem0_qdrant`、`.venv`。
单独的部署 checkout 会得到第二套 chat.db 和第二把 UI 锁，与开发工具（rt_live、devtest、daemon --dry-run）抢微信窗口却互不知情，
所以 plist 固定指向主仓库。代价：主仓库工作区的代码就是线上代码 —— 改 `src/` 后 `tools/xiaotian_ctl.sh restart wechat` 才生效，
调试前先 `tools/xiaotian_ctl.sh stop wechat proactive`。

## 任务与作息

| job | 时间 | 为什么 |
|---|---|---|
| wechat | **默认禁用**（RunAtLoad=false；`disable-ocr`） | OCR/mac UI 对照用；真发走 iLink。显式 start 且 `ocr_daemon_enabled=true` 才发送 |
| ilink | KeepAlive（`tools/ilink_weixin_run.py --send`） | 微信真发主路径；DEFAULT 含 ilink。若已有手动 PID，`start` 会跳过以免双开 |
| onebot | KeepAlive（`tools/onebot_qq_run.py --send`） | QQ OneBot 真回；DEFAULT 含 onebot。依赖 NapCat WS `:3001`；手动 PID 时 start 跳过 |

> **OCR vs 入站冻结：** `src/daemon.py` 的 `max_regenerate` /「对方一直在发，放弃本轮，下一轮再回」只属于上表 wechat OCR 路径（防过期半截回复，下轮仍回）。**不是**「他连发她就不回」；iLink `bridge.handle_inbound` 无此逻辑。主动侧「未回冻结」见 `docs/proactive-scheduler.md`（`unanswered_freeze_at`）。
| magpie | KeepAlive（`~/Applications/magpie.app` → :3425） | 本机 Codex/生图网关；DEFAULT 含 magpie。端口已被 `open` 占用时只 install、勿重复 bootstrap |

| proactive | 09:00–00:29 每 5 分钟（:02/:07/…） | 醒着才可能主动找他；真正是否发由 scheduler 随机 next_check（3–60min）、quiet 00:30–09:00、`is_sleeping`（周末约 10 点才起、周五六晚睡）、每日≤2 条等闸门决定 |
| campus | 09:17、18:43 + 0–10 分钟 | 早上到校、晚饭时刷学校新闻 |
| collect | 08/10/…/22/00 点 :05 + 0–30 分钟 | 实际 08:05–00:35，整段避开 source_poll quiet 01:00–07:30 |
| avatar-qq | 07:40 + 0–15 分钟 | **source_poll quiet（≈01:00–07:30）结束后**跑 QQ `--process-queue`，再偶发 `--album`；与 proactive quiet_hours 无关；不碰 iLink |
| materials-purge | 04:23 | 她睡得最沉的时候做维护 |
| reflection | 03:50 + 0–15 分钟，`--date 昨天` | 熬夜最晚到 3 点，之后整理前一天 |
| schedule-month | 周日 20:00 | 下月行程 `apply --inject-research --freeze-after && sync`；本地规则校验（无日程 review Agent）；**不**启 wechat |

注意：launchd 没有 `RandomDelay` 键（旧 collect 模板里的会被静默忽略），抖动改用 `bash -c 'sleep $((RANDOM % N)); exec …'`。

## 权限（必须用户在系统设置里手动给）

launchd 拉起的进程「责任进程」是 Python 本身，不继承终端/agent 的授权。需在 系统设置 → 隐私与安全性 给
`/opt/homebrew/Cellar/python@3.13/3.13.16/Frameworks/Python.framework/Versions/3.13/Resources/Python.app`
勾选 **屏幕录制**、**辅助功能**，并在 **自动化** 里允许控制 System Events / 微信 / Chrome。未授权时 wechat.err 会刷
`screencapture failed: could not create image from window`。Python 升级后路径会变，需要重新授权。

## 一键停止

`~/xiaotian-wechat/tools/xiaotian_ctl.sh stop`，
或 `for j in wechat ilink onebot magpie proactive campus collect materials-purge reflection schedule-month avatar-qq; do launchctl bootout gui/$(id -u)/com.xiaotian.$j; done`。

### 把现网手动 iLink 交给 launchd

1. `tools/xiaotian_ctl.sh install ilink`（复制 plist）
2. 确认 `launchd/com.xiaotian.ilink.plist` 的 ProgramArguments 为
   `.venv/bin/python tools/ilink_weixin_run.py --send`，WorkingDirectory=仓库根
3. 结束手动 PID（例 `kill <pid>`；**勿在未安装 plist 时乱杀**）
4. `tools/xiaotian_ctl.sh start ilink` → KeepAlive 拉起
5. `tools/xiaotian_ctl.sh status ilink` 应 LOADED=yes 且有 pid
彻底移除再删 `~/Library/LaunchAgents/com.xiaotian.*.plist`。
