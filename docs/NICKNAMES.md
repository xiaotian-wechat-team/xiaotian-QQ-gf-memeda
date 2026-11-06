# 昵称库与语境选称呼

## 人设档案（必读）
功能改动前先读并遵守：[`../data/persona_dossier.json`](../data/persona_dossier.json)（摘要 [`persona-dossier.md`](persona-dossier.md)）。
禁止编造档案未载明的生平；聊天/生图/日程各自只用不为 null 且列入 `usage` 的字段。

- 数据：`data/nicknames.json`，377 条不重复（2026-10-23 从 395 条清理，见 nickname-slang-notes.md）（宁缺毋滥，不凑 1000），每条含 `name`、`category`、`gender`（m/f/n，带性别的称呼保留并标注，由用户决定取舍）、5 维坐标 `vec`（0~1）：`sweet` 甜度、`tease` 调侃、`intimacy` 亲密、`serious` 正式/认真、`playful` 活泼。
- 生成：`tools/llm_nicknames.py`（cursor-agent 非交互）。`gen`：按 11 类场景（sweet/tease/intimacy/serious/playful/food_animal/dialect/daily/pet_suffix/english_mix/bear）各生成候选并按词义打分；`review`：分批审核「真实情侣会不会这样叫」、嘴损情侣关系、小熊相关保留，剔除生硬拼凑/侮辱/不雅/爸爸主人类并重新打分；`build`：去重、禁词过滤、去掉仅差 ~/呀/啊/哒 的水词变体与少量人工剔除项，再由模型标注 gender。中间产物在 `data/nick_candidates/`。旧的规则拼词脚本 `tools/gen_nicknames.py` 已删除。
- 选择：`src/nickname.py` 的 `pick(message, state) -> (nickname, vector, reason)`
  1. 目标向量 = 损友基线 `[.6,.4,.7,.2,.55]` + `STATE.json` 的 `mood`/`energy`（0~10）调整 + 对方最新消息情绪（关键词规则：撒娇/难过/生气/开心/认真/调侃）调整。
  2. 排除 `contacts/demo_contact/STATE.json` 里 `recent_nicknames` 的最近 20 个；难过/认真/生气时排除 `tease > 0.25` 的昵称。
  3. 余弦相似度取 top-12，温度 0.05 的 softmax 采样。
- 接入：`brain.build_prompt()` 调 `_nick()`，提示词注入「这次称呼对方为：X」，并写回 `recent_nicknames`。
- 测试：`/opt/homebrew/bin/python3.13 tests/test_nickname.py`（离线，不改 STATE、不发消息）。
