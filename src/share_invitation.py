"""Travel invitation lottery; persona SSOT: data/persona_dossier.json.

Pure planning: stored in material, only successful share receipts enter history.
"""
import datetime as dt
import hashlib
import random
import share_selection


def plan(material, shares, now, rng=None):
    name = share_selection.topic(material)
    blob = ' '.join([str(material.get('title') or ''), str(material.get('text') or ''), ' '.join(material.get('tags') or [])])
    outing = any(word in blob for word in ('旅游', '旅行', '出去玩', '出游', '探店', '剧本杀', '游乐园', '迪士尼', '环球影城', '露营', '逛展', '演唱会'))
    if not outing and name not in {'旅行户外', '滑雪', '艺术展览'}:
        return {'eligible': False, 'include': False, 'probability': 0.0}
    exposure = 0.0
    for old in shares:
        if not (old.get('invitation') or {}).get('include'):
            continue
        try:
            age = max(0, (now.replace(tzinfo=None) - dt.datetime.fromisoformat(old['shared_at']).replace(tzinfo=None)).total_seconds()/3600)
        except (KeyError, ValueError, TypeError):
            age = 0
        if age < 12:
            return {'eligible': True, 'include': False, 'probability': 0.0, 'reason': '12h_cooldown'}
        if age <= 168:
            exposure += 2 ** (-age / 48)
    probability = .35 * (.4 ** exposure)
    if rng is None:
        key = now.date().isoformat() + '|' + str(material.get('source_url')) + '|' + '|'.join(str(s.get('shared_at')) for s in shares)
        rng = random.Random(hashlib.sha256(key.encode()).digest())
    return {'eligible': True, 'include': rng.random() < probability, 'probability': probability}


def prompt(decision):
    if not decision.get('eligible'):
        return ''
    if not decision.get('include'):
        return ' 本次只分享景点或体验，不加一起去的邀约，也不换说法暗示邀约。'
    return (' 本次抽中出游邀约：结合这个具体地点自然添一句想和他一起去的短话，'
            '表达愿望即可，不约定日期、不声称已订票或已有出行计划。'
            '例如“好想跟你一起去”只作语气参考，勿固定照抄；总字数和短气泡限制不变。')
