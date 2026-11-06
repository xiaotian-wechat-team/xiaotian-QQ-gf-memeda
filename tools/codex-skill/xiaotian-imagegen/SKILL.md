---
name: xiaotian-imagegen
description: Generate a photo of 小甜 (the xiaotian-wechat persona) with the built-in image_gen tool, always keeping her real face and style from the local reference library (~/xiaotian-assets/appearance/<profile>/raw/, 451 photos). Use when asked to 生图 / 画小甜 / 给小甜拍一张 / make a selfie or scene picture of 小甜 or 她, or when xiaotian-wechat's imagegen.py calls Codex. Do not use for images of other people or for generic illustrations.
---

# 小甜生图

## 人设档案（必读）
功能改动前先读并遵守：`data/persona_dossier.json`（摘要 [`docs/persona-dossier.md`](../../../docs/persona-dossier.md)）。
禁止编造档案未载明的生平；聊天/生图/日程各自只用不为 null 且列入 `usage` 的字段。

目标：画出来的必须是**参考图里的同一个女生**（小甜），不是另一个长得像的女生。

## 人物外貌锁定（小甜身份 · 写入本 SOP，勿只靠临时 description 短语）

> 长相/体型是**稳定身份规则**，放在本女性向生图 SOP（本 SKILL）里；`description.md` 只作客观补充，**禁止**写会偏姿态的默认动作（托腮/歪头/回眸/低头）。
> 模型 README 若存在，只写用法/后端，不写外貌。

每次生图必须遵守（提示词 + 参考图双保险）：
- **脸与发型**：东亚年轻女生；黑色及腰超长直发（中分或微侧分，偶碎发，发尾微弯，几乎总披发）；白皙肤色；淡妆偏韩系/清透眼妆、豆沙/粉调唇。
- **体型**：身高约 **173 cm**、体重约 **50 kg**，模特身材纤细修长；头身比与四肢长度符合该身高；与门框/桌椅/走廊透视尺度一致，勿偏矮或头大身小。全身比例以 `canonical/body/sheet.png`（全身环视五视图）为标准；SOP：仓库 [`docs/body-orbit-yikao-sop.md`](../../../docs/body-orbit-yikao-sop.md)。
- **身份来源**：有露脸镜头时，第 1 张 `-i` 必须是 `canonical/sheet.png`，脸部身份只认它；有全身镜头且 body approved 时，第 2 张必须是 `canonical/body/sheet.png`。纯手/腿/第一视角按对应模卡与场景构图，不强行露脸。多图混合按整批需求选参考，不能因为第一张不露脸就漏掉后面全身照的脸。
- **禁止**：换脸、生成另一个长相似的女生、照搬身份参考的棚拍背景/穿着；不暴露、不性感化。
- **换装硬约束**：face/body sheet HARD **只参考脸型与身形比例**，**绝不是衣服参考**（identity + body proportion only, not clothing）；Outfit MUST change for scene；禁止灰上衣黑短裤艺考模卡装（not grey tee / not model-card outfit）。
- 配置：`config.json` → `appearance.body.height_cm/weight_kg`（默认 173/50）；换人设改 `appearance.profile`。


## 参考库
- 当前 profile：读 `~/xiaotian-wechat/config.json` 的 `appearance.profile`（目前是 `zhuzhuxiaoye`）。
- 参考库 = `~/xiaotian-assets/appearance/<profile>/raw/` 下的**全部** 451 张照片，每一张都算参考图。`refs/` 只是精选快捷方式，不是全部。
- 索引：`~/xiaotian-assets/appearance/<profile>/index.json`。每张照片有：`quality`（Apple Vision 人脸质量）、`face_area`、`yaw`、`bbox`、`scene`（outdoor/indoor/cafe/campus/night）、`modest`/`covered`（机器打的衣着标签，仅供参考，**选图不再使用**）、`usable`（单人正脸清晰）、`group`（同一篇笔记，也就是同一次拍摄）。
- raw/ 有新照片时重建索引：`cd ~/xiaotian-wechat && .venv/bin/python tools/build_ref_index.py`

