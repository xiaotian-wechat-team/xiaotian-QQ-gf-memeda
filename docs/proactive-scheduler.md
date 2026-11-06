# 主动联系与分享节奏（2026-10-28）

人设 SSOT：`data/persona_dossier.json`。适用 QQ primary；不启用 OCR 微信。

## 当前规则

2026-10-29 新增 `proactive.idle_contact`：每次实际互相联系后稳定抽取90–150分钟，累计清醒时间到点即主动联系；重复扫描、重启、生成失败不重抽。每5分钟的既有worker检查，因此触发可比抽定时刻晚至多约5分钟，另加生成/传输耗时。任一通道demo_contact真实入站或确认发出的正文/照片均重置；草稿、发送失败、未知回执不算联系。队列的 `idle_contact` 保存起点、阈值、已累计清醒秒数、剩余秒数与到期记录。

到期事件先于普通候选，不受普通主动10轮、分时额度、随机留白、图片比例或模型SKIP阻挡，另记 `STATE.proactive_idle`；原普通主动参数、晚间被动热情不变。成功后普通主动也从这次联系开始计算间隔，避免随后紧挨着又发。每轮可以撒娇问近况、只分享当前日程，或只发一张当前场景照片；图片不可拍/生成失败退为自然文字。发送前再核对新联系、暂停与睡眠；未知回执占住同一事件，不自动重发。

白天「接着睡吧」是对他的建议，不登记为她自己晚安。明确「我再睡会/睡个午觉」暂停主动，默认最多2小时（`sleep_chat.daytime_sleep_max_sec`），用户重新来信提前结束；日程中的睡眠仍以实际结束时间为准。旧版本误挂到次日的白天会话睡眠按该上限自动修复。清醒计时同时扣除日程睡眠与会话睡眠，重叠不重复扣除。明确夜间晚安仍暂停到日程起床。

事件发现和实际发送分开。全天每 5 分钟扫描全部候选，`next_consider_at` 只用于没有节拍时的普通闲聊。
未回复、忙碌、旧 `no_double_text`、`mid_quiet_buckets`、`max_beat_per_day`、`meal_p` 均不阻断事件发现。
候选逐个评估，不因首个候选受限而隐藏后续可发事件。忙碌只改变文案长度。

统一限制器 `src/proactive_limiter.py`：

- 一次成功主动联系算一轮；同轮多个气泡、图片、合并的报备只计一次。
- `daily_contact_range=[10,10]` 固定每天最多10轮；社交分享、随机问候、关心及其补发共用预算。早安 `morning`、晨间行程 `schedule_brief`、吃饭 `meal`、晚安 `pre_sleep` 独立记入 `proactive_routine={day,count}`，不扣这10轮；身份以事件ID判定，不能靠文案绕过额度。未回复不重新抽签、不冻结；普通被动回复不占主动额度。
- 每次普通主动联系成功后，按 `last_budgeted_proactive_at` 稳定抽间隔：17点前75–120分钟，17点后35–65分钟，最短仍不低于30分钟；重复轮询不重抽。12点前累计最多2轮、17点前4轮、20点前7轮，其余留给晚间。每30分钟只抽一次统一触发机会，概率按7/12/17/20/22点的15%/25%/55%/85%/95%插值；发送前、失败补发均检查。上述日常报备不受普通长间隔、累计额度及随机留白限制，只保留120秒短防连发、事件去重和睡眠闸门。
- 社交分享单独在 1–5 次间抽取上限，相隔至少 2 小时（跨日也检查），同时受当天10轮总额度约束。不保证填满分享配额，睡觉、晚安和不合适的素材都可跳过。
- 关心分9–12、12–15、15–18、18–20、20点至睡前五个窗口，每窗最多一次，不追补错过的窗口。关心状态、接着他提过的小事、好奇近况、随口问候择一，不盘问、不催回复。22点前为最多三轮关心机会预留剩余额度，睡前收口可以使用；不要求凑够。
- 可用候选按日期和已完成事件稳定加权随机排序（关心3、分享1.5、晚间问候1.5、其余1）；日常报备按当前事件窗口优先，准备睡觉时优先收口。`must` / `bypass_quiet` / 旧 `surf_ignore_max_per_day` 不能突破总量、间隔或睡眠门控。
- 平台在每个题材内先分配权重，再选素材，避免抖音库存多吞掉小红书机会；上一分享平台权重乘 0.35。保留题材的 50% 轻松向、5% 校园、45% 其他分配和同题材抑制。
- 小红书沿用 Mac mini 的 `src/collectors/xiaohongshu.py`、`com.xiaotian.collect` 和现有 Chrome Default 登录；`/search_result/笔记ID` 允许作为内容链接，搜索列表页仍排除，并与 `/explore/笔记ID` 共用去重标识。
- 生图等待气泡在发出后存入聊天记录与 `recent_photo_holds`（最多 8 条）；模型会看到最近用语，换标点/同义找光也会被拦截。失败兜底同样避重，不为了提示等待而重复一句话。
- 起床和行程同窗合成一条自然近况，共用回执；错过早窗可在 17:00 前补充当前近况，禁止假称刚起床。
- 其他事件只在当前窗口考虑，不把未发送的草稿积攒到夜里；过期队列沿用 TTL。
- 睡眠、明确暂停主动联系、用户消息待回复仍延后发送。精确的「先别打扰我」「不要主动找我」暂停，明确「可以主动找我了」「恢复主动联系」恢复；普通引用不触发。
- 用户真实入站记 `last_inbound_at` 并将未回轮数清零，不再用 bot 的 `last_reply_at` 判定未回。

