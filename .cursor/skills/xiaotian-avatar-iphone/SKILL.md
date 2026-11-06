---
name: xiaotian-avatar-iphone
description: >-
  通过 USB iPhone 8 Plus（WDA）更换小甜自己的微信头像（Mac 微信不能改头像），包括小甜自己挑头像（--auto）。
  当需要“换/改小甜微信头像”或让小甜自己换个头像时使用。不需要用户确认；自动换头像 14 天最多 1 次。
---

# xiaotian-avatar-iphone

## 人设档案（必读）
功能改动前先读并遵守：`data/persona_dossier.json`（摘要 [`docs/persona-dossier.md`](../../../docs/persona-dossier.md)）。
禁止编造档案未载明的生平；聊天/生图/日程各自只用不为 null 且列入 `usage` 的字段。

## 一句话
小甜自己挑并换（推荐，无需用户确认）：
```bash
/opt/homebrew/bin/python3.13 tools/avatar_iphone.py --auto --delete-photo
```
指定图片：
```bash
/opt/homebrew/bin/python3.13 tools/avatar_iphone.py --image data/avatar/new_avatar.png --yes --delete-photo
```
只挑不换（看理由）：`/opt/homebrew/bin/python3.13 tools/avatar_pick.py [--no-search]`

参数：`--force` 忽略 14 天限制；`--force-upload` 已是目标图也重传（测速）；`--test` 测速运行（history 标 test，不计入 14 天）；
`--keep-clipboard` 备份/恢复手机剪贴板（+~3s，默认不做）；`--debug` 存过程截图；`--skip-transfer` 图已在相册。
退出码：0 成功（JSON status=changed/already），2 缺 --auto/--yes，3 rate-limited（JSON 带 next），4 没挑到合格图。
（必须用 `/opt/homebrew/bin/python3.13`：`facebook-wda` 装在 `~/Library/Python/3.13`。）

## 规则
- **用户不再需要确认头像选择**：小甜按人设自己挑、自己换；挑选理由写进 `data/avatar/history.json`（reason/score/source_url/rejected/runner_up）。
- 频率：真实更换（非 test）**14 天最多 1 次**，脚本自动拦（退出码 3）；只有用户明确要求才加 `--force`。
- 头像硬规则：**不能有可识别的真人脸**，只用插画/宠物/风景/物件。`avatar_pick.py`：标题含真人类词（真人/自拍/小姐姐/明星/写真/穿搭…）直接丢；
  macOS Vision 检出任何人脸/人体也丢；Vision 不可用 → 不用该图。卡通/动漫脸 Vision 通常不报，允许。
- 小红书只读：走全局串行锁 + `source_poll.acquire("xiaohongshu")`（日上限/全局上限永远生效）；
  **限额与锁共用主仓库** `~/xiaotian-wechat/data/{source_poll_state.json,collectors.lock}`
  （worktree `data/` 下是相对符号链接；`avatar_pick.use_main_poll()` 再强制 override，避免双计数）。
  `skipped:*`（静默时段/间隔/上限）就只用本地候选池 `data/avatar/candidates/`，**不要为此加 `--xhs-force`**；
  `--xhs-force` 只在用户要求手动试跑时用。验证码/登录墙 → 停。搜索结束把小红书标签页导航回原 URL。不点赞/评论/关注。
- 发布者过滤：作者/标题含 公司/旗舰店/广告/招租/代购/品牌…，或命中已知品牌号 → 丢；优先个人/插画号。
- 分辨率：`media_download.hires` 优先高清 URL；最短边 ≥600 加分，<300 强降权（仍可进池，几乎选不上）。
- 只改小甜自己的头像：「我」页校验 `WeChat ID: demo_xiaotian_wxid`，不是这个号就停。
- 不发消息、不发朋友圈、不改别的资料/设置；Mac 微信完全不动。
- WDA 挂了重启会占手机，须用户明确要求（命令见 AGENTS.md）。

## 人设打分（avatar_pick.py）
甜、俏皮的大学女生：关键词（可爱/甜/软萌/俏皮/少女/粉/猫/狗/兔/小熊/插画/卡通/手绘/治愈…加分；暗黑/丧/emo/酷/男生/情头…减分）
+ 画面（明亮度、饱和度、暖粉色占比）+ 点赞（log，小权重）。排除最近 6 次用过的图和当前头像。缩略图（最短边 <300）强降权；高清（≥600）加分；品牌/公司号直接丢。

## 流程（v3 提速版）
0. 微信 →「返回」退到根 →「我」Tab → 校验账号 → **直接在「我」页头部裁头像比对**（相对 ID 文本 `ME_AVATAR=(-84,-39,64,64)` pt），
   一致即 `already`（~1.5s，不进个人资料）。
1. 传图（AP 隔离，只能走剪贴板）：图 → JPEG 512 q85 data URL（~45K 字符）→ WDA 前台 `setPasteboard`（**不读回**，读回每次 ~2s）→
   Safari **长按底部地址栏 →「粘贴并前往」** → 等地址栏回到底部且有图 → 长按 (207,200) **1.2s**（0.8s 不出菜单）→「存储到“照片”」。
   没出现「粘贴并前往」= 剪贴板没写进去 → 自动带读回核对重写一次。data: 标签页不关，下次复用同一个。
2. 回微信（停在「我」页）→ 点昵称区进个人资料 → 头像 → 更多 → 从手机相册选择 → 选择器 Image（labelContains「照片」）**取文档顺序最后一个**（=最新，
   不逐个取 label/bounds，省 ~2s）→ 截图比对 → 点 → 裁剪页「完成」→「返回」。
3. 校验：个人资料页 `Image label=头像` 单次查询裁剪比对。`--delete-photo`：照片 App 按屏幕位置取右下角（**照片 App 文档顺序不可靠**）比对后删。

## 实测（2026-10-24，UTC+8）
| 步骤 | v2 (97s) | v3 |
|---|---|---|
| 校验账号/比对 | 16 | 1.3–4.6（已是目标图总计 1.5s） |
| 剪贴板 | 7 | 0.8 |
| Safari 打开 | 19 | 5.9–6.3 |
| 存图 | 5 | 4.2 |
| 关标签页 | 5.5 | 0（不关） |
| 回微信进资料 | 8.5 | 2.8 |
| 进相册 | 9 | 5.3 |
| 选图 | 4 | 2.5 |
| 完成上传 | 4 | 2.4–3.4 |
| 校验 | 8 | 1.1 |
| 删临时图 | 10 | 8.4–10.9 |
| **总计** | **97** | **39.1 / 39.8（含删图）；不删图 ≈29–31** |

## 坑
- wda `tap_hold`/`click` 坐标必须 int（float 被当百分比）。wda Selector 的 `.bounds` 元素不存在时会等 30s，先 `.exists`。
- WDA 不在前台时 setPasteboard 静默失败。设置剪贴板会覆盖手机剪贴板（要保留加 `--keep-clipboard`）。
- 未聚焦地址栏长按菜单：拷贝 / 粘贴并前往 / 语音搜索 / 移到标签页组。
- 测试：`~/xiaotian-wechat/.venv/bin/python -m pytest -q tests/test_avatar_iphone.py tests/test_avatar_pick.py`（全 mock）。
