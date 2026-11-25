"""Collection mix; persona SSOT: data/persona_dossier.json.

Queries find material, not facts about her experience or game history.
"""
import random
from share_selection import BAND_TARGETS

QUERIES = {
    'light': [
        '大学生 搞笑 吐槽', '宿舍 搞笑 日常', '情侣 搞笑 热梗', '表情包 快乐日常',
        '双人合作 游戏 搞笑', '星露谷 游戏 日常', '动物森友会 岛屿设计', '无限暖暖 穿搭',
        '猫咪 搞笑', '狗狗 可爱 日常', '大学生 日常穿搭', '日常 淡妆 教程',
        '拍照 构图 灵感', '日常好物 学生', '手帐 创意', '美甲 灵感',
    ],
    'school': ['北舞 校园', '大学校园 生活'],
    'other': ['北京 周末 逛展', '旅行 风景', '海岛 日落', '徒步 公园',
              '滑雪 风景', '美食 探店', '电影 幕后', '话剧 有趣片段',
              '现代舞 编舞', '音乐 现场', '网球 日常', '游泳 运动'],
}


def choose_topic(rng=None):
    rng = rng or random
    from config import load
    from personalization_runtime import band_targets, option
    cfg = load()
    targets, queries = BAND_TARGETS, dict(QUERIES)
    if cfg.get("persona_version"):
        targets = band_targets(cfg)
        queries["light"] = ["有趣 日常", "自然风景", "创意 灵感"]
        queries["other"] = ["艺术 创作", "生活 发现", "电影 幕后"]
        institution = ((cfg.get("role_dossier") or {}).get("school") or {}).get("institution")
        queries["school"] = [institution + " 校园"] if institution else ["公共文化 资讯"]
        interests = option("content.interests", [], cfg=cfg) or []
        if interests:
            queries["other"] = interests
    band = rng.choices(list(targets), weights=list(targets.values()), k=1)[0]
    return rng.choice(queries[band])
