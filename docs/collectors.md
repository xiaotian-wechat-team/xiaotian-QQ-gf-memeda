# 素材采集器（小红书/抖音/微博）

- `python -m collectors.runner [--force] [--no-ocr] [平台...]`（在 src/ 下运行）。fcntl 全局锁 `data/collectors.lock`，任何时候只跑一个平台，平台之间随机间隔 20-60s，所有动作都走 `source_poll`（`--force` 只跳过静默时段和最小间隔，日上限和全局上限照常生效）。
- 主路径：用 osascript 在现有 Chrome 标签页里执行 JS 读 DOM（需要 Chrome 开启「允许 Apple 事件中的 JavaScript」，本机已开启），不切前台。
- OCR 路径：切到对应标签页，截窗口图，用 Apple Vision OCR 识别，结束后恢复原来的前台应用。只能读到首屏，用于对比和兜底。
- LCU 路径：只有 Chrome 被 `lcu apps allow "Google Chrome"` 授权后才会启用，否则跳过。
- 遇到验证码或登录墙会调用 done(captcha) 退避，该平台停止。只读，不点赞、不评论、不关注。
- 2026-10-24 01:10 试跑结果：DOM 路径三个平台各 10 条；OCR 首屏标题匹配率：抖音 0.83，小红书 0.3，微博 0.09。
