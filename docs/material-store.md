# 素材本地库 (src/material_store.py)

抓取模块（经 src/source_poll.py 限流）拿到的素材统一落本地库。本模块只做存储：不联网、不抓取、不登录。

## 路径
- DB：`~/xiaotian-assets/materials/materials.db`（SQLite，WAL）；环境变量 `XIAOTIAN_MATERIALS_DIR` 可覆盖根目录
- 媒体：`<root>/media/<platform>/<yyyymm>/<sha256>.<ext>`
- 平台：xiaohongshu, douyin, wechat_mp, weibo, bilibili, bda_site

## 表
- `items`：id, platform, source_url(UNIQUE), author, title, text, tags(JSON), published_at, fetched_at, expires_at(=fetched_at+30天), content_hash, lang, extra(JSON), embedding(BLOB，可选)
- `media`：id, item_id(FK→items ON DELETE CASCADE), kind(image/video/audio), local_path, sha256(UNIQUE), bytes, width, height, duration, source_url
- `items_fts`：FTS5（trigram 分词，中文可用）覆盖 title/text/tags，由触发器同步；<3 字查询退化为 LIKE
- 时间均为 Unix 秒

## API
```python
import material_store as ms   # 或 from src import material_store
ms.add_item("xiaohongshu", url, title=..., text=..., tags=[...], author=..., published_at=..., lang="zh",
            extra={...}, media=["/tmp/a.jpg", {"path": "/tmp/v.mp4", "kind": "video", "source_url": ..., "duration": 12}],
            move=False, embed_fn=None)
ms.search("银杏", platform="xiaohongshu", since="2026-10-01")
ms.recent(10); ms.get(id); ms.get_by_url(url); ms.purge_expired(); ms.stats()
```
- `embed_fn`：可选，传入文本→向量函数（如 mem.py 同款 bge-small-zh-v1.5），以 float32 存入 `embedding`；默认不算，零开销。

## 去重与保留
- 同一 source_url 再入库 = **刷新**：更新字段、有效期重置为 30 天、旧媒体换成新媒体，旧文件无引用即删除（旧版本被替换）。
- 媒体按 sha256 去重：同样内容只存一份；若已属于另一条目，只在本条 `extra.shared_media` 记 sha（文件归属原条目，原条目过期时随之删除）。
- 每次 `add_item` 后自动 `purge_expired()`：删除 `expires_at < now` 的条目（级联媒体行）及无引用文件。
- 每日兜底：`launchd/com.xiaotian.materials-purge.plist`（04:23，**未 load**）。启用：`cp launchd/com.xiaotian.materials-purge.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.xiaotian.materials-purge.plist`

## CLI
```
.venv/bin/python -m src.material_store stats
.venv/bin/python -m src.material_store purge
.venv/bin/python -m src.material_store search 穿搭 --platform xiaohongshu --since 2026-10-01
.venv/bin/python -m src.material_store recent 5
```
