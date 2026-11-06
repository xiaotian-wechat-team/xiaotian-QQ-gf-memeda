# 常用场景库 SOP（common scenes）

Installed 2026-10-26（feat/schedule-shot-modes）：为高概率生活场景预置**统一校区来源**的真实空景参考，配合同日 `scene_ref` 探针，降低多次生图穿帮。

## 校区身份

以 `data/life_schedule_*.json` → `rules.school` 为准：**北京舞蹈学院**创意学院编导系（**非北京大学**）。  
`燕园` 仅作用户侧天气默认，不作为小甜校园场景库。

## 布局

`~/xiaotian-assets/scene_refs/common/<pack_id>/`

| pack_id | 中文 | 日程高频映射 |
|---|---|---|
| `dorm` | 宿舍 | scene_hints/活动含 宿舍/寝室/洗漱… |
| `canteen` | 食堂 | 食堂/饭堂/窗口… |
| `library` | 图书馆 | 图书馆/自习/阅览室… |
| `stadium` | 体育场 | 体育场/操场/田径场… |
| `classroom` | 教室 | 教室/阶梯教室/上课… |
| `studio` | 练功房 | 练功房/排练厅/把杆/基训… |

每包：

- `images/*.jpg` — 通过质量门的图（无硬上限）
- `rejected/` — 簇外/异源被拒
- `pack.json` — `source_family`、bge centroid、`kept_files`、queries

代码：`src/common_scenes.py`。构建：`tools/build_common_scenes.py`。

## 质量门（CRITICAL）

1. **统一来源族**：同一 pack 只保留一个 `source_family`（Wikimedia 够量时优先；否则 Bing 等单一族）。**校区锁定北舞**（`life_schedule.rules.school` 明确非北京大学；燕园仅用户天气默认）。
2. **场景向量近邻**：用现有 **bge-small-zh**（`outfit` / `scene_refs` 同款）对查询句嵌入；相对规范查询 cosine 过滤离群（默认门槛可低至 ~0.55，自适应）。
3. **偏向更大保留集**：无每包硬上限；默认至少保留 ~20 张或候选的 ~75%（`COMMON_SCENE_MIN_KEEP` / `KEEP_FRAC`），只丢真正尾部离群。
4. **pHash**：仅剔极端离群（默认 hamming >36）；若会跌破目标保留数则跳过。
5. 查询句**强制带校名**（北京舞蹈学院 / Beijing Dance Academy / 北舞）。

重跑：

```bash
.venv/bin/python tools/build_common_scenes.py --fetch
.venv/bin/python tools/build_common_scenes.py --recluster
.venv/bin/python tools/build_common_scenes.py --status
.venv/bin/python tools/build_common_scenes.py --dedupe-cross-pack
```


## 跨 pack 排他 / 去重（CRITICAL）

Bing 常把同一张校园概览图返回给多个 query，导致同一 `sha256` 出现在多个 pack。

构建后（或库接近目标规模后）跑：

```bash
.venv/bin/python tools/build_common_scenes.py --dedupe-cross-pack
```

规则：

1. 按文件内容 `sha256` 建全局索引；同 hash 出现在 ≥2 个 pack 的 `kept_files`/`images/` 即为冲突。
2. **保留**余弦最高者：对该图的抓取 `query` 与各 pack 规范查询（queries[0]）做 bge embedding cosine；同分则保留原 `n_kept` 更大的 pack，再按 `pack_id` 字典序稳定打破平局。
3. 落败侧：`images/` → `rejected/`，从 `kept_files` 剔除，`reject_reason=cross_pack_dup_kept_in:<winner>`。
4. 不跨 `source_family` 混合；本步骤只做排他，不改族。

目标规模：六包合计 **~1000 kept**（无每包硬上限；MIN_KEEP≈20 / KEEP_FRAC≈0.75）。若 Bing 召回饱和，以 `--status` 天花板为准并在提交说明中记录。

## 运行时优先级

`src/scene_refs.pick_with_common_and_probes`（`imagegen.pick_scene_refs` 已接）：

1. **同日探针** `data/scene_ref_day_probes.json` — 某日某 `scene_key` 绑死同一组路径  
2. **常用场景包** `common/<pack>`  
3. 泛化 `scene_refs.sqlite` 向量检索

日程已知时：`life_calendar.get_day` → `scene_refs.ensure_day_probes` 预绑定。

## 镜头概率（模卡对齐）

非报备主动节拍（`schedule_shot`）：**全身 `full_body` ≈40%**，其余  
`hand_closeup` / `pov_first_person` / `mirror_selfie` / `legs_focus` / `portrait` **在剩余概率上等分**（bucket 仅轻偏置）。  
报备 `schedule_brief` 仍为 hand|POV **50/50**（特殊路径）。  
用户话术（`看看手/腿/全身/对镜/自拍/你拍的`…）经 `shot_modes.route_user_shot` **100% 命中分流**。

## 与泛化 scene_refs 关系

- 泛化库：`docs/scene-refs.md`（~2000 张，按 outfit scene_id）
- 常用库：校区锁定 + 簇过滤，服务日程高频场景；**不替代**旅行/城市等长尾 scene_refs
