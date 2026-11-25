"""Editable persona contract. Facts SSOT: data/persona_dossier.json.

Only these fields may enter a console draft. Deployment grants, paths, routes,
live emotion and memories are deliberately absent. Consumers share this schema.
"""
from __future__ import annotations

import copy
import datetime as dt
import math
import re

SECTIONS = [
    {"id": "background", "name": "背景与关系", "note": "她是谁，以及你们共同确认的故事", "modules": ["A01", "A02", "A03", "A04", "A05"]},
    {"id": "personality", "name": "性格与情绪", "note": "定义表达倾向，保留真实情绪的变化", "modules": ["B01", "B08"]},
    {"id": "chat", "name": "聊天与称呼", "note": "从称呼、语言到每一次具体的回应", "modules": ["B02", "B03", "B04", "B05", "B06", "B07", "B09"]},
    {"id": "proactive", "name": "主动互动", "note": "把想念放进合适的联系节奏里", "modules": ["C01", "C02", "C03"]},
    {"id": "appearance", "name": "外貌与标准素材", "note": "从原始照片建立一致的脸部与全身标准", "modules": ["D01", "D02", "D03"]},
    {"id": "image", "name": "生图与穿搭", "note": "她的衣橱、镜头习惯与照片质感", "modules": ["D04", "D05", "D06", "D07", "D08", "D09", "D10"]},
    {"id": "content", "name": "记忆与内容", "note": "如何记住分享，又如何发现新的话题", "modules": ["C04", "C05", "C06", "C07", "E01", "E02", "E08"]},
    {"id": "media", "name": "声音与头像", "note": "查看当前通道支持的声音和头像能力", "modules": ["E03", "E04", "E05", "E06", "E07"]},
]
FIELDS = []


def field(section, module, key, label, kind="text", *, target="dossier", path=None,
          default=None, minimum=None, maximum=None, options=None, consumer=None, note="", status="ready"):
    FIELDS.append(dict(id=key, section=section, module=module, label=label, type=kind,
                       target=target, path=path or key, default=default, min=minimum, max=maximum,
                       options=options, consumer=consumer or ("persona_context, role_chat" if target == "dossier" else "personalization_runtime"),
                       source=("data/persona_dossier.json" if target == "dossier" else "config.json / 人物策略"),
                       reload="轮次版本快照", status=status, note=note))


for key, label, kind, module in [
    ("identity.name", "名字", "text", "A01"), ("identity.birthday", "生日", "date", "A01"),
    ("identity.age_stated", "年龄（无生日时）", "text", "A01"),
    ("identity.gender", "性别", "text", "A01"), ("identity.role", "身份描述", "textarea", "A01"),
    ("school.hometown", "家乡与成长地区", "text", "A01"),
    ("school.institution", "学校", "text", "A02"), ("school.college", "学院", "text", "A02"),
    ("school.major", "专业", "text", "A02"), ("school.major_note", "专业补充", "textarea", "A02"),
    ("school.cohort_year", "入学年份", "year", "A02"), ("school.grade", "年级", "text", "A02"),
    ("orgs.student_union.org", "学生组织", "text", "A02"), ("orgs.student_union.title", "组织职务", "text", "A02"),
    ("orgs.clubs", "社团与角色", "clubs", "A02"),
    ("work.internship.org_type", "工作或实习单位类型", "text", "A02"),
    ("work.internship.role", "工作职责", "textarea", "A02"),
    ("family.background", "家庭背景", "textarea", "A03"), ("family.parents", "父母背景", "textarea", "A03"),
    ("school.dorm", "宿舍背景", "textarea", "A03"), ("roommates", "室友与社交圈", "roommates", "A03"),
    ("preferences.dislikes_food", "不喜欢的食物", "tags", "A04"), ("preferences.likes_food", "喜欢的食物", "tags", "A04"),
    ("preferences.drink", "饮品偏好", "text", "A04"), ("preferences.likes_sports", "运动兴趣", "tags", "A04"),
    ("preferences.likes_style", "审美偏好", "tags", "A04"), ("preferences.interests", "其他兴趣", "tags", "A04"),
    ("identity.relationship_to_user", "你们的关系", "text", "A05"),
    ("relationship_story.how_met", "如何认识", "textarea", "A05"),
    ("relationship_story.meet_cute", "共同故事", "textarea", "A05"),
    ("relationship_story.first_date", "第一次约会", "text", "A05"),
    ("relationship_story.anniversary", "纪念日", "date", "A05"),
    ("relationship_story.dating_since", "关系开始日期", "date", "A05"),
    ("relationship_story.tone", "故事表达方式", "textarea", "A05"),
]:
    field("background", module, key, label, kind)
