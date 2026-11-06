"""生图镜头模式：手部特写 / 第一视角 / 对镜自拍 / 全身 / 人像 / 腿部；报备 50/50；用户请求向量路由。

shot_type:
  hand_closeup      手部特写·主观第一视角（手模五视图 HARD；脸/身体不硬挂）
  pov_first_person  她拍的第一视角外景（scene_refs 为主；可露手臂手）
  mirror_selfie     对镜自拍（脸 HARD；手软可选；身体跳过或软）
  full_body         全身第三人称
  portrait          「看看你」上半身/自拍类（非独立对镜）
  face_closeup      脸特写 = 既有长相五视图 face sheet（alias→portrait；无新资产）
  legs_focus        腿部特写·主观第一视角（腿模 approved 后 HARD 独占 1 槽；否则 body 软）
  third_person      默认第三人称（现有路径）

报备（schedule_brief）：等分 hand_closeup | pov_first_person。
其它主动节拍 WANT_IMAGE：见 schedule_shot（日程槽 → shot + prompt 模板）。
用户「看看手/腿/全身/你/你拍的/对镜/自拍」等：正则优先 + embedding 相似度，命中 100% 硬锁分流（见 route_user_shot）；
绝不串卡（要腿不能发手）。默认每次发送走大树单选一条 SOP；仅当单次总张数 N>1 时才可多选，且多选仍按概率表独立抽取（见 schedule_shot.sample_shot_types_for_send）。
"""
from __future__ import annotations

import random
import re
from typing import Optional

SHOT_HAND = "hand_closeup"
SHOT_POV = "pov_first_person"
SHOT_MIRROR = "mirror_selfie"
SHOT_FULL_BODY = "full_body"
SHOT_PORTRAIT = "portrait"
SHOT_LEGS = "legs_focus"
SHOT_THIRD = "third_person"

BAOBEI_SHOTS = (SHOT_HAND, SHOT_POV)

ALL_SHOTS = (
    SHOT_HAND,
    SHOT_POV,
    SHOT_MIRROR,
    SHOT_FULL_BODY,
    SHOT_PORTRAIT,
    SHOT_LEGS,
    SHOT_THIRD,
)

# 用户请求种子（向量路由）；正则作 boost
SEED_CLUSTERS = {
    SHOT_MIRROR: (
        "对镜自拍", "镜子自拍", "试衣镜", "化妆镜自拍", "镜子里看看",
        "对镜拍一张", "镜前自拍", "mirror selfie", "看看镜子里",
        "对镜", "镜子里", "镜前看看", "拍对镜",
    ),
    SHOT_FULL_BODY: (
        "看看全身", "全身照", "露个全身", "全身给我看看", "发张全身",
        "看一下全身", "站着全身", "从头到脚", "完整全身",
        "全身呢", "全身来一张", "拍张全身", "要全身",
    ),
    SHOT_PORTRAIT: (
        "看看你", "看看脸", "自拍呢", "你的脸", "露个脸",
        "发张自拍", "看看模样", "你长啥样", "上半身",
        "来张自拍", "自拍一张", "自拍给我", "脸呢",
    ),
    SHOT_LEGS: (
        "看看腿", "腿呢", "美腿", "小腿", "腿部特写",
        "露个腿", "大长腿", "看看你的腿",
        "腿给我看", "拍腿", "要看腿",
        "腿照片", "发张腿", "发个腿照", "给我看腿", "腿照",
    ),
    SHOT_HAND: (
        "看看手", "手呢", "手部特写", "你的手", "露个手",
        "手模", "纤细的手", "看看你的手", "手给我看看",
        "手给我看", "拍手", "要看手", "手来一张",
        "手照片", "发张手", "发个手照", "给我看手", "手照",
    ),
    SHOT_POV: (
        "看看你拍的", "你拍的", "第一视角", "你拍的风景",
        "你刚拍的", "pov", "你镜头里", "外景你拍的", "看看风景照",
        "你拍一张", "你拍的照片", "用你视角", "你拍的那张",
    ),
}

# 明显短语 → 直接命中（boost）；仍可与向量并用
_REGEX_BOOST = {
    # 顺序敏感：route_user_shot 先匹配 POV，避免「看看你拍的」落入 portrait
    SHOT_MIRROR: re.compile(
        r"对镜自拍|镜子自拍|试衣镜|化妆镜自拍|镜前自拍|镜子里看看|对镜拍|对镜|镜子里|镜前看看|拍对镜",
        re.I,
    ),
    SHOT_FULL_BODY: re.compile(
        r"看看全身|全身照|露个全身|从头到脚|发张全身|全身呢|全身来一张|拍张全身|要全身|全身给我看",
        re.I,
    ),
    SHOT_LEGS: re.compile(
        r"看看腿|腿呢|美腿|腿部特写|大长腿|看看你的腿|腿给我看|拍腿|要看腿|露个腿|"
        r"腿照片|发张腿|发个腿|给我看腿|腿照|看下腿|看一下腿",
        re.I,
    ),
    SHOT_HAND: re.compile(
        r"看看手|手呢|手部特写|看看你的手|手给我看|露个手|拍手|要看手|手来一张|手模|"
        r"手照片|发张手|发个手|给我看手|手照|看下手|看一下手",
        re.I,
    ),
    SHOT_POV: re.compile(
        r"看看你拍的|你拍的|第一视角|你刚拍的|你镜头里|你拍一张|你拍的照片|用你视角|外景你拍的|看看风景照",
        re.I,
    ),
    SHOT_PORTRAIT: re.compile(
        r"看看你(?!拍的)|看看脸|自拍呢|露个脸|发张自拍|上半身|来张自拍|自拍一张|自拍给我|脸呢|"
        r"脸特写|面部特写|长相特写|看看五官",
        re.I,
    ),
}

