"""日程槽 → 生图镜头 + prompt/ref 模板（主动节拍统一入口）。

将 life_schedule 当前槽（+ beat id）映射为：
  shot_type / scene 短语 / shot_bits / ref_policy

供 proactive_beats（报备 / 吃饭 / activity_end 等 WANT_IMAGE）调用；
底层镜头约束仍走 shot_modes.prompt_shot_block，参考图走 refpick.pick_identity。

决策树（大树单选）：
  - 用户显式路由（regex/embed）→ 硬锁该 shot_type，整次发送不串卡。
  - 主动/日程：每次先抽恰好 1 条 SOP（weights：full_body 0.40，其余五类各 0.12），
    除非 _ACT_FORCE / 报备 baobei 特殊路径。
  - 仅当单次总张数 N>1 时允许多选：对同一权重表做 N 次独立有放回抽样
    （sample_shot_types_for_send）；每张图各自走自己的卡路径，不做糊成一团的混合。

用法（库）：
  from schedule_shot import image_hint_for_beat, pick_shot_for_slot
  hint = image_hint_for_beat("meal", slot, today, rng=rng)

CLI（本机核验）：
  .venv/bin/python src/schedule_shot.py --beat meal --activity 吃晚饭 --bucket campus_life
"""
from __future__ import annotations

import argparse
import json
import random
import re
from typing import Optional

import shot_modes as sm

# 非报备主动节拍：全身更高概率；其余 body-part 镜头在剩余概率上等分。
# 报备 schedule_brief 仍走 shot_modes 50/50 hand|POV（特殊路径，见 BAOBEI_BEATS）。
# FULL_BODY_WEIGHT + 其余五类均分 (1-FULL_BODY_WEIGHT)/5。
FULL_BODY_WEIGHT = 0.40
_PART_EQUAL = ("hand_closeup", "pov_first_person", "mirror_selfie", "legs_focus", "portrait")

def _body_part_weights(full_body: float = FULL_BODY_WEIGHT) -> dict[str, float]:
    rest = max(0.0, 1.0 - float(full_body))
    each = rest / len(_PART_EQUAL)
    w = {sm.SHOT_FULL_BODY: float(full_body)}
    for name in _PART_EQUAL:
        w[name] = each
    return w

_DEFAULT_WEIGHTS = _body_part_weights()

# bucket 仅作轻偏置：在等分底座上微调，再归一；不推翻「全身更高、其余约等分」
_BUCKET_BIAS: dict[str, dict[str, float]] = {
    "weekend_home": {sm.SHOT_MIRROR: 0.06, sm.SHOT_PORTRAIT: 0.04, sm.SHOT_POV: -0.04, sm.SHOT_LEGS: -0.02},
    "campus_life": {},
    "school_library": {sm.SHOT_HAND: 0.06, sm.SHOT_POV: -0.02, sm.SHOT_LEGS: -0.04},
    "school_class": {sm.SHOT_HAND: 0.04, sm.SHOT_PORTRAIT: 0.02, sm.SHOT_LEGS: -0.04},
    "school_rehearsal": {sm.SHOT_FULL_BODY: 0.05, sm.SHOT_LEGS: 0.04, sm.SHOT_MIRROR: -0.04},
    "campus_fun": {sm.SHOT_POV: 0.05, sm.SHOT_FULL_BODY: 0.03, sm.SHOT_MIRROR: -0.04},
    "city_fun": {sm.SHOT_MIRROR: 0.05, sm.SHOT_POV: 0.03, sm.SHOT_LEGS: -0.04},
    "outdoor_outing": {sm.SHOT_POV: 0.06, sm.SHOT_FULL_BODY: 0.03, sm.SHOT_MIRROR: -0.05},
    "travel": {sm.SHOT_POV: 0.05, sm.SHOT_MIRROR: 0.02, sm.SHOT_LEGS: -0.03},
    "work_internship": {sm.SHOT_HAND: 0.04, sm.SHOT_POV: 0.02, sm.SHOT_LEGS: -0.04},
}

def _weights_for_bucket(bucket: str) -> dict[str, float]:
    w = dict(_DEFAULT_WEIGHTS)
    for k, delta in (_BUCKET_BIAS.get(bucket) or {}).items():
        w[k] = max(0.0, w.get(k, 0.0) + float(delta))
    # 保底：全身不低于底座的 85%
    w[sm.SHOT_FULL_BODY] = max(w.get(sm.SHOT_FULL_BODY, 0.0), FULL_BODY_WEIGHT * 0.85)
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items() if v > 0}

# 兼容旧名：测试/外部若读 _BUCKET_WEIGHTS
_BUCKET_WEIGHTS: dict[str, dict[str, float]] = {
    b: _weights_for_bucket(b) for b in (
        "weekend_home", "campus_life", "school_library", "school_class",
        "school_rehearsal", "campus_fun", "city_fun", "outdoor_outing",
        "travel", "work_internship",
    )
}

