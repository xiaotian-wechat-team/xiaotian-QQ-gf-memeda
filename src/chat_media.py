"""Chat ↔ imagegen bridge（薄适配层）。

- imagegen.generate 保持纯生图（不碰微信 UI）。
- 本模块：解析模型发图意图 → 调 generate(chat=True) → 经 mac_wechat_io.send_image 发出。
- 聊天路径 skip face-fallback 双跑；参考图由 imagegen.cap_refs 硬裁 ≤5（五视图 sheet 必留）。
- 可注入 generate_fn / io，便于单测与扩展其它通道。
- 一次 WANT_IMAGE 先定 N，再经 imagegen.build_quantity_prompt 独立数量通道 + 内容 prompt 同一次 Codex 出齐（禁止串行多轮 resume 一张一张凑；数量指令不混写进场景/妆造/构图）。
- 上限分车道：明确要图（demand）默认 max=5 / p≈[0.40,0.30,0.18,0.08,0.04]；主动节拍（proactive）默认 max=5 / p≈[0.50,0.28,0.12,0.07,0.03]（偏 1–2，偶发 3–5，允许>1）。绝对顶 MAX_CHAT_IMAGES_ABS=5。
- 参考图冲突：GPT/Codex 单次 -i 槽 ≤5；N>1 时全部张数共享同一套 refs（按整批镜头需求的并集保留 face/body/hands/legs HARD，再填场景），禁止「每张图各挂一套 face/body/hands/legs」。配情侣式多图互动短句。

模型触发（短 DeepSeek / 长 cursor 共用）：
  1) 行标签：WANT_IMAGE:场景描述
  2) JSON：{"want_image":"场景","caption":"可选配文"}

稳定 API：
  parse_image_intent(text) -> {"scene","caption"} | None
  generate_path(scene, **ctx) -> Path
  send_chat_image(path, *, io=None, ...)
  send_generated_image(scene|path, *, caption=None, dry=False, ...) -> dict
  deliver_model_output(raw_or_text, *, scene=None, ...) -> dict
"""
from __future__ import annotations

import json
import pathlib
import random
import time
import re
import sys

from config import load

# 行首/独立行：WANT_IMAGE:图书馆窗边看书
_WANT_LINE = re.compile(
    r"(?im)^\s*WANT_IMAGE\s*[:：]\s*(.+?)\s*$"
)
# 行内兜底（模型偶发写在句末）
_WANT_INLINE = re.compile(
    r"(?i)\bWANT_IMAGE\s*[:：]\s*(.+?)(?:\s*$|(?=\s*[。！？\n]))"
)
# 宽松 JSON 块
_JSON_BLOB = re.compile(r"\{[^{}]*\}", re.S)

ALLOWED_CONTACT_IDS = {"demo_contact"}
ALLOWED_REMARKS = {"小熊"}


