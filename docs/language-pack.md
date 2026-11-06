# 语言包（轻量）

## 人设档案（必读）
功能改动前先读并遵守：[`../data/persona_dossier.json`](../data/persona_dossier.json)（摘要 [`persona-dossier.md`](persona-dossier.md)）。
禁止编造档案未载明的生平；聊天/生图/日程各自只用不为 null 且列入 `usage` 的字段。


- 数据：[`../data/language_pack.json`](../data/language_pack.json)
- 模块：[`../src/lang_pack.py`](../src/lang_pack.py) — 检测英/法/粤语倾向，给 `brain` 一行提示词；**不**加载大模型权重。
- 人设字段：`data/persona_dossier.json` → `languages`

## 能力
普通话（默认，厦门/闽南甜美+会损）、英语、法语、粤语（点到为止）。

## 开源参考（未 vendoring 权重/大词库）
| 项目 | 用途 |
|---|---|
| [OpenBuddy](https://github.com/OpenBuddy/OpenBuddy) | 多语聊天能力参考 |
| [XPersona](https://github.com/HLTCHKUST/Xpersona) | 多语 persona-chat 研究范式 |
| [OpenCC](https://github.com/BYVoid/OpenCC) | 未来可选简繁/方言变体转换 |
| [Digital-human-live2d](https://github.com/aalizelau/Digital-human-live2d) | 普/粤/英陪伴交互参考 |

实际句子仍由 LLM + `xiaotian-chat` skill 生成；语言包只约束「何时切换、别炫技」。