事件审计位于 `data/proactive_queue.json` 的 `events_day/events`，含 triggered、limited、generation_skipped、sent、merged 及原因。
日常报备沿用事件窗口：早安和晨间行程合并一次，每个饭点一次，睡前一次；同事件已完成或有待补发队列时不再生成。睡着后不补发晚安。普通轮次和日常报备均只有传输确认成功才写 STATE 各自计数与 beats.done；`proactive_receipts` 按 round_id 幂等，补发成功使用同一记账入口。
队列先保存 sent 回执，再原子更新 STATE，重启补记账而不重发；旧版本 sent 队列不会被升级重计。
主动 worker 与 outbox 补发使用同一跨进程锁，避免并发发送绕过预算。outbox 补发仍复核新状态、额度和日程新鲜度。

## 2026-10-27 内容节奏修正

日预算和10分钟防连发下限不变；下限不是目标频率，不因未回延长间隔。
排查发现节拍持续候选会绕过普通闲聊的25–45分钟考虑间隔，而任务提示强求钩子、久未回分支强化嘴硬/轻嗔，容易产生找话和索取回应。
本轮先纠正内容选择：允许有内容地多分享和平实陈述，不要求句句有梗或问句；未接上的问题不换个说法继续问。久未回只提供分享新近况的机会，不强化调侃或委屈；没有新进展允许 SKIP。
生成后对最近6小时自己的消息做保守的原文去重（仅忽略空白和标点），重复草稿跳过且不记 done、不扣额度；新事实、不同感受和后续报备仍可发送。不用语义相似度阈值，避免把同话题的新进展一并封住。
提示词约束需要后续真实聊天观察；离线回归不能证明模型永远不会贫嘴。

## 验证与部署

离线：`.venv/bin/python tests/test_proactive_unanswered.py`；另跑 proactive、proactive_scheduler、proactive_beats、proactive_sender、channel_review_regressions、sync_preserves_state、schedule_fidelity、outbox_send_dedupe、sleep_chat。

线上（仅用户明确授权时）：Mac mini 拉取已测提交；用本机路径重新生成并安装 proactive plist（288 个时间点）；串行启动 onebot/proactive，勿启 OCR。
先备份相关运行状态、核对登录，再使用真实当前时间、不改联系人 STATE/未回计数、不用 `--force` 运行一次正常 proactive。
端到端通过需有生成正文、QQ 成功回执/message_id、队列 sent、STATE 一次扣额及 beat done；紧接第二次应限流/等待而非重复发送。
不得把 `use_llm=False` 的仿真结果作为真实投递成功。

## 原设计背景（以下为历史记录，冲突时以上述当前规则为准）

# 主动聊天忙闲调度（src/proactive_scheduler.py）

核心目标：贴近真实情侣互动频率，**不刷屏**。定时器只唤醒模型**决定** send|skip，不是每次到点就发。

## 忙闲值与考虑间隔
- `B = busy_value(now)` ∈ [0,100]：由作息意愿权重反推（越闲 B 越低），上课/排练等忙碌活动再按 `busy_factor`（默认 0.3）抬高 B。
- `T_consider = consider_t_min_sec * (consider_t_max_sec/consider_t_min_sec)^(B/100)` 秒，再 ×`consider_jitter`（默认 0.7–1.3），clamp 到 `[5, 1800]`。
  - B=0 → ~5s；B=50 → ~95s；B=100 → 1800s（30min）。
- `next_consider(now)`：休息（睡觉 / quiet：起点≈00:30±12分，**终点=当日模糊 wake**，非固定 09:00/10:00；config 的 `quiet_hours`/`quiet_hours_weekend` 仅作起点与回退）→ 跳到醒来后再随机 0–30 分钟；否则 `now + T_consider`；**成功发送后**改为 `now + min_gap_sec`（默认 1800–3000s）。非 must 在 `sleep_pressure≥0.55` 时 SKIP；pre_sleep 等 must 仍可发。