def parse_image_intent(text: str | None):
    """从模型原文解析发图意图。

    返回 {"scene": str, "caption": str} 或 None。
    caption 为去掉标记后的配文（可空）；scene 非空才算命中。
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # 1) JSON 优先（整段或内嵌）；必须带 want_image / WANT_IMAGE 键
    for blob in _JSON_BLOB.findall(raw):
        try:
            obj = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if "want_image" not in obj and "WANT_IMAGE" not in obj:
            continue
        scene = (obj.get("want_image") or obj.get("WANT_IMAGE") or "").strip()
        if not scene:
            continue
        if "caption" in obj:
            caption = str(obj.get("caption") or "").strip()
        else:
            caption = _JSON_BLOB.sub("", raw).strip()
        out = {"scene": scene, "caption": caption}
        st = obj.get("shot_type") or obj.get("photo_mode") or obj.get("shot")
        if st:
            out["shot_type"] = str(st).strip()
        return out

    # 2) WANT_IMAGE: 行
    m = _WANT_LINE.search(raw)
    if not m:
        m = _WANT_INLINE.search(raw)
    if not m:
        return None
    scene = m.group(1).strip().strip('"“”')
    if not scene:
        return None
    shot_type = None
    sm = re.match(r"\[([a-zA-Z0-9_\-]+)\]\s*(.*)$", scene)
    if sm:
        shot_type = sm.group(1).strip()
        scene = sm.group(2).strip() or scene
    caption = (raw[: m.start()] + raw[m.end() :]).strip()
    caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
    out = {"scene": scene, "caption": caption}
    if shot_type:
        out["shot_type"] = shot_type
    return out


def assert_allowed_contact(cfg=None):
    """硬白名单：只允许对 demo_contact / 小熊 发图。"""
    cfg = cfg or load()
    c = cfg.get("contact") or {}
    cid, remark = str(c.get("id") or ""), (c.get("remark") or "").strip()
    if cid not in ALLOWED_CONTACT_IDS or remark not in ALLOWED_REMARKS:
        raise PermissionError(
            f"chat_media: contact not whitelisted (id={cid!r} remark={remark!r})"
        )
    return c


# 多图 one-shot 上限（嘴损情侣：控成本/延迟/五官身形手腿参考质量；用户定 1～5 含端点）
MAX_CHAT_IMAGES_ABS = 5  # 代码绝对顶；config.chat_image_count_max_abs 不得高于此
DEFAULT_CHAT_IMAGE_COUNT_MAX = 5  # 对方明确要图
DEFAULT_PROACTIVE_IMAGE_COUNT_MAX = 5  # 主动节拍可 >1，仍偏少张防刷屏
# 需求侧默认分布（长度=max；偏 1–2，偶发 3–5）；CHAT_IMAGE_COUNT_P / chat_image_count_p 可覆盖
DEFAULT_CHAT_IMAGE_COUNT_P = (0.40, 0.30, 0.18, 0.08, 0.04)
# 主动侧：偏 1–2、偶发 3–5，必须允许 >1 且可达 5（嘴损情侣别刷屏）
DEFAULT_PROACTIVE_IMAGE_COUNT_P = (0.50, 0.28, 0.12, 0.07, 0.03)

_MULTI_OPENERS = (
    "多拍了几张你挑",
    "啧 一下子好几个",
    "给你看几张 别嫌多",
    "随手拍了几张",
)
_MULTI_BETWEEN = (
    "这张？",
    "还是这张",
    "这张光线一般 下一张",
    "哼 这张也还行",
    "再来一张",
)
_MULTI_CLOSERS = (
    "你喜欢哪张",
    "挑一张呗",
    "别全要 选一个",
    "哪张顺眼说一声",
)


def enrich_scene(scene: str | None, *, st=None, now=None, chat_tail="", force_schedule=False,
                 rng=None, imagined=False) -> str:
    """人设日程加权场景；供 WANT_IMAGE 与他要照片时使用。"""
    import photo_scene
    return photo_scene.suggest_scene(
        model_scene=scene, st=st, now=now, chat_tail=chat_tail,
        force_schedule=force_schedule, rng=rng, imagined=imagined,
    )


def hold_on_line(rng=None, **kwargs) -> str:
    """生图前等待气泡；kwargs 透传 shot_type/user_text/recent_outbound/complete_fn。"""
    import photo_scene
    return photo_scene.hold_on_line(rng, **kwargs)


def image_count_max(cfg=None, *, lane: str = "demand") -> int:
    """本车道允许的最大 N（再被 MAX_CHAT_IMAGES_ABS 夹紧）。

    lane:
      - "demand"：对方明确要图（看看/发图/自拍…）
      - "proactive"：主动节拍（报备/meal/activity…）
    """
    ig = ((cfg or load()).get("imagegen") or {})
    try:
        abs_max = int(ig.get("chat_image_count_max_abs") or MAX_CHAT_IMAGES_ABS)
    except (TypeError, ValueError):
        abs_max = MAX_CHAT_IMAGES_ABS
    abs_max = max(1, min(MAX_CHAT_IMAGES_ABS, abs_max))
    key = "proactive_image_count_max" if lane == "proactive" else "chat_image_count_max"
    default = DEFAULT_PROACTIVE_IMAGE_COUNT_MAX if lane == "proactive" else DEFAULT_CHAT_IMAGE_COUNT_MAX
    try:
        m = int(ig.get(key) if ig.get(key) is not None else default)
    except (TypeError, ValueError):
        m = default
    return max(1, min(abs_max, m))


def clamp_image_count(n, cfg=None, *, lane: str = "demand") -> int:
    """把调用方给定的 N 夹到当前车道上限。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(image_count_max(cfg, lane=lane), n))


