"""小甜生图接口。默认后端 local：Mac mini 本机 Codex CLI（已用用户同一 ChatGPT 账号登录，`codex login status`
= Logged in using ChatGPT；**不发设备码**）调内置 image_gen。参考图来自当前 profile 的 raw/ 全库（451 张都是参考图，
refs/ 只是精选快捷方式）：src/refpick.py 按 index.json（tools/build_ref_index.py，Apple Vision）每次按场景挑 3 张
单人正脸、无泳装/内衣类标签、不同拍摄组的照片，裁成头肩近景作为 -i 输入，提示词要求画的是参考图里的同一个人。
后端选择：默认永远走 Mac mini 本机（local）。MacBook（tools/imagegen_macbook.sh）只是应急路线：IMAGEGEN_BACKEND=macbook
（或 config imagegen.backend=macbook）强制；或本机非审核原因失败（Codex/Magpie 挂了、未登录、超时）重试 1 次仍失败时自动切过去。
审核拦截不切 MacBook（只换参考图/降级）。每次用的后端写进输出目录 run.json（backend/fallback_reason/attempts）。
ssh_host 为空时 MacBook 路线只写 pending 任务。
用法：python3 src/imagegen.py "在图书馆看书" [--dry]
找图：$CODEX_HOME/generated_images 本次新图 → 兜底从 sessions/rollout-*.jsonl 解 base64 → 复制到 imagegen.out_dir/<时间戳>/。
限制：只生成小甜本人的原创场景图，不换到别人脸上；不上传素材到其他网站（参考图只随该次 Codex 请求发给 OpenAI）。
同一套规则也写成 Codex skill：~/.codex/skills/xiaotian-imagegen/SKILL.md（副本 tools/codex-skill/）。"""
import argparse, base64, datetime, json, os, pathlib, re, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load

def codex_home(): return pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex"))