field("background", "A05", "relationship.kind", "关系类型", target="relationship", path="kind", default="romantic_partner")
for key, label, kind in [("personality.traits_summary", "核心性格", "textarea"), ("personality.mbti", "MBTI（可留空）", "text"),
                         ("personality.overlays", "叠加气质", "tags"), ("personality.accent", "语气与口音", "text")]:
    field("personality", "B01", key, label, kind)
for key, label, default in [("emotion.sweetness", "亲密表达", "自然、按关系和语境表达"),
                            ("emotion.teasing", "调侃边界", "正事和难过时减少调侃"),
                            ("emotion.care", "关心方式", "先接住具体内容，再自然延续")]:
    field("personality", "B08", key, label, "textarea", target="policy", default=default)
for key, label, default, lo, hi, kind in [
    ("emotion.daily_sweet_max", "每日亲密表达上限", 3, 0, 30, "integer"),
    ("emotion.consecutive_sweet_max", "连续亲密表达上限", 2, 1, 8, "integer"),
    ("emotion.hour_bias_scale", "昼夜表达变化强度", 1., 0, 2, "number"),
    ("emotion.daily_happy_max", "每日明显开心的活动槽上限", 2, 0, 20, "integer"),
    ("emotion.pleasure_bias", "愉悦表达倾向（不改当前心情）", 0., -.3, .3, "number"),
    ("emotion.arousal_bias", "活跃表达倾向（不改当前精力）", 0., -.3, .3, "number"),
    ("emotion.dominance_bias", "自主表达倾向", 0., -.3, .3, "number"),
]:
    field("personality", "B08", key, label, kind, target="policy", default=default, minimum=lo, maximum=hi, consumer="emotion.hour_soft_bias, emotion.sweet_mode")
field("chat", "B02", "language.default", "默认语言", target="policy", default="普通话")
field("chat", "B02", "languages.speaks", "支持的语言", "tags")
field("chat", "B02", "language.follow", "跟随对方使用的语言", "bool", target="policy", default=True)
field("chat", "B02", "languages.native_flavor", "口音底色")
field("chat", "B03", "nicknames.preferred", "常用称呼", "tags", target="policy", default=[])
field("chat", "B03", "nicknames.banned", "禁用称呼", "tags", target="policy", default=["王某某"])
field("chat", "B03", "nicknames.extension_probability", "扩展称呼使用概率", "number", target="policy", default=.12, minimum=0, maximum=1)
field("chat", "B03", "nicknames.sticky", "同一天沿用称呼", "bool", target="policy", default=True)
field("chat", "B04", "speech.catchphrases", "口头禅", "tags")
field("chat", "B04", "speech.emoji_allowlist", "常用表情", "tags")
field("chat", "B04", "speech.style", "日常说话方式", "textarea")
field("chat", "B04", "chat.question_style", "提问习惯", "textarea", target="policy", default="不频繁反问，按分享内容自然追问")
field("chat", "B05", "chat.feedback", "收到不满反馈时", "textarea", target="policy", default="减少调侃，具体回应对方觉得凶或敷衍的地方")
field("chat", "B06", "chat.normal_chars", "普通回复字数上限", "integer", target="policy", default=80, minimum=20, maximum=120)
field("chat", "B06", "chat.enthusiastic_chars", "热情回复字数上限", "integer", target="policy", default=120, minimum=40, maximum=240)
field("chat", "B06", "chat.max_bubbles", "单轮气泡上限", "integer", target="policy", default=8, minimum=1, maximum=8)
field("chat", "B06", "reply.human_typing", "自然打字节奏", "bool", target="settings", default=True, consumer="channels.bridge._reply_delay")
field("chat", "B06", "reply.typing_sec_per_char", "每字打字时间（秒）", "number", target="settings", default=.3, minimum=0, maximum=2, consumer="channels.bridge._reply_delay")
for key, label, default in [("typing_min_sec", "最短打字等待（秒）", 2), ("typing_max_sec", "最长打字等待（秒）", 15)]:
    field("chat", "B06", "reply." + key, label, "number", target="settings", default=default, minimum=0, maximum=60, consumer="channels.bridge._reply_delay")