def sample_image_count(cfg=None, rng=None, *, lane: str = "demand") -> int:
    """采样本次发图张数；demand/proactive 均默认 ∈[1,5]（分布不同，均偏 1–2）。"""
    rng = rng or random.Random()
    cfg = cfg or load()
    ig = (cfg.get("imagegen") or {})
    max_n = image_count_max(cfg, lane=lane)
    raw = None
    if lane != "proactive":
        env = __import__("os").environ.get("CHAT_IMAGE_COUNT_P")
        if env:
            try:
                raw = [float(x) for x in env.split(",")]
            except ValueError:
                raw = None
    if raw is None:
        if lane == "proactive":
            raw = ig.get("proactive_image_count_p") or list(DEFAULT_PROACTIVE_IMAGE_COUNT_P)
        else:
            raw = ig.get("chat_image_count_p") or list(DEFAULT_CHAT_IMAGE_COUNT_P)
    p = [max(0.0, float(x)) for x in raw][:max_n]
    while len(p) < max_n:
        p.append(0.0)
    s = sum(p)
    if s <= 0:
        # 旧更长分布被截到 max 后可能全 0 → 退回单张
        return 1
    p = [x / s for x in p]
    return int(rng.choices(list(range(1, max_n + 1)), p)[0])


def multi_interaction_texts(n: int, caption: str | None = None, rng=None) -> list[str | None]:
    """为 n 张图生成配套短句列表（长度 n）：第 i 张发送前的配文；None=该张不附文字。

    张数=1：仅用模型 caption（可空）。
    张数>1：opener（优先用 caption）+ 中间损句/挑图 + 末张 closer，情侣随手感。
    """
    rng = rng or random.Random()
    n = clamp_image_count(n, lane="demand")
    cap = (caption or "").strip() or None
    if n == 1:
        return [cap]
    lines: list[str | None] = [None] * n
    lines[0] = cap or rng.choice(_MULTI_OPENERS)
    if n >= 3:
        lines[1] = rng.choice(_MULTI_BETWEEN)
    if n >= 4:
        lines[2] = rng.choice(_MULTI_BETWEEN)
    if n >= 5:
        lines[3] = rng.choice(_MULTI_BETWEEN)
    lines[n - 1] = rng.choice(_MULTI_CLOSERS)
    # 若 opener 已是 caption 且与 closer 撞车极少见；确保末张有互动
    if not lines[n - 1]:
        lines[n - 1] = rng.choice(_MULTI_CLOSERS)
    return lines


def generate_path(scene: str, *, generate_fn=None, dry=False, **ctx) -> pathlib.Path | None:
    """调 imagegen.generate(chat=True)；返回首张图 Path。dry=True 时不真正生图，返回 None。
    聊天路径默认跳过 face-fallback 第二次 Codex；调用方可显式传 chat=False / skip_face_fallback=False 覆盖（仅测试）。
    shot_type via ctx：hand_closeup / pov_first_person / mirror_selfie / full_body / portrait / legs_focus。"""
    if dry:
        return None
    fn = generate_fn
    if fn is None:
        import imagegen
        fn = imagegen.generate
    # 聊天发图：只留 scene_refs 第一张，不双跑 A/B（除非调用方显式关掉）
    ctx.setdefault("chat", True)
    ctx.setdefault("skip_face_fallback", True)
    out = fn(scene, **ctx)
    # 兼容 (cmd, imgs) 与 直接 path / [path]
    if isinstance(out, (str, pathlib.Path)):
        p = pathlib.Path(out)
        if not p.is_file():
            raise RuntimeError(f"generate_path: not a file: {p}")
        return p
    if isinstance(out, (list, tuple)) and out and not isinstance(out[0], (str, pathlib.Path)) and len(out) == 2:
        _cmd, imgs = out
    else:
        imgs = out
    if not imgs:
        raise RuntimeError("generate_path: imagegen produced no images")
    p = pathlib.Path(imgs[0])
    if not p.is_file():
        raise RuntimeError(f"generate_path: missing image {p}")
    return p


