# 小甜日程设计原则（拟真优先）

日期：2026-10-27（Asia/Shanghai）
机器：Mac mini `~/xiaotian-wechat`  
**核心思想（用户定调，必须刻进生成源头）：让小甜更拟真。拟真 > 好玩堆砌。**

**时区底层**：小甜所有日程 / 作息 / STATE 时间以 **Asia/Shanghai（东八区）** 为准；`slot_at` / `now` / `generate_day` / sync 写入勿用其它时区（见 `config.json` → `tz`、`life_calendar.tz`，`life_calendar.now_local` / `schedule.now_local`）。末槽 end 用 **24:00**（不用 23:59），半开区间 `a<=t<b` 盖满当日；quiet 终点=当日模糊 wake；`sleep_pressure` 功能不变。

## 人设锚点

- 北京舞蹈学院 · 创意学院 · 编导系（偏导演/编创）· 大三（2024 级；2026 年秋）
- 校学生会文艺部副部长；影像社、话剧社成员
- **兼职实习：小电影公司上班实习**（场记/资料/粗剪助理一类轻度实务，非全职）
- 对象：小熊 / 小熊（禁「小熊」）

## 公开常识归纳（北舞本科女生典型节奏）

基于公开报道/校园纪实（早功与基训、编导系「零号教室」、剧目排练、实验剧场实践等），**不是**把所有人设塞满每一天：

| 时段 | 工作日典型 | 周末典型 |
|------|------------|----------|
| 早 | 起床赶课；表演向常有早功，编导大三以课/基训/自习为主，不必天天 6:30 出早功表演感 | 赖床到 ~10 点很常见 |
| 上午 | 理论/技法/镜头语言等课，或图书馆 | 休息 / 轻娱乐 |
| 下午 | 基训/剧目/编创排练，或自习写阐述；**部分下午去电影公司实习** | 出门玩可集中 |
| 晚 | **多数晚上**：回宿舍冲浪、写作业、和男友微信；偶有社团/学生会；**城市娱乐稀疏（约每周 1–2 晚）** | 娱乐可丰富 |
| 睡前 | 洗漱 / 准备睡 → **必须留出可触发晚安的槽** | 同左，可更晚 |

要点：

1. **课 + 基训/排练 + 自习** 是平日骨架，不是「每晚探店剧本杀」。
2. **实习** 是大三真实感的一部分：每周随机 2～4 个工作日有半天/偶晚段，bucket=`work_internship`（电影公司）；不实习日上课或学校活动/偶出门。
3. **学习 + 网上冲浪 + 和男友聊天** 的可开口空档要多——否则主动节拍（surf_share / daily_chat / pre_sleep）吃不到合理槽。
4. **偶尔一两天娱乐丰富可以**；连续工作日每晚 `city_fun` = 假。
5. 周末/假日才把 city_fun / outdoor_outing 铺开；寒假旅弧另见 `seed_winter_schedule`。

## 生成规则（源头）

实现：`src/life_calendar.py::generate_day`（窗后自动日 + 月补齐同源）。  
活动池：`data/activity_pool.json`。  
落盘：`data/life_schedule_2026q4.json`（经 reasonableness 时长闸 + 拟真密度复查）。

| 约束 | 目标量级（工作日，非考试硬块） |
|------|--------------------------------|
| 白天 school_class / school_rehearsal / school_library | 为主 |
| work_internship | **每周随机 2～4 个工作日**有半天（下午为主，偶晚段）；ISO 周种子可复现 |
| 平日晚 city_fun / campus_fun | **稀疏**：有可用空档时每周 1–2 晚；所有星期可选，按疲劳降权；同周勿堆满 |
| 平日晚 dorm_surf / 自习 / 闲聊空档（weekend_home 或 campus_life） | **多数晚** |
| 每日末槽 | 显式洗漱/准备睡槽；若午夜前入睡，另接「睡觉」到24:00 |
| 周末 | 娱乐可集中；起床更晚；quiet 终点=当日模糊 wake（不再写死 10:00） |

禁止：为了「好玩」把 WEEKDAY_EVE_POOL 每晚必抽。调研素材只附灵感，不能直接覆盖饭点、睡前或本地活动。

## 晚间安排（2026-10-27）

