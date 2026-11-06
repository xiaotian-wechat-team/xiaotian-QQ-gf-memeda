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
    band = rng.choices(list(BAND_TARGETS), weights=list(BAND_TARGETS.values()), k=1)[0]
    return rng.choice(QUERIES[band])