field("chat", "B06", "reply.engagement.internship_enthusiastic_probability", "实习时热情回应概率", "number", target="settings", default=.3, minimum=0, maximum=1, consumer="reply_engagement.select_response")
for mode, label, default in [("pre_sleep", "睡前", 40), ("asleep", "会话睡眠时", 24)]:
    field("chat", "B07", f"sleep_chat.{mode}.max_chars_total", f"{label}字数上限", "integer", target="settings", default=default, minimum=8, maximum=120, consumer="sleep_chat.style_for")
    field("chat", "B07", f"sleep_chat.{mode}.prompt_tip", f"{label}表达偏好", "textarea", target="settings", consumer="sleep_chat.style_for")
    field("chat", "B07", f"sleep_chat.{mode}.gap_scale", f"{label}气泡间隔倍率", "number", target="settings", default=1.75 if mode == "pre_sleep" else 2.2, minimum=1, maximum=5, consumer="sleep_chat.style_for")
    for suffix, title, value, kind, lo, hi in [
        ("max_bubbles", "气泡上限", 3 if mode == "pre_sleep" else 2, "integer", 1, 8),
        ("max_bubble_chars", "单个气泡字数上限", 14 if mode == "pre_sleep" else 12, "integer", 4, 120),
        ("gap_min_sec", "最短气泡间隔（秒）", 1.5 if mode == "pre_sleep" else 1.8, "number", 0, 30),
        ("gap_max_sec", "最长气泡间隔（秒）", 4.2 if mode == "pre_sleep" else 4.8, "number", 0, 30),
    ]:
        field("chat", "B07", f"sleep_chat.{mode}.{suffix}", label + title, kind, target="settings", default=value, minimum=lo, maximum=hi, consumer="sleep_chat.style_for")
field("chat", "B09", "chat.photo_wait", "等候照片时的语气", "textarea", target="policy", default="简短自然，生成完成前不声称已发送")
field("proactive", "C01", "proactive.style", "主动话题与表达偏好", "textarea", target="policy", default="分享自己、记得对方之前的事、关心当下和轻松玩笑")
for key, label, weight in [("share_self", "分享自己的话题权重", .35), ("callback", "接续往事的话题权重", .3), ("context", "关心当下的话题权重", .2), ("playful", "轻松玩笑的话题权重", .15)]:
    field("proactive", "C01", "proactive.topics." + key, label, "number", target="settings", default=weight, minimum=0, maximum=1, consumer="proactive.pick_topic")
for key, label, kind, default, lo, hi in [
    ("proactive.enabled", "主动互动", "bool", True, None, None),
    ("proactive.max_per_day", "普通主动联系每日上限", "integer", 10, 0, 30),
    ("proactive.no_double_text", "对方未回复时暂停普通主动联系", "bool", False, None, None),
    ("proactive.idle_contact.enabled", "久未联系时主动关心", "bool", True, None, None),
    ("proactive.image_share_min_ratio", "普通主动联系附图倾向", "number", .6, 0, 1),
    ("proactive.beats.meal_p", "吃饭分享倾向", "number", .45, 0, 1),
]:
    field("proactive", "C03" if "image_share" in key else "C02", key, label, kind, target="settings", default=default, minimum=lo, maximum=hi, consumer="proactive, proactive_limiter, proactive_beats")
