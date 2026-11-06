# Legs model card（腿部五视图模卡）

> 生成/出站：legs_focus 用「女生的腿」形容小甜腿部；hand_closeup 用「女生的手」。

Installed wiring 2026-10-26；生成脚本对齐全身艺考 SOP（`docs/body-orbit-yikao-sop.md`）。
**资产 `status=approved`（2026-10-26 17:23 Asia/Shanghai）**——视觉 QA 通过（五视图齐全、与 hands/body 同尺寸棚拍质量、黑短裤+赤脚对齐 body canonical）。`refpick.legs_refs()` 对 `legs_focus` 返回 `canonical/legs/sheet.png` HARD。

回滚：改 `legs.json` → `candidates`，或环境变量 `LEGS_REFS=0`。

## Scope（对齐全身五视图精神）
- **Body sheet** 锁全身比例与服装样例；**Legs sheet** 只锁下半身比例/腿长/膝踝脚型——供 `legs_focus` HARD，不覆盖 face/body/hands。
- 视角顺序与全身模卡相同：`lprofile → l34 → front → r34 → rprofile`（左纯侧→左45→正→右45→右纯侧），裁切髋/大腿中段→赤脚。
- 样例服装：与 body canonical 一致——纯黑运动短裤 + 赤脚；浅灰棚拍无缝背景。

## Assets
`appearance/<profile>/canonical/legs/`（本机：`~/xiaotian-assets/appearance/zhuzhuxiaoye/canonical/legs/`；资产在 git 外）
- `sheet.png` — 横排五格腿部模卡（1619×971，与 hands/body sheet 同规格）
- `lprofile.png` … `rprofile.png` — 等分裁切单格
- `legs.json` — `status=approved` 时 `refpick.legs_refs()` 可附
- `candidates/1/` — 原始候选 + `codex.log` / `prompt.txt` / `legs-five-view.png`
- `APPROVAL.md` — 批准记录与回滚说明

## Inventory（2026-10-26）
| 项 | 状态 |
|---|---|
| candidates/1 five-view sheet | 有 |
| 五单格 lprofile/l34/front/r34/rprofile | 有（canonical 根与 candidates/1 各一份） |
| model | gpt-6-astra + reasoning ultra（默认 6 astra ultra / Magpie） |
| vs hands/body SOP | 完整；质量对齐后已 approved |
| face/body/hands | 未改动 |

## Regen（勿默认 approve）
```bash
.venv/bin/python tools/imagegen_greenlight.py   # 须 GREEN
.venv/bin/python tools/gen_canonical_legs.py -k 1 --promote
# 人工/QA 看图通过后：
.venv/bin/python tools/gen_canonical_legs.py --reuse --promote --approve
# 或手改 legs.json status → approved（并写 approved_at_AsiaShanghai）
```

`-i` 顺序（脚本内）：`body/front`（比例/短裤 CORE）→ `body/sheet`（若有）→ face `sheet`（肤色氛围，不出脸）。

## Chat / schedule
- 用户「看看腿」→ `legs_focus`（`shot_modes` 已有路由）
- approved 腿模：`legs/sheet` HARD **独占 1 身份槽**（不软挂 face → k_scene=4）；未批准时退回 body 软挂 + 弱 face
- **构图硬约束**：`legs_focus` 必须第一人称/主观视角（低头看自己的腿），禁止第三人称美腿写真
- 主动节拍：`schedule_shot` 非报备路径 `_PART_EQUAL` 含 `legs_focus`（全身 0.40 后其余五类均分 → 约 **0.12**）；排练/关键词仍可强制
