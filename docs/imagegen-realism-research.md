# GPT Image 写实性调研（小甜生图）

日期：2026-10-24（Asia/Shanghai）  
范围：gpt-image-1 / Codex 内置 image_gen；提示词侧 + 参考图侧。目标：避免「全图从 0 生成」导致的假感，把小甜合理放进真实场景。

## 1. 结论摘要（可直接进 SOP）

1. **明确写 photorealistic / real photograph / iPhone photo**，比堆相机参数更稳（OpenAI Prompting Guide）。
2. **参考图顺序与角色要标注**：Image 1 = 身份标准，Image N = 场景底板；合成时写清「把谁放进哪、保什么、改什么」。
3. **场景底板 > 从零造景**：多图编辑（最多约 10 张）时，把真实空景/远景照片作为 base，再要求匹配光照/透视/比例把人物放进去；`input_fidelity=high` 时**第一张**细节保留最好（脸放第一或紧跟身份 sheet）。
4. **像手机随手拍，不像棚拍广告**： candid / off-center / slight motion blur / mild JPEG / harsh flash / flyaway hair / visible skin texture；避免 8K、studio lighting、perfect composition、cinematic。
5. **结构顺序**：背景/场景 → 主体姿态与动作 → 材质与光 → 约束（保脸、不暴露、不换人）。
6. **编辑式 vs 参考式**：
   - *edit/insert*：「Image k 是真实场景照片；把参考图中的女生放进该场景，匹配原图光照/透视/焦段；尽量保留原背景与相机角度。」
   - *match location*：「场景光影、陈设、景深要贴近 Image k，人物身份以 canonical 为准。」  
   两者都要重复「preserve identity / face from canonical」。
7. **缺陷只加在背景/肢体**，脸保持清晰（与现有 realism.py 一致）；正式/寺庙等场景继续 ban 闪光灯类。

## 2. OpenAI 文档要点