for key, label, default in [
    ("proactive.idle_contact.interval_sec", "久未联系间隔（秒，仅计清醒时间）", [5400, 9000]),
    ("proactive.contact_rhythm.day_spacing_sec", "白天主动联系间隔（秒）", [4500, 7200]),
    ("proactive.contact_rhythm.evening_spacing_sec", "晚间主动联系间隔（秒）", [2100, 3900]),
]:
    field("proactive", "C02", key, label, "range", target="settings", default=default, minimum=120, maximum=86400, consumer="idle_contact, proactive_limiter")
field("appearance", "D01", "appearance.description", "外貌补充说明", "textarea")
field("appearance", "D01", "appearance.source_note", "原图来源说明", "textarea")
for key, label, lo, hi in [("body.height_cm", "身高（cm，可留空）", 50, 250), ("body.weight_kg", "体重（kg，可留空）", 10, 300)]:
    field("appearance", "D01", key, label, "number", minimum=lo, maximum=hi)
for key, label in [("hair", "发型"), ("skin", "肤色"), ("makeup", "妆容"), ("face_shape", "脸型"), ("figure", "身形描述")]:
    field("appearance", "D01", f"appearance.structured_features.{key}", label)
for key, label, default in [("image.wardrobe", "衣橱与穿搭偏好", ""), ("image.avoid", "避用服装与颜色", ""),
                            ("image.makeup", "日常妆造", ""), ("image.texture", "拍摄质感", "自然手机照片"),
                            ("image.expression", "表情习惯", "自然，跟随当前情绪"),
                            ("image.caption", "照片配文习惯", "根据实际生成结果简短分享，不编拍摄经历")]:
    field("image", "D04" if key in ("image.wardrobe", "image.avoid", "image.makeup") else "D06" if key in ("image.texture", "image.expression") else "D09", key, label, "textarea", target="policy", default=default)
field("image", "D05", "image.full_body_weight", "全身镜头偏好", "number", target="policy", default=.4, minimum=0, maximum=1)
for shot, label in [("hand_closeup", "手部"), ("pov_first_person", "第一视角"), ("mirror_selfie", "对镜自拍"), ("legs_focus", "腿部"), ("portrait", "人像")]:
    field("image", "D05", "image.shots." + shot, "其余镜头相对权重 · " + label, "number", target="policy", default=1., minimum=0, maximum=10, consumer="schedule_shot._weights_for_bucket", note="分配全身镜头之外的概率；明确要求的镜头与实际活动优先")
for key, label in [("chat_image_count_max", "被要求发图时的张数上限"), ("proactive_image_count_max", "主动分享时的张数上限")]:
    field("image", "D08", "imagegen." + key, label, "integer", target="settings", default=5, minimum=1, maximum=5, consumer="chat_media, imagegen")
for key, label, default in [("chat_image_count_p", "被要求发图的张数权重（1至5张）", [.4, .3, .18, .08, .04]),
                            ("proactive_image_count_p", "主动发图的张数权重（1至5张）", [.5, .28, .12, .07, .03])]:
    field("image", "D08", "imagegen." + key, label, "weights", target="settings", default=default, minimum=0, maximum=1, consumer="chat_media, imagegen")
field("image", "D07", "image.scene_pack", "背景素材包", target="policy", default=None, status="pending", note="素材包需由受管素材目录注册；不接受任意路径")
for key, label, default, module in [
    ("content.interests", "感兴趣的分享主题", [], "C04"),
    ("content.platforms", "内容来源偏好", ["xiaohongshu", "douyin"], "C04"),
    ("content.campus_sources", "学校公开来源", [], "C05"),
]:
    field("content", module, key, label, "tags", target="policy", default=default)
