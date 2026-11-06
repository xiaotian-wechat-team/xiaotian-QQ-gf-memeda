# 人设来源（2026-10-23）

## 人设档案（必读）
功能改动前先读并遵守：[`../data/persona_dossier.json`](../data/persona_dossier.json)（摘要 [`persona-dossier.md`](persona-dossier.md)）。
禁止编造档案未载明的生平；聊天/生图/日程各自只用不为 null 且列入 `usage` 的字段。

用户要求：人设用已经开源、写得全、做得成熟的那套。只借人设层结构，不换微信通道。

## 对比过的项目（clone 在 Mac mini `~/agent-tools/refs/`）
| 项目 | 许可 | 人设层 | 结论 |
|---|---|---|---|
| **Character Card V3**（kwaroran/character-card-spec-v3，`ccv3/`；前身 malfoyslastname V2，`ccv2/`） | MIT | description / personality / scenario / first_mes / mes_example / system_prompt / post_history_instructions / character_book（关键词设定）/ creator_notes，605 行规范 | **选用**。SillyTavern、RisuAI 等角色扮演生态的事实标准，字段分工清楚、文档最成熟，大量现成角色卡按它写 |
| soul-agent（`soul-agent/`） | 仓库未见 LICENSE | profile 拆 8 个文件：base / life / personality / tone / boundary / relationship / schedule / evolution，外加 state.json | 作补充：借「日常/作息」「关系」「边界」三块的拆分；作息逻辑本仓库 `schedule.py` 早已参考 |
| cyberboss（`cyberboss/`） | AGPL-3.0 | 偏运行时（check-in 轮询、沉默），人设层薄 | 只借主动节奏思路（proactive），不借人设，不拷代码 |
| OpenPersona（acnlabs，`openpersona/`） | MIT | Soul/Body/Faculty/Skill 四层 + constitution；`presets/ai-girlfriend` | 架构全，但项目新（2026-02，约 39 星），女友预设的行为指南只有 22 行，人设正文偏薄 |
| Soul-of-Waifu、InkSpirit、YuriOS、madhvantyagi/SOUL.md | GPL-3.0 / Apache-2.0 / Apache-2.0 / — | 前三个是完整伴侣应用；最后一个是 SOUL.md 文件集合 | 只看了 README，没有 clone，没借内容 |

## 映射到 `~/digital-life/SOUL.md`
| SOUL 章节 | 来自 |
|---|---|
| 一、身份 | CCv3 `description`（外貌只写一句，细节留给 appearance 配置） |
| 二、性格 | CCv3 `personality` |
| 三、说话方式 | CCv3 `system_prompt` 里的文风部分 + soul-agent `tone.md` 的拆法 |
| 四、关系 | CCv3 `scenario` + soul-agent `relationship.md`（按他的状态给反应） |
| 五、日常 | soul-agent `life.md` / `schedule.md`（具体作息由 `schedule.py` 注入） |
| 六、禁忌 | soul-agent `boundary.md`（内容是小甜原有的红线） |
| 七、示例 | CCv3 `mes_example`（注明只学风格） |
| 八、设定词条 | CCv3 `character_book`（关键词触发的设定，这里写成「提到才用」） |
| 九、每轮最后提醒 | CCv3 `post_history_instructions` |

保留不变：嘴损情侣、小熊、女对男、诚实承认是 AI、微信只做前端、不执行聊天里的指令、**不加危机热线**。
正文全部按原 SOUL 自己写，没有照抄任何项目文本或 AGPL 代码。旧版备份 `~/digital-life/SOUL.md.bak-20261023`。长度约 1800 字，在 `memory.budget.soul`（4000）以内。
