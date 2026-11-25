"""Opt-in photo recipe. Persona SSOT: data/persona_dossier.json.

Freefield contributes separate identity references and a fresh scene composition;
CreatorOS contributes an immutable identity layer and variable photographic context.
This module renders text only: no model, network, asset writes or provider routing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re


RECIPE = "freefield_creatoros_v1"
IDENTITY_ROLES = {"face", "body", "hands", "legs", "pose", "identity"}
ROLE_TEXT = {
    "face": "唯一脸部身份标准：五官、脸型、发型、肤色；不借用衣服、表情、背景或模卡构图",
    "body": "身体比例标准：头身比、四肢长度、多角度轮廓；脸以脸图为准，不借用衣服或棚拍光",
    "hands": "手型、肤色和甲型参考；不要求露脸",
    "legs": "腿型、比例和肤色参考；不要求露脸",
    "pose": "仅在当前活动需要时参考朝向；不改变身份，不强加模卡动作",
    "identity": "同一人物的补充身份参考；不借用衣服、动作或背景",
    "scene": "仅参考场景空间与陈设；其中人物不是身份来源，光照服从本次拍摄时刻",
}


@dataclass(frozen=True)
class Reference:
    index: int
    role: str


@dataclass(frozen=True)
class PhotoPlan:
    recipe: str
    scene: str
    scene_mode: str
    references: tuple[Reference, ...]
    height_cm: int | None
    weight_kg: int | None
    identity_notes: tuple[str, ...]
    omitted_description_lines: tuple[str, ...]
    outfit: str
    expression: str
    lighting: str
    shots: tuple[str, ...]
    realism: str

    def record(self):
        return asdict(self)


def identity_notes(description):
    """Only labelled appearance facts can enter identity; examples remain data.

    The current description.md also contains sample clothes, cafes and poses.
    Unknown/unlabelled prose is deliberately left out, rather than interpreted
    by another paid model call. Images remain the identity authority.
    """
    accepted, omitted = [], []
    labels = r"人物|发型|面部风格|肤色|脸型|妆容|体型|身高|体重"
    for line in str(description or "").splitlines():
        line = line.strip().lstrip("-* ")
        if re.match(rf"^(?:{labels})[：:]", line):
            accepted.append(line)
        elif line and not line.startswith("#"):
            omitted.append(line)
    return tuple(accepted), tuple(omitted)


def validate_reference_roles(roles, shot_type):
    from shot_modes import normalize_shot
    shot_type = normalize_shot(shot_type)
    if not roles and shot_type != "pov_first_person":
        raise ValueError("identity references are required except for a pure first-person scene")
    if shot_type not in ("pov_first_person", "hand_closeup", "legs_focus"):
        if "face" not in roles:
            raise ValueError("visible-person photos require the canonical face reference")
        if shot_type in (None, "third_person", "full_body") and "body" not in roles:
            raise ValueError("full-body/default photos require the approved body reference")


def compile_plan(scene, desc, outfit_text, realism_text="", *, n_canonical=0,
                 n_scene_refs=0, scene_mode="match", pose_kind=None,
                 emotion_text="", body_kind=None, night=False, shot_type=None,
                 shot_block="", n_images=1, shot_briefs=None, reference_roles=None,
                 lighting_text=None, height_cm=None, weight_kg=None):
    from shot_modes import normalize_shot
    shot_type = normalize_shot(shot_type)
    if not str(scene or "").strip():
        raise ValueError("scene is required")
    if not str(outfit_text or "").strip():
        raise ValueError("explicit scene outfit is required; identity clothes are not a fallback")
    if scene_mode not in ("match", "insert"):
        raise ValueError("unsupported scene mode")
    if type(n_images) is not int or not 1 <= n_images <= 5:
        raise ValueError("image count must be 1..5")
    if any(type(n) is not int or n < 0 for n in (n_canonical, n_scene_refs)):
        raise ValueError("invalid reference counts")
    if not 0 <= n_canonical + n_scene_refs <= 5:
        raise ValueError("attach at most 5 references; never silently drop required identity")
    if not n_canonical and shot_type != "pov_first_person":
        raise ValueError("identity references are required except for a pure first-person scene")
    if reference_roles is not None:
        roles = list(reference_roles)
        if len(roles) != n_canonical or any(r not in IDENTITY_ROLES for r in roles):
            raise ValueError("reference role manifest does not match identity attachments")
    else:
        first = {"hand_closeup": "hands", "legs_focus": "legs"}.get(shot_type, "face")
        roles = [first] if n_canonical else []
        if body_kind and len(roles) < n_canonical:
            roles.append("body")
        if pose_kind and len(roles) < n_canonical:
            roles.append("pose")
        roles += ["identity"] * (n_canonical - len(roles))
    # Pure POV and hand/leg details must not acquire a portrait just because a
    # face reference was attached. Mixed shots use the explicit union manifest.
    validate_reference_roles(roles, shot_type)
    briefs = tuple(str(b).strip() for b in (shot_briefs or ()) if str(b).strip())
    if briefs and len(briefs) != n_images:
        raise ValueError("provide exactly one brief per output")
    if not briefs:
        briefs = tuple(shot_block.strip() or "同一场景活动中的自然取景" for _ in range(n_images))
    notes, omitted = identity_notes(desc)
    light = lighting_text or (
        "夜间，窗外与室外是夜色，室内使用场所灯光。" if night else
        "白昼，室内服从场所照明，不臆断晴天或直射阳光。")
    all_roles = roles + ["scene"] * n_scene_refs
    return PhotoPlan(
        RECIPE, scene.strip(), scene_mode,
        tuple(Reference(i + 1, r) for i, r in enumerate(all_roles)),
        height_cm, weight_kg, notes, omitted, outfit_text.strip(),
        emotion_text.strip(), light.strip(), briefs, realism_text.strip(),
    )


def render(plan):
    refs = "\n".join(f"Image {r.index}：{ROLE_TEXT[r.role]}。" for r in plan.references)
    metrics = "、".join(f"{label}{value}{unit}" for label, value, unit in (
        ("身高", plan.height_cm, "cm"), ("体重", plan.weight_kg, "kg")) if value is not None)
    identity = "\n".join(plan.identity_notes)
    shots = "\n".join(f"第 {i + 1} 张：{brief}" for i, brief in enumerate(plan.shots))
    mode = ("场景图是环境底图，保持空间结构，按本次时刻调整照明。" if plan.scene_mode == "insert"
            else "生成全新照片；身份图不是初始画布，不沿用模卡的构图、棚拍背景和姿势。")
    return "\n\n".join(filter(None, (
        "【场景契约·固定】\n" + plan.scene + "\n" + plan.lighting +
        "\n地点、时刻、活动及明确要求均为硬约束；场景素材和摄影风格不能改写它们。多张均在同一地点内取景。",
        "【参考图分工·固定】\n" + refs + "\n" +
        "实际每次调用 image_gen 时都传入本镜头所需的身份参考路径；不能只在文字里声明同一人。" +
        "脸以 face 图为准，身形以 body 图为准；场景路人及会话旧图不能提供身份。" +
        "模卡不提供服装或表情。按当前穿搭换装，不复刻灰上衣黑短裤艺考模卡装。" +
        ("\n已确认体型：" + metrics + "；比例以身体图为准，与门框、家具和透视尺度一致。" if metrics else "") +
        ("\n身份文字补充（从属于参考图）：\n" + identity if identity else ""),
        "【本次拍摄·可变情境】\n穿搭：" + plan.outfit +
        ("\n" + plan.expression if plan.expression else "") +
        "\n穿搭和动作只适配上述活动，不改写人物身份；同一次拍摄保持服装连续。\n" + mode,
        "【逐张镜头】\n" + shots +
        "\n每张构图按本张说明；手部、腿部或第一视角不因共享脸图而强行露脸。忽略会话先前的画面，重新拍摄。",
        "【摄影质感·服从前述硬约束】\nphotorealistic candid phone photo，竖构图。" +
        "脸部身份清晰，皮肤保留细微纹理和自然色差，衣料有真实纹理与受力褶皱。" +
        "人物和环境共享光源方向、色温与接触阴影，反射与空间透视合理。" +
        "不默认黄金时刻、闪光灯、强景深或磨皮，不为真实感刻意糊脸。" +
        ("\n可选修饰（仅在与场景、光照和本张镜头兼容时使用）：" + plan.realism if plan.realism else "") +
        "\n独立照片，不拼成模卡、海报或多格拼图。 $imagegen",
    )))
