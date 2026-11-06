# 小甜换头像策略（QQ）

给聊天 bot / 运维：相册与小红书两条路径的频率与排队。**当前只做 QQ**（NapCat `set_qq_avatar`）；微信 WDA / `avatar_iphone` 先不做。勿动 iLink。

## 两个「quiet」别搞混

| 名称 | 配置 | 默认窗口 | 谁用 |
|---|---|---|---|
| **source_poll quiet** | `config/poll_limits.json` → `defaults.quiet` | **≈01:00–07:30** | 小红书检索、`avatar` **换头队列**（`enqueue` / `peek_ready` / `--process-queue`） |
| proactive `quiet_hours` / `quiet_hours_weekend` | `config.json` → `proactive` | 约 00:30–09:00（周末到 ~10:00） | **仅**主动私聊调度，与换头队列无关 |

队列文案里的「quiet 结束后」一律指 **source_poll quiet 结束（默认 07:30）**，不是 proactive 起床时间。

## 相册（`--album`）

- **无 14 天硬约束**（`album_min_days` 默认 0）。
- **允许人像**（自己相册人像包）：不做标题真人词 / Vision 人脸硬筛；不走品牌号过滤、不走 source_poll。**小红书调研选图仍禁真人脸**（见下节 / `safe_reason`）。
- 频率跟「心情 / 小甜心波动」+ 随机 + 最短间隔：
  - `mood.min_interval_h`（默认 6）：距上次 **album 真实更换** 不足则跳过。
  - `mood.mood_min`（默认 6.0）：心情低于阈值且无 `sweet_pulse` → 跳过。
  - 日概率：`daily_p`（0.28）+ 高心情加成 + 小甜心波动加成；**同一自然日只掷一次**（`data/avatar/album_gate.json`）。
- CLI：`--mood 8 --sweet-pulse` 可覆盖门控输入；`--force` 跳过门控。

## 小红书检索（`--auto`）与队列

- **取消时长硬约束**：`xhs_min_days` 默认 **0**。
- **必须尊重** `source_poll`（`config/poll_limits.json`）：**source_poll quiet ≈01:00–07:30**、平台/全局日上限、最小间隔、captcha backoff。
- **排队**（`config.avatar.queue_path` → `data/avatar/queue.json`）：
  - 今日 `daily-cap` / `global-daily-cap` 仍想换 → **入队到明天 source_poll quiet 结束后第一号**（priority=0）。
  - `quiet-hours`（source_poll）→ 排到今日 quiet 结束（默认 07:30）。
  - 启动时 `--auto` / `--process-queue`：不在 source_poll quiet 内且 `earliest<=now` 则优先 dequeue。
- 静默勿加 `--xhs-force`。仅本地池：`--auto --no-search`。

## QQ 换头互斥（process-queue ↔ album）

- 文件：`data/avatar/last_change.lock`（字段 `last_avatar_change_ts`）。
- 默认 `config.avatar.qq_change_min_h` / `cross_change_min_h` = **24**：24h 内 QQ 侧不连换（队列兑现与偶发相册互斥）。
- 真实 `changed` 成功后写入；`--dry-run` / `--test` 不写、不拦；`--force` 跳过。
- 微信换头暂不做，故暂无跨端对齐。

## launchd

- 标签：`com.xiaotian.avatar-qq`
- 时刻：**每天 07:40**（source_poll quiet 默认 07:30 结束后）+ 0–15 分钟抖动 → 约 07:40–07:55。
- 动作：先 `avatar_qq.py --process-queue`；再按 STATE 心情偶发 `--album`（门控 + 互斥；多数天会 `mood-skip` / `daily-miss` / `change-interval`）。
- 生成：`tools/gen_launchd.py`；安装加载：`tools/xiaotian_ctl.sh install avatar-qq && tools/xiaotian_ctl.sh start avatar-qq`。
- **不**碰 iLink / wechat daemon。

## 用法（dry-run 冒烟）

```bash
.venv/bin/python tools/avatar_qq.py --album --dry-run --mood 8 --sweet-pulse
.venv/bin/python tools/avatar_qq.py --auto --no-search --dry-run
.venv/bin/python tools/avatar_qq.py --process-queue --dry-run
```

勿动 iLink；密钥不要打印。真换头像去掉 `--dry-run` 且需 NapCat WS。