def generate_paths(scene: str, n: int = 1, *, generate_fn=None, dry=False,
                   shot_types: list | None = None, **ctx) -> list[pathlib.Path]:
    """生成 n 张图。

    N=1：单次 generate（与旧路径一致）。
    N>1：先定张数，把 n_images 与可选分张 shot 说明交给同一次 imagegen 调用一次出齐；
    禁止在此循环串行多轮 resume / 一张一张凑到 N。

    shot_types：可选，长度 n 的每张图 shot_type 列表；缺省则所有张共用 ctx['shot_type']。
    """
    n = clamp_image_count(n, lane=str(ctx.get("image_lane") or "demand"))
    if dry:
        return []
    types = list(shot_types) if shot_types else None
    if n == 1:
        ctx_i = dict(ctx)
        if types is not None and types:
            ctx_i["shot_type"] = types[0]
        p = generate_path(scene, generate_fn=generate_fn, dry=False, **ctx_i)
        if p is None:
            raise RuntimeError("generate_paths: got None at 1/1")
        return [p]

    # N>1：一次调用；全部张数共享同一套 refs（shot_type=types[0] 决定 HARD），
    # 不按张分别挂 face/body/hands/legs——GPT 单次 -i ≤5，乘 N 会爆槽。
    ctx_n = dict(ctx)
    ctx_n["n_images"] = n
    ctx_n.setdefault("chat", True)
    ctx_n.setdefault("skip_face_fallback", True)
    if types is not None and types:
        ctx_n["shot_types"] = types
        ctx_n["shot_type"] = types[0]
    fn = generate_fn
    if fn is None:
        import imagegen
        fn = imagegen.generate
    out = fn(scene, **ctx_n)
    if isinstance(out, (str, pathlib.Path)):
        paths = [pathlib.Path(out)]
    elif isinstance(out, (list, tuple)) and out and not isinstance(out[0], (str, pathlib.Path)) and len(out) == 2:
        _cmd, imgs = out
        paths = [pathlib.Path(x) for x in (imgs or [])]
    else:
        paths = [pathlib.Path(x) for x in (out or [])]
    paths = [p for p in paths if p and p.is_file()]
    if not paths:
        raise RuntimeError(f"generate_paths: one-shot n={n} produced no images")
    if len(paths) < n:
        # 不足也不串行补齐
        import sys
        print(f"[chat_media] one-shot wanted {n}, got {len(paths)} (no serial fill)", file=sys.stderr)
    return paths[:n]


def plan_chat_images(
    scene: str,
    *,
    caption=None,
    cfg=None,
    n_images=None,
    rng=None,
    dry=False,
    generate_fn=None,
    shot_lock: bool = False,
    slot=None,
    today=None,
    beat_id=None,
    image_lane: str = "demand",
    imagined: bool = False,
    **ctx,
):
    """锁外准备：采样张数、镜头路径、生成路径、互动短句。不发送。

    image_lane: "demand"（对方明确要图，默认 max=5）或 "proactive"（主动节拍，默认 max=5 / 偏少张）。

    镜头树：
    - shot_lock=True 或用户硬锁 shot_type：整次发送同卡（不串卡）。
    - 否则 N==1：单路径（ctx.shot_type 或日程抽样）。
    - 否则 N>1：schedule_shot.sample_shot_types_for_send 独立有放回抽 N 条 SOP。
      （多卡仅改 prompt 分张说明；refs 仍共享一套，见 generate_paths。）
    """
    cfg = cfg or load()
    rng = rng or random.Random()
    lane = image_lane if image_lane in ("demand", "proactive") else "demand"
    ctx = dict(ctx)
    ctx["image_lane"] = lane
    if imagined:
        # An imagined scene has no current real-world slot or location. Keep
        # the user's shot request, but do not graft current activity props or
        # real-life memory into its scene/outfit selection.
        slot, today = None, None
        ctx.pop("shot_bits", None)
        ctx.update(imagined=True, activity="", chat=[], st={}, emo=None,
                   campus_life=[], persist_happy_slot=False)
    if n_images is not None:
        n = clamp_image_count(n_images, cfg, lane=lane)
    else:
        n = sample_image_count(cfg, rng, lane=lane)
    if imagined:
        cap = (caption or "").strip()
        label = cap if cap.startswith("想象场景") else "想象场景｜" + (cap or "按你说的画了一下")
        texts = [label] + [None] * (n - 1)
        if n > 1:
            texts[-1] = "你喜欢哪张"
    else:
        texts = multi_interaction_texts(n, caption, rng)

    locked = None
    if shot_lock and ctx.get("shot_type"):
        locked = ctx.get("shot_type")
    elif shot_lock is False and ctx.get("shot_type") and n == 1:
        # 单张已选定路径：沿用
        locked = None  # keep ctx.shot_type below
    shot_types = None
    try:
        import schedule_shot as _ss
        import shot_modes as _sm
        if shot_lock and ctx.get("shot_type"):
            shot_types = _ss.sample_shot_types_for_send(
                n, slot, today, beat_id=beat_id, rng=rng, locked_shot=ctx.get("shot_type"),
            )
        elif n > 1 and not shot_lock:
            # 多图且未硬锁：独立抽 N 条；忽略单一 ctx.shot_type（避免复读同一卡）
            shot_types = _ss.sample_shot_types_for_send(
                n, slot, today, beat_id=beat_id, rng=rng, locked_shot=None,
            )
        elif ctx.get("shot_type"):
            st = _sm.normalize_shot(ctx.get("shot_type")) or ctx.get("shot_type")
            shot_types = [st] * n
        else:
            shot_types = _ss.sample_shot_types_for_send(
                n, slot, today, beat_id=beat_id, rng=rng, locked_shot=None,
            )
    except Exception:
        st0 = ctx.get("shot_type")
        shot_types = [st0] * n if st0 else [None] * n

    if dry:
        return {
            "scene": scene, "n_images": n, "paths": [], "texts": texts, "dry": True,
            "shot_types": shot_types, "shot_type": (shot_types[0] if shot_types else ctx.get("shot_type")),
            "slot": slot, "beat_id": beat_id, "imagined": bool(imagined),
        }
    # 透传日程槽给 imagegen.pick_scene_refs（common pack resolve）
    gen_ctx = dict(ctx)
    if isinstance(slot, dict):
        gen_ctx.setdefault("slot", slot)
    if today is not None:
        gen_ctx.setdefault("today", today)
        if isinstance(today, dict) and today.get("date"):
            gen_ctx.setdefault("date", today["date"])
        elif hasattr(today, "isoformat"):
            gen_ctx.setdefault("date", today.isoformat())
    paths = generate_paths(
        scene, n, generate_fn=generate_fn, dry=False, shot_types=shot_types, **gen_ctx,
    )
    return {
        "scene": scene,
        "n_images": len(paths),
        "paths": paths,
        "texts": texts[: len(paths)],
        "dry": False,
        "shot_types": list(shot_types or [])[: len(paths)],
        "shot_type": (shot_types[0] if shot_types else ctx.get("shot_type")),
        "slot": slot,
        "imagined": bool(imagined),
        "beat_id": beat_id,
    }


