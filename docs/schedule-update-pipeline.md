# 小甜日程更新管线

2026-10-27：日程补充 Agent 直接负责调研、时间与交通安排；独立日程 review Agent 已移除。写入前由本地规则校验，周日 20:00 的 `com.xiaotian.schedule-month` 使用同一入口。

| 步骤 | 入口与行为 |
|---|---|
| 人设与既有计划 | 读 `data/persona_dossier.json`、目标日期前后 7 天、学业硬块和用户约定 |
| 调研入池 | `tools/enrich_life_from_research.py research` / `upsert-pool`；本地短活动优先 |
| 补时长 | `update_month_schedule.py pool-duration`；可选模型只补活动池 |
| 生成计划 | `life_calendar.generate_day` + `evening_plan`；工作日按空闲/疲劳选每周 1–2 晚，本地往返齐全 |
| 调研灵感 | `inject_research_into_month` 只附本地灵感，不再覆盖活动、饭点或洗漱 |
| 写入闸门 | `reasonableness.review` 的本地时间/交通/学业规则通过才落盘，没有模型复审 |
| 冻结与同步 | 月更通过后冻结；`sync --cid demo_contact` 更新 STATE/mem0 |

```bash
.venv/bin/python tools/update_month_schedule.py plan --month YYYY-MM
.venv/bin/python tools/update_month_schedule.py apply --month YYYY-MM --inject-research --freeze-after
.venv/bin/python tools/update_month_schedule.py sync --cid demo_contact
```

冻结月份默认不再自动修改；用户明确要求的调整可以覆盖指定范围并留记录。已有用户 override、课程、实习出差和完整旅行优先，过去的聊天经历不重写。用户约定流程保留 `override-begin` / `override-confirm` / `override-apply`，学业硬块可拒绝安排；不会自动启动聊天发送。

已获用户要求更新未来晚间时，使用：

```bash
.venv/bin/python tools/update_evening_schedule.py --from-date YYYY-MM-DD
# 若用户同时授权调整冻结月份：
.venv/bin/python tools/update_evening_schedule.py --from-date YYYY-MM-DD --include-frozen --apply
```

旧的「日程 review」Grok Bot 和本地 Cursor 审查均不再派任务，不新建替代 reviewer。`REASONABLENESS_AGENT=1` 不能重新启用日程复审；只保留显式 `pool-duration --agent` 的活动池补充用途。自动日程任务的 plist 以 `launchd/com.xiaotian.schedule-month.plist` 为准，RunAtLoad=false。同步安装此 plist 后只重载该任务，不立即触发月更，也不启动 OCR 微信服务。

默认采集仍走锁与 `source_poll`；参考图用 `tools/queue_scene_refs.py` 闲时补齐。日程不编造具体演出场次、车次、已订票等事实。新计划通过 `tests/test_evening_plan.py`、日程/实习/合理度回归后同步 Mac mini。