## 硬门槛（gates）

先检查睡眠/晚安及暂停主动状态，然后检查未处理的对方消息、分享独立限额与间隔、全体主动联系间隔、当天总额度。发送前及 outbox 重试重复检查；`force`、`must` 不能豁免。历史固定预算配置未设置 `daily_contact_range` 时仍保留旧兼容算法；当前生产配置使用本页的10轮与晚间倾斜规则。

## 模型决定（替代 0.15 lottery）
- `probability` 默认 **0**（关闭随机抽奖）。若 >0，仅作软过滤：`p × weight(活动) ×(待办加成)`。
- 到点后选题 → `brain.reply(task=...)`：模型输出带**具体钩子**的一句，或不适合时 `[SKIP]`。
- 空招呼 /「在吗/在么/在不在」草稿直接丢弃。

## launchd / `--once` 与设计如何共存
`launchd/com.xiaotian.proactive.plist` 仍可每 5 分钟跑 `proactive_sender.py --once`。  
日历覆盖 **01:02–23:57**（及 00:02–00:27）：早窗 1–8 必须有 tick，否则起床报备窗口空转；是否真发仍由 gates/节拍决定。  
`--once` **尊重** `data/proactive_queue.json` 的 `next_consider_at`（兼容旧字段 `next_check_at`）：未到点 → `WAIT` 退出，不会和忙闲间隔打架。  
若将来接常驻 in-process 循环，以 `next_consider` 睡眠为准，launchd 可卸掉；在那之前 cron 只是廉价敲门。

## 发送路径（分工）

| 模块 | 职责 |
|------|------|
| `src/proactive.py` | **只出草稿 / 硬门槛**（`should_initiate`、quiet 对齐）。**无真发路径**；`python -m proactive` 仅打印。 |
| `src/proactive_scheduler.py` + `proactive_beats.py` | `tick()` 选题/节拍 → `DRAFT` |
| `src/proactive_sender.py` | **唯一真发**：入队 / TTL / 去重 / 发送前重检 → `transport=ilink\|ui\|auto` |
| iLink（默认试用） | `send_via_ilink`；runner=`tools/ilink_weixin_run.py --send` |
| UI/OCR | 仅 `transport=ui` 时窗口核对；**不得与 iLink 并行真发** |

硬规则：`com.xiaotian.wechat` OCR daemon 默认 `disable-ocr` / `ocr_daemon_enabled=false`；微信真发走 iLink。白名单仅联系人 demo_contact。


## 日程节拍（src/proactive_beats.py）

软情侣分享，**不是**系统报备。`tick()` 优先选节拍，再才走 lottery 话题。

| 节拍 | 触发 | 闸门 | 内容 |
|------|------|------|------|
| morning | 起床槽开始后 `morning_window_min`（默认 90）内 | must；bypass quiet **与 mid_quiet**；睡觉仍禁 | 早起招呼/报备；文案走模型，禁固定「早上好」 |
| schedule_brief | 起床后约 2.5h 内一次 | must；bypass quiet+mid_quiet | 今日 life_today 亮点随口说，禁列表腔 |
| evening | `evening_start`～`evening_end`（默认 17:00–21:30）一次 | must；bypass quiet+mid_quiet | 傍晚照面式报备；文案走模型，禁固定「晚上好」 |
| meal | 槽位活动匹配吃饭，且 `meal_p`（默认 0.45）稳定命中 | **非 must**（可选） | 碎碎念 + material_store 真图优先，否则 `WANT_IMAGE` |
| surf_share | 非 mid_quiet；槽内≥15min；日目标 `surf_min..surf_max`（默认 1～5，日期稳定随机） | 非 must；须截图 | 仅小红书/抖音；`material_store` 检索；原链写入 `shares` |
| activity_end | 活跃槽结束后 `activity_end_grace_min` 内 | — | 刚才感受/一个细节 |
| daily_chat | 白天空闲且今日尚无闲聊触达 | must | 有钩子闲聊 |
| silence_nudge | `last_reply`≥`silence_min_hours` **或** 未回主动轮次≥软阈值；日 ≤`silence_max_per_day`（默认 1） | soft_reach：破 no_double；非 must | 情绪/语义向量软撒娇（怎么不找我气质），禁固定模板/催读/报数 |
| pre_sleep | 准备睡/末槽末段 | must；bypass quiet+mid_quiet | 晚安/嘱咐 |

