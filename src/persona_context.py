"""Read-only chat facts from data/persona_dossier.json, shared by all backends.

No memory, tools, state writes or appearance assets are loaded here. The caller
must append this block outside the truncatable USER/history budgets.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

DOSSIER = Path(__file__).resolve().parents[1] / "data" / "persona_dossier.json"


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def prompt_block(user_text="", *, now=None, dossier=None) -> str:
    """Persistent identity plus relevant SSOT facts; ``dossier`` aids replay/tests.

    Read every time so an approved dossier edit is effective without a stale
    process cache. Missing/invalid files fail visibly instead of inventing facts.
    """
    d = dossier if dossier is not None else json.loads(DOSSIER.read_text(encoding="utf-8"))
    identity, school = d["identity"], d["school"]
    date = now or dt.date.today()
    if isinstance(date, dt.datetime):
        date = date.date()
    age = identity.get("age_stated")
    if identity.get("birthday"):
        birthday = dt.date.fromisoformat(identity["birthday"])
        age = date.year - birthday.year - ((date.month, date.day) < (birthday.month, birthday.day))
    core = {
        "姓名": identity["name"], "年龄": age, "生日": identity.get("birthday"),
        "身份": identity.get("role"), "籍贯": school.get("hometown"),
        "学校": "；".join(str(school.get(k) or "未知") for k in ("institution", "college", "major")),
        "年级": f"{school.get('cohort_year') or '未知'}级/{school.get('grade') or '未知'}",
        "职务": "".join(str(v or "") for v in d.get("orgs", {}).get("student_union", {}).values()),
        "社团": [c.get("name") for c in d.get("orgs", {}).get("clubs", [])],
        "性格": d.get("personality", {}).get("traits_summary"),
        "语言": d.get("languages", {}).get("speaks"),
        "实习单位类型": d.get("work", {}).get("internship", {}).get("org_type"),
        "实习职务": d.get("work", {}).get("internship", {}).get("role"),
    }
    user = d.get("user_ref", {})
    lines = [
        "# 人设事实（SSOT: data/persona_dossier.json）",
        "以下学校、工作、室友和偏好属于小甜，不得移给对方；对方事实只取 USER/PROFILE 或其明确陈述。",
        "对方：" + str(user.get("name") or "未知") + "；与小甜是恋人。称呼以本轮昵称规则为准。",
        "小甜常驻事实：" + _json(core),
        "不是助手/客服，不叫主人/爸爸。默认普通话，对方明确用其他语言再跟随。空值/未记载即未知，不编公司、项目、经历、拍摄日期。",
        "日程以当前有效日程为准；候选活动/公开新闻不代表亲身参与。外貌不主动报；缺资料就承认不确定。",
    ]
    text = str(user_text or "")
    topics = (
        (("哪里", "哪儿", "在哪", "校区", "地址", "学校", "专业"), "小甜学校详情", {
            "校区": school.get("campus"), "专业说明": school.get("major_note")}),
        (("家里", "家庭", "爸", "妈", "父母", "老家", "家乡", "籍贯", "厦门"), "小甜家庭", d.get("family")),
        (("室友", "舍友", "寝室", "宿舍", "晓晚", "予安", "可心"), "小甜宿舍与室友", {
            "宿舍": school.get("dorm"),
            "室友": [{k: r.get(k) for k in ("name", "major_hint", "traits")} for r in d.get("roommates", [])]}),
        (("喜欢", "不爱", "不吃", "爱吃", "吃什么", "吃啥", "喝", "运动", "口味", "偏好", "苦瓜", "咖啡"), "小甜偏好", d.get("preferences")),
        (("认识", "相识", "怎么遇", "纪念", "周年", "在一起", "交往", "恋爱", "第一次", "初次", "初见", "约会", "多久"), "两人的既定故事", {
            k: v for k, v in d.get("relationship_story", {}).items() if k != "dating_length_as_of"}),
    )
    for keywords, label, facts in topics:
        if any(k in text for k in keywords):
            lines.append(label + "：" + _json(facts))
    lines.append("自然回应，别背档案；故事背景不等于最近发生的事件。")
    return "\n".join(lines) + "\n"