`src/evening_plan.py` 先排已确认课业/实习/社团，再从空闲工作日晚无放回抽 1–2 晚。实际疲劳按实习和排练时长估计，权重 `w = 1 - 0.65 × fatigue`，晚实习后休息；已抽中某晚后，相邻晚权重乘 0.2。没有足够空闲时减少次数。

外出多数为30–90分钟小活动；不疲劳且时间足够时，以22%分支尝试电影、桌游、演出（90–150分钟）。活动时长之外独立安排往返、回寝和洗漱，单日活动轮换避免连续两晚相同。周末按周抽签，80%概率安排一晚外出，其余晚休息，避开出差和既有约定。休息晚也可分为写作业/画画/看剧/聊天等自然片段。完整旅行弧保留；单日兜底只安排北京本地活动，不随机跳到远途目的地。

日程补充 Agent 的提示词在 `.cursor/skills/xiaotian-life-enrich/SKILL.md`。独立日程 review Agent 已删除，落盘前仅跑本地规则。

## 与主动聊天 / emotion 的挂钩（只吃结构化字段）

主动节拍吃 `life_today` 的 `bucket` / `energy` / `photo_bias`（及槽边界），**不**另发明情绪分类器：

| 节拍流（顺序） | 靠什么保证 |
|----------------|------------|
| morning → schedule_brief | 起床槽 + bypass quiet |
| meal | 午饭/晚饭槽 |
| 活动中少聊 | mid_quiet：上课/排练/实习；WEIGHTS 低意愿 |
| activity_end | 活跃槽结束后 grace |
| surf_share 偶尔 | 宿舍/空闲桶 + 日限 |
| **pre_sleep 晚安（必须）** | 显式「准备睡/洗漱」槽 + must 节拍；普通回宿舍不触发 |

emotion soft-bias：只消费 `kind/bucket/energy/photo_bias`；outing/fun 开心、class/busy 疲惫；`sleep` 忽略日级 free；`work_internship` 进 busy；单日甜蜜/开心槽封顶；view 同时进聊天与生图表情。另合入 `hour_soft_bias`（白天 tease+playful；傍晚→睡前渐进甜/亲密；tip lean，禁「现在晚上所以更甜」宣告）。

## 模糊作息与睡眠压力（日程约束的一部分）

**禁止整点刻板**（不要每天固定 22:00 / 10:00）。起床、准备睡、入睡、周末/工作日 quiet 终点都在**模糊区间**内按日种子抽取，槽 `start`/`end` 用非整点分钟（如 22:17）。

| 项 | 实现 | 区间量级 |
|----|------|----------|
| 工作日起床 | `schedule.day_plan` → `life_calendar` 睡觉槽终点 | 约 07:00–08:15（高斯+夹紧） |
| 周末起床 | 同上 | 约 08:30–12:00 |
| 准备睡 / 入睡 | `_day_sleep_bounds`：入睡前 35–85 分钟起洗漱 | 准备睡约 21:20–23:25 起；入睡随 `sleep_*_night` |
| quiet 终点 | `proactive_scheduler._quiet_bounds`：**对齐当日 wake**（非固定 09:00/10:00）；起点 ±12 分抖动 | 深夜起 → 当日起床 |

**睡眠压力**（`schedule.sleep_pressure`）是日程约束的一部分：越晚越高，**仍可回复**用户，但压低主动意愿、拉长 `reply_delay`；非 must 主动在压力 ≥0.55 时跳过。

公式（可经 `config.json` → `life_schedule` 覆盖）：

```
p = 1 - base ** (-elapsed / scale)   ∈ [0,1]
elapsed = 当前分钟 - anchor   （过午夜且未 wake：用 t+1440）
```

默认：`anchor≈21:40`（1300 分），`base=1.15`，`scale=30` 分钟，`delay_factor=2.5`（`reply_delay *= 1 + factor·p`）。
主动权重：`weight *= (1 - 0.85·p)`。

## 频率旋钮（日程侧可调；beats 话术文件勿改）


见 `config.json` → `proactive`：`quiet_hours` / `quiet_hours_weekend`、`max_per_day`、`min_gap_sec`；  
`src/proactive_scheduler.py` → `WEIGHTS`（按活动名/前缀/bucket 对齐 life_calendar）。

## 相关文档

- 管线：`docs/schedule-update-pipeline.md`
- 时长闸：`docs/reasonableness.md`（拟真密度为附加复查，不替代时长）
- 主动：`docs/proactive-scheduler.md`
- 人设：`data/persona_dossier.json`
