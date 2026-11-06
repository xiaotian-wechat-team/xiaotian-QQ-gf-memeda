#!/usr/bin/env python3
"""Read-only usable inventory mix. Persona SSOT: data/persona_dossier.json."""
import argparse
import collections
import json
import pathlib
import sqlite3
import sys
import time
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from share_selection import topic, topic_band


def audit(path):
    counts = collections.Counter()
    topics = collections.Counter()
    examples = collections.defaultdict(list)
    with sqlite3.connect(pathlib.Path(path).expanduser().resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM items WHERE platform IN ('xiaohongshu','douyin') AND expires_at>?", (time.time(),)):
            item = dict(row)
            title = (item.get('title') or item.get('text') or '').strip()
            url = item.get('source_url') or ''
            if len(title)<4 or any(w in title for w in ('搜索','热搜榜','登录','验证码','search_result')):
                continue
            if not urlsplit(url).hostname or 'search_result' in url:
                continue
            media = db.execute("SELECT local_path FROM media WHERE item_id=? AND kind='image'", (item['id'],)).fetchall()
            if not any(m[0] and pathlib.Path(m[0]).is_file() for m in media):
                continue
            item['tags'] = json.loads(item.get('tags') or '[]')
            name = topic(item); band = topic_band(name)
            counts[band] += 1; topics[name] += 1
            if len(examples[name]) < 2:
                examples[name].append({'id':item['id'], 'title':title[:80], 'url':url})
    total = sum(counts.values()); light = counts['light']
    return {'usable':total, 'bands':dict(counts), 'topics':dict(topics),
            'light_fraction':light/total if total else 0,
            'additional_light_for_half':max(0,total-2*light),
            'examples':dict(examples), 'note':'库存比例；未减去demo_contact已分享作品，关键词分类需人工核验质量'}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db', default='~/xiaotian-assets/materials/materials.db')
    print(json.dumps(audit(parser.parse_args().db), ensure_ascii=False, indent=2))
