---
name: xiaotian-media-download
description: >-
  从已登录 Chrome 中打开的小红书/抖音/微博页面下载图片、封面、视频到小甜素材库（material_store）。
  当需要“下载/保存这篇笔记/视频/微博的图片”“给朋友圈找配图素材”时使用。只读，绝不互动。
---

# xiaotian-media-download

## 人设档案（必读）
功能改动前先读并遵守：`data/persona_dossier.json`（摘要 [`docs/persona-dossier.md`](../../../docs/persona-dossier.md)）。
禁止编造档案未载明的生平；聊天/生图/日程各自只用不为 null 且列入 `usage` 的字段。

## 一句话
在仓库根目录运行：
```bash
/opt/homebrew/bin/python3.13 tools/media_download.py --platform xiaohongshu|douyin|weibo [--dry-run] [--max 6] [--lcu-fallback] [--force]
```
它读 Chrome 里**第一个匹配域名的标签页**（当前打开的笔记/视频/微博详情页），不导航、不点击。

## 硬规则
- 只读：绝不点赞、评论、关注、收藏、私信、发布；不在页面输入任何内容；不发微信。
- 必须经过 `source_poll`（脚本内 `acquire`）和全局串行锁 `data/collectors.lock`（脚本内 `global_lock`）。返回 `skipped:*` 就停止，**不要**为绕过而加 `--force`；`--force` 只在用户明确要求手动试跑时用，它只跳过静默时段/最小间隔，日上限和全局上限仍生效。
- 返回 `captcha` / 页面出现验证码、登录墙：立刻停止，交给用户，不重试。
- Cookie 永不读取、不打印、不写日志；下载只带 `User-Agent` + 平台 `Referer`。
- 只通过 `src/material_store.add_item(..., move=True)` 入库；保留 30 天由 store 的 `expires_at`/`purge_expired` 负责，不要自己另存目录。

## 流程（脚本已实现）
1. osascript `execute javascript` 在标签页抽取：标题/作者/正文、`img`（取 `srcset` 最大项、`currentSrc`）、背景封面、`video.currentSrc`/`<source>`/`poster`。
2. 高清变体 `hires()`：小红书去掉 `!nd_...` 缩略后缀；微博 `orj360/mw690/thumb150/bmiddle` → `large`；抖音 tos URL 带签名，原样使用。每项按“高清→原图”依次尝试。
3. 视频是 `blob:`（MSE 流）无法下载 → 改存 `poster` 封面。有 http(s) mp4 则下载（≤40MB）。
4. 返回 `text/html` 视为登录/拦截页丢弃；sha256 本次去重，入库时 store 再按 sha256 去重。
5. 全部失败且加了 `--lcu-fallback`：用 LCU（`~/.local/share/lcu/current/bin/lcu`，需 `lcu apps allow "Google Chrome"`）截 Chrome 窗口作为图片兜底；可在代码里传 `crop=(x,y,w,h)` 裁剪。

## 建议用法
- 先 `--dry-run` 看候选 URL（仍消耗一次 source_poll 计数）。
- 批量采集请用 `python -m collectors.runner`（在 src/ 下），它已经会下载列表/详情里的图片；本 skill 用于“当前页这一篇”的补充下载。
- 测试：`python -m pytest -q tests/test_media_download.py`（mock HTTP，无网络）。