# 活动/地点关键词 → 强制偏好（覆盖加权，仍在合法集合内）
_ACT_FORCE = (
    (re.compile(r"试衣|化妆镜|镜子|宿舍.*自拍|镜前", re.I), sm.SHOT_MIRROR),
    (re.compile(r"吃|餐|饭|咖啡|奶茶|喝|食堂|外卖", re.I), sm.SHOT_HAND),
    (re.compile(r"书|自习|图书|编创|笔记|写|课桌", re.I), sm.SHOT_HAND),
    (re.compile(r"腿|短裙|短裤|丝袜|美腿", re.I), sm.SHOT_LEGS),
    (re.compile(r"夜景|外景|街拍|操场|湖边|山|公园|地铁|路上", re.I), sm.SHOT_POV),
)

# 报备仍严格 50/50 hand|POV（不经 bucket 加权）
BAOBEI_BEATS = frozenset({"schedule_brief"})

# 默认可附图的主动节拍（material 优先的 meal 另算）
WANT_IMAGE_BEATS = frozenset({"schedule_brief", "meal", "activity_end"})


def ref_policy(shot: str | None) -> dict:
    """镜头 → 参考图策略说明（实际挂载在 refpick / imagegen.cap_refs）。"""
    s = sm.normalize_shot(shot) or sm.SHOT_THIRD
    if s == sm.SHOT_HAND:
        return {"hard": ["hands/sheet"], "soft": [], "skip": ["body/sheet"], "face": "skip_or_weak"}
    if s == sm.SHOT_POV:
        return {"hard": [], "soft": ["face/sheet", "hands/sheet"], "skip": ["body/sheet"], "face": "soft"}
    if s == sm.SHOT_MIRROR:
        return {"hard": ["face/sheet"], "soft": ["hands/sheet"], "skip": ["body/sheet"], "face": "hard"}
    if s == sm.SHOT_LEGS:
        # approved：仅 legs 1 身份槽；未批准时 soft body（+弱 face 仅 fallback 路径）
        return {"hard": ["legs/sheet_if_approved"], "soft": ["body/sheet"], "skip": ["face/sheet_when_legs_approved"], "face": "skip_when_legs_hard"}
    if s == sm.SHOT_FULL_BODY:
        return {"hard": ["face/sheet", "body/sheet"], "soft": [], "skip": [], "face": "hard"}
    if s == sm.SHOT_PORTRAIT:
        return {"hard": ["face/sheet"], "soft": [], "skip": ["body/sheet"], "face": "hard"}
    return {"hard": ["face/sheet", "body/sheet"], "soft": [], "skip": [], "face": "hard"}


def _weighted_pick(weights: dict[str, float], rng: random.Random) -> str:
    items = [(k, float(v)) for k, v in weights.items() if v > 0]
    if not items:
        return sm.SHOT_HAND
    total = sum(v for _, v in items)
    x = rng.random() * total
    acc = 0.0
    for k, v in items:
        acc += v
        if x <= acc:
            return k
    return items[-1][0]


def _act_blob(slot: dict | None) -> str:
    slot = slot or {}
    hints = " ".join(str(h) for h in (slot.get("scene_hints") or []))
    return f"{slot.get('activity') or ''} {hints} {slot.get('tone') or ''}"


def pick_shot_for_slot(
    slot: dict | None = None,
    today: dict | None = None,
    *,
    beat_id: str | None = None,
    rng: random.Random | None = None,
) -> str:
    """日程槽 (+ 节拍) → shot_type。

    schedule_brief：报备 50/50 hand|POV。
    其它：活动关键词优先，否则按 bucket 加权（含 mirror_selfie / full_body 等）。
    """
    rng = rng or random.Random()
    bid = (beat_id or "").strip()
    if bid in BAOBEI_BEATS:
        return sm.pick_baobei_shot(rng)

    slot = slot or {}
    blob = _act_blob(slot)
    for rx, shot in _ACT_FORCE:
        if rx.search(blob):
            # 腿模未批准时仍可返回 legs_focus（refpick 退回 body 软挂）
            return shot

    bucket = (slot.get("bucket") or (today or {}).get("photo_bias") or "campus_life").strip()
    weights = _weights_for_bucket(bucket)

    # meal 节拍：进一步偏手部特写（杯碗筷）
    if bid == "meal":
        weights[sm.SHOT_HAND] = weights.get(sm.SHOT_HAND, 0) + 0.35
        weights[sm.SHOT_POV] = weights.get(sm.SHOT_POV, 0) + 0.10
        weights.pop(sm.SHOT_LEGS, None)

    # activity_end：刚结束的活动用 ended 槽更贴；偏 POV/全身「刚忙完」
    if bid == "activity_end":
        weights[sm.SHOT_POV] = weights.get(sm.SHOT_POV, 0) + 0.15
        weights[sm.SHOT_FULL_BODY] = weights.get(sm.SHOT_FULL_BODY, 0) + 0.10

    return _weighted_pick(weights, rng)