# 向量门槛（相对次优 margin）；embed 不可用时退回正则
MIN_SCORE = 0.42
MIN_MARGIN = 0.04

_emb_cache = None  # {shot: vec} or False


def normalize_shot(shot: Optional[str]) -> Optional[str]:
    if not shot:
        return None
    s = str(shot).strip().lower().replace("-", "_")
    aliases = {
        "hand": SHOT_HAND, "hands": SHOT_HAND, "hand_closeup": SHOT_HAND,
        "pov": SHOT_POV, "first_person": SHOT_POV, "pov_first_person": SHOT_POV,
        "full": SHOT_FULL_BODY, "full_body": SHOT_FULL_BODY, "body": SHOT_FULL_BODY,
        "portrait": SHOT_PORTRAIT, "selfie": SHOT_PORTRAIT, "face": SHOT_PORTRAIT,
        "face_closeup": SHOT_PORTRAIT, "face_close": SHOT_PORTRAIT, "closeup_face": SHOT_PORTRAIT,
        "mirror": SHOT_MIRROR, "mirror_selfie": SHOT_MIRROR, "mirror_self": SHOT_MIRROR,
        "legs": SHOT_LEGS, "leg": SHOT_LEGS, "legs_focus": SHOT_LEGS,
        "third": SHOT_THIRD, "third_person": SHOT_THIRD,
    }
    return aliases.get(s) or (s if s in ALL_SHOTS else None)


def pick_baobei_shot(rng: random.Random | None = None) -> str:
    """报备生图来源：hand_closeup / pov_first_person 等分 50/50。"""
    rng = rng or random.Random()
    return rng.choice(list(BAOBEI_SHOTS))


def _load_cluster_emb():
    """种子短语均值嵌入；失败返回 None。"""
    global _emb_cache
    if _emb_cache is not None:
        return _emb_cache or None
    try:
        import numpy as np
        import outfit
        m = outfit._load_model()
        if not m:
            _emb_cache = False
            return None
        out = {}
        for shot, seeds in SEED_CLUSTERS.items():
            v = m.encode(list(seeds), normalize_embeddings=True, show_progress_bar=False)
            out[shot] = np.asarray(v, dtype="float32").mean(axis=0)
            n = float((out[shot] ** 2).sum()) ** 0.5
            if n > 0:
                out[shot] = out[shot] / n
        _emb_cache = out
        return out
    except Exception as e:
        import sys
        print(f"[shot_modes] embed unavailable, regex-only: {e}", file=sys.stderr)
        _emb_cache = False
        return None


def route_user_shot(text: str | None, *, min_score: float = MIN_SCORE,
                    min_margin: float = MIN_MARGIN) -> Optional[str]:
    """用户话术 → shot_type；无明确请求返回 None（保持默认第三人称路径）。

    优先正则 boost；否则 bge 向量对种子簇相似度（需 score≥min_score 且相对次优≥min_margin）。
    """
    t = (text or "").strip()
    if not t:
        return None
    # 正则直接命中（先 POV/腿/手/全身，再「看看你」避免吞掉「看看你拍的」）
    for shot in (SHOT_POV, SHOT_HAND, SHOT_MIRROR, SHOT_LEGS, SHOT_FULL_BODY, SHOT_PORTRAIT):
        rx = _REGEX_BOOST.get(shot)
        if rx and rx.search(t):
            return shot
    emb = _load_cluster_emb()
    if emb is None:
        return None
    try:
        import numpy as np
        import outfit
        m = outfit._load_model()
        if not m:
            return None
        q = m.encode([t], normalize_embeddings=True, show_progress_bar=False)
        qv = np.asarray(q[0], dtype="float32")
        scores = {k: float(qv @ v) for k, v in emb.items()}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, best_s = ranked[0]
        second_s = ranked[1][1] if len(ranked) > 1 else -1.0
        if best_s >= min_score and (best_s - second_s) >= min_margin:
            return best
    except Exception:
        return None
    return None


def slot_activity_bits(slot: dict | None = None, today: dict | None = None) -> dict:
    """从此刻槽抽生活动/地点碎片，供 prompt（不改 life_schedule 文件）。"""
    slot = slot or {}
    today = today or {}
    hints = list(slot.get("scene_hints") or [])
    act = (slot.get("activity") or "").strip()
    bucket = slot.get("bucket") or today.get("photo_bias") or "campus_life"
    place = hints[0] if hints else ""
    return {
        "activity": act,
        "bucket": bucket,
        "place": place,
        "hints": hints,
        "tone": (slot.get("tone") or "").strip(),
        "photo_ok": bool(slot.get("photo_ok", True)) if slot else True,
    }