def send_prepared_images(plan, *, io=None, open_chat_first=True, cfg=None, send_caption_first=True):
    """锁内：按 plan 的 texts/paths 依次发配文+图（白名单）。"""
    assert_allowed_contact(cfg)
    paths = plan.get("paths") or []
    texts = plan.get("texts") or []
    if not paths:
        raise RuntimeError("send_prepared_images: empty paths")
    io = io or __import__("mac_wechat_io")
    if open_chat_first and hasattr(io, "open_chat"):
        io.open_chat()
    from bubble_split import bubble_gap_sec
    sent_caps = 0
    for i, path in enumerate(paths):
        if i:
            time.sleep(bubble_gap_sec(texts[i] if i < len(texts) else None))
        line = texts[i] if i < len(texts) else None
        if line and send_caption_first and hasattr(io, "send_message"):
            io.send_message(line)
            sent_caps += 1
            time.sleep(bubble_gap_sec(line))
        send_chat_image(path, io=io, open_chat_first=False, cfg=cfg)
    return {
        "ok": True,
        "paths": [str(x) for x in paths],
        "n_images": len(paths),
        "texts": list(texts),
        "sent_captions": sent_caps,
        "sent_images": len(paths),
        "scene": plan.get("scene"),
    }


def resolve_source(scene_or_path, *, generate_fn=None, dry=False, no_generate=False, **ctx):
    """scene 或已有路径 → Path | None（dry / 仅场景未生成时）。"""
    p = pathlib.Path(scene_or_path).expanduser()
    if p.is_file():
        return p.resolve()
    if no_generate:
        raise FileNotFoundError(f"no-generate but path missing: {scene_or_path}")
    return generate_path(str(scene_or_path), generate_fn=generate_fn, dry=dry, **ctx)


def send_chat_image(path, *, io=None, open_chat_first=True, cfg=None):
    """经 mac_wechat_io（或注入的 io）发送本地图片。"""
    assert_allowed_contact(cfg)
    io = io or __import__("mac_wechat_io")
    if open_chat_first and hasattr(io, "open_chat"):
        io.open_chat()
    if not hasattr(io, "send_image"):
        raise RuntimeError("io has no send_image; extend adapter or upgrade mac_wechat_io")
    io.send_image(str(path))
    return pathlib.Path(path)