def appearance(cfg=None):
    a = (cfg or load())["appearance"]; root = pathlib.Path(a["assets_dir"]); prof = a["profiles"][a["profile"]]
    desc = (root / prof["description_file"]).read_text() if prof.get("description_file") else prof.get("description", "")
    refs = sorted(p for p in (root / prof.get("ref_dir", "")).glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")) \
        if prof.get("ref_dir") else [root / a["profile"] / r for r in prof.get("ref_images", [])]
    return desc, refs

def body_metrics(cfg=None):
    """身高/体重锁定（appearance.body 或默认 173cm/50kg）。用于提示词比例与场景尺度。"""
    a = (cfg or load()).get("appearance", {})
    b = a.get("body") or {}
    try: h = int(b.get("height_cm", 173))
    except (TypeError, ValueError): h = 173
    try: w = int(b.get("weight_kg", 50))
    except (TypeError, ValueError): w = 50
    return h, w

def _is_night_hour(now=None, hour=None):
    """Asia/Shanghai 墙钟：19:00–05:59 为夜。now 优先用调用方从 life_now 传入的时刻。"""
    if hour is None:
        hour = (now or datetime.datetime.now()).hour
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        hour = datetime.datetime.now().hour
    return hour >= 19 or hour < 6


def _scene_night(scene, plan=None, now=None, imagined=False):
    """Fiction follows the depicted time; ordinary photos keep wall-clock rules."""
    if not imagined:
        return bool(((plan or {}).get("realism") or {}).get("night")) or _is_night_hour(now)
    if re.search(r"白天|白昼|日光|阳光|晴天|清晨|早晨|早上|上午|中午|午后|下午|黄昏|夕阳|日落|晚霞|daytime|daylight", scene or "", re.I):
        return False
    return bool(re.search(r"夜晚|夜里|夜景|夜空|夜色|深夜|午夜|凌晨|晚上|星空|星光|月光|night", scene or "", re.I))


def build_quantity_prompt(n_images=1, *, max_n=None) -> str:
    """独立「数量 prompt」通道：只声明本次出几张、如何一次出齐。

    不得写入场景/妆造/构图/表情等生图内容；由调用方与 build_prompt 的内容段拼接
    （前缀隔离），避免与 shot_briefs / outfit / scene 长 prompt 混写撞车。
    """
    try:
        from chat_media import MAX_CHAT_IMAGES_ABS as _ABS
    except Exception:
        _ABS = 5
    try:
        ceiling = int(max_n) if max_n is not None else int(_ABS)
    except (TypeError, ValueError):
        ceiling = int(_ABS)
    ceiling = max(1, min(int(_ABS), ceiling))
    try:
        n_out = max(1, min(ceiling, int(n_images or 1)))
    except (TypeError, ValueError):
        n_out = 1
    if n_out == 1:
        return (
            "【数量指令】本次生成 1 张。"
            "用内置 image_gen 工具生成一张图片（不要用代码/PIL 画，不要改动任何文件）。"
            "生成一张后停止。"
        )
    return (
        f"【数量指令】本次生成 {n_out} 张。"
        f"用内置 image_gen 工具连续生成 {n_out} 张不同构图/机位的图片"
        f"（每张各调用一次 image_gen；不要用代码/PIL 画，不要改动任何文件）。"
        f"同一人、同一场景活动，但构图/角度/远近要有区分；不要复制同一张。"
        f"共 {n_out} 张全部生成后再停止；禁止只出一张就结束；不要分多轮会话补齐。"
    )


def build_prompt(scene, desc, outfit_text="穿着日常、得体、保守。", realism_text="", n_canonical=0, n_scene_refs=0, scene_mode="match", pose_kind=None, cfg=None, emotion_text="", body_kind=None, night=False, shot_type=None, shot_bits=None, n_images=1, shot_briefs=None, reference_roles=None):
    """以场景+活动为核心任务；n_canonical = 身份(+body/+姿态)张数（第 1 张永远是正视五视图 HARD）；
    n_scene_refs = 紧随其后的真实场景底板张数。scene_mode 默认 match（更自然）；insert 仅显式开启。
    pose_kind: 'up'/'down'/'back'/None —— 仅当实际附了姿态 sheet 时传入，避免无姿态 sheet 仍写头部角度。
    body_kind: 'front'/'sheet'/truthy/None —— 实际附了 canonical/body 时传入；槽位在 face sheet 之后、姿态之前。
    n_images: 本次要出的张数（绝对顶 5；demand/proactive 均∈[1,5]）。先由调用方随机/按需定 N；数量指令走 build_quantity_prompt 独立通道，不与场景/妆造/构图混写。N>1 时一次 Codex 调用出齐，禁止串行多轮 resume 凑数。N 张共享同一套 -i 参考图（≤MAX_IMAGE_REFS），禁止每张各挂 refs。
    shot_briefs: 可选，长度≤n_images 的每张镜头短说明（多图异卡时用）。"""
    h, w = body_metrics(cfg)
    body_lock = (f"体型锁定：身高约 {h} cm、体重约 {w} kg，纤细修长；头身比与四肢长度符合这个身高，"
                 f"与场景中门框/桌椅/走廊透视尺度一致，不要画成偏矮或头大身小。")
    # 核心：场景+活动在前，姿态语言不默认出现
    core = (f"任务核心是场景与活动（不是摆姿势）：{scene}。"
            f"按这个场景里自然会发生的动作来画，不要为了好看而额外加歪头/托腮/回眸等定型姿势。")
    if n_canonical:
        refs = (f"本条消息附带的参考图：前 {n_canonical} 张是同一个女生（小甜）的身份参考。"
                f"第 1 张「正视标准肖像五视图」（canonical）是默认必附的唯一身份标准：脸型、五官比例、眼睛、眉毛、鼻子、嘴唇、黑色长直发、肤色必须和它一致；"
                f"五视图只锁长相与面部轮廓（identity only），不要照搬其中侧脸/3/4 格的转头或倾斜，也不要照搬五视图里的中性/面瘫表情；"
                f"表情必须严格按下方「表情：」情绪单元（STATE emotion/PAD→mood/energy→expression_cue），禁止跟着 face sheet 摆中性脸。"
                f"face sheet / body sheet HARD 只参考脸型与身形比例（identity + body proportion only），绝不是衣服参考、也绝不是表情参考；禁止照搬参考图穿着/构图/棚拍背景；场景必须按穿搭描述换装，禁止灰上衣黑短裤模卡装。如果本会话之前生成过图，忽略之前的画面，只按本条消息的参考图和要求画新的一张。"
                f"画面里必须是她本人，不要生成另一个长相的女生。{body_lock}\n")
        idx = 2
        if n_canonical > 1 and body_kind:
            kind = body_kind if body_kind in ("front", "sheet") else "sheet"
            lab = "全身环视五视图（体型）" if kind == "sheet" else "全身正面体型参考"
            refs += (f"第 {idx} 张是「{lab}」（canonical/body/sheet）：HARD 只锁身份体态——身高比例、头身比、四肢长度与多角度轮廓；"
                     f"HARD 只锁脸型+身形比例，绝不是衣服参考（not a clothing reference）。"
                     f"脸仍严格以第 1 张为准，不要改脸或换人。"
                     f"Outfit MUST change for the scene：必须按下方穿搭描述换装；"
                     f"禁止复刻艺考模卡样衣（not grey tee / not grey top / not black athletic shorts / not model-card outfit / 不要灰上衣黑短裤 / 不要赤脚棚拍模卡装）。"
                     f"不要照搬模卡灰底、棚拍光或样衣。\n")
            idx += 1
        if n_canonical >= idx and pose_kind in ("up", "down", "back"):
            lab = {"up": "仰头", "down": "低头", "back": "背影"}[pose_kind]
            refs += (f"第 {idx} 张是「{lab}五视图」：仅参照其俯仰/朝向与发型轮廓以匹配当前活动所需朝向；"
                     f"不要左右歪头或托腮侧倾，长相与体型仍以第 1 张（及体型参考，若有）为准。\n")
        elif n_canonical > 1 and not body_kind and pose_kind not in ("up", "down", "back"):
            # 兼容旧调用：有第 2 张但未传 body/pose 时不写「头部角度」诱导语
            refs += ("第 2 张同为身份参考（若为姿态五视图，只借发型/轮廓，不照搬转头或倾斜）。\n")
    else:
        refs = ("附带的几张参考图是同一个女生（小甜）。画面里必须是她本人：脸型、五官、眼睛、黑色长直发、肤色、气质都和参考图保持一致，"
                f"不要生成另一个长相的女生，也不要照搬参考图的构图和背景。{body_lock}\n")
    scene_block = ""
    if n_scene_refs > 0:
        a = n_canonical + 1
        b = n_canonical + n_scene_refs
        label = f"Image {a}" + (f"–{b}" if n_scene_refs > 1 else "")
        if scene_mode == "insert":
            scene_block = (
                f"{label} 是真实场景照片（场景底板）。以它为底图编辑：把小甜放进该照片的空间里，"
                f"尽量保留原图的相机机位、透视、焦距感、背景陈设与光影方向/色温；只新增/替换人物与她的穿着动作，不要另造一座建筑或换地点。"
                f"人物身高约 {h} cm，比例、站位、遮挡关系要与场景尺度一致；若原图有路人则避开或让路人保持远处模糊。\n")
        else:
            scene_block = (
                f"{label} 是真实场景参考照片。地点、陈设、空间结构和光影（光源方向、色温、对比、环境反射）要贴近这些照片；"
                f"允许为了构图轻微重绘，但不要换成另一个地点。把小甜自然放进该场景，动作与上述场景活动匹配；"
                f"身高约 {h} cm，与门框/家具透视比例正确。\n")
        scene_block += ("身份仍严格以第 1 张 canonical 为准；场景图里若有人脸不要模仿。"
                        "photorealistic candid phone photo，匹配场景图的噪点与压缩感。\n")
    emo_block = ("\n" + emotion_text) if emotion_text else ""
    night_lock = (
        "光照硬约束：室内夜灯/台灯暖光，窗外是黑夜；禁止白天日光、明亮天空或午后自然光。"
        "若场景参考图为白天，只借空间与陈设，光照必须改成夜晚。"
    ) if night else ""
    shot_block = ""
    try:
        import shot_modes as _sm
        shot_block = _sm.prompt_shot_block(shot_type, scene, shot_bits)
        # hand/POV/legs：弱化「必须完整身份脸」话术——禁止把 ref#1 写成正视肖像五视图
        if _sm.skips_hard_face_body(shot_type) and n_canonical:
            s = _sm.normalize_shot(shot_type)
            if s == _sm.SHOT_LEGS:
                refs = (f"本条消息附带的参考图：前 {n_canonical} 张用于腿部比例/肤色锁定（主观第一视角看自己的腿）；"
                        f"legs_focus 时第 1 张优先为腿部五视图模卡（女生腿型/比例/肤色，独占身份槽）；"
                        f"若无腿模则第 1 张为弱身份/体型参考。"
                        f"脸可不入镜或极少；禁止第三人称美腿写真；禁止把第 1 张当正脸身份模卡硬锁。"
                        f"注意真实脚部生理结构与细节。"
                        f"{body_lock}\n")
            else:
                refs = (f"本条消息附带的参考图：前 {n_canonical} 张用于手部肤色/身份气质或弱身份锁定；"
                        f"hand_closeup 时第 1 张优先为手部五视图模卡（只锁女生手型/肤色/甲型；主观第一视角看自己的手）；"
                        f"pov_first_person 时场景参考优先，人物仅可露手臂/手边缘。"
                        f"hand_closeup 禁止第三人称手部写真；禁止把第 1 张当成完整正脸五视图硬锁。"
                        f"注意真实手部生理结构与细节。"
                        f"{body_lock}\n")
    except Exception:
        shot_block = ""
    try:
        import shot_modes as _sm2
        if scene_block and _sm2.skips_hard_face_body(shot_type):
            scene_block = scene_block.replace(
                "身份仍严格以第 1 张 canonical 为准；",
                "身份气质弱参考第 1 张模卡即可，勿当正脸硬锁；",
            )
    except Exception:
        pass
    if reference_roles is not None:
        # Mixed shots share files, not the first shot's identity/composition rules.
        labels = {
            "face": "脸部五视图：唯一脸部身份标准，只锁五官、脸型与发型",
            "body": "身体五视图：只锁身高、头身比、四肢比例，不借用其中的脸或服装",
            "hands": "手部五视图：只锁手型、肤色和甲型",
            "legs": "腿部五视图：只锁腿型、比例和肤色",
        }
        refs = "【本批参考图分工】\n" + "\n".join(
            f"第 {i + 1} 张：{labels[role]}。" for i, role in enumerate(reference_roles))
        refs += ("\n每张输出都按其分张镜头要求构图，优先于场景里泛称的自拍/上半身或通用摄影修饰；"
                 "出现脸的照片只能使用脸部参考中的同一个女生，"
                 "不能借用身体模卡、场景路人或本会话旧图片的脸。"
                 "手部、腿部及第一视角镜头不因附了脸图就强行露脸。"
                 "实际调用生图工具时，露脸镜头必须传入脸部参考，全身镜头还必须传入身体参考（本批已附时）；"
                 "不能只在文字里说保持长相却漏传参考图。"
                 "参考图不是服装、表情或背景模板；按当前场景换装。表情按本轮情绪，不照搬模卡中性脸。"
                 "忽略先前生成的画面。" + body_lock + "\n")
        shot_block = ""  # All composition instructions are scoped in shot_briefs below.
        scene_block = scene_block.replace("身份仍严格以第 1 张 canonical 为准；",
                                           "场景参考只提供环境，不提供人物身份；")
        scene_block = scene_block.replace("身份气质弱参考第 1 张模卡即可，勿当正脸硬锁；",
                                           "场景参考只提供环境，不提供人物身份；")
    try:
        from chat_media import MAX_CHAT_IMAGES_ABS as _MAX_N
    except Exception:
        _MAX_N = 5
    try:
        n_out = max(1, min(int(_MAX_N), int(n_images or 1)))
    except (TypeError, ValueError):
        n_out = 1
    briefs = [str(x).strip() for x in (shot_briefs or []) if str(x).strip()]
    brief_block = ""
    if n_out > 1 and briefs:
        lines = []
        for i in range(n_out):
            b = briefs[i] if i < len(briefs) else briefs[-1]
            lines.append(f"第 {i+1} 张：{b}")
        brief_block = "分张要求：\n" + "\n".join(lines) + "\n"
    # 多图时把「画新的一张」改成 N 张（refs 文案，仍属内容侧）
    if n_out > 1:
        refs = refs.replace("画新的一张。", f"画新的 {n_out} 张。")
        refs = refs.replace("只按本条消息的参考图和要求画新的一张。",
                            f"只按本条消息的参考图和要求画新的 {n_out} 张。")
    # 内容 prompt：场景/妆造/构图/refs —— 不含数量指令
    content = (
        f"{core}\n{shot_block}{brief_block}{refs}{scene_block}"
        f"外貌补充：\n{desc}\n{outfit_text}"
        f"换装硬约束：身材图/脸图只参考身形和脸型，不是衣服；必须按场景穿搭换装，禁止灰上衣黑短裤艺考模卡装（not grey tee / not model-card outfit）。"
        f"{emo_block}"
        f"{'不要从零虚构场景，优先沿用场景参考图的空间。' if n_scene_refs else ''}"
        f"手机直出照片质感，竖构图。{night_lock}{realism_text} $imagegen"
    )
    # 数量通道独立前缀，与内容语义隔离（不嵌进 scene/outfit/shot brief）
    qty = build_quantity_prompt(n_out, max_n=_MAX_N)
    return f"{qty}\n\n{content}"

def featureprint_dist_to_canonical(img, cfg=None):
    """越小越好：生成图脸部区域 vs canonical front/sheet 的 FeaturePrint L2。失败返回 None。"""
    cfg = cfg or load()
    try:
        import numpy as np
        from PIL import Image
        import sys as _sys
        tools = pathlib.Path(__file__).resolve().parents[1] / "tools"
        if str(tools) not in _sys.path: _sys.path.insert(0, str(tools))
        import gen_canonical as gc
    except Exception:
        return None
    root = pathlib.Path(cfg["appearance"]["assets_dir"]) / cfg["appearance"]["profile"]
    ref = root / "canonical" / "front.png"
    if not ref.exists(): ref = root / "canonical" / "sheet.png"
    if not ref.exists(): return None
    try:
        im = Image.open(img).convert("RGB"); W, H = im.size
        crop = im.crop((int(W * 0.2), int(H * 0.08), int(W * 0.8), int(H * 0.45)))
        tmp = pathlib.Path(img).with_suffix(".face_crop.jpg"); crop.save(tmp, quality=90)
        return float(gc.fp_dist(gc.featureprint(tmp), gc.featureprint(ref)))
    except Exception as e:
        print(f"[imagegen] FeaturePrint 评分失败：{e}", file=sys.stderr)
        return None



def _pose_kind_from_refs(can, scene=None):
    """从已选 canonical 路径推断姿态 kind；若无姿态 sheet 则 None（不写姿态诱导语）。"""
    for c in can or []:
        name = pathlib.Path(c).parent.name
        if name in ("up", "down", "back"): return name
    return None

def _body_kind_from_refs(can):
    """从已选 canonical 路径推断 body 参考 kind：'front' / 'sheet' / None。"""
    for c in can or []:
        p = pathlib.Path(c)
        if p.parent.name == "body":
            return "front" if p.name.startswith("front") else "sheet"
    return None

def pick_refs(scene, cfg, exclude=(), shot_type=None):
    """(canonical, others)：默认 face+body；hand/POV 见 refpick.pick_identity(shot_type=...)。"""
    ig = cfg.get("imagegen", {})
    try:
        from refpick import pick_identity
        return pick_identity(scene, ig.get("other_refs", 3), cfg, exclude=exclude, shot_type=shot_type)
    except FileNotFoundError:
        return [], appearance(cfg)[1][: ig.get("max_refs", 3)]

def pick_scene_refs(scene, plan, cfg, k=None, *, slot=None, date=None):
    """从本地 scene_refs 库按场景向量取 top-k 真实场景图。返回 [Path,...]。

    日程路径：pack resolve 用 slot + 中文 scene；outfit id 仅作泛化 pick_key，
    不得抢 common pack 解析。无 pack 时回退 generic，不留空。
    """
    ig = cfg.get("imagegen", {})
    if os.environ.get("XIAOTIAN_TEST") == "1": return []
    if os.environ.get("SCENE_REFS", ig.get("scene_refs", "1")) in ("0", "false", "no"): return []
    k = ig.get("scene_refs_k", 2) if k is None else k
    if k <= 0: return []
    try:
        import scene_refs
        outfit_key = None
        if isinstance(plan, dict):
            # outfit id 仅用于泛化向量/scene_id 检索（people_free 严筛时中文裸名易打空）
            top0 = (plan.get("top") or [None])[0]
            if isinstance(top0, dict) and top0.get("id"):
                outfit_key = top0["id"]
            else:
                name = plan.get("scene")
                if isinstance(name, str) and name:
                    try:
                        import outfit as _outfit
                        d = _outfit.BY_NAME.get(name) or _outfit.BY_ID.get(name)
                        if d:
                            outfit_key = d["id"]
                    except Exception:
                        pass
                    if outfit_key is None and getattr(scene_refs, "is_outfit_scene_id", lambda _x: False)(name):
                        outfit_key = name
        if isinstance(plan, dict):
            if date is None:
                date = plan.get("date") or plan.get("day_date")
            if slot is None:
                slot = plan.get("slot") if isinstance(plan.get("slot"), dict) else None
            if not date:
                try:
                    import life_calendar as _lc
                    date = _lc.today_local().isoformat()
                except Exception:
                    date = None
        if (slot or {}).get("scene_id"):
            hit = scene_refs.pick(slot["scene_id"], k=k, cfg=cfg,
                                  people_free_only=bool(ig.get("scene_refs_people_free", True)))
            if hit:
                return [p for p, _ in hit]
        # 中文 scene 留给 pack resolve；outfit id 只进 pick_key
        pack_scene = scene
        if getattr(scene_refs, "is_outfit_scene_id", lambda _x: False)(scene):
            pack_scene = None
        pick_key = outfit_key or scene
        picker = getattr(scene_refs, "pick_with_common_and_probes", None)
        if picker:
            hit = picker(
                pack_scene or scene, k=k, cfg=cfg,
                people_free_only=bool(ig.get("scene_refs_people_free", True)),
                date=str(date) if date else None,
                slot=slot,
                pick_key=pick_key,
                pack_scene=pack_scene,
            )
        else:
            hit = scene_refs.pick(pick_key, k=k, cfg=cfg, people_free_only=bool(ig.get("scene_refs_people_free", True)))
        return [p for p, _ in hit]
    except Exception as e:
        print(f"[imagegen] scene_refs 不可用：{e}", file=sys.stderr); return []

# Codex image_gen 硬上限：referenced_image_paths ≤ 5（超出会 tool error 再重试，白白多几十秒）
MAX_IMAGE_REFS = 5

def _is_body_sheet(p):
    p = pathlib.Path(p)
    return p.parent.name == "body" and p.name == "sheet.png"

def _is_hands_sheet(p):
    p = pathlib.Path(p)
    return p.parent.name == "hands" and p.name == "sheet.png"

def _is_legs_sheet(p):
    p = pathlib.Path(p)
    return p.parent.name == "legs" and p.name == "sheet.png"

def cap_refs(can, srefs, oth, max_refs=None, shot_type=None, required_refs=None):
    """硬裁参考图 ≤ max_refs。
    默认 HARD：can[0] face sheet + canonical/body/sheet.png（若在 can 里）必留；
    hand_closeup：HARD 为 hands/sheet；不强制 face+body；其余给 scene_refs。
    pov_first_person：优先 scene_refs；can 中 face/hands 软保留但不强制 body。
    其余优先 scene_refs（目标补满 3 张场景），再 can 里非 HARD 项（姿态等），最后 other。
    返回 (can_out, srefs_out, oth_out)。"""
    max_refs = int(max_refs if max_refs is not None else MAX_IMAGE_REFS)
    if max_refs < 1: raise ValueError("max_refs must be >= 1")
    can = [pathlib.Path(p) if not isinstance(p, pathlib.Path) else p for p in (can or [])]
    srefs = [pathlib.Path(p) if not isinstance(p, pathlib.Path) else p for p in (srefs or [])]
    oth = [pathlib.Path(p) if not isinstance(p, pathlib.Path) else p for p in (oth or [])]
    try:
        import shot_modes as _sm
        shot = _sm.normalize_shot(shot_type)
    except Exception:
        shot = shot_type
    if required_refs and not can:
        raise ValueError("Required identity reference missing from selected references")
    if not can:
        out_srefs, out_oth = [], []
        slots = max_refs
        for p in srefs:
            if slots <= 0: break
            out_srefs.append(p); slots -= 1
        for p in oth:
            if slots <= 0: break
            out_oth.append(p); slots -= 1
        return [], out_srefs, out_oth
    if required_refs is not None:
        hard = list(dict.fromkeys(pathlib.Path(p) for p in required_refs))
        if len(hard) > max_refs:
            raise ValueError("Required identity references exceed image reference limit")
        if any(p not in can for p in hard):
            raise ValueError("Required identity reference missing from selected references")
    elif shot == "hand_closeup":
        hands = next((p for p in can if _is_hands_sheet(p)), None)
        hard = [hands] if hands is not None else ([can[0]] if can else [])
    elif shot == "pov_first_person":
        # POV：不 HARD body；最多保留 1 张 face 或 hands，其余槽给场景
        hard = []
        face = can[0] if can and not _is_body_sheet(can[0]) and not _is_hands_sheet(can[0]) and not _is_legs_sheet(can[0]) else None
        hands = next((p for p in can if _is_hands_sheet(p)), None)
        if face is not None:
            hard.append(face)
        elif hands is not None:
            hard.append(hands)
        # 若已有 face，hands 不进 HARD（留给 soft 填充）
    elif shot == "mirror_selfie":
        # 对镜：face HARD；不强制 body；hands 软
        hard = []
        face = next((p for p in can if not _is_body_sheet(p) and not _is_hands_sheet(p) and not _is_legs_sheet(p)), None)
        if face is not None:
            hard.append(face)
        elif can:
            hard.append(can[0])
    elif shot == "legs_focus":
        legs = next((p for p in can if _is_legs_sheet(p)), None)
        if legs is not None:
            hard = [legs]
        else:
            # 无腿模：face + body（若有）作软身份/体型
            hard = [can[0]] if can else []
            body = next((p for p in can if _is_body_sheet(p) and p not in hard), None)
            if body is not None and len(hard) < 2:
                hard.append(body)
    elif shot == "portrait":
        hard = []
        face = next((p for p in can if not _is_body_sheet(p) and not _is_hands_sheet(p) and not _is_legs_sheet(p)), None)
        hard = [face] if face is not None else ([can[0]] if can else [])
    else:
        hard = [can[0]]  # face sheet
        body = next((p for p in can if _is_body_sheet(p) and p != can[0]), None)
        if body is not None:
            hard.append(body)
    # HARD 优先占满（通常 2）；若 max_refs 极小则仍保 face
    out_can = []
    for p in hard:
        if len(out_can) >= max_refs: break
        out_can.append(p)
    slots = max_refs - len(out_can)
    out_srefs = []
    for p in srefs:
        if slots <= 0: break
        out_srefs.append(p); slots -= 1
    hard_set = {str(p.resolve()) if p.exists() else str(p) for p in out_can}
    def _key(p):
        try: return str(p.resolve())
        except OSError: return str(p)
    for p in can:
        if slots <= 0: break
        if _key(p) in hard_set or str(p) in {str(x) for x in out_can}: continue
        out_can.append(p); slots -= 1
    out_oth = []
    for p in oth:
        if slots <= 0: break
        out_oth.append(p); slots -= 1
    return out_can, out_srefs, out_oth

def build_cmd(scene, cfg=None, out_dir=None, exclude=(), session_id=None, refs=None, scene_refs=None, shot_type=None, required_refs=None):
    """codex 命令。参考图顺序：canonical(+姿态) → scene_refs → 其余角度参考；硬上限 MAX_IMAGE_REFS。"""
    cfg = cfg or load(); ig = cfg.get("imagegen", {})
    can, oth = refs if refs is not None else pick_refs(scene, cfg, exclude, shot_type=shot_type)
    srefs = list(scene_refs or [])
    can, srefs, oth = cap_refs(can, srefs, oth, ig.get("max_image_refs", MAX_IMAGE_REFS), shot_type=shot_type,
                             required_refs=required_refs)
    import codex_session
    extra = []
    effort = os.environ.get("IMAGEGEN_REASONING_EFFORT", ig.get("reasoning_effort") or "").strip()
    if effort:
        extra += ["-c", f"model_reasoning_effort={effort}"]
    cmd, _ = codex_session.build(ig.get("cmd", "codex"), list(can) + srefs + list(oth), out_dir or ig["out_dir"], session_id, extra_args=extra)
    return cmd  # 提示词走 stdin：-i 是多值参数，会吞掉放在后面的位置参数（2026-10-23 实测）


def batch_identity_refs(cfg, shot_types):
    """Union of identity needs across mixed shots, ordered before scene references."""
    import refpick
    import shot_modes as sm
    shots = {sm.normalize_shot(s) or sm.SHOT_THIRD for s in shot_types}
    full = bool(shots & {sm.SHOT_FULL_BODY, sm.SHOT_THIRD})
    face = full or bool(shots & {sm.SHOT_PORTRAIT, sm.SHOT_MIRROR})
    selected = []
    if face:
        selected += [(p, "face") for p in refpick.canonical_refs(cfg)[:1]]
        if not selected:
            raise RuntimeError("Missing canonical face reference for visible-face image batch")
    if full:
        bodies = refpick.body_orbit_refs(cfg)
        if refpick.body_orbit_approved(cfg) and not bodies:
            raise RuntimeError("Approved canonical body reference is missing")
        selected += [(p, "body") for p in bodies]
    if sm.SHOT_HAND in shots:
        selected += [(p, "hands") for p in refpick.hands_refs(cfg)]
    if sm.SHOT_LEGS in shots:
        legs = refpick.legs_refs(cfg)
        selected += [(p, "legs") for p in legs]
        if not legs and not full:
            selected += [(p, "body") for p in refpick.body_orbit_refs(cfg)]
    return [p for p, _ in selected], [role for _, role in selected]

def _new_images(since):
    d = codex_home() / "generated_images"
    return sorted((p for p in d.rglob("*.png") if p.stat().st_mtime >= since), key=lambda p: p.stat().st_mtime) if d.exists() else []

def rollout_sizes():
    """各 rollout 文件当前大小；复用会话时只解析本次追加的部分，避免把当天之前的图又捞出来。"""
    d = codex_home() / "sessions"
    return {str(f): f.stat().st_size for f in d.rglob("rollout-*.jsonl")} if d.exists() else {}

def _from_rollouts(since, out_dir, offsets=None):
    out = []
    for f in (codex_home() / "sessions").rglob("rollout-*.jsonl"):
        if f.stat().st_mtime < since: continue
        with open(f, "rb") as fh:
            fh.seek((offsets or {}).get(str(f), 0)); text = fh.read().decode(errors="ignore")
        for line in text.splitlines():
            if '"image_generation_call"' not in line: continue
            try: obj = json.loads(line)
            except Exception: continue
            stack = [obj]
            while stack:
                o = stack.pop()
                if isinstance(o, dict):
                    if o.get("type") == "image_generation_call" and o.get("result"):
                        p = out_dir / f"rollout_{o.get('id', len(out))}.png"; p.write_bytes(base64.b64decode(o["result"])); out.append(p)
                    stack += list(o.values())
                elif isinstance(o, list): stack += o
    return out

MB_SCRIPT = "~/xiaotian-assets/bin/imagegen_macbook.sh"

def macbook_cmd(scene, cfg=None, outfit_text=None, realism_text=None, emotion_text=None):
    """在 MacBook 上要执行的命令（字符串）。ssh_host 非空时包成 ssh。outfit_text 经 OUTFIT_TEXT 环境变量传给脚本。"""
    import shlex
    ig = (cfg or load()).get("imagegen", {}); inner = f"{ig.get('macbook_script', MB_SCRIPT)} {shlex.quote(scene)}"
    if outfit_text: inner = f"OUTFIT_TEXT={shlex.quote(outfit_text)} {inner}"
    if realism_text is not None: inner = f"REALISM_TEXT={shlex.quote(realism_text)} {inner}"   # 空串 = 本张不加
    if emotion_text: inner = f"EMOTION_TEXT={shlex.quote(emotion_text)} {inner}"
    return (["ssh", "-o", "BatchMode=yes", ig["ssh_host"], inner] if ig.get("ssh_host") else None), inner

def generate_macbook(scene, dry=False, cfg=None, outfit_scene=None, **ctx):
    ctx = dict(ctx); _meta = ctx.pop("_meta", None); ctx_m = {"_meta": _meta}
    cfg = cfg or load(); ig = cfg.get("imagegen", {}); plan = outfit_plan(scene, outfit_scene, cfg, **ctx)
    if ctx.get("imagined"): plan["imagined"] = True
    ctx = ctx_m
    ssh, inner = macbook_cmd(scene, cfg, plan["text"], plan.get("realism_text"), plan.get("emotion_text"))
    if dry: return (ssh or ["# 在 MacBook 上运行:", inner]), []
    if not ssh:
        job = write_pending(scene, inner, plan, cfg, ctx.get("_meta") or {}, "imagegen.ssh_host 为空")
        raise RuntimeError(f"Mac mini 无法直接调 MacBook（imagegen.ssh_host 为空）。任务已写到 {job}" + _relay_note(job, inner, ig))
    try:
        r = subprocess.run(ssh, capture_output=True, text=True, timeout=ig.get("timeout", 600))
        remote = [l.strip() for l in r.stdout.splitlines() if l.strip().endswith(".png")]
        if r.returncode or not remote: raise RuntimeError(f"MacBook 生图失败（exit {r.returncode}）：{r.stderr[-500:]}")
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        import relay
        if not relay.configured(): raise
        job = write_pending(scene, inner, plan, cfg, ctx.get("_meta") or {}, f"ssh 失败：{e}")
        raise RuntimeError(f"ssh 到 MacBook 失败（{str(e)[:200]}）。任务已写到 {job}" + _relay_note(job, inner, ig)) from e
    out_dir = pathlib.Path(ig["out_dir"]) / datetime.datetime.now().strftime("%Y%m%d-%H%M%S"); out_dir.mkdir(parents=True, exist_ok=True)
    for f in remote: subprocess.run(["scp", "-o", "BatchMode=yes", f"{ig['ssh_host']}:{f}", str(out_dir)], check=True, timeout=120)
    write_meta(out_dir, dict(ctx.get("_meta") or {}, backend="macbook"))
    return ssh, sorted(out_dir.glob("*.png"))

def _pending_refs(scene, cfg):
    try:
        can, oth = pick_refs(scene, cfg)
    except Exception:
        can, oth = [], []
    return [str(pathlib.Path(r).resolve()) for r in can], [str(pathlib.Path(r).resolve()) for r in oth]

def write_pending(scene, inner, plan, cfg, meta, error):
    """写 pending/<job_id>.json（含 prompt、穿搭、写实修饰、参考图绝对路径、期望结果路径）。返回路径。"""
    ig = cfg.get("imagegen", {}); now = datetime.datetime.now(); job_id = now.strftime("%Y%m%d-%H%M%S-%f")[:-3]
    pend = pathlib.Path(ig["out_dir"]).resolve() / "pending"; pend.mkdir(parents=True, exist_ok=True)
    try: desc = appearance(cfg)[0]
    except Exception: desc = ""
    job = pend / f"{job_id}.json"; can, oth = _pending_refs(scene, cfg)
    job.write_text(json.dumps({"scene": scene, "macbook_cmd": inner, **meta, "backend": "macbook", "job_id": job_id, "status": "pending",
        "created_at": now.isoformat(timespec="seconds"), "prompt": build_prompt(scene, desc, plan["text"], plan.get("realism_text", ""), len(can), pose_kind=_pose_kind_from_refs(can, scene), cfg=cfg, emotion_text=plan.get("emotion_text", ""), body_kind=_body_kind_from_refs(can), night=_scene_night(scene, plan, imagined=plan.get("imagined", False))),
        "outfit_text": plan["text"], "realism_text": plan.get("realism_text", ""), "emotion_text": plan.get("emotion_text", ""), "reference_images": can + oth, "canonical_refs": can, "other_refs": oth, "extra_sheet": next((c for c in can if pathlib.Path(c).parent.name in ("up", "down", "back")), None),
        "body_ref": next((c for c in can if pathlib.Path(c).parent.name == "body"), None),
        "session_policy": "MacBook 端同一天复用一个 Codex 会话（codex exec resume），跨天新开；见 tools/imagegen_macbook.sh",
        "result_path": str(pathlib.Path(ig["out_dir"]).resolve() / f"relay-{job_id}" / "result.png"), "error": (f"本机失败：{meta['fallback_reason']}；" if meta.get("fallback_reason") else "") + str(error)[:1000]},
        ensure_ascii=False, indent=1))
    return job

def _relay_note(job, inner, ig):
    import relay
    if relay.configured():
        ok = relay.relay_job(job)
        return "；已通过 relay webhook 发给开发助手转 MacBook（status=relayed_pending）" if ok else f"；relay webhook 发送失败，请手动在 MacBook 上跑：{inner}"
    return f"；在 MacBook 上跑：{inner}，再把图拷回 {ig['out_dir']}"

def relay_done(job_id, image_path, cfg=None):
    """开发助手从 MacBook 拿回图后调用：把图放到 result_path，pending json 标 done，写 run.json。"""
    cfg = cfg or load(); job = pathlib.Path(cfg["imagegen"]["out_dir"]).resolve() / "pending" / f"{job_id}.json"
    if not job.exists(): raise FileNotFoundError(f"找不到 pending 任务 {job}")
    d = json.loads(job.read_text()); src = pathlib.Path(image_path).expanduser()
    if not src.is_file(): raise FileNotFoundError(f"图片不存在：{src}")
    dst = pathlib.Path(d["result_path"]); dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
    d.update(status="done", done_at=datetime.datetime.now().isoformat(timespec="seconds"), result=str(dst))
    job.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    write_meta(dst.parent, {k: d.get(k) for k in ("backend", "job_id", "fallback_reason", "forced")} | {"status": "ok", "relayed": True, "images": [dst.name]})
    return dst

BLOCK_WORDS = ("moderation", "safety", "content policy", "content_policy", "policy violation", "blocked", "审核", "违反", "不能生成")

def blocked(log):
    l = log.lower(); return any(w in l for w in BLOCK_WORDS)

def _ref_sha(cmd): return {pathlib.Path(c).stem.split("_")[0] for c in cmd[cmd.index("-i") + 1::2]} if "-i" in cmd else set()

def outfit_plan(scene, outfit_scene=None, cfg=None, **ctx):
    """场景驱动的浮动穿搭（src/outfit.py）。outfit_scene 显式指定场景；推断失败退回保守描述。"""
    # generate(..., chat=True) puts a bool in ctx; outfit.plan expects chat as message list.
    chat_flag = bool(ctx.get("chat")) if not isinstance(ctx.get("chat"), (list, tuple)) else True
    octx = {k: v for k, v in ctx.items() if k != "chat"}
    if isinstance(ctx.get("chat"), (list, tuple)):
        octx["chat"] = ctx["chat"]
    try:
        import outfit; plan = outfit.plan(scene, outfit_scene, **octx)
    except ValueError: raise
    except Exception as e:
        print(f"[imagegen] 穿搭推断失败，用保守描述：{e}", file=sys.stderr)
        plan = {"weights": {}, "vector": {}, "level": 0, "text": "穿着日常、得体、保守。", "emotion": {}, "emotion_text": ""}
    if ctx.get("imagined"): plan["imagined"] = True
    return add_realism(plan, scene, ctx.get("now"), cfg=cfg, chat=chat_flag, imagined=bool(ctx.get("imagined")))

def add_realism(plan, scene, now=None, seed=None, cfg=None, chat=False, photo_mode=None, imagined=False):
    """写实修饰（src/realism.py）：按场景加权随机 0~3 个，记进 plan["realism"]（会写入 outfit.json）。REALISM=0 关闭。
    自拍场景强制注入自拍相关 realism；聊天发图(chat)或文案含「给你拍」等时按
    imagegen.realism_casual_photo_p / REALISM_CASUAL_PHOTO_P（默认 0.4）偏置手机随手拍。"""
    if os.environ.get("REALISM", "1") == "0": plan["realism"], plan["realism_text"] = None, ""; return plan
    try:
        import realism
        seed = seed if seed is not None else (int(os.environ["REALISM_SEED"]) if os.environ.get("REALISM_SEED") else None)
        ig = (cfg or {}).get("imagegen") or {}
        casual_p = os.environ.get("REALISM_CASUAL_PHOTO_P")
        if casual_p is None:
            casual_p = ig.get("realism_casual_photo_p", realism.DEFAULT_CASUAL_PHOTO_P)
        casual_p = float(casual_p)
        force = os.environ.get("REALISM_FORCE_SELFIE")
        force_selfie = None if force is None else force not in ("0", "false", "no")
        mode = photo_mode
        if mode is None:
            mode = realism.detect_photo_mode(plan.get("scene"), scene)
        # 聊天路径非自拍 = 偶发「给你拍照」：走 casual 概率注入（非每次强制）
        if mode is None and chat:
            mode = "casual"
        hour = (21 if _scene_night(scene, imagined=True) else 12) if imagined else (now or datetime.datetime.now()).hour
        sel = realism.pick(
            plan.get("scene"), scene, vec=plan.get("vector") or None,
            hour=hour, seed=seed,
            photo_mode=mode, casual_p=casual_p, force_selfie=force_selfie,
        )
        if imagined:
            # Time weights are soft in realism.pick; never add a contradicting
            # night/day modifier to an explicitly fictional scene.
            keep = [i for i in sel["ids"] if realism.BY_ID[i].get("time", "any") in ("any", "night" if hour == 21 else "day")]
            sel = dict(sel, ids=keep, n=len(keep), **{key: [realism.BY_ID[i][field] for i in keep]
                for key, field in (("en", "en"), ("zh", "zh"), ("groups", "group"))})
        plan["realism"], plan["realism_text"] = sel, realism.text(sel)
    except Exception as e:
        print(f"[imagegen] 写实修饰失败，跳过：{e}", file=sys.stderr); plan["realism"], plan["realism_text"] = None, ""
    return plan

MAX_ATTEMPTS = 5
LOCAL_TRIES = 2   # 本机非审核失败：重试 1 次后才切 MacBook 应急

class ModerationBlocked(RuntimeError): """审核拦截（换参考图/降级都用完）。不切 MacBook。"""
class LocalBackendError(RuntimeError): """本机 Codex/Magpie 不可用、未登录、超时、无产出等非审核失败。"""

def magpie_ok(cfg=None, timeout=2.0):
    """Magpie :3425 是否在听（委托 imagegen_greenlight）。"""
    import imagegen_greenlight as gl
    ig = (cfg or load()).get("imagegen") or {}
    return gl.magpie_ok(ig.get("magpie_url") or gl.DEFAULT_MAGPIE_URL, timeout=timeout)

def ensure_magpie(cfg=None):
    """生图前 greenlight：Magpie 挂了就 open 拉起；红灯抛 LocalBackendError。"""
    import imagegen_greenlight as gl
    gl.greenlight(cfg or load(), auto_start_magpie=True, raise_on_red=True)
    return True


def write_meta(out_dir, meta):
    try: (pathlib.Path(out_dir) / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    except OSError: pass

def resolve_backend(cfg=None):
    """返回 (backend, forced)。IMAGEGEN_BACKEND 环境变量优先，其次 config；默认 local。"""
    b = (os.environ.get("IMAGEGEN_BACKEND") or (cfg or load()).get("imagegen", {}).get("backend") or "local").strip().lower()
    return ("macbook", True) if b == "macbook" else ("local", False)

def generate(scene, dry=False, cfg=None, outfit_scene=None, chat=False, skip_face_fallback=None, **ctx):
    """默认 Mac mini 本机；本机非审核失败重试 1 次后自动切 MacBook 应急；IMAGEGEN_BACKEND=macbook 强制 MacBook。

    chat=True（聊天发图）：只跑 scene_refs 第一张，跳过 face-fallback 第二次 Codex。
    skip_face_fallback=True：同上。研究/离线 A/B 保持默认（config/env SCENE_REFS_FACE_FALLBACK）。
    """
    cfg = cfg or load(); backend, forced = resolve_backend(cfg)
    # 聊天路径永远跳过双跑；显式 skip_face_fallback 也可；否则交给 generate_local 读 config/env
    skip_fb = True if chat else (bool(skip_face_fallback) if skip_face_fallback is not None else None)
    def _meta(base):
        m = dict(base)
        if skip_fb is True: m["skip_face_fallback"] = True
        if chat: m["chat"] = True
        return m
    if forced: return generate_macbook(scene, dry, cfg, outfit_scene, _meta=_meta({"backend": "macbook", "forced": True}), **ctx)
    errs = []
    if not dry:
        try:
            ensure_magpie(cfg)  # greenlight: Magpie↑ / codex / out_dir
        except LocalBackendError as e:
            # 预检红灯：记一次后直接走应急（不必空等两轮 TimeoutExpired）
            errs.append(f"{type(e).__name__}: {e}"[:500])
            print(f"[imagegen] greenlight 失败：{errs[-1]}", file=sys.stderr)
            print("[imagegen] 本机后端不可用，切 MacBook 应急路线", file=sys.stderr)
            return generate_macbook(scene, dry, cfg, outfit_scene, _meta=_meta({"backend": "macbook", "forced": False, "fallback_reason": errs[-1], "local_errors": errs}), **ctx)
    for i in range(1, LOCAL_TRIES + 1):
        try: return generate_local(scene, dry, cfg, outfit_scene, _meta=_meta({"backend": "local", "local_try": i, "local_errors": errs[:]}), **ctx)
        except ModerationBlocked: raise
        except (LocalBackendError, subprocess.TimeoutExpired, OSError) as e:
            errs.append(f"{type(e).__name__}: {e}"[:500])
            print(f"[imagegen] 本机后端失败（第 {i} 次）：{errs[-1]}", file=sys.stderr)
            try:
                import codex_session as _cs; _cs.forget(cfg)
                print("[imagegen] 已清除当天 sticky Codex 会话，下次新开", file=sys.stderr)
            except Exception as fe:
                print(f"[imagegen] forget 会话失败：{fe}", file=sys.stderr)
    print("[imagegen] 本机后端两次失败，切 MacBook 应急路线", file=sys.stderr)
    return generate_macbook(scene, dry, cfg, outfit_scene, _meta=_meta({"backend": "macbook", "forced": False, "fallback_reason": errs[-1], "local_errors": errs}), **ctx)

def generate_local(scene, dry=False, cfg=None, outfit_scene=None, _exclude=(), _attempt=1, _plan=None, _meta=None, **ctx):
    """被安全审核拦截时：第 1 次换一组参考图重试；之后每次把放开程度降一级再试（降到 0 级仍被拦就报错）。
    outfit_scene：显式指定穿搭场景（如 "约会"、"见老师/学生会正式场合"），不传则按作息/校园/情绪/聊天推断。"""
    cfg = cfg or load(); ig = cfg.get("imagegen", {})
    if _plan is None: _plan = outfit_plan(scene, outfit_scene, cfg, **ctx)
    # 日程槽透传：供 pick_scene_refs 优先 resolve common pack（勿丢 slot）
    _plan = dict(_plan or {})
    if isinstance(ctx.get("slot"), dict):
        _plan.setdefault("slot", ctx["slot"])
    if ctx.get("date"):
        _plan.setdefault("date", ctx["date"])
    elif isinstance(ctx.get("today"), dict) and ctx["today"].get("date"):
        _plan.setdefault("date", ctx["today"]["date"])
        _plan.setdefault("today", ctx["today"])
    out_dir = pathlib.Path(ig["out_dir"]) / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ("" if _attempt == 1 else f"-retry{_attempt}") + ("" if (_meta or {}).get("local_try", 1) == 1 else f"-try{_meta['local_try']}"))
    import codex_session
    shot_type = ctx.get("shot_type") or (_plan or {}).get("shot_type") or (_meta or {}).get("shot_type")
    shot_bits = ctx.get("shot_bits")
    try:
        from chat_media import MAX_CHAT_IMAGES_ABS as _MAX_N
        n_out = max(1, min(int(_MAX_N), int(ctx.get("n_images") or (_meta or {}).get("n_images") or 1)))
    except (TypeError, ValueError):
        n_out = 1
    import shot_modes as sm
    raw_types = ctx.get("shot_types")
    shot_types = ([sm.normalize_shot(s) or sm.SHOT_THIRD for s in raw_types[:n_out]]
                  if isinstance(raw_types, (list, tuple)) and raw_types else None)
    if shot_types:
        shot_types += [shot_types[-1]] * (n_out - len(shot_types))
    mixed_shots = bool(shot_types and len(set(shot_types)) > 1)
    required_refs = reference_roles = None
    if mixed_shots:
        can, reference_roles = batch_identity_refs(cfg, shot_types)
        required_refs, oth = list(can), []
    else:
        can, oth = pick_refs(scene, cfg, _exclude, shot_type=shot_type)
    max_refs = int(ig.get("max_image_refs", MAX_IMAGE_REFS))
    # 脸五视图+身体五视图占 2 槽时，其余全部给场景（通常 3）；hand/POV 给更多 scene
    if mixed_shots:
        k_scene = max(0, max_refs - len(can))
    elif shot_type in ("hand_closeup", "pov_first_person", "mirror_selfie", "legs_focus", "portrait"):
        k_scene = max(0, max_refs - len(can))
        if shot_type == "pov_first_person":
            k_scene = max(k_scene, max_refs - 1)  # POV 场景优先
        if shot_type == "mirror_selfie":
            k_scene = max(k_scene, max_refs - 2)  # face(+hands) + 场景
    elif any(_is_body_sheet(p) for p in can):
        k_scene = max(0, max_refs - len(can))
    else:
        k_scene = None  # pick_scene_refs 用 config scene_refs_k
    srefs = pick_scene_refs(
        scene, _plan, cfg, k=k_scene,
        slot=_plan.get("slot") if isinstance(_plan.get("slot"), dict) else None,
        date=_plan.get("date"),
    )
    can, srefs, oth = cap_refs(can, srefs, oth, max_refs, shot_type=shot_type, required_refs=required_refs)
    refs = (can, oth)
    sid = codex_session.current(cfg)
    cmd = build_cmd(scene, cfg, out_dir, _exclude, sid, refs, srefs, shot_type=shot_type, required_refs=required_refs)
    if dry: return cmd, []
    out_dir.mkdir(parents=True, exist_ok=True); t0 = time.time() - 1; offs = rollout_sizes()
    desc, _ = appearance(cfg)
    mode = os.environ.get("SCENE_REFS_MODE") or ig.get("scene_refs_mode", "match")
    _now = ctx.get("now")
    _night = _scene_night(scene, _plan, _now, imagined=bool(ctx.get("imagined") or _plan.get("imagined")))
    # hand/POV/legs/mirror/portrait：body_kind 不按 body sheet 硬写（legs 有腿模时也不写 body HARD 话术）
    _body_kind = None if shot_type in ("hand_closeup", "pov_first_person", "mirror_selfie", "portrait", "legs_focus") else _body_kind_from_refs(refs[0])
    # 张数：调用方先随机/按需定 N，再写入同一次 prompt；N>1 一次 Codex 出齐（禁止 Python 侧串行 resume 凑数）。
    # All N shots share <=5 inputs. Mixed batches reserve the union of required
    # face/body/hand/leg references before filling remaining slots with scenery.
    shot_briefs = None
    if n_out > 1:
        try:
            import shot_modes as _smb
            bits = shot_bits if isinstance(shot_bits, dict) else {}
            types_for_brief = shot_types or ([shot_type] * n_out if shot_type else [None] * n_out)
            shot_briefs = []
            for st in types_for_brief:
                blk = (_smb.prompt_shot_block(st, scene, bits) or "").strip()
                shot_briefs.append(blk or (f"镜头 {st}" if st else "同场景自然变体构图"))
        except Exception:
            shot_briefs = [f"第 {i+1} 张：同场景不同构图" for i in range(n_out)]
    prompt = build_prompt(scene, desc, _plan["text"], _plan.get("realism_text", ""), len(refs[0]), len(srefs), mode, pose_kind=_pose_kind_from_refs(refs[0], scene), cfg=cfg, emotion_text=_plan.get("emotion_text", ""), body_kind=_body_kind, night=_night, shot_type=shot_type, shot_bits=shot_bits, n_images=n_out, shot_briefs=shot_briefs, reference_roles=reference_roles)
    _plan = dict(_plan, scene_refs=[str(p) for p in srefs], scene_refs_mode=mode, shot_type=shot_type, n_images=n_out, shot_types=shot_types)
    (out_dir / "outfit.json").write_text(json.dumps(_plan, ensure_ascii=False, indent=1))
    meta = dict(_meta or {"backend": "local"}, moderation_attempt=_attempt, canonical_refs=[str(p) for p in refs[0]], other_refs=[str(p) for p in refs[1]], scene_refs=[str(p) for p in srefs], scene_refs_mode=mode, body_ref=next((str(p) for p in refs[0] if pathlib.Path(p).parent.name == "body"), None), shot_type=shot_type, hands_ref=next((str(p) for p in refs[0] if pathlib.Path(p).parent.name == "hands"), None), legs_ref=next((str(p) for p in refs[0] if pathlib.Path(p).parent.name == "legs"), None), n_images=n_out, shot_types=shot_types)
    # Persist exact input for diagnosis; no need to reconstruct a prompt from logs.
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    import hashlib
    meta.update(reference_roles=reference_roles,
                reference_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in can + srefs + oth if p.is_file()},
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    timeout = float(ig.get("timeout", 900))
    def run(c):
        try:
            return subprocess.run(c, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(out_dir))
        except subprocess.TimeoutExpired:
            # sticky resume 超时后必须清会话，否则下一轮还 resume 同一个膨胀上下文
            try: codex_session.forget(cfg)
            except Exception: pass
            write_meta(out_dir, dict(meta, status="timeout", session_id=sid, session=("resume" if sid else "new"), timeout=timeout))
            raise
    r = None
    if sid:
        write_meta(out_dir, dict(meta, status="running", session_id=sid, session="resume")); r = run(cmd)
        if r.returncode and codex_session.resume_broken(r.stderr):
            print(f"[imagegen] 当天会话 {sid} 无法 resume，为今天新开会话", file=sys.stderr)
            codex_session.forget(cfg); sid = None; cmd = build_cmd(scene, cfg, out_dir, _exclude, None, refs, srefs, shot_type=shot_type, required_refs=required_refs); r = None
    if r is None:
        write_meta(out_dir, dict(meta, status="running", session="new")); r = run(cmd)
    new_sid = codex_session.thread_id(r.stdout) or sid
    # 只看 stderr + 退出码：stdout 是 agent 事件流，会回显文档/代码里的 "no rollout found" 等字样，误判成坏会话导致从不保存
    if new_sid and not (r.returncode and codex_session.resume_broken(r.stderr)): codex_session.save(cfg, new_sid)
    meta.update(session_id=new_sid, session="resume" if sid else "new")
    (out_dir / "codex.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    imgs = [pathlib.Path(shutil.copy2(p, out_dir / p.name)) for p in _new_images(t0)] or _from_rollouts(t0, out_dir, offs)
    imgs += [p for p in (out_dir / "generated_images").glob("*.png")] if (out_dir / "generated_images").exists() else []
    # 去重保序，多图只取本次最多 n_out 张；不足也不串行补跑（禁止一张一张凑数）
    seen = set(); uniq = []
    for im in imgs:
        k = str(im.resolve()) if im.exists() else str(im)
        if k in seen: continue
        seen.add(k); uniq.append(im)
    imgs = uniq[:n_out] if n_out > 1 else uniq[:1] if uniq else uniq
    if n_out > 1 and 0 < len(imgs) < n_out:
        print(f"[imagegen] 本次要 {n_out} 张，实际收到 {len(imgs)} 张（不串行补齐）", file=sys.stderr)
    if not imgs and blocked(r.stdout + r.stderr) and _attempt < MAX_ATTEMPTS:
        ex = set(_exclude) | _ref_sha(cmd) | {pathlib.Path(p).name.split("_")[1] for p in refs[1] if pathlib.Path(p).name.startswith("ang_")}
        if _attempt == 1:
            print(f"[imagegen] 被审核拦截，换一组参考图重试（见 {out_dir/'codex.log'}）", file=sys.stderr)
            return generate_local(scene, dry, cfg, outfit_scene, ex, 2, _plan, _meta, **ctx)
        if _plan.get("vector") and _plan["level"] > 0:
            import outfit; lvl = _plan["level"] - 1
            _plan = dict(_plan, level=lvl, text=outfit.describe(_plan["vector"], lvl, _plan.get("scene")))
            print(f"[imagegen] 仍被拦截，放开程度降到 {lvl} 级重试", file=sys.stderr)
            return generate_local(scene, dry, cfg, outfit_scene, ex, _attempt + 1, _plan, _meta, **ctx)
    if not imgs and blocked(r.stdout + r.stderr):
        write_meta(out_dir, dict(meta, status="moderation_blocked")); raise ModerationBlocked(f"审核拦截，已换参考图并降到 0 级仍失败，见 {out_dir/'codex.log'}")
    if not imgs:
        write_meta(out_dir, dict(meta, status="failed", exit=r.returncode)); raise LocalBackendError(f"codex 没有产出图片（exit {r.returncode}），见 {out_dir/'codex.log'}；先确认 `codex login status` 已登录（不要在 mini 上发设备码）；stderr: {r.stderr.strip()[-300:]}")
    # Face-fallback（研究/离线 A/B）：再跑一次无 scene_refs 比脸。聊天路径 _meta.skip_face_fallback / chat=True 跳过。
    ig_fb = cfg.get("imagegen", {})
    fb_on = os.environ.get("SCENE_REFS_FACE_FALLBACK", str(ig_fb.get("scene_refs_face_fallback", "1"))) not in ("0", "false", "no")
    thr = float(ig_fb.get("scene_refs_face_fallback_delta", 0.15))
    already_fb = bool((_meta or {}).get("face_fallback") or (_meta or {}).get("skip_face_fallback") or (_meta or {}).get("chat"))
    if fb_on and srefs and not already_fb and not dry:
        d_new = featureprint_dist_to_canonical(imgs[0], cfg)
        print(f"[imagegen] scene_refs FeaturePrint dist={d_new}", file=sys.stderr)
        env_save = os.environ.get("SCENE_REFS")
        try:
            os.environ["SCENE_REFS"] = "0"
            fb_meta = dict(_meta or {"backend": "local"}, face_fallback=True, skip_face_fallback=True,
                           scene_refs_first_dist=d_new, scene_refs_first_dir=str(out_dir))
            cmd_old, imgs_old = generate_local(scene, False, cfg, outfit_scene, _exclude, 1, _plan, fb_meta, **ctx)
        finally:
            if env_save is None: os.environ.pop("SCENE_REFS", None)
            else: os.environ["SCENE_REFS"] = env_save
        d_old = featureprint_dist_to_canonical(imgs_old[0], cfg) if imgs_old else None
        print(f"[imagegen] old-path FeaturePrint dist={d_old} (thr delta={thr})", file=sys.stderr)
        pick = "scene_refs"
        if d_new is not None and d_old is not None and d_new > d_old + thr:
            pick = "face_fallback"
            write_meta(out_dir, dict(meta, status="ok_but_fallback", images=[p.name for p in imgs],
                                     featureprint_dist=d_new, old_path_dist=d_old, chose="face_fallback"))
            # copy winner note onto old out dir meta already written inside recurse; annotate
            try:
                old_dir = imgs_old[0].parent
                rj = old_dir / "run.json"
                if rj.exists():
                    m = json.loads(rj.read_text()); m.update(chose="face_fallback", scene_refs_dist=d_new, old_path_dist=d_old); rj.write_text(json.dumps(m, ensure_ascii=False, indent=1))
            except Exception: pass
            return cmd_old, imgs_old
        write_meta(out_dir, dict(meta, status="ok", images=[p.name for p in imgs],
                                 featureprint_dist=d_new, old_path_dist=d_old, chose=pick))
        return cmd, imgs
    write_meta(out_dir, dict(meta, status="ok", images=[p.name for p in imgs])); return cmd, imgs

if __name__ == "__main__":
    if "--relay-done" in sys.argv:
        i = sys.argv.index("--relay-done"); print(relay_done(sys.argv[i + 1], sys.argv[i + 2])); sys.exit(0)
    ap = argparse.ArgumentParser(); ap.add_argument("scene"); ap.add_argument("--dry", action="store_true")
    ap.add_argument("--outfit-scene", help="显式指定穿搭场景，如 约会 / 上课 / 正式"); a = ap.parse_args()
    cmd, imgs = generate(a.scene, a.dry, outfit_scene=a.outfit_scene); print(" ".join(cmd)); print(imgs)