for name, label, value in [("light", "轻松日常内容权重", .5), ("school", "校园内容权重", .05), ("other", "其他兴趣内容权重", .45)]:
    field("content", "C04", "content." + name + "_weight", label, "number", target="policy", default=value, minimum=0, maximum=1, consumer="share_selection, collectors.topics")
field("content", "C04", "content.invitation_probability", "分享时表达共同邀约的倾向", "number", target="policy", default=.35, minimum=0, maximum=1, consumer="share_invitation.plan")
field("content", "E08", "image_caption.enabled", "理解收到的图片", "bool", target="settings", default=True, consumer="image_caption.caption_image, native_client")
field("content", "E08", "content.image_feedback", "看到对方图片时的回应偏好", "textarea", target="policy", default="根据实际识别到的内容回应，识别失败不猜细节", consumer="role_chat, personalization_runtime.style_prompt")
for key, label, default, module in [
    ("content.share_style", "内容分享语气", "像分享有趣发现，公开新闻只说听说", "C04"),
    ("content.weather_care", "天气关心方式", "只引用新鲜天气，地点由独立位置配置提供", "C06"),
    ("memory.reference", "如何引用记忆", "只引用有来源的事实，不把检索兴趣当亲历", "E01"),
    ("reflection.style", "日记与反思偏好", "记录具体互动与感受，不编事实", "E02"),
]:
    field("content", module, key, label, "textarea", target="policy", default=default)
field("content", "C07", "chat_search.enabled", "使用公开资料查证", "bool", target="settings", default=True, consumer="chat_search")
for key, label, default in [("memory.enabled", "引用长期记忆", True), ("memory.extract_enabled", "提取并保存新的事实记忆", True), ("reflection.enabled", "使用已授权的反思能力", True)]:
    field("content", "E01" if key.startswith("memory") else "E02", key, label, "bool", target="policy", default=default)
for key, label, default in [("voice_asr.enabled", "听取语音", True)]:
    field("media", "E03", key, label, "bool", target="settings", default=default, consumer="voice_asr, native_gateway")
field("media", "E04", "voice.reply", "使用语音回复", "bool", target="policy", default=False, status="pending", note="正常聊天的语音回复触发链尚未接通")
field("media", "E05", "avatar.style", "头像素材偏好", "textarea", target="policy", default=None, status="pending", note="更换真实账号头像需走独立通道确认，不随人设应用")

BY_ID = {f["id"]: f for f in FIELDS}
RELATION_FACTS = {"identity.relationship_to_user"} | {f["id"] for f in FIELDS if f["id"].startswith("relationship_story.")}
SHARED_FACTS = {f["id"] for f in FIELDS if f["target"] == "dossier"} - RELATION_FACTS


def get_path(obj, path, default=None):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return copy.deepcopy(default)
        obj = obj[part]
    return copy.deepcopy(obj)