def send_generated_image(
    scene_or_path,
    *,
    caption=None,
    dry=False,
    io=None,
    generate_fn=None,
    open_chat_first=True,
    cfg=None,
    send_caption_first=True,
    no_generate=False,
    n_images=None,
    rng=None,
    **gen_ctx,
):
    """生图（若需要）并发送到白名单聊天；可选配文。

    scene_or_path: 场景文案，或已有图片路径（路径模式始终 1 张）。
    n_images: 固定张数（夹到车道上限）；None 时按 chat_image_count_p 采样（仅场景生图）。
    dry: 不生图、不发送；返回 would_* 字段。
    返回 dict: ok, path(s), scene, caption, n_images, texts, dry, sent_*
    """
    assert_allowed_contact(cfg)
    cfg = cfg or load()
    rng = rng or random.Random()
    lane = str(gen_ctx.get("image_lane") or "demand")
    if lane not in ("demand", "proactive"):
        lane = "demand"
    gen_ctx = dict(gen_ctx)
    gen_ctx["image_lane"] = lane
    src = str(scene_or_path)
    as_path = pathlib.Path(src).expanduser()
    scene = None if as_path.is_file() else src
    if as_path.is_file():
        n = 1
    elif n_images is not None:
        n = clamp_image_count(n_images, cfg, lane=lane)
    else:
        n = sample_image_count(cfg, rng, lane=lane)
    texts = multi_interaction_texts(n, caption, rng)
    if dry:
        return {
            "ok": True,
            "dry": True,
            "path": str(as_path.resolve()) if as_path.is_file() else None,
            "paths": [str(as_path.resolve())] if as_path.is_file() else None,
            "scene": scene,
            "caption": (caption or "").strip() or None,
            "n_images": n,
            "texts": texts,
            "would_generate": not as_path.is_file() and not no_generate,
            "would_generate_n": 0 if as_path.is_file() or no_generate else n,
            "would_send_image": True,
            "would_send_caption": any(bool(x) for x in texts),
            "sent_caption": False,
            "sent_image": False,
            "sent_images": 0,
        }

    if as_path.is_file() or no_generate:
        paths = [resolve_source(src, generate_fn=generate_fn, dry=False, no_generate=no_generate, **gen_ctx)]
    else:
        paths = generate_paths(src, n, generate_fn=generate_fn, dry=False, **gen_ctx)

    io = io or __import__("mac_wechat_io")
    if open_chat_first and hasattr(io, "open_chat"):
        io.open_chat()
    sent_caps = 0
    for i, path in enumerate(paths):
        line = texts[i] if i < len(texts) else None
        if line and send_caption_first and hasattr(io, "send_message"):
            io.send_message(line)
            sent_caps += 1
        send_chat_image(path, io=io, open_chat_first=False, cfg=cfg)
    return {
        "ok": True,
        "dry": False,
        "path": str(paths[0]),
        "paths": [str(x) for x in paths],
        "scene": scene,
        "caption": (caption or "").strip() or None,
        "n_images": len(paths),
        "texts": texts[: len(paths)],
        "sent_caption": sent_caps > 0,
        "sent_captions": sent_caps,
        "sent_image": True,
        "sent_images": len(paths),
    }


def deliver_model_output(
    text,
    *,
    scene=None,
    dry=False,
    io=None,
    generate_fn=None,
    open_chat_first=True,
    cfg=None,
    n_images=None,
    rng=None,
    **gen_ctx,
):
    """处理脑回路输出：有意图则桥接发图（+配文），否则仅当普通文本由调用方发送。

    scene 可从 brain.LAST['want_image'] 传入；若 text 内仍带标记会再 parse 一次。
    返回 {"kind": "image"|"text"|"empty", ...}
    """
    intent = parse_image_intent(text) if text else None
    sc = (scene or (intent or {}).get("scene") or "").strip()
    cap = (intent or {}).get("caption") if intent else text
    cap = (cap or "").strip()
    if not sc:
        if not cap:
            return {"kind": "empty", "ok": True}
        return {"kind": "text", "ok": True, "text": cap, "dry": dry}
    r = send_generated_image(
        sc,
        caption=cap or None,
        dry=dry,
        io=io,
        generate_fn=generate_fn,
        open_chat_first=open_chat_first,
        cfg=cfg,
        n_images=n_images,
        rng=rng,
        **gen_ctx,
    )
    r["kind"] = "image"
    return r


if __name__ == "__main__":
    # 最小自检：只 parse
    samples = [
        "给你看一眼\nWANT_IMAGE:图书馆窗边看书",
        '{"want_image":"操场散步","caption":"下课啦"}',
        "就是普通回复",
    ]
    for s in samples:
        print(repr(s), "->", parse_image_intent(s))
