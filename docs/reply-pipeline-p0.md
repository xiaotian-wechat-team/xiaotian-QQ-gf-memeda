# 回复管线 P0（outbox / 中断 / 原子落库）

## 1. Outbox vs 当前入站
- **先** `handle_inbound` 回当前 them，**再** `flush_ilink_outbox`。
- 陈旧晚安（`sleep_chat` 晚安句）在 **夜里** 或 **正聊**（本次有入站文案）→ `status=dropped`，不插队。
- 非上述场景的残留可标 `补发` 再发（带「（补发）」前缀）。

## 2. 等待中新消息
- iLink `run()` 用 feeder 队列；等待/气泡间隔里 `should_cancel` 窥同 peer 新消息。
- 取消未发草稿；已发气泡已落库。按最新 them **重生成**（中间条先 insert，短 merge）。

## 3. 分气泡原子性
- **每条** WeChat 发送成功 → 立刻 `chat.db` insert；**首条**成功 → `mark_replied`。
- 禁止「微信已发出 / DB 仍 unreplied」。中断后 `cancelled=True`，未发部分丢弃。

## 4. 昵称
- `bubble_split.merge_sole_nick_bubbles`：active nick 禁止单独成气泡，并入邻条或丢弃；sole-nick ≤1/轮。
