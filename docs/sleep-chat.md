# 睡前 / 已睡聊法（sleep_chat）

日期：2026-10-27（Asia/Shanghai）
模块：`src/sleep_chat.py` · 配置：`config.json` → `sleep_chat`

与日常回复频率分开：少说、少气泡、拉间隔。**睡眠优先于随机分享和补发**：开始道晚安就暂停主动，不等待 600 秒的已睡判定。

**被动回复例外（2026-10-29）**：开启 `reply.engagement` 时，17:00至次日07:00对方来聊，`brain` 与 `channels.bridge` 对本轮使用热情回应方式，覆盖下述睡聊的提示词、裁短与慢间隔；不能只因原日程睡眠或他只发了一条就敷衍收场。对方明确道晚安/结束聊天仍收口，“别说晚安，我还想聊”不算告别。这个例外不清除持久化睡眠状态，不放行主动消息。完整优先级见 `docs/reply-burst.md`；下面模式表描述睡聊的默认规则。

## 模式

| mode | 触发 | 行为 |
|---|---|---|
| `pre_sleep` | 他说晚安/快睡吧等，或日程睡前窗（准备睡 / 末槽末段 / 睡觉槽） | 字数↓ 气泡↓ 间隔↑；提示模型收住；默认不配图刷屏 |
| `asleep` | 见下方判定 | 主动全抑；被 ping 也极短回 |
| `normal` | 其它 / 醒来后 | 日常密度 |

## 已睡判定（默认 `asleep_after_sec=600`）

1. **他发晚安后**，她 **10 分钟内没回** → `asleep`
2. **她发晚安后**，他 **10 分钟内没回** → `asleep`

状态挂在联系人 `STATE.json` → `sleep_chat`。`pending_goodnight` / `asleep_since` 控制回复密度；`goodnight_at` 记录晚安起点，`proactive_sleep_until` 是按实际日程计算的起床边界，控制主动暂停。对方回复「嗯」或夜里再次发消息，不清除主动暂停。

### 明确告别与睡眠话题（2026-10-29 修复）

`conversation_semantics.is_explicit_goodnight` 供 `sleep_chat` 使用，
`is_explicit_closing` 供 `reply_engagement` 使用，两者共用同一判断。
“晚安”“我先睡了”“睡吧小熊”才建立晚安状态；
“今晚记得早点睡”“别说晚安，继续聊”“闭眼想象一下”、问句、转述、引用不建立状态。
“先聊到这，明天再聊”是结束聊天，但不等于准备睡觉。
判断不确定时保留对话，不因出现一个睡眠关键词建立整夜主动暂停。

白天补觉修正（2026-10-29 下午）：她白天说「接着睡吧/快睡吧」属于劝他休息，不等于她自己道晚安。用户明确「我再睡会」「我睡个午觉」则暂停主动，日程睡眠以当前睡眠槽结束为准，其他白天会话小睡默认最多2小时（`daytime_sleep_max_sec`），用户重新来信提前结束。旧版本误设成次日起床的白天睡眠在 `refresh()` 中自动按此上限恢复，保留 `last_sleep` 起止供久未联系计时扣除。夜间晚安的次日起床规则不变。回归见 `tests/test_idle_contact.py`。

此修复不调整主动次数、间隔、甜蜜额度或17:00–07:00热情优先级。
验证见 `tests/test_conversation_fidelity.py`，同时覆盖真实晚安后的主动暂停。

## 醒来

- 夜里收到非晚安消息 → 睡聊状态机保留首轮 asleep 风格建议，但被动回复在上述晚间例外下热情接话；随机主动继续暂停。
- 自动恢复必须跨过**本次晚安之后的日程起床时间**，且此刻不在睡觉槽。优先读取同日 `STATE.life_today` / 落盘日程的 `sleep_bounds.wake`，其次首个睡觉槽的结束时间，缺失才用 `schedule.day_plan`。午夜后睡觉使用当天起床边界，晚上睡觉使用次日起床边界，周末按实际晚起时间。
- 不再用 `hour >= 8` 判断早晨；23 点不可能因此清除刚发生的晚安。

