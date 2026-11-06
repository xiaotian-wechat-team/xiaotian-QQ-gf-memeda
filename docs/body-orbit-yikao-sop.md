# Body orbit SOP: 艺考模卡 (body-orbit-yikao)

Official SOP for 小甜 full-body five-view orbit. Installed 2026-10-24 (user pt209u).

## Scope
- **Chat HARD 用途：** face sheet 只锁脸型；body sheet 只锁身形比例——**绝不是衣服参考**；场景服装必须按 `outfit` 文案换装，禁止复刻模卡灰衣黑短裤。
- **Body orbit only.** Face identity HARD lock remains the approved face five-views:
  `canonical/sheet.png`, `front.png`, `lprofile.png`, `rprofile.png` (and face angles as needed).
- SOP example images illustrate **outfit + body proportions + sheet layout**. Their faces must **not** override the approved face sheet.

## Assets
`appearance/<profile>/sop/body-orbit-yikao/`
- `正面全身像-灰上衣黑短裤.png` — `sop_front_example` (body CORE style: heather-gray fitted tee + black athletic shorts + barefoot)
- `五视图全身模卡-灰上衣黑短裤.png` — `sop_sheet_example` (L profile → L45 → front → R45 → R profile)
- `五视图生成提示词.txt` — canonical English five-view prompt
- `生成说明.txt` — front-portrait prompt + notes (workflow docs)

Symlinks: `body_refs/sop_front_example.png`, `body_refs/sop_sheet_example.png`.

## Regen command
```bash
.venv/bin/python tools/gen_canonical_body.py -k 1
# optional: use SOP front as body CORE (does not overwrite approved body/front.png)
.venv/bin/python tools/gen_canonical_body.py -k 1 --sop-front
```

## Regen `-i`（仅 `tools/gen_canonical_body.py`；勿用于聊天生图）
聊天路径见下一节 Chat imagegen，挂 body/sheet 而非 body/front。
1. `canonical/body/front.png` (or SOP front with `--sop-front`) — body CORE
2. `canonical/sheet.png` — face
3. `canonical/front.png` — face
4. `canonical/lprofile.png` — face
5. `canonical/rprofile.png` — face

Do **not** attach old body sides / `body_refs/*` / old body sheet.

## Chat imagegen `-i` (MAX=5, commit `a2e6e2f`)
When `canonical/body/body.json` `status=approved`:
1. `canonical/sheet.png` — face five-view **HARD**
2. `canonical/body/sheet.png` — body five-view **HARD** (not `body/front.png`)
3. Remaining **3** slots → `scene_refs`

Wired in `src/refpick.pick_identity` + `src/imagegen.cap_refs`. See README / AGENTS / `tools/codex-skill/xiaotian-imagegen/SKILL.md`.
字段指针：[`data/persona_dossier.json`](../data/persona_dossier.json) → `appearance.body_orbit`。

## Outfit (BODY CANONICAL sample clothing)
Heather-gray fitted crewneck tee + black fitted athletic shorts + barefoot. Not pants+sneakers.