def set_path(obj, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = copy.deepcopy(value)


def initial_values(dossier, settings, *, empty=False):
    out = {}
    for spec in FIELDS:
        if empty and spec["target"] in {"dossier", "relationship"}:
            value = None
        elif spec["target"] == "dossier":
            value = get_path(dossier, spec["path"], spec["default"])
        elif spec["target"] == "settings":
            value = get_path(settings, spec["path"], spec["default"])
        else:
            value = copy.deepcopy(spec["default"])
        out[spec["id"]] = value
    return out


def role_values(role, relation_id, *, defaults=None):
    """Combine authoritative shared facts with only this relationship's policy."""
    values = copy.deepcopy(defaults) if defaults is not None else initial_values(role["dossier"], role.get("settings") or {})
    personal = role.get("personalization") or {}
    if "relationships" in personal:
        personal = personal["relationships"].get(relation_id, {})
    values.update(copy.deepcopy(personal.get("values") or {}))
    for key in SHARED_FACTS:
        values[key] = get_path(role["dossier"], key)
    relation = role["relationships"][relation_id]
    values["relationship.kind"] = relation["kind"]
    values["identity.relationship_to_user"] = relation.get("description")
    for key in RELATION_FACTS - {"identity.relationship_to_user"}:
        values[key] = get_path(relation.get("story") or {}, key.removeprefix("relationship_story."))
    return values


def relationship_errors(values):
    # Only reject explicit contradictions for recognized non-romantic kinds;
    # custom relationship names and historical stories are not inferred/edited.
    kind = str(values.get("relationship.kind") or "").strip().casefold().replace("-", "_")
    if kind not in {"friend", "colleague", "coworker", "classmate", "mentor", "teacher", "student", "stranger",
                    "朋友", "普通朋友", "同事", "同学", "导师", "老师", "师生", "陌生人"}:
        return {}
    romantic = r"女朋友|男朋友|恋人|情侣|夫妻|配偶|妻子|丈夫|老婆|老公|\b(?:girlfriend|boyfriend|lover|spouse|wife|husband|romantic[ _-]+partner)\b"
    return {key: "此描述包含恋爱或配偶称谓，与当前关系类型冲突，请修改描述后保存"
            for key in ("identity.role", "identity.relationship_to_user")
            if re.search(romantic, str(values.get(key) or ""), re.I)}


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("；".join(f"{k}: {v}" for k, v in errors.items()))


def fields_for(role):
    fields = copy.deepcopy(FIELDS)
    if (role or {}).get("adapter") != "legacy":
        for spec in fields:
            if spec["id"] == "chat_search.enabled":
                spec.update(status="pending", note="当前注册人物的聊天工作流尚未接入公开资料查证")
    return fields


def validate(values, *, baseline=None, fields=None):
    if not isinstance(values, dict):
        raise ValidationError({"values": "需要人物字段对象"})
    errors, clean = {}, {}
    if set(values) - set(BY_ID):
        errors["values"] = "包含未开放或运行权限字段"
    for spec in fields if fields is not None else FIELDS:
        key = spec["id"]
        value = values.get(key)
        kind = spec["type"]
        if spec["status"] != "ready" and baseline is not None and value != baseline.get(key):
            errors[key] = "此能力尚未接入，不能修改"
        if value is None or value == "":
            clean[key] = None
            continue
        if kind in ("text", "textarea", "date"):
            if not isinstance(value, str) or len(value) > (4000 if kind == "textarea" else 300):
                errors[key] = "文本过长或类型不正确"
            elif kind == "date":
                try:
                    parsed = dt.date.fromisoformat(value)
                    if key == "identity.birthday" and parsed > dt.date.today():
                        raise ValueError()
                except ValueError:
                    errors[key] = "日期无效"
        elif kind == "bool":
            if type(value) is not bool:
                errors[key] = "需要开关值"
        elif kind in ("number", "integer", "year"):
            lo, hi = (1900, 2200) if kind == "year" else (spec["min"], spec["max"])
            if type(value) not in (int, float) or not math.isfinite(value) or (kind in ("integer", "year") and type(value) is not int):
                errors[key] = "数值类型不正确"
            elif (lo is not None and value < lo) or (hi is not None and value > hi):
                errors[key] = f"请填写{lo}至{hi}之间的数值"
        elif kind in ("tags", "range", "weights", "clubs", "roommates"):
            if not isinstance(value, list) or len(value) > 50:
                errors[key] = "列表类型不正确或条目过多"
            elif kind == "tags" and any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in value):
                errors[key] = "列表项需要非空短文本"
            elif kind in ("range", "weights"):
                expected = 2 if kind == "range" else 5
                if len(value) != expected or any(type(v) not in (int, float) or not math.isfinite(v) or v < spec["min"] or v > spec["max"] for v in value):
                    errors[key] = f"需要{expected}个有效数值"
                elif (kind == "range" and value[0] > value[1]) or (kind == "weights" and sum(value) <= 0):
                    errors[key] = "范围顺序或权重无效"
            elif kind in ("clubs", "roommates"):
                allowed = {"name", "role"} if kind == "clubs" else {"name", "major_hint", "traits", "vec"}
                for row in value:
                    if not isinstance(row, dict) or set(row) - allowed or not isinstance(row.get("name"), str) or not row["name"].strip():
                        errors[key] = "每一项需填写名称，且不能包含额外字段"
                        break
                    if any(not isinstance(v, str) or len(v) > 1000 for k, v in row.items() if k != "vec"):
                        errors[key] = "人物描述需为文本"
                    if "vec" in row and (not isinstance(row["vec"], dict) or any(k not in {"sweet", "tease", "energy"} or type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for k, v in row["vec"].items())):
                        errors[key] = "室友气质参数无效"
        clean[key] = copy.deepcopy(value.strip() if isinstance(value, str) else value)
    if not clean.get("identity.name"):
        errors["identity.name"] = "请填写名字"
    if not clean.get("relationship.kind"):
        errors["relationship.kind"] = "请确认关系类型"
    errors.update(relationship_errors(clean))
    for keys, label in [(["content." + band + "_weight" for band in ("light", "school", "other")], "内容主题权重"),
                        (["proactive.topics." + topic for topic in ("share_self", "callback", "context", "playful")], "主动话题权重")]:
        weights = [clean.get(key) for key in keys]
        if all(type(w) in (int, float) for w in weights) and sum(weights) <= 0:
            errors[keys[0]] = label + "至少一项需要大于零"
    for prefix, low, high in [("reply.", "typing_min_sec", "typing_max_sec"),
                              ("sleep_chat.pre_sleep.", "gap_min_sec", "gap_max_sec"),
                              ("sleep_chat.asleep.", "gap_min_sec", "gap_max_sec")]:
        a, b = clean.get(prefix + low), clean.get(prefix + high)
        if type(a) in (int, float) and type(b) in (int, float) and a > b:
            errors[prefix + low] = "最短间隔不能超过最长间隔"
    shot_values = [clean.get("image.shots." + shot) for shot in ("hand_closeup", "pov_first_person", "mirror_selfie", "legs_focus", "portrait")]
    if clean.get("image.full_body_weight") != 1 and all(type(v) in (int, float) for v in shot_values) and sum(shot_values) <= 0:
        errors["image.full_body_weight"] = "其余镜头至少需要一个正权重"
    for key in ("content.campus_sources",):
        from urllib.parse import urlsplit
        for source in clean.get(key) or []:
            try:
                parsed = urlsplit(source)
                valid = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443) and not parsed.fragment
            except ValueError:
                valid = False
            if not valid:
                errors[key] = "学校公开来源需要不带账号、非标准端口或片段的HTTPS网址"
    if set(clean.get("content.platforms") or []) - {"xiaohongshu", "douyin", "weibo"}:
        errors["content.platforms"] = "请选择已接入来源：xiaohongshu、douyin、weibo"
    if errors:
        raise ValidationError(errors)
    return clean


def project(values):
    """Derive runtime projections, never live state or deployment authority."""
    dossier, settings, policy, relation = {}, {}, {}, {}
    buckets = dict(dossier=dossier, settings=settings, policy=policy, relationship=relation)
    for spec in FIELDS:
        if spec["status"] == "ready" and (values.get(spec["id"]) is not None or spec["target"] == "dossier"):
            set_path(buckets[spec["target"]], spec["path"], values.get(spec["id"]))
    # Mirrored descriptions must not reintroduce old facts or style rules.
    dossier.setdefault("identity", {})["mbti"] = get_path(dossier, "personality.mbti")
    dossier["identity"]["persona_overlays"] = get_path(dossier, "personality.overlays")
    dossier.setdefault("appearance", {})["chat_policy"] = "外貌不主动报；未知事实不编造"
    return dict(dossier=dossier, settings=settings, policy=policy, relationship=relation)
