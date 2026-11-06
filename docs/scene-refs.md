# 场景真实参考图库（scene_refs）

- 库：`~/xiaotian-assets/scene_refs/`（约目标 2000 张）
- 构建脚本：`tools/build_scene_refs.py`（可 resume）
- 运行时：`src/scene_refs.py` → `imagegen.pick_scene_refs`
- 调研：`docs/imagegen-realism-research.md`
- SOP：`tools/codex-skill/xiaotian-imagegen/SKILL.md`（已同步 `~/.codex/skills/xiaotian-imagegen/`）

## 临时限流（建库期间）

`config/poll_limits.json` 已临时改为：平台最小间隔 ≈10 分钟、无日上限、清空 quiet；`SCENE_REFS_BUILD=1` 时 `source_poll.cfg` 跳过 1.5h 硬下限。  
原文件备份：`config/poll_limits.json.bak-pre-scene-refs`。  
建库结束后执行：

```bash
SCENE_REFS_BUILD=1 .venv/bin/python tools/build_scene_refs.py --restore-limits
# 或: cp config/poll_limits.json.bak-pre-scene-refs config/poll_limits.json
```

动作间隔仍为 3–12s；采集只读。公开 Bing/Wikimedia 使用更短礼貌延时（见 platforms.bing_images / wikimedia）。


## 中文空景采集（只读）

```bash
SCENE_REFS_BUILD=1 .venv/bin/python tools/build_scene_refs.py \
  --target 2000 --per-scene 4 --collectors --collector-every 15 --max-hours 6
```

- 平台：小红书 / 微博（Chrome 已登录标签页；`source_poll` + `data/collectors.lock` 串行，与主仓库共享）
- 行为：只读搜索「场景名 + 空景/无人」，下载封面/正文图；**不点赞、不评论、不关注**
- Bing/Wikimedia 仍是主力填充；`--collectors` 每隔 N 个 bing 场景穿插一轮中文空景
- worktree 下 `data/{collectors.lock,source_poll_state.json}` 符号链接到 `~/xiaotian-wechat/data/`

## 生图 face-fallback

默认 `imagegen.scene_refs_mode=match`。生成后对 canonical 算 FeaturePrint L2；再跑一次无 `scene_refs` 的旧路径。
若 `dist(match) > dist(old) + 0.15`，采用旧路径结果（`run.json` 记 `chose=face_fallback`）。


## 闲时补齐队列

新活动/场景目标 **≥4 张/场景**。开发优先定活动与 `life_schedule`；图库可排队：

```bash
.venv/bin/python tools/queue_scene_refs.py status
.venv/bin/python tools/queue_scene_refs.py enqueue --from-pool   # 或 --missing
.venv/bin/python tools/queue_scene_refs.py process --limit 8     # 闲时回填，可 resume
```

队列：`data/scene_refs_queue.json`。抓取仍复用 `tools/build_scene_refs.py`。

## 常用场景库

高频校区场景（宿舍/食堂/图书馆/体育场/教室/练功房）见 [`docs/common-scenes-sop.md`](common-scenes-sop.md)；
运行时优先于泛化库，并由同日探针绑定（`data/scene_ref_day_probes.json`）。
