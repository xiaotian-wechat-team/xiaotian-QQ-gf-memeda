"""Topic-balanced share sampling. Persona SSOT: data/persona_dossier.json.

No network/model calls. Topic aliases are broad enough to merge ski resorts.
Unknown topics fall back to tags; no invented persona interests.
"""
import datetime as dt
import hashlib
import math
import random
import re
from collections import defaultdict

# Baseline mass, before same-topic cooldown. Missing bands leave SKIP mass.
BAND_TARGETS = {'light': .50, 'school': .05, 'other': .45}
LIGHT_TOPICS = {'趣闻搞笑', '游戏', '萌宠', '穿搭美妆', '审美日常'}
TOPICS = {
    '趣闻搞笑': ('搞笑', '段子', '笑死', '沙雕', '趣闻', '吐槽', '抽象', '表情包', '热梗', '玩梗'),
    '游戏': ('游戏', '双人成行', '星露谷', '动森', '动物森友会', '蛋仔', '光遇', '无限暖暖', '恋与深空'),
    '学校': ('北舞', '北京舞蹈学院', '校庆', '招生', '教务', '学校通知'),
    '审美日常': ('拍照', '构图', '氛围感', '日常好物', '生活好物', '手帐', '美甲', '发饰'),
    '滑雪': ('滑雪', '雪场', '雪如意', '翠云山', '崇礼', '单板', '双板', 'ski', 'snowboard'),
    '舞蹈': ('舞蹈', '编舞', '现代舞', '芭蕾', '街舞', '北舞', '排练'),
    '戏剧影视': ('话剧', '戏剧', '电影', '剧本', '影评', '短片', '导演'),
    '美食': ('美食', '食堂', '料理', '菜谱', '火锅', '烤肉', '甜品', '咖啡', '探店'),
    '旅行户外': ('旅行', '旅游', '徒步', '露营', '爬山', 'citywalk', '公园', '郊游', '三亚', '海岛', '禾木', '阿勒泰', '出游', '出去玩', '旅行攻略'),
    '艺术展览': ('展览', '逛展', '博物馆', '美术', '摄影', '艺术展'),
    '音乐': ('音乐', '演唱会', '乐队', 'livehouse', '吉他', '钢琴'),
    '萌宠': ('猫咪', '小猫', '狗狗', '小狗', '宠物', '萌宠'),
    '穿搭美妆': ('穿搭', '美妆', '护肤', '化妆', '口红', '发型'),
    '校园生活': ('校园', '大学生', '宿舍', '上课', '考试', '学习'),
    '运动健身': ('健身', '跑步', '篮球', '羽毛球', '游泳', '骑行', '瑜伽'),
}


def topic(item):
    if item.get('share_topic'):
        return item['share_topic']
    blob = ' '.join([str(item.get('title') or ''), str(item.get('text') or ''),
                     ' '.join(str(t) for t in (item.get('tags') or []))]).lower()
    for name, aliases in TOPICS.items():
        if any(a in blob for a in aliases):
            return name
    tags = [str(t).strip().lower() for t in item.get('tags', []) if str(t).strip()]
    tags = [t for t in tags if t not in ('推荐', '热门', '日常', '生活', '抖音', '小红书')]
    return '标签:' + sorted(tags)[0] if tags else '其他'


def topic_band(name):
    if name in LIGHT_TOPICS:
        return 'light'
    if name in {'学校', '校园生活'}:
        return 'school'
    return 'other'


def distribution(scored, shares, now):
    """Return per-item probabilities; missing mass means SKIP.

    Base exp(min(score,4)/2); topic weight = mean(base) * exp(-1.5*exposure).
    Exposure decays with a 24h half-life over 7d. Last topic mass <=2% for 72h,
    even when it is the ONLY topic (remaining mass goes to SKIP).
    """
    groups = defaultdict(list)
    for score, item, images in scored:
        groups[topic(item)].append((math.exp(min(max(score, 0), 4) / 2), item, images))
    recent = []
    for s in shares:
        try:
            stamp = dt.datetime.fromisoformat(s.get('shared_at') or '')
            # Both values represent the configured local wall clock.
            hours = max(0, (now.replace(tzinfo=None) - stamp.replace(tzinfo=None)).total_seconds() / 3600)
        except (ValueError, TypeError):
            hours = 0  # legacy missing date: conservatively count as recent
        if hours <= 168:
            recent.append((topic(s), hours, s))
    exposure = defaultdict(float)
    for name, hours, _ in recent:
        exposure[name] += 2 ** (-hours / 24)
    weights = {name: sum(v[0] for v in rows) / len(rows) * math.exp(-1.5 * exposure[name])
               for name, rows in groups.items()}
    probs = {}
    for band, quota in BAND_TARGETS.items():
        names = [name for name in groups if topic_band(name) == band]
        total = sum(weights[name] for name in names)
        if total:
            probs.update({name: quota * weights[name] / total for name in names})
    last = min(recent, key=lambda v: v[1])[0] if recent else None
    if recent and min(v[1] for v in recent) <= 72 and last in probs:
        old = probs[last]
        probs[last] = min(old, .02)
        # Keep the 50/5/45 baseline: redirect only within the same band.
        peers = [name for name in probs if name != last and topic_band(name) == topic_band(last)]
        others = sum(probs[name] for name in peers)
        if others:
            for name in peers:
                probs[name] *= (others + old - probs[last]) / others
    out = []
    for name, rows in groups.items():
        platforms = defaultdict(list)
        for row in rows:
            platforms[row[1].get('platform') or 'unknown'].append(row)
        last_platform = min(recent, key=lambda v: v[1])[2].get('platform') if recent else None
        source_weights = {p: (.35 if p == last_platform else 1.) for p in platforms}
        source_total = sum(source_weights.values())
        for platform, source_rows in platforms.items():
            subtotal = sum(v[0] for v in source_rows)
            for base, item, images in source_rows:
                out.append((probs[name] * source_weights[platform] / source_total * base / subtotal,
                            item, images, name))
    return out


def choose(scored, shares, now, rng=None):
    # Same history/day = same draw: a SKIP cannot be retried every five minutes
    # until a low-probability repeat eventually wins.
    if rng is None:
        history = '|'.join(str(s.get('shared_at')) + ':' + str(s.get('source_url')) for s in shares)
        seed = hashlib.sha256((now.date().isoformat() + '|' + history).encode()).digest()
        rng = random.Random(seed)
    draw = rng.random()
    for probability, item, images, name in distribution(scored, shares, now):
        draw -= probability
        if draw < 0:
            return item, images, name, probability
    return None
