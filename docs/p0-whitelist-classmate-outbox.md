# P0：白名单情侣 / 同学人设 / QQ 回执 / iLink 退出码 / outbox SQLite

日期：2026-10-26（Asia/Shanghai）

## 规则
1. **情侣**：仅 `persona.contact_aliases` 明确映射到 `primary_contact_id`（demo_contact）的 peer；QQ 另受 `allow_user_ids` 约束（好友申请只批准白名单）。
2. **同学**：未映射陌生人 → `classmate:<channel>:<peer>`；称呼「X同学」；**禁止发图**；记忆在 `data/classmate_memory.db`，与 `~/digital-life/contacts/demo_contact` **隔离**。
3. **危险通配** `wechat_ilink:* → demo_contact` 已移除；若配置 star→primary 会被忽略。
4. **QQ 回执**：API echo / notice 写入 `data/qq_receipts.db`（TTL 6h，上限 2000），不进情侣记忆。
5. **iLink**：feeder 致命断开 → `run()` 返回 **1**，launchd `KeepAlive/SuccessfulExit=false` 可拉起。
6. **outbox**：`data/outbox.db`（SQLite WAL）；旧 JSON 自动 migrate 为 `*.migrated`；terminal TTL 48h，pending/terminal 有上限。

## 验证
```bash
cd ~/xiaotian-wechat
.venv/bin/python tests/test_channels_persona.py
.venv/bin/python -m pytest tests/test_outbox_sqlite_classmate_receipts.py -q
.venv/bin/python -c 'import sys;sys.path.insert(0,"src"); from channels.outbox_store import stats; from channels.qq_receipts import stats as rs; print(stats()); print(rs())'
tools/xiaotian_ctl.sh restart onebot ilink
tools/xiaotian_ctl.sh status onebot ilink
```

QQ primary / iLink backup：`proactive.primary_channel=qq_onebot`。
