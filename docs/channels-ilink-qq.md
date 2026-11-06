# 多通道：自建 iLink 微信 + QQ OneBot + 共享人格记忆

日期：2026-10-24（Asia/Shanghai）  
机器：Mac mini `~/xiaotian-wechat`  
硬规则：**不启动** OCR/`daemon.py` wechat 轮询；**不安装 OpenClaw**；记忆主面仍是 **日程差分账本**（`schedule_memory`）。

## P0 白名单 / 同学人设（2026-10-26）

- 仅 `contact_aliases` → `demo_contact` 的 peer 走情侣小甜；陌生人 → `classmate:<channel>:<peer>`（`data/classmate_memory.db`，禁图，「X同学」）。
- QQ API/notice 回执：`data/qq_receipts.db`（短 TTL）；outbox：`data/outbox.db`。
- iLink feeder 致命断开 exit 1 → launchd KeepAlive。详见 `docs/p0-whitelist-classmate-outbox.md`。

## 目标

| 线 | 内容 | 状态 |
|---|---|---|
| A | 微信：自建 Python iLink（`weixin-ilink` / 同协议 CGI）→ `channels.bridge` | 通道已接；试用中（勿杀 PID） |
| B | QQ：NapCat（OneBot v11）→ 现有 Python `brain`，**不要 DSH** | 适配器 + launchd KeepAlive `com.xiaotian.onebot`；协议端 Colima+Docker |
| C | 共享记忆：按 **persona/contact**，不按通道分叉 | `persona_map` + config.persona |
| D | 记忆形态：日程表挂情绪点 + 核心事件差分；高重复日常不记 | `src/schedule_memory.py` 已测 |

## 架构

```
微信 iLink（Python weixin-ilink / 自建） ──┐
                                          ├─► channels.bridge.handle_inbound
QQ OneBot WS/HTTP (SnowLuma/NapCat) ──────┘         │
                                                    ▼
                                        resolve_contact_id → demo_contact
                                                    │
                          ┌─────────────────────────┼─────────────────────────┐
                          ▼                         ▼                         ▼
                    mem0 user_id=demo_contact      STATE/diary/emotion      life_schedule.memory_ledger
                    (可选二级向量)           (PAD 共享)              (核心事件+情绪差分·人读主账本)
                                                    │
                                                    ▼
                                                 brain.reply
```

OCR Mac 微信（`mac_wechat_io`）暂时保留作对照，试用期不要并行抢同一会话发送。  
**OpenClaw / Node Gateway 不是本项目路径**（协议文档仍可参考）。

## (a) 微信 iLink — 自建（主路径）

协议参考：

- https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md
- Python SDK：https://github.com/zongrongjin/weixin-ilink（`pip install "weixin-ilink[qr]"`）

```bash
cd ~/xiaotian-wechat
.venv/bin/pip install 'weixin-ilink[qr]'          # 若尚未装
.venv/bin/python tools/ilink_smoke_check.py check
.venv/bin/python tools/ilink_weixin_run.py login   # 扫码
.venv/bin/python tools/ilink_weixin_run.py --dry-run --once
.venv/bin/python tools/ilink_weixin_run.py --send
```

配置：`config.json` → `persona.channels.wechat_ilink`（`credentials_file`）。  
扫码成功后会把 `wechat_ilink:<userId>` 写进 `persona.contact_aliases` → `demo_contact`。

## (b) QQ OneBot — 适配器已就绪，等协议端

### NapCat（本机已选路径，2026-10-25）

用户选定 **NapCat**（非 SnowLuma）。Mac mini 无 `/Applications/QQ.app`，故用官方 Docker 镜像 + Colima（比 Docker Desktop 轻）：

| 项 | 值 |
|---|---|
| 目录 | `~/xiaotian-wechat/tools/napcat` |
| 镜像 | `mlikiowa/napcat-docker:latest` |
| 正向 WS | `ws://127.0.0.1:3001`（`persona.channels.qq.ws_uri`） |
| WebUI | `http://127.0.0.1:6099/webui`（token 在 `tools/napcat/.env`，勿提交） |
| 扫码图 | 启动后 `tools/napcat/data/cache/qrcode.png`（容器内 `/app/napcat/cache/qrcode.png`） |

```bash
export PATH="/opt/homebrew/bin:$PATH"
colima start                                          # 若未运行
cd ~/xiaotian-wechat/tools/napcat && docker compose up -d
# 扫码登录（手机 QQ 扫 qrcode.png 或开 WebUI）后：
cd ~/xiaotian-wechat && .venv/bin/python tools/onebot_qq_run.py --probe
```

