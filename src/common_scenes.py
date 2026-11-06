"""常用场景库：宿舍/食堂/图书馆/体育场/教室/练功房 等（北舞校区）。

布局：~/xiaotian-assets/scene_refs/common/<pack_id>/
  images/*.jpg|png|webp
  pack.json  — 元数据、统一来源、embedding centroid、保留/剔除

SOP：docs/common-scenes-sop.md
构建：tools/build_common_scenes.py
运行时优先于泛化 scene_refs（见 scene_refs.pick / day probes）。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Optional

DEFAULT_COMMON_DIR = pathlib.Path(
    os.path.expanduser(os.environ.get("COMMON_SCENES_DIR", "~/xiaotian-assets/scene_refs/common"))
)

# 校区：life_schedule.rules.school 明确「北京舞蹈学院…（非北京大学）」；燕园仅用户天气默认
CAMPUS_NAME = "北京舞蹈学院"
CAMPUS_NAME_EN = "Beijing Dance Academy"
CAMPUS_ALIASES = ("北舞", "北京舞蹈学院", "Beijing Dance Academy", "BDA")

# pack_id → 中文名 / 匹配词 / 关联 outfit scene_id（可选）
PACKS: dict[str, dict] = {
    "dorm": {
        "zh": "宿舍",
        "aliases": ("宿舍", "寝室", "宿舍楼", "宿舍窗边", "洗漱", "被窝"),
        "outfit_ids": ("campus-007", "campus-017", "campus-043"),
        "queries": (
            "北京舞蹈学院 宿舍楼 外景",
            "北京舞蹈学院 学生宿舍 走廊 空景",
            "北京舞蹈学院 宿舍 寝室 内景 空景",
            "北京舞蹈学院 宿舍楼 阳台 窗边",
            "北舞 宿舍 洗漱间 走廊",
            "Beijing Dance Academy dormitory exterior",
            "Beijing Dance Academy student dorm corridor empty",
            "BDA Beijing dormitory room interior empty",
        ),
    },
    "canteen": {
        "zh": "食堂",
        "aliases": ("食堂", "饭堂", "窗口", "午饭", "晚饭", "餐盘"),
        "outfit_ids": ("campus-010",),
        "queries": (
            "北京舞蹈学院 食堂 内景 空景",
            "北京舞蹈学院 学生食堂",
            "北京舞蹈学院 食堂 窗口 打饭",
            "北京舞蹈学院 食堂 二楼 餐桌 空景",
            "北舞 学生餐厅 内景",
            "Beijing Dance Academy cafeteria interior empty",
            "Beijing Dance Academy canteen dining hall empty",
            "BDA campus cafeteria food court empty",
        ),
    },
    "library": {
        "zh": "图书馆",
        "aliases": ("图书馆", "自习", "书库", "阅览室"),
        "outfit_ids": ("campus-009",),
        "queries": (
            "北京舞蹈学院 图书馆 内景 空景",
            "北京舞蹈学院 图书馆 阅览室",
            "北京舞蹈学院 图书馆 自习室 空景",
            "北京舞蹈学院 图书馆 书架 书库",
            "北舞 图书馆 夜场 阅览",
            "Beijing Dance Academy library interior empty",
            "Beijing Dance Academy library reading room empty",
            "BDA campus library study hall empty",
        ),
    },
    "stadium": {
        "zh": "体育场",
        "aliases": ("体育场", "操场", "跑道", "田径场", "运动场"),
        "outfit_ids": ("campus-020", "sport-005"),
        "queries": (
            "北京舞蹈学院 操场 体育场",
            "北京舞蹈学院 田径场 空景",
            "北京舞蹈学院 操场 跑道 空景",
            "北京舞蹈学院 运动场 看台",
            "北舞 操场 晨练 空景",
            "Beijing Dance Academy stadium track field",
            "Beijing Dance Academy campus playground track empty",
            "BDA Beijing outdoor sports field empty",
        ),
    },
    "classroom": {
        "zh": "教室",
        "aliases": ("教室", "阶梯教室", "上课", "课桌", "大课"),
        "outfit_ids": ("campus-011", "campus-001"),
        "queries": (
            "北京舞蹈学院 教室 内景 空景",
            "北京舞蹈学院 阶梯教室",
            "北京舞蹈学院 多媒体教室 空景",
            "北京舞蹈学院 理论课 教室 课桌",
            "北舞 创意学院 教室 空景",
            "Beijing Dance Academy classroom interior empty",
            "Beijing Dance Academy lecture hall empty",
            "BDA campus classroom desks empty",
        ),
    },
    "studio": {
        "zh": "练功房",
        "aliases": ("练功房", "排练厅", "把杆", "基训", "排练", "舞蹈教室"),
        "outfit_ids": ("campus-001",),
        "queries": (
            "北京舞蹈学院 练功房 把杆 空景",
            "北京舞蹈学院 舞蹈教室 镜子",
            "北京舞蹈学院 排练厅 把杆 空景",
            "北京舞蹈学院 基训教室 镜子墙",
            "北舞 练功房 木地板 空景",
            "Beijing Dance Academy dance studio barre empty",
            "Beijing Dance Academy rehearsal hall mirrors empty",
            "BDA dance classroom barre mirror empty",
        ),
    },
}

# 活动/hint 文本 → pack（长词优先）
_PACK_PATTERNS: list[tuple[re.Pattern, str]] = []
for _pid, _meta in PACKS.items():
    words = sorted(set(_meta["aliases"]) | {_meta["zh"]}, key=len, reverse=True)
    _PACK_PATTERNS.append((re.compile("|".join(re.escape(w) for w in words)), _pid))


def common_dir(cfg=None) -> pathlib.Path:
    if cfg and cfg.get("imagegen", {}).get("common_scenes_dir"):
        return pathlib.Path(cfg["imagegen"]["common_scenes_dir"]).expanduser()
    return DEFAULT_COMMON_DIR


def pack_dir(pack_id: str, cfg=None) -> pathlib.Path:
    return common_dir(cfg) / pack_id


def pack_meta_path(pack_id: str, cfg=None) -> pathlib.Path:
    return pack_dir(pack_id, cfg) / "pack.json"


def load_pack_meta(pack_id: str, cfg=None) -> dict | None:
    p = pack_meta_path(pack_id, cfg)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def resolve_pack(text: str | None) -> Optional[str]:
    """从活动/scene_hints/自由描述解析常用场景 pack_id。"""
    t = (text or "").strip()
    if not t:
        return None
    for rx, pid in _PACK_PATTERNS:
        if rx.search(t):
            return pid
    return None


def resolve_pack_from_slot(slot: dict | None = None, scene: str | None = None) -> Optional[str]:
    slot = slot or {}
    hints = " ".join(str(h) for h in (slot.get("scene_hints") or []))
    blob = " ".join(
        x for x in (
            scene,
            slot.get("activity"),
            hints,
            slot.get("bucket"),
            slot.get("tone"),
        ) if x
    )
    return resolve_pack(blob)


def list_pack_images(pack_id: str, cfg=None) -> list[pathlib.Path]:
    d = pack_dir(pack_id, cfg) / "images"
    if not d.is_dir():
        # 兼容：图片直接放在 pack 根
        d = pack_dir(pack_id, cfg)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.is_file():
            # 跳过被 cluster 拒绝的
            if p.name.startswith("_rejected_"):
                continue
            out.append(p)
    meta = load_pack_meta(pack_id, cfg) or {}
    kept = set(meta.get("kept_files") or [])
    if kept:
        filtered = [p for p in out if p.name in kept or str(p) in kept]
        if filtered:
            return filtered
    return out


def pick_common(pack_id: str, k: int = 2, cfg=None, *, rng=None) -> list[pathlib.Path]:
    """从常用场景包取最多 k 张（稳定顺序；可选 rng 洗牌）。"""
    imgs = list_pack_images(pack_id, cfg)
    if not imgs or k <= 0:
        return []
    if rng is not None:
        imgs = list(imgs)
        rng.shuffle(imgs)
    return imgs[:k]


def scene_key_for_slot(slot: dict | None = None, scene: str | None = None) -> str:
    """日内探针键：优先 common pack，否则规范化 hint/activity。"""
    pid = resolve_pack_from_slot(slot, scene)
    if pid:
        return f"common:{pid}"
    slot = slot or {}
    hints = [str(h).strip() for h in (slot.get("scene_hints") or []) if str(h).strip()]
    if hints:
        return f"hint:{hints[0][:24]}"
    act = (slot.get("activity") or scene or "campus").strip()[:32]
    return f"act:{act}"


def manifest(cfg=None) -> dict:
    root = common_dir(cfg)
    packs = {}
    for pid, meta in PACKS.items():
        imgs = list_pack_images(pid, cfg)
        pm = load_pack_meta(pid, cfg) or {}
        packs[pid] = {
            "zh": meta["zh"],
            "n_images": len(imgs),
            "dir": str(pack_dir(pid, cfg)),
            "source_family": pm.get("source_family"),
            "campus": pm.get("campus") or CAMPUS_NAME,
        }
    return {
        "campus": CAMPUS_NAME,
        "campus_en": CAMPUS_NAME_EN,
        "root": str(root),
        "packs": packs,
    }