## 每次生图的步骤
1. **挑参考图**（每次重新挑，不要一直用同一组）：
   body approved 时不要按下面旧「3 张头肩」手动挂图；以「参考图：脸五视图 + 身体五视图 HARD + 3 场景」为准（或直接跑 imagegen.py）。下列步骤仅作无 body / 排障补充。
   `cd ~/xiaotian-wechat/src && ../.venv/bin/python refpick.py "<场景>" -n 3`
   脚本从全部 `usable`（单人正脸清晰，约 159 张）的照片里挑，**不按穿着过滤**（用户已确认这些照片都是她的长相），只按人脸质量、场景命中和多样性排序，加一点随机，每个 `group` 最多取 1 张。它把照片裁成头肩近景 jpg（`<profile>/cache/*_face.jpg`）：锁定同一张脸，也避免原图穿着触发安全审核。
   如果手动挑：照同样的规则从 index.json 的 usable 里选 3 张，确认是清晰正脸，并且用裁好的头肩图。
2. **把挑出的图作为参考图附上**。如果你是 `codex exec`，就用 `-i a.jpg -i b.jpg -i c.jpg`，提示词从 stdin 传（`-i` 是多值参数，后面的位置参数会被它吞掉）。
3. **提示词要点**（必须包含）：
   - 「参考图是同一个女生（小甜）；画面里必须是她本人：脸型、五官、眼睛、黑色长直发、肤色、气质与参考图一致；不要生成另一个长相的女生；不要照搬参考图的构图和背景。」
   - 把 `~/xiaotian-assets/appearance/<profile>/description.md` 的外貌描述附上。
   - **穿搭描述（不再固定写「穿着得体」）**：运行 `cd ~/xiaotian-wechat/src && ../.venv/bin/python outfit.py "<场景>" [--scene 约会]`，把输出的 `text` 原样放进提示词，再接具体场景 + 「手机直出照片质感，竖构图」。张数：调用方先定 N（demand/proactive 均默认 max=5 / 1～5 含端点；分布偏 1–2 偶发 3–5），数量指令走独立 `imagegen.build_quantity_prompt(N)` 前缀通道（【数量指令】），不与场景/妆造/构图混写；再与 `build_prompt` 内容段同一次 Codex 出齐——禁止分多轮 resume / 一张一张凑。N 张共享同一套 ≤5 参考图（face/body/hands/legs+scene），禁止每张各挂一套 refs。`src/chat_media.generate_paths` + `src/imagegen.build_quantity_prompt` / `build_prompt(n_images=…)` 已封装。
     - 场景库 `data/outfit_scenes.json`（579 个，`tools/gen_outfit_scenes.py` 生成）：校园日常（含旧 8 场景：上课/排练、见老师/学生会正式场合、朋友聚会、约会、准备见他、和他在一起(居家/独处)、宅宿舍/睡前、外出日常）、体育（爬山/打网球/游泳/滑雪/瑜伽/跑步…）、娱乐（KTV/剧本杀/演唱会/游乐园/看展…）、约会（烛光晚餐/看电影约会/逛街/见家长…）、旅游（海边/露营/古镇/自驾…）、旅游国家（日本/泰国/法国/冰岛…，带气候和礼仪提示）、旅游景点（三里屯/故宫/长城/外滩/洱海/富士山…）。每个场景有 别名、简短穿搭提示 hint、8 维向量（formality 正式 / exposure 放开 / trendiness 时髦 / sportiness 运动 / coziness 居家 + warmth 保暖↔清凉 / outdoor 户外 / water 玩水）和放开上限 cap。
     - 选场景用向量：本地 bge-small-zh-v1.5（与 mem.py 同款，场景嵌入缓存在 data/outfit_scene_emb.npz）嵌入 画面描述（权重最高）/最近聊天/作息活动/校园动态，加权余弦 + 别名命中加分 + 先验（作息/工作日周末/情绪的旧推断、周末晚上偏出门、季节不符扣分），top-8 温度采样主场景并与前几名软混合，加 ±0.05 抖动。模型不可用时自动退回别名+字符匹配。季节/天气只用月份和 weather.py 已有缓存，不额外联网；国家类场景用目的地气候。
     - 输出 `scene`（主场景）、`top`（前 5 名及分数/概率）、`vector`、`level`、`cap`、`text`；imagegen.py 把它写进输出目录的 `outfit.json`。
     - exposure 量化成 0~3 级：0 保守规矩 / 1 日常得体 / 2 稍放开、修身 / 3 更放开、更显身材、更精致。任何一级都写明「不暴露、不性感化」。
     - exposure 等级受主场景 cap 限制：见老师/见家长/寺庙/中东国家等 cap=0；海边、约会可到 3 级但依然得体。
     - 用户明确指定场景时用 `--scene`（名称、id 如 spot-001、或别名；imagegen.py 用 `--outfit-scene`）覆盖推断。
   - **写实修饰（随机，可为空）**：运行 `cd ~/xiaotian-wechat/src && ../.venv/bin/python realism.py "<场景>" [--hour 21] [--seed N] --text-only`，输出非空就放在「手机直出照片质感，竖构图。」之后。库 `data/realism_modifiers.json`（97 条，7 组：camera_device/lighting_flash/blur_focus/grain_noise/framing_composition/lens_artifact/color_film，中英文，来自 X 上常用写法：直闪、运动模糊、胶片颗粒、iPhone 随手拍、0.5x 超广角、CCD、脏镜头、JPEG 压缩、霓虹夜街、低机位、随意裁切…）。没有固定规则或常驻修饰：每条有 weight、vec（对场景 8 维向量）、tags、time，logit=log(weight)+向量点积+标签命中+昼夜项，softmax 后随机抽 0~3 个（数量也随机，约 30% 不加），每组最多一个、exclusive 互斥。唯一硬约束：ban_tags（见老师/正式、寺庙/教堂、剧场、博物馆不用闪光灯类）；所有模糊/畸变只作用于背景或肢体，片段末尾自带「脸始终清晰、与参考图一致、得体不性感化」。不要自己额外加会糊脸、改五官的效果。imagegen.py 自动调用并记在 outfit.json 的 `realism`；`REALISM=0` 关闭，`REALISM_SEED` 固定；macbook 脚本接 `REALISM_TEXT=`（空串=本张不加）或第 4 个参数。
   - 结尾写 `$imagegen`，或直接调用内置 image_gen 工具。不要用 PIL 或代码去画。
