# 人设数字档案（指针）

**SSOT：** [`../data/persona_dossier.json`](../data/persona_dossier.json)

- 只写已确认/用户授权补全的字段；其余保持 `null`，禁止编造。
- 新功能开发前必读该 JSON；聊天发送路径须覆盖 `usage.chat_must_read`，生图走 `usage.imagegen_must_read`（含全身环视 `appearance.body_orbit`）。
- 全身环视标准与 `-i` 槽位：[`body-orbit-yikao-sop.md`](body-orbit-yikao-sop.md)。`body_orbit.status=approved` 时聊天生图 **HARD** 附脸五视图 `canonical/sheet.png` + 身体五视图 `canonical/body/sheet.png`，其余 3 槽 `scene_refs`（`a2e6e2f`）。
- 语言：[`language-pack.md`](language-pack.md) / `data/language_pack.json`。
- 叙事正文仍在 `~/digital-life/SOUL.md` 与 [`.cursor/skills/xiaotian-chat/SKILL.md`](../.cursor/skills/xiaotian-chat/SKILL.md)；档案是字段索引与约束。
- 结构来源说明见 [`persona-source.md`](persona-source.md)。

聊天事实统一由 `src/persona_context.py` 读取：身份、学校和事实主体常驻，家庭、室友、偏好与相识/纪念日按话题提供。2026 年秋为 **2024 级大三**，2006-05-15 出生；档案空值或未载明的细节不补写。公开校园新闻只证明报道过，不能推导小甜本人参与。

实习扩展（2026-10-27）：活动树 `data/internship_tree.json`；本市外出、室外踩点、外景跟组、案头协助及低频外地出差。交通与停留审查配置 `config/schedule_review.json`，不编公司/客户/项目名。

实习频率（2026-10-28 用户更新）：每周从合格工作日随机抽 2–4 天，每天一个半天或晚段；室内与外出尽量均衡，空档允许时每周至少一个晚段外拍。课业、考试、已确认约定与睡眠优先。SSOT 见 work.internship.cadence。