def sample_shot_types_for_send(
    n: int,
    slot: dict | None = None,
    today: dict | None = None,
    *,
    beat_id: str | None = None,
    rng: random.Random | None = None,
    locked_shot: str | None = None,
) -> list[str]:
    """一次发送的镜头路径列表（树状单选；N>1 才多选）。

    规则（有文档、可测）：
    1) locked_shot（用户显式路由硬锁）：返回 [locked] * n，绝不串到其它卡。
    2) n <= 1：恰抽 1 条 SOP（pick_shot_for_slot / baobei）。
    3) n > 1：对同一权重表做 n 次**独立有放回**抽样（independent draws with
       replacement）。每张图各自执行该 shot 的 SOP，不混合 prompt/refs。
    """
    rng = rng or random.Random()
    n = max(1, min(5, int(n)))  # align MAX_CHAT_IMAGES_ABS
    lock = sm.normalize_shot(locked_shot)
    if lock:
        return [lock] * n
    return [pick_shot_for_slot(slot, today, beat_id=beat_id, rng=rng) for _ in range(n)]


def prompt_template(shot: str | None, scene: str = "", bits: dict | None = None) -> str:
    """镜头 prompt 块（委托 shot_modes）。"""
    return sm.prompt_shot_block(shot, scene, bits)


def image_hint_for_beat(
    beat_id: str,
    slot: dict | None = None,
    today: dict | None = None,
    *,
    rng: random.Random | None = None,
    shot: str | None = None,
) -> dict:
    """主动节拍附图计划：mode=want_image + shot_type + scene + shot_bits + ref_policy。"""
    rng = rng or random.Random()
    bid = (beat_id or "").strip() or "daily_chat"
    # 报备走原 baobei_image_hint 语义（保持 source 字段兼容测试）
    if bid in BAOBEI_BEATS:
        hint = sm.baobei_image_hint(slot, today, rng=rng)
        st = hint.get("shot_type")
        bits = sm.slot_activity_bits(slot, today)
        hint["shot_bits"] = bits
        hint["ref_policy"] = ref_policy(st)
        hint["prompt_shot"] = prompt_template(st, hint.get("scene") or "", bits)
        mp = (slot or {}).get("meal_period")
        if mp and mp != "any":
            hint["meal_period"] = mp
        return hint

    st = sm.normalize_shot(shot) or pick_shot_for_slot(slot, today, beat_id=bid, rng=rng)
    scene = sm.schedule_scene_for_shot(st, slot, today)
    bits = sm.slot_activity_bits(slot, today)
    if (slot or {}).get("photo_note"):
        scene += "；" + slot["photo_note"]
    if (slot or {}).get("photo_subject"):
        scene += "；画面重点：" + slot["photo_subject"] + "；不据此编造门店招牌、电影片名或同行者合照"
    out = {
        "mode": "want_image",
        "shot_type": st,
        "scene": scene,
        "shot_bits": bits,
        "image_path": None,
        "material_id": None,
        "source": f"schedule_shot:{bid}",
        "ref_policy": ref_policy(st),
        "prompt_shot": prompt_template(st, scene, bits),
    }
    # WANT_IMAGE / image pick share slot.meal_period (normalized on get_day)
    mp = (slot or {}).get("meal_period")
    if not mp:
        try:
            import proactive_beats as _pb
            mp = _pb.meal_period_for_slot(slot)
        except Exception:
            mp = None
    if mp and mp != "any":
        out["meal_period"] = mp
    return out


def describe_mapping(
    slot: dict | None = None,
    today: dict | None = None,
    *,
    beat_id: str = "meal",
    seed: int = 0,
) -> dict:
    """调试用：固定 seed 看槽→镜头→场景→ref。"""
    rng = random.Random(seed)
    hint = image_hint_for_beat(beat_id, slot, today, rng=rng)
    return {
        "beat_id": beat_id,
        "slot": {
            "activity": (slot or {}).get("activity"),
            "bucket": (slot or {}).get("bucket"),
            "scene_hints": (slot or {}).get("scene_hints"),
            "photo_ok": (slot or {}).get("photo_ok", True),
        },
        "hint": {k: hint[k] for k in ("shot_type", "scene", "source", "ref_policy") if k in hint},
        "prompt_shot_preview": (hint.get("prompt_shot") or "")[:200],
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="日程槽 → shot_type + prompt 模板（核验用）")
    ap.add_argument("--beat", default="meal", help="节拍 id：schedule_brief|meal|activity_end|…")
    ap.add_argument("--activity", default="吃晚饭")
    ap.add_argument("--bucket", default="campus_life")
    ap.add_argument("--hint", action="append", default=[], help="scene_hints，可重复")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-n", type=int, default=1, help="抽样次数（看分布）")
    args = ap.parse_args(argv)
    slot = {
        "activity": args.activity,
        "bucket": args.bucket,
        "scene_hints": args.hint or ([args.activity] if args.activity else []),
        "photo_ok": True,
        "tone": "",
    }
    if args.n <= 1:
        print(json.dumps(describe_mapping(slot, beat_id=args.beat, seed=args.seed),
                         ensure_ascii=False, indent=2))
        return 0
    from collections import Counter
    c: Counter[str] = Counter()
    for i in range(args.n):
        h = image_hint_for_beat(args.beat, slot, rng=random.Random(args.seed + i))
        c[h["shot_type"]] += 1
    print(json.dumps({"beat": args.beat, "slot": slot, "counts": dict(c)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