def schedule_scene_for_shot(shot: str, slot: dict | None = None, today: dict | None = None) -> str:
    """结合日程槽生成短场景词（给 WANT_IMAGE / imagegen）。"""
    bits = slot_activity_bits(slot, today)
    act = bits["activity"] or "日常"
    place = bits["place"]
    base = place or act
    shot = normalize_shot(shot) or SHOT_THIRD
    if shot == SHOT_HAND:
        # 手部：强调手里/手边正在做的事
        if re.search(r"吃|餐|饭|咖啡|奶茶|喝", act + place):
            return f"{base}手边特写·握杯或拿筷子"[ :40]
        if re.search(r"书|自习|图书|编创|笔记|课", act + place):
            return f"{base}手部特写·翻书或握笔"[ :40]
        if re.search(r"手机|刷|冲浪", act + place):
            return f"{base}手部特写·握手机"[ :40]
        return f"{base}手部特写·纤细手"[ :40]
    if shot == SHOT_POV:
        # 第一视角外景：地点优先，像手机朝外拍
        if place:
            return f"{place}第一视角随手拍"[ :40]
        return f"{act}第一视角手机外景"[ :40]
    if shot == SHOT_FULL_BODY:
        return f"{base}全身站立随手拍"[ :40]
    if shot == SHOT_LEGS:
        return f"{base}腿部特写"[ :40]
    if shot == SHOT_MIRROR:
        if re.search(r"试衣|商场|店", act + place):
            return f"{base}试衣镜对镜自拍"[ :40]
        if re.search(r"化妆|宿舍|家|起床", act + place):
            return f"{base}化妆镜对镜自拍"[ :40]
        return f"{base}对镜自拍"[ :40]
    if shot == SHOT_PORTRAIT:
        return f"{base}上半身自拍"[ :40]
    return (base or act or "校园日常随手拍")[:40]


def baobei_image_hint(slot: dict | None = None, today: dict | None = None,
                      rng: random.Random | None = None) -> dict:
    """报备节拍附图：等分 hand | POV + 日程贴合场景。"""
    shot = pick_baobei_shot(rng)
    scene = schedule_scene_for_shot(shot, slot, today)
    return {
        "mode": "want_image",
        "shot_type": shot,
        "scene": scene,
        "image_path": None,
        "material_id": None,
        "source": "baobei_equal_split",
    }


def skips_hard_face_body(shot: str | None) -> bool:
    """hand / POV / legs 不强制挂脸+身体五视图 HARD（prompt 不得把 ref#1 写成正脸五视图）。"""
    s = normalize_shot(shot)
    return s in (SHOT_HAND, SHOT_POV, SHOT_LEGS)


def prompt_shot_block(shot: str | None, scene: str = "", bits: dict | None = None) -> str:
    """追加到 imagegen.build_prompt 的镜头约束。"""
    s = normalize_shot(shot)
    if not s or s == SHOT_THIRD:
        return ""
    bits = bits or {}
    act = bits.get("activity") or ""
    place = bits.get("place") or ""
    if s == SHOT_HAND:
        return ("镜头模式 hand_closeup：第一人称；拍自己的手（女生的手）。右手或左手举手机，拍当前正在做的事。结合当前活动与场景。\n"
                "注意真实手部生理结构与细节（指节/指甲/左右手朝向自然）。\n")
    if s == SHOT_POV:
        loc = place or act or scene or "当下场景"
        return (
            f"镜头模式 pov_first_person：第一人称手机外景/场景随手拍，仿佛她举起手机朝前拍摄「{loc}」；"
            f"可露出持机的手臂或手边缘，不要摆拍全身人像，不要正对镜头的完整自拍脸。"
            f"优先沿用场景参考图的空间与光影。竖构图。\n"
        )
    if s == SHOT_FULL_BODY:
        return "镜头模式 full_body：第三人称全身入镜，从头到脚可见，自然站立或走动。\n"
    if s == SHOT_LEGS:
        return ("镜头模式 legs_focus：第一人称拍自己的腿（女生的腿）。结合当前活动与场景。\n"
                "注意真实脚部生理结构与细节（左右脚/脚趾朝向自然）。\n")
    if s == SHOT_MIRROR:
        loc = place or act or scene or "当下场景"
        return (
            f"镜头模式 mirror_selfie：对镜自拍，手机/镜子同时入镜或可见镜框边缘；"
            f"脸与上半身清晰，气质贴合身份脸模；环境贴合「{loc}」。"
            f"自然握机构图，不要棚拍正脸证件照，不要纯前置无镜感。"
            f"竖构图手机直出。\n"
        )
    if s == SHOT_PORTRAIT:
        return (
            "镜头模式 portrait/face_closeup：上半身/面部近景；身份锁既有长相五视图 face sheet"
            "（canonical/sheet.png），无单独脸特写资产。前置自拍感，非必须对镜。\n"
        )
    return ""
