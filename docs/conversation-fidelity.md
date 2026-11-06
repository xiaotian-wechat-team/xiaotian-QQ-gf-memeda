# 对话事实与日程语义校验

人设事实来源：`data/persona_dossier.json`。修复日期：2026-10-29。

`src/conversation_semantics.py` 为无 I/O 的共用判断：

- `is_explicit_goodnight(text)` 只识别此刻明确睡前告别。
- `is_explicit_closing(text)` 另包含“不聊了/先聊到这”等非睡眠告别。
- `is_current_self_claim(clause, match_start)` 区分当前自身动作与对方、过去、未来、否定、引用、假设。

日程过滤 `schedule_fidelity.enforce_outbound_text` 只修复有冲突的分句，保留其余回应。
例如实习槽的“我刚下课，你实验没成功也别急”只纠正前半句；
“等我回宿舍陪你慢慢聊”“你明天上课别迟到”原样保留。
纯规则无法理解全部自然语言，证据不足时不强行改成整句套话。

生图的 `scene_matches_slot(scene, slot, *, imagined=False)` /
`force_scene_for_slot(slot, scene, *, imagined=False)` 在泛化“校园”提示前检查具体学校。
学校默认从人设档案读取；明确日程访问别校时以槽地点为准。
北京大学是用户学校不能把她的北舞 OOTD 自动迁到北京大学。
`imagined=True` 只能由上游基于用户明确想象请求传入；模型自行生成的场景标签不能授权跳过。
调用者应标注其为想象场景，不能写成小甜实际到访经历。

`is_imagined_request` 仅从用户明确、未否定、未引用的虚构请求取值；“生成/画一张今天照片”仍是现实场景。
此标志在 `brain` 日程补场景之前计算，经 `chat_media.enrich_scene` 传给
`photo_scene.suggest_scene`，虚构场景在读取当前地点前直接保留；没有模型场景时使用用户的设想描述。
`plan_chat_images(..., imagined=True)` 清除现实 `slot/today/shot_bits` 和校园提示，
首张配文标明“想象场景”，不套用“随手拍了几张”话术；返回值包含 `imagined`，
出站与图片存储也应保留虚构标记。

## 午夜睡眠兼容

新生成日程的 `sleep_bounds.sleep_min` 保存从当日零点累计的入睡分钟，可大于1440；
`sleep_bounds.late` 保留晚睡标志。
读取旧日程时，`repair_midnight_carryover` 仅分割第一段 `00:00` 开始的睡眠占位槽，
把上一晚尚未睡觉的时段延续到实际入睡点，再接原起床时间；白天所有槽原样保留。
`slot_at` 与旧 `STATE.life_today` 的 `resolve_life_now` 使用同一投影，避免缓存重新覆盖结果。
投影仅适用当前和未来，历史日记与旧日程不重解释；不写回真实日程文件。

旧数据没有 `sleep_min` 时，必须同时满足前一天末槽为宿舍洗漱/准备睡、
`prep_end=24:00`，才按原有 `schedule.day_plan` 日期种子恢复被裁掉的分钟；没有重新抽样。
用户明确授权本轮修复午夜截断，因此冻结月份也允许这一窄兼容投影，冻结元数据保持原样。
显式 `user_override`、day/slot `locked` / `frozen`、旅行，以及相关槽已有执行状态，仍优先保留。
冻结月份中的非旧格式日程不能用兼容例外覆盖。

`sleep_rollover` 记录 `reason`、`source`、`from`、`sleep_min`、
`frozen_compatibility` 与本次授权范围，便于审计；再次读取不会重复分割。
准备睡前40分钟仍按现有主动睡眠门槛处理；已明确道晚安后的暂停不会被清醒投影解除。
主动频率、概率、每轮计账规则、17:00–07:00被动热情均不在本次日程修改中调整。

离线验证：`.venv/bin/python -B tests/test_conversation_fidelity.py`。