## 主动发送的硬门槛

- 当前日程为睡觉时，所有主动节拍停止，包括 `must`、`bypass_quiet`、`--force`。
- 洗漱/准备睡时停止随机分享和普通主动，只允许尚未道晚安时的 `pre_sleep` 收口。`resolve_life_now` 返回当前槽本身，不把它误当作嵌套 `slot`。
- 晚安后立即暂停所有主动；主动发送的晚安也写入同一状态，不依赖对方回复。
- 起草前、出队后、耗时生图后、每个文字气泡/整链/图片发送前，都按当前时间和最新状态重查。队列中旧的清醒槽不能覆盖现在的睡眠槽。
- QQ / iLink 的主通道、镜像及旧版主动 outbox 都检查；被睡眠拦截的在途余项作废并保留记录，不进入网络失败补发队列。普通用户消息的回复路径保留。
- 状态读取或睡眠检查出错时停止主动发送。`sleep_chat.enabled` 只控制睡聊风格/晚安状态功能，不能关闭日程睡眠硬门槛；旧 `asleep.suppress_proactive` 字段保留兼容，不能绕过已睡限制。

## 接入点

- `channels.bridge`：入站 `note_inbound` / 出站 `note_outbound`；`split_bubbles` + `bubble_gap_sec` 用 style
- `brain`：`state_block` 一行 + prompt「# 睡聊」；`clean`/`tighten_text` 夹密度；asleep 去掉 want_image
- `proactive_sender` / `proactive_scheduler.gates`：`should_suppress_proactive`
- `proactive_limiter.record_success`：主动晚安写入；`channels.outbox_send`：补发前的睡眠检查
- `bubble_split.bubble_gap_sec(..., scale=, min_sec=, max_sec=)`

## 旋钮（`config.sleep_chat`）

| 键 | 默认 | 含义 |
|---|---|---|
| `enabled` | true | 总开关 |
| `asleep_after_sec` | 600 | 道晚安后判睡秒数 |
| `pre_sleep.max_bubbles` | 3 | 睡前最多气泡 |
| `pre_sleep.max_chars_total` | 40 | 睡前总字数软顶 |
| `pre_sleep.max_bubble_chars` | 14 | 单气泡字数 |
| `pre_sleep.gap_scale` | 1.75 | 气泡间隔倍率 |
| `pre_sleep.gap_min_sec` / `gap_max_sec` | 1.5 / 4.2 | 间隔夹逼 |
| `pre_sleep.prompt_tip` | （见配置） | 塞进模型的睡前提示 |
| `asleep.*` | 更严一套 | 同上；主动停发为硬门槛 |
| `asleep.prompt_tip` | （见配置） | 已睡/夜戳提示 |

无需迁库；下一条入站或主动定时检查会刷新超时。旧 pending/asleep 状态自动推导晚安起点和起床边界。

离线回归：`.venv/bin/python tests/test_sleep_priority.py`，覆盖 22:47 晚安→23:02、跨午夜/时区/周末起床、夜里 ping、强制发送、耗时生图、逐段发送和两个通道补发。所有传输与联系人状态均打桩。

## emotion
- `emotion.sweet_mode(..., sleep_style=)`：`pre_sleep`/`asleep` 强制 `idle`（sad/angry 仍 `care`），不因缺席/示弱升糖。
- 由 `brain.intimacy_gate_block` 从本轮实际回复风格传入；晚间热情例外使用 normal，门控仍约束亲密词，不得把热情陪聊误解成堆亲密词。

## 睡前收口（P0）
- 实际按睡聊收口时整轮 ≤2～3 气泡；`pre_sleep.max_bubbles` 默认 3。晚间热情例外不使用此上限。
- 「还活动了？」= 他还没睡/还在玩，**不是**问日程行程；禁止新开行程梗。
- `tighten_text` 会丢掉明显行程句（明天/安排/路线…），除非同句在道晚安。

- 「活动 / 还活动了？」：夜里 = 还没睡/还在玩，**不是**日程活动；勿追问「啥活动」。

