# Hands five-view SOP（手部五视图模卡）

Installed 2026-10-26（用户 t493u）：纤细手模五视图，供 `hand_closeup` 生图 HARD 参考；**不覆盖** face/body sheet。

## Assets
`appearance/<profile>/canonical/hands/`
- `sheet.png` — 横排五格手部模卡（左→右：dorsal / dorsal34 / palm / palm34 / side）
- `dorsal.png` … `side.png` — 等分裁切单格
- `hands.json` — `status=approved` 时 `refpick.hands_refs()` 可附

## Regen
```bash
.venv/bin/python tools/imagegen_greenlight.py   # 须 GREEN
.venv/bin/python tools/gen_canonical_hands.py -k 1 --promote
```

## Chat imagegen
见 `src/shot_modes.py` + `src/schedule_shot.py`：
- **报备** `schedule_brief`：`hand_closeup` / `pov_first_person` 等分 50/50；日程槽写入场景词。
- **其它主动节拍**（meal / activity_end 等 WANT_IMAGE）：`schedule_shot` 默认 **全身更高（≈40%）**，其余 hand/POV/mirror/legs/portrait 在剩余概率**约等分**（bucket 轻偏置；活动关键词 `_ACT_FORCE` 仍可强制）。
- **用户请求（100% 分流）**：`看看手/手呢`→hand、`看看你拍的/你拍的`→POV、`看看全身/全身呢/发张全身`→full_body、`看看你/自拍呢/来张自拍`→portrait、`看看腿/腿呢`→legs、`对镜/对镜自拍/试衣镜/镜子里`→mirror_selfie（正则优先 + 向量）。
- **常用场景 + 同日探针**：见 `docs/common-scenes-sop.md`（北舞校区 dorm/canteen/library/stadium/classroom/studio）。
- `hand_closeup`：HARD `hands/sheet` **仅 1 身份槽**，不强制 face+body；脸出画或虚化；**必须主观第一视角看自己的手**，禁止第三人称手部写真。
- `pov_first_person`：scene_refs 为主，可露手臂/手；不挂 body sheet。
- `mirror_selfie`：face HARD，hands 软可选，跳过 body；对镜构图 prompt。
- `legs_focus`：`legs/sheet` 仅 `status=approved` 时 HARD **独占 1 槽**（不软挂 face）；否则 body 软挂；**必须主观第一视角看自己的腿**（见 `docs/legs-model-card.md`）。
- 其它节拍默认第三人称 face+body HARD。

## Deferred / status（2026-10-26 feat/schedule-shot-modes）
| 项 | 状态 |
|---|---|
| 对镜自拍独立 mode | **done** — `mirror_selfie` in shot_modes + refpick + schedule_shot |
| 腿部专用模卡 | **approved** — sheet/views 齐全（gpt-6-astra ultra）；`legs.json` status=approved（2026-10-26 17:23 Asia/Shanghai）；见 `docs/legs-model-card.md` |
| 报备以外主动节拍 hand/POV/mirror 分流 | **done** — meal / activity_end（photo_ok）走 `schedule_shot`；全身加权+其余等分 |
| 常用场景库 + 同日 scene_ref 探针 | **done** — `common_scenes` + `scene_ref_day_probes.json` |
| 日程→prompt 模板脚本 | **done** — `src/schedule_shot.py`（库 + CLI） |

腿部模卡已批准并接入 `legs_focus` HARD；scene_refs 扩库见并行 common 流。