**中段安静** `mid_quiet_buckets` 默认 `school_class` / `school_rehearsal` / `work_internship` / `sleep`：上课排练不刷 surf/daily_chat；`bypass_quiet_for`（morning/schedule_brief/evening/pre_sleep）**不会**被 mid_quiet 静默丢掉。  
**睡觉**：主动全禁；用户夜里 ping 仍由 iLink bridge 回复（短拟人垫，无睡觉长等待）。
**会话已睡** `sleep_chat.asleep`（双方道晚安后静默≥`asleep_after_sec`）：主动强压制，见 `docs/sleep-chat.md`；与日程睡觉槽独立。  
**冲浪库**：`~/xiaotian-assets/materials`（小红书/抖音/微博），与 collectors 同源；特征=槽位 activity + scene_hints + bucket 词 + photo_bias。

### knobs（`config.json` → `proactive.beats`）
- `enabled` / `max_beat_per_day` / `morning_window_min` / `evening_start`+`evening_end` / `pre_sleep_window_min` / `activity_end_grace_min`
- `meal_want_image` / `meal_p`（吃饭可选概率） / `surf_min_per_day`+`surf_max_per_day`（日上限 1～5） / `surf_require_media` / `surf_platforms` / `surf_prefer_media` / `mid_quiet_buckets` / `surf_ignore_max_per_day`
- `must` / `bypass_quiet_for`（同时破 quiet_hours 与 mid_quiet） / `must_ignore_max_per_day` / `soft_reach`+`silence_*`
- 顶层：`unanswered_miss_at` / `unanswered_miss_freq_slope` / `unanswered_miss_freq_cap`（替代已删的 `unanswered_freeze_at`）
- 发送：`proactive.transport` = `ilink` | `ui` | `auto`（试用默认 ilink）

状态：`STATE.proactive_beats = {day, done, last_slot_key, shared_material_ids, surf_target, shares[]}`。
`shares` 跨日保留（id/platform/source_url/title/image_path/top_comments/shared_at），`brain.state_block` 注入最近几条供续聊评论区；素材本体仍在 `~/xiaotian-assets/materials`。

### 本次本地验证记录

- `test_proactive_unanswered.py`：24 个隔离回归通过；其中触发不被旧额度遮住、首候选回退、冷却不遮新事件、漏早窗补近况这 4 个行为用例在未修改的 c9be093 上全部失败，修复版通过。
- channel_review_regressions、proactive_beats、sync_preserves_state、schedule_fidelity：pytest 共 89 项通过；另通过主动调度/发送、QQ bridge、outbox 去重、iLink fail-closed、sleep_chat 和配文图片顺序脚本。
- 已知既存失败：`tests/test_memory.py:15` 对 `brain.clean` 的旧文本断言，在未修改的 c9be093 隔离副本也失败；本次未改 clean 实现或该断言。
- 本地未做真实 QQ 发送；部署后需另取 Mac mini 的真实回执。

- 补充：旧主动测试脚本改为在 pytest 子进程中运行，避免收集时的全局 monkeypatch 污染其他模块。主动回归、旧主动脚本、节拍、通道回归、状态同步、日程保真及 outbox 去重合并运行 120 项通过。

## 主动附图下限（2026-10-28）

`proactive.image_share_min_ratio=0.6`，普通分享/问候共用每天10轮上限及分时节奏。日常报备不强制带图，不能因为睡前不可拍照而丢掉晚安。
图片比例分母仍为全部实际成功轮次（普通主动计数 + 日常报备计数），不是缩小分母。普通候选的 `proactive_media.required` 按成功轮次计算下一轮：`图片轮数 < ceil(0.6 × (已成功总轮数 + 1))` 时必须附图。
例如10轮成功联系的目标是6轮图片；日常报备优先，暂时不足由后续合适的普通联系承担，不为凑比例延误报备或睡眠。社交平台封面和日程生图均计入，单轮多张只计一次。
新增 `life_photo` 日程分享候选，主动关心也可随生活图自然衔接。只用当前/刚结束的实际日程，电影散场才可拍大厅。

生图路径是既有 schedule_shot → chat_media.plan_chat_images → imagegen → 主通道图文发送。
必须附图的轮次由元数据驱动，不依赖模型是否输出 WANT_IMAGE；生成失败/文件丢失不能退成纯文字。
计数在完整发送成功后写入 STATE.proactive_media；失败/入队不计，重试成功同一 receipt 幂等计一次。
旧纯文字普通联系重试也重检比例，日常报备重试同样豁免强制图片。睡眠、photo_ok=false、交通中、过期、去重和日额度始终优先，不为比例补发。
上线当天保留旧计数；历史无图片回执的轮次不猜测为成功图片，次日自然开始完整统计。
