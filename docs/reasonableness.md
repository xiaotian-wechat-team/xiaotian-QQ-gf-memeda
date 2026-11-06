# 日程本地校验

2026-10-27 起删除独立日程 review Agent。日程补充 Agent 负责完整排程，`src/reasonableness.py` 只执行本地确定性校验，不调用 Cursor/Grok、不改写日程。旧 `use_agent` / `--agent` 参数兼容保留，但不能重新启用日程 Agent；结果标记 `agent_hook.removed=true`、`invoked=false`。

校验覆盖活动所需天数/小时、跨城交通间隔、实习出差连贯性、学业与娱乐密度。逐槽拒绝没有完整旅行弧的长途活动，避免仅检查每天一个主要活动而漏掉睡前「瞬移」。新晚间计划还检查全日时间连续、本地去程/活动/返程完整。活动质量与 `rating` 不参与时长分数。

```bash
.venv/bin/python src/reasonableness.py --all-days
.venv/bin/python tests/test_reasonableness.py
.venv/bin/python tests/test_evening_plan.py
```

活动池保留已有旅行预算：崇礼滑雪通常 4 天、至少 3 天；禾木通常 7 天、至少 5 天；三亚默认 12 天、至少 7 天。跨地需独立交通日。完整寒假弧由 `tools/seed_winter_schedule.py` 维护，单日生成器不自行安排远途。

活动池缺失时长仍可由补充流程写入 `activity_pool.json` 的 `reasonableness` 字段；这个入口不允许改 `life_schedule`：

```bash
.venv/bin/python tools/update_month_schedule.py pool-duration
# 如明确选择模型补充池时长
REASONABLENESS_AGENT=1 .venv/bin/python src/reasonableness.py --pool-duration --agent
```

只有上述池时长入口使用 `REASONABLENESS_AGENT`、`REASONABLENESS_MODEL`（默认 `grok-4.7-high`）、`REASONABLENESS_AGENT_BIN` 和超时参数。月更 launchd 不再设置它们。

人设事实来自 `data/persona_dossier.json`；晚间规则见 `src/evening_plan.py`，补充提示词见 `.cursor/skills/xiaotian-life-enrich/SKILL.md`，实习线路规则见 `config/schedule_review.json`。校验命令不启动微信或发送消息。