4. **找图**：新图在 `$CODEX_HOME/generated_images/` 下（按修改时间找最新的）。复制到 `~/xiaotian-assets/generated/<YYYYmmdd-HHMMSS>/`。
5. **自查**：看一眼生成的图。如果不像参考图里的人（脸型、五官、发型对不上），换一组参考图重画一次，最多重试 2 次。

## 小甜设定（用于场景合理）
北京舞蹈学院创意学院编导系（舞蹈编导专业，偏导演/编创方向）大三学生，校学生会文艺部副部长，影像社、话剧社成员。常出现的场景有排练厅、剧场后台、图书馆、食堂、宿舍楼下、学校附近的咖啡店。

## 禁止
- 不暴露、不性感化，不画内衣、床上的场景。泳装只在游泳/水上场景出现，且只用场景库里的写法（运动款连体泳衣外披防晒衫/浴巾，大方自然）；降到 0 级时改用 hint_safe（防晒长袖+短裤，只拍上半身）。被安全审核拦截时，`imagegen.py` 先避开这组参考图换一组再试一次；仍被拦就把放开程度降一级（用 `outfit.describe(vector, level-1, scene)`）再试，直到 0 级；0 级仍被拦就停下报告，不要用别的措辞绕过审核。
- 不做换脸，不把她的脸放到别人照片上，不画其他真实人物。
- 不把参考图上传到 OpenAI 这次请求以外的任何地方。
- 不需要登录。`codex login status` 显示未登录时，停下来报告，**不要发设备码**。

一行命令（等价流程已封装）：`cd ~/xiaotian-wechat/src && ../.venv/bin/python imagegen.py "<场景>" [--outfit-scene 约会]`（本次穿搭推断结果写在输出目录的 `outfit.json`）

## 后端（2026-10-24）

- 默认永远用 **Mac mini 本机 Codex**（Magpie 订阅，127.0.0.1:3425），即 `src/imagegen.py` 的 local 后端。
- MacBook（`~/xiaotian-assets/bin/imagegen_macbook.sh`，用户 you）只是**应急路线**：仅当 `IMAGEGEN_BACKEND=macbook` 强制，或本机因非审核原因（Codex/Magpie 挂了、鉴权失败、超时）失败且重试 1 次仍失败时自动切换。
- 审核拦截不切 MacBook：照旧换参考图、逐级降放开程度。
- 每次所用后端写在输出目录 `run.json`（`backend`、`forced`、`fallback_reason`、`local_errors`）。


## 参考图：脸五视图 + 身体五视图 HARD + 3 场景（2026-10-24，`a2e6e2f`）

官方全身环视 SOP：仓库 [`docs/body-orbit-yikao-sop.md`](../../../docs/body-orbit-yikao-sop.md)（艺考模卡）。`MAX_IMAGE_REFS=5`。