原生备选：App Store 装 QQ 到 `/Applications/QQ.app` 后用 [NapCat-Mac-Installer](https://github.com/NapNeko/NapCat-Mac-Installer)；本机未走此路。  
**勿杀** iLink PID（`tools/ilink_weixin_run.py`）。

### KeepAlive（launchd）

`com.xiaotian.onebot` → `tools/onebot_qq_run.py --send`（与 iLink 并行）。

```bash
tools/xiaotian_ctl.sh install onebot
tools/xiaotian_ctl.sh start onebot   # 若已有手动 --send PID 会跳过
tools/xiaotian_ctl.sh status onebot
```


**重要（Mac mini）**：已选 **NapCat**（见上节）。SnowLuma 无 macOS 原生包；本机用 **Colima + NapCat Docker**，正向 WS `:3001`。原生 Mac Installer 需先装 App Store QQ，未采用。

```bash
# 0) 依赖
.venv/bin/pip install websocket-client websockets

# 1) 无协议端：本地假 WS 冒烟
.venv/bin/python tools/onebot_mock_server.py --port 3001 &
.venv/bin/python tools/onebot_qq_run.py --probe --ws ws://127.0.0.1:3001
.venv/bin/python tools/onebot_qq_run.py --dry-run --once --ws ws://127.0.0.1:3001
# 另开：curl -s -X POST http://127.0.0.1:3002/inject -H 'content-type: application/json' \
#         -d '{"user_id":10001,"text":"mock 在吗"}'

# 2) 不连网：伪造入站 → bridge（默认 stub、不写 demo_contact 库）
.venv/bin/python tools/onebot_qq_run.py --self-test
.venv/bin/python tools/onebot_qq_run.py --inject '在吗' --stub-reply '嗯？'

# 3) 真协议端已起（SnowLuma/NapCat WS :3001）
.venv/bin/python tools/onebot_qq_run.py --probe
.venv/bin/python tools/onebot_qq_run.py --dry-run --once   # 收一条，不算发送
.venv/bin/python tools/onebot_qq_run.py --send             # 真回（与 iLink 并行，共享 demo_contact）

# 4) 可选：HTTP 事件上报（协议端 POST → 本进程）
.venv/bin/python tools/onebot_qq_run.py --http-listen 127.0.0.1:8765 --dry-run
```

配置：`config.json` → `persona.channels.qq`：

| 键 | 含义 |
|---|---|
| `ws_uri` | 正向 WS，默认 `ws://127.0.0.1:3001` |
| `ws_token` | OneBot access_token（WebUI / onebot.json；可空） |
| `http_api` | HTTP API 根，默认 `http://127.0.0.1:3000`（HTTP 模式发信用） |
| `webui_uri` | SnowLuma WebUI，默认 `http://127.0.0.1:5099` |

把用户 QQ uin 写进 `persona.contact_aliases["qq_onebot:<uin>"] = "demo_contact"`（试用已有通配 `qq_onebot:*`）。

### 用户需要提供 / 完成的

- [x] 能跑 QQ+OneBot 的环境（Colima + NapCat Docker，`tools/napcat`）  
- [ ] QQ 扫码登录（`tools/napcat/data/cache/qrcode.png` 或 WebUI `:6099`）  
- [ ] 若开了 token：填 `persona.channels.qq.ws_token`（**不要**把 token 贴进聊天）  
- [ ] （建议）精确别名：`qq_onebot:<你的QQ>` → `demo_contact`

**不需要** AppID / 开放平台机器人密钥（SnowLuma 走真人 QQ 客户端注入，不是 QQ 开放平台 bot）。

## (c) 共享记忆键

- **主键**：`persona.primary_contact_id`（现 `demo_contact`）= mem0 `user_id` = `digital-life/contacts/demo_contact`。
- **别名**：`wechat:*` / `wechat_ilink:*` / `qq_onebot:*` → 同一 id（试用通配；稳定后改精确 peer）。
- **日程差分**：`data/life_schedule_*.json` 的 `day.memory` + `memory_ledger.weeks[]`（全人格一份）。
- **聊天库**：仍写同一 `chat.db` contact；通道名进日记前缀 `[qq_onebot]` / `[wechat_ilink]`。
- **不要**：按微信/QQ 各建一套 mem0 collection 或两份 STATE。
- **smoke**：`--inject` 默认 `persist=False`，避免污染正在跑的 iLink 会话。

## 成功判据

微信：「**不用 OpenClaw，自建 iLink 通道已接上**」——`tools/ilink_weixin_run.py login` / `--dry-run`。

QQ：「**OneBot 正向 WS 探测 OK，干跑能进 brain**」——`tools/onebot_qq_run.py --probe` 后 `--dry-run --once`。

## 发图（bridge → 双通道）

`handle_inbound` 在 `brain.reply` 后读 `brain.LAST["want_image"]`，经 `chat_media.plan_chat_images` 生图，出站设 `Outbound.meta.image_path`（配文在 `text`）。

- 微信 iLink：`ILinkWeixinRunner._send` 已支持 `send_image`
- QQ OneBot：`send_private_image_ws` / `send_private_image_http`（image 段，本地文件转 `base64://`）

单测：`tests/test_bridge_want_image.py`（mock 生图，不刷真聊）。

## iLink 发图硬化 + Clash DIRECT（2026-10-25）

`send_image` 失败闭环：`prepare failed` / 非 0 `ret` 视为失败并抛错；**仅有配文 `message_id`不算成功**，必须拿到 IMAGE（type=2）的 `message_id`。发送前从 `data/ilink_ctx.json` 刷新 `context_token`，缺失则拒绝发图。

E2E：`data/devtest/e2e_want_image_ilink.py` 以 `image_message_ids` 判 ok，caption-only mid → fail。

若代理路径触发 `prepare failed`，可在自己的代理客户端中将 `ilinkai.weixin.qq.com` 设为 `DIRECT`，再重试通道自检。公开快照不包含任何本机代理配置。

## 主动消息通道策略（2026-10-26）

`config.json` → `proactive`：

| 键 | 默认 | 含义 |
|---|---|---|
| `transport` | `ilink` | `ilink` / `onebot` / `qq` / `ui` / `auto` |
| `channel_policy` | `primary` | `primary` 只走主通道；`mirror` 主成功后再尽力发次通道（失败进 outbox） |
| `primary_channel` | `wechat_ilink` | `wechat_ilink` 或 `qq_onebot`（`transport=auto` 时生效） |

默认 **primary + wechat_ilink**：主动节拍不双发微信/QQ。QQ 入站仍由 `com.xiaotian.onebot` 真回。

## Proactive prepare-failed（P0，2026-10-26）

`transport=ilink` 且 `prepare failed` / stale `context_token` 时：**只入 `ilink_outbox`，不走 UI**（互斥）。outbox 不可用时才允许 UI。UI SENT 成功会清除同 id outbox，防 inbound flush 双发。

launchd `com.xiaotian.proactive` 传 `--once --no-ui`（见 `tools/gen_launchd.py`），纯 ilink/outbox。

Outbox 条目可带 `image_path` / `require_media`；flush 发附件。`surf_require_media` 条目若图发不出 → HOLD/DROP，**禁止降级纯文字**。

`send_via_onebot`：仅 `retcode==0` 算 SENT（`status=ok` 且非 0 retcode 不算）。

QQ `flush_qq_outbox`：夜里/正聊丢弃陈旧晚安（与 iLink 对齐）。QQ 无 iLink feeder，暂无气泡中 `should_cancel`。

## QQ outbox

`data/outbox.db`：主动/发送失败入队，下一条入站回复之后 `flush_qq_outbox`。iLink/QQ 共用 SQLite、按 channel 隔离；旧 JSON 仅供迁移。

## 消息可靠性与预览（2026-10-27）

- Outbox `load` 返回版本与原快照；`save` 只合并修改过的条目，不因快照缺少某 ID 而删除它。重复入队同 ID 不覆盖原记录；修改旧版本抛 `OutboxConflict`，须重新读取。终态清理由 TTL/cap 负责。iLink/QQ flush 各自持线程锁和进程文件锁，同通道不能并发补发。
- iLink 最多连续重生成三轮；到上限时保留 inbox 中的新消息，交给外层 worker 下一轮处理。
- OneBot HTTP 文字、图片、语音须返回 `status=ok`、`retcode=0` 及 `data.message_id` 才算发送确认；异常向 bridge 传播，失败气泡不落已发送记录。此前已确认的气泡仍保留。
- bridge 整轮串行，包括生成、等待、生图和发送，防止 HTTP 并发覆盖 `brain.LAST`。长生图期间后续请求会等待。
- bridge/brain 的 `dry=True` 禁止写聊天库、STATE、日记、日程 override 和 mem0；预览使用文件记忆、已有天气缓存，跳过 ASR/识图缓存与日程探针写入，Cursor 使用临时会话。即使传 `persist=True` 也不能覆盖 dry 限制。预览上下文因此可能少于真实聊天。

回归测试（临时数据库、模拟传输与模型，不发真消息）：

```bash
.venv/bin/python tests/test_channel_review_regressions.py
.venv/bin/python tests/test_onebot_bridge.py
.venv/bin/python tests/test_outbox_send_dedupe.py
.venv/bin/python tests/test_bubble_split.py
```

## want_voice（QQ）

`brain.LAST["want_voice"]` → bridge 调 `tools/tts_doubao_mvp.synthesize`（不改 TTS 实现）→ `Outbound.meta.voice_path` → OneBot `record`。缺 `config/doubao_voice.env` 时软失败、退回纯文本。入站 image/record 归一成 `[图片]` / `[语音]`；couple 路径在 bridge 里 Magpie（auto/gpt-6-luna）识图成 `[图片：…]`，优先 NapCat fetch_ptt_text（QQ 内置 translatePtt2Text），失败再 mlx-whisper → `[语音：…]`（失败保留裸标签）再进 brain hist。

## fail-closed（QQ，P2 摘要）

- 白名单空或不匹配 → 不入 brain、不回
- 主动 onebot HTTP `retcode!=0` → HOLD + `qq_outbox`，不落 SENT
- TTS 缺凭据 / 合成失败 → 不发伪语音条，退回文本（若仍有 text）
- `channel_policy=mirror` 次通道失败不推翻主通道 SENT
- **今晚不做** 双通道真发 E2E；明日再验