| 来源 | 要点 |
|---|---|
| [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation) | 多图输入；mask 只作用在第一张。 |
| [Generate and edit with GPT Image](https://developers.openai.com/cookbook/examples/generate_images_with_gpt_image) | `images.edit` 可传入多张；prompt 描述合成结果。 |
| [High input fidelity](https://developers.openai.com/cookbook/examples/generate_images_with_high_input_fidelity/) | `input_fidelity="high"` 保脸/logo；**列表第一张**纹理与细节保留最强；耗更多 image input tokens。 |
| [Prompting Guide](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide) | 结构：scene→subject→details→constraints；写明 photorealistic；多图用「Image 1 / Image 2」点名；编辑用 change only X + keep everything else；人物写清比例/视线/手部动作。 |
| [Image prompting](https://developers.openai.com/api/docs/guides/image-prompting) | 相机参数当观感线索，勿当物理保证。 |

Codex/`$imagegen` 走的是 Responses 里的 image 工具，不一定暴露 `input_fidelity` API 字段；等价做法是：**身份 sheet 放最前**，场景底板紧随其后或按「edit 时第一张=底板、身份紧随」做 A/B，并在提示词里显式锁定五官。

## 3. 社区提示词实践（X / Reddit）

反复出现、且与「不像 AI」相关的信号：

- **媒介**：`shot on iPhone` / `amateur smartphone snapshot` / `unremarkable candid photo` / `RAW iPhone look`
- **构图**：`slightly awkward angle` / `off-center framing` / `tilted` / `no perfect composition`
- **光学缺陷**：`subtle barrel distortion` / `mild motion blur` / `JPEG compression` / `direct flash` / `overexposed highlights`
- **人体**：`visible skin texture and pores` / `flyaway hair` / `tired eyes` / `mid-action`（走路、低头看手机）
- **反模式**：`8K ultra HD`、`DSLR 85mm studio`、`cinematic god rays`、`beautiful perfect skin` → 更容易塑料感

参考讨论：

- Reddit r/singularity「unremarkable amateur iPhone photos」(2025-04)  
  https://www.reddit.com/r/singularity/comments/1jwe2z8/you_can_get_chatgpt_to_make_extremely_realistic/
- Reddit r/ChatGPT「Words to use to get a normal photo」(2026)  
  https://www.reddit.com/r/ChatGPT/comments/1rw0wn4/words_to_use_to_get_a_normal_photo_of_a_person/
- PromptHero 等汇总的 iPhone candid 模板（amateur / off-center / barrel distortion）

本仓库已有 `data/realism_modifiers.json`（97 条）覆盖同类短语；调研建议：**在有场景底板时降低「从零造景」权重，把随机修饰当作辅料**，并增加「匹配参考场景的光源方向与色温」类约束。

## 4. 参考图策略（针对 579 场景）

### 4.1 为什么要场景底板

全图生成时模型同时发明：人脸、身体、服装、室内外结构、道具、光。结构越「像海报」越假。  
先检索**同场景真实照片**（优先无人/人脸极小），再要求「人物进入该照片的空间」，模型只需解决插入与光照一致性，背景纹理来自真实照片。

### 4.2 选图标准

- 优先：空景、过道、远景人群、背影小、无清晰可辨人脸
- 避免：大脸网红自拍、水印墙、明显 AI 空景、过度 HDR
- 去重：sha256 + 感知哈希（pHash）
- 元数据：scene_id、source_url、embedding、quality、people_free

### 4.3 生成时挂载

> **Deprecated（历史调研顺序）**：下列 1～4 已不是现行 Chat 挂图。现行 HARD：`canonical/sheet.png`（脸）+ `canonical/body/sheet.png`（身体）+ 其余 3 槽 `scene_refs`；见 [`docs/body-orbit-yikao-sop.md`](body-orbit-yikao-sop.md) / commit `a2e6e2f`。本节保留作调研记录。

顺序建议（Codex `-i`，历史）：

1. canonical `sheet.png`（身份，唯一标准）
2. 可选姿态 sheet（仰头/低头/背影）
3. **1～2 张 scene_ref**（真实场景）
4. 其余 2～3 张角度/表情参考（头肩）

提示词模板要点：

```
Image 1 = 身份标准五视图（唯一长相来源）。
Image k = 真实场景照片（背景/光影/透视以它为准）。
把小甜放进 Image k 的空间：姿态与场景活动匹配，服装见穿搭段。
匹配 Image k 的光源方向、色温、景深与噪点；不要另造一座建筑。
脸/五官/发型严格跟 Image 1；不要照搬其他参考图的衣服与构图。
photorealistic candid phone photo, vertical…
```

两种可比模式：

| 模式 | 提示重心 | 适用 |
|---|---|---|
| `insert` | 「以场景图为底，插入人物，保留原机位」 | 空景清晰、透视简单 |
| `match` | 「地点/陈设/光线要像场景图，允许轻微重绘」 | 原图有路人需避开、或构图不适合直接插入 |

## 5. 与当前管线的差距

| 现状 | 建议 |
|---|---|
| 只有身份 + 姿态 + 随机写实短语 | + 场景向量库 top-k 真实图 |
| `build_prompt` 不区分参考图角色 | 按 index 标注 identity / scene |
| 场景完全由文案生成 | 场景以图为主、文案为辅 |
| realism 随机 0～3 条 | 有 scene_ref 时强制加「匹配场景图光影」句 |

## 6. 风险与开放问题

1. Codex image 工具是否支持等价 `input_fidelity=high`？（需 A/B：场景图放第 1 vs 第 2）
2. 插入真人进第三方照片的安全审核率？
3. 中文特色场景（北舞排练厅、燕园宿舍）公开图少，是否要更多小红书只读采集？（「燕园宿舍」非 SSOT：校区见 `data/persona_dossier.json`；燕园仅用户侧天气默认）
4. CLIP 图嵌入 vs bge 场景名嵌入，哪个更利于「自由文案场景」检索？
5. 场景图版权：本地缓存仅供参考输入、不外发；生成结果仍是新图。

## 7. 来源列表

- OpenAI Cookbook: generate_images_with_gpt_image  
- OpenAI Cookbook: generate_images_with_high_input_fidelity  
- OpenAI Cookbook: multimodal/image-gen-models-prompting-guide  
- OpenAI Docs: guides/image-generation, guides/image-prompting  
- Reddit r/singularity 1jwe2z8 (2025)  
- Reddit r/ChatGPT 1rw0wn4 (2026)  
- 本仓库 `src/realism.py` / `data/realism_modifiers.json` / `tools/codex-skill/xiaotian-imagegen/SKILL.md`