混合多图补充（2026-10-29）：先取整批镜头所需的 face/body/hands/legs 并集，再用剩余槽填场景，总数仍≤5。例：腿部＋全身 = face + body + legs + 2 场景；手＋腿＋全身 = face + body + hands + legs + 1 场景。下面的“2身份＋3场景”适用于只需 face/body 的批次。参考图分工是整批规则，构图按每张的分张要求；第一张的弱身份要求不能覆盖第二张。实际调用 image_gen 时也必须传入该镜头所需身份图，不能只口头要求同一个人。输出目录保存 prompt.txt 及 run.json 中输入参考图/提示词 SHA256，便于核对。

- **① 脸五视图 HARD**：`<profile>/canonical/sheet.png`（左侧脸 / 左 3/4 / 正脸 / 右 3/4 / 右侧脸）——**第一张** `-i`，唯一脸部身份标准。生成：`tools/frontal_extract.py` + `tools/gen_canonical.py`。
- **② 身体五视图 HARD**（`appearance.body_orbit` / `body/body.json` `status=approved`）：`<profile>/canonical/body/sheet.png`——**第二张** `-i`，**只锁身形比例**（身高/头身比/四肢/多角度轮廓），**不是衣服参考**；模卡灰衣黑短裤禁止复刻。**不要**用 `body/front.png` 顶替这张必附槽；脸仍只认 ①。生成/晋级：`tools/gen_canonical_body.py` + SOP promote。
- **③ 其余 3 槽 = `scene_refs`**：真实场景底板（`src/scene_refs.py`）。有 body sheet 时 **不附** 姿态 sheet、**不附** other 头肩裁图。
- 无 approved body sheet 时退回旧逻辑：face sheet（± 姿态 sheet）+ `pick_identity` other 头肩 + 较少 scene_refs。
- 换人设：`appearance.profile` / `profile_dir` / `canonical_refs`。relay pending 含 `canonical_refs` / `other_refs` / `body_ref`。

## 会话：同一天复用一个 Codex 会话，跨天换新（2026-10-24）

- `src/codex_session.py`：状态文件 `imagegen.session_state`（默认 `data/codex_session.json`，不入库）记 `{date, session_id, count, history}`。
- 当天第一张：`codex exec --json ...` 新会话，从 `thread.started` 事件取 `thread_id` 存下；当天后续每张：`codex exec resume --json <id> - -i ...`（id 和 `-` 放在 `-i` 前）。
- 日期一变就开新会话（旧 id 只进 history，不再 resume），避免上下文爆炸；当天记录的会话 resume 失败（`no rollout found` / `thread/resume failed`）→ 清掉、当天新开。
- 复用会话时只解析 rollout 本次新增部分收图，不会把之前的图当成新图。`run.json` 记 `session`（new/resume）与 `session_id`。
- MacBook `imagegen_macbook.sh` 同规则：`~/.xiaotian/codex_session_<YYYY-MM-DD>`，保留最近 14 个日期文件。
- 提示词每次都说明“忽略本会话之前生成的画面，只按本条消息的参考图”。单张画一张；多张（N>1）在**同一条** prompt 内一次出齐 N 张，不要为凑数再 resume。

## 姿态五视图：仰头 / 低头 / 背影（2026-10-24）

- `tools/gen_canonical_extra.py` 按 gen_canonical 同样流程再生成 3 套横排 5 格：
  `canonical/up/`（仰头 × 左侧/左3/4/正/右3/4/右侧）、`canonical/down/`（低头 × 同 5 个偏转）、`canonical/back/`（正背面 / 左后3/4 / 右后3/4 / 左肩回头 / 右肩回头），
  每套 `sheet.png` + 5 张单视图 + `<set>.json`，候选全部保留在 `canonical/<set>/candidates/<k>/`；`canonical/canonical.json` 的 `extra_sheets` 是索引。
- 参考：frontal_index 前 8 张里清晰度（脸部 Laplacian 方差 >= 200）过关的前 4 张真实正脸 + 主五视图 sheet.png（锁发型/衣着/背景）。
- 每套 >= 3 个候选；拒收：不是横排 5 格、偏转顺序颠倒、漏脸、背面格出现正脸。评分同主五视图（五官比例误差 + 0.3*FeaturePrint）：
  仰头/低头看中间格；背影只看两张回头脸 + 0.3*发型一致性（头发占比/颜色 vs 主五视图）。最优 > 0.30 自动再生成一轮。
- **用法（场景+活动为核心）**：
  1. **脸五视图永远必附**：`canonical/sheet.png` 作第 1 张 `-i`。
  2. **身体五视图 approved 时必附**：`canonical/body/sheet.png` 作第 2 张；此时 **不附姿态 sheet**（3 槽留给 scene_refs；见上节与 [`docs/body-orbit-yikao-sop.md`](../../../docs/body-orbit-yikao-sop.md)）。
  3. **无 body sheet 时**，姿态五视图仅向量自信才附：`refpick.extra_kind()`（bge；门槛 `POSE_MIN_SCORE`/`POSE_MIN_MARGIN`）；否则窄姿态关键词（**绝不**用看书/看手机等活动词）。
  4. **提示词**：以「任务核心是场景与活动」起笔；有 body 时第 2 张写体型五视图；有姿态（无 body）时约束俯仰/朝向。含 173cm/50kg 体型锁定。
  5. `appearance.extra_sheets=false` 关闭姿态 sheet；`BODY_ORBIT_REFS=0` 临时关掉身体五视图必附；`POSE_EMBED=0` 强制关键词。
- 手动生图：同样先附正视 sheet；仅当向量/明确姿态需要时再附 `canonical/<up|down|back>/sheet.png`。
- MacBook / relay：`canonical_refs` 含姿态 sheet 时另有 `extra_sheet` 字段；MacBook 需同步 `$ASSETS/canonical/{up,down,back}/sheet.png`。


## 场景真实参考图（scene_refs，2026-10-24）

调研见 `docs/imagegen-realism-research.md`。目标：避免全图从 0 造景导致的假感，先检索同场景真实照片，再把小甜放进去。

### 本地库
- 目录：`~/xiaotian-assets/scene_refs/`（`images/<scene_id>/*.jpg` + `scene_refs.sqlite`）
- 构建：`SCENE_REFS_BUILD=1 .venv/bin/python tools/build_scene_refs.py --target 2000 --per-scene 4`
  - 源：Bing 图片搜索（礼貌限速）+ Wikimedia Commons；可选登录平台采集器（只读、串行锁）
  - 去重：sha256 + pHash；可选 `--rescore-faces`（Apple Vision）标 `people_free`（无人或人脸占比 <2%）
  - 向量：bge-small-zh 嵌入检索 query，供自由文案场景召回
- 状态：`tools/build_scene_refs.py --status`；恢复限流：`--restore-limits`（从 `config/poll_limits.json.bak-pre-scene-refs`）

### 生成时挂载（`src/imagegen.py` + `src/scene_refs.py`）
1. 参考图顺序（body approved，`a2e6e2f`）：`canonical/sheet.png` + `canonical/body/sheet.png` → **3 张 scene_ref**（占满剩余槽；见 [`docs/body-orbit-yikao-sop.md`](../../../docs/body-orbit-yikao-sop.md)）
2. 提示词按 index 标明角色；模式：
   - `match`（**默认，更自然**）：地点/陈设/光影贴近场景图，允许轻微重绘；A/B 默认也只跑 old+match
   - `insert`：以场景图为底插入人物（易比例失调，需显式 `SCENE_REFS_MODE=insert`；提示词仍带 173cm 体型尺度）
3. 身份规则不变：脸只认 face sheet；体型认 body sheet；不要模仿场景图里的路人脸；不暴露
4. 开关：`SCENE_REFS=0` 或 config `imagegen.scene_refs=0` 关闭；有 body sheet 时 `generate_local` 自动把 scene k 设为 `max_refs - 2`（通常 3）

5. **Face-fallback（默认开）**：`match` 生成后对 canonical 算 FeaturePrint 距离，并再跑一次无 scene_refs 的旧路径；
   若 `dist(match) > dist(old) + 0.15`（`imagegen.scene_refs_face_fallback_delta`）则采用旧路径，避免场景底板带偏五官。
   `SCENE_REFS_FACE_FALLBACK=0` 或 config `scene_refs_face_fallback=0` 关闭（不再双跑）。

5. 有 scene_ref 时提示词强调 photorealistic candid phone photo，并要求匹配场景图噪点/色温；写实修饰（realism.py）仍随机附加

### 人工自查
生成后看：背景纹理是否像照片而非插画、人物是否与场景透视/光一致、脸是否仍是小甜。不像就换一组 scene_ref 或改 `insert`/`match` 重试。
