"""Topic probability and candidate-pool regressions; temp material stores only."""
import datetime as dt
import pathlib
import random
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import share_selection as ss

NOW = dt.datetime(2026, 10, 27, 18)
def item(title):
    return {'title': title, 'source_url': title}
def past(title, hours=1):
    return {**item(title), 'shared_at': (NOW-dt.timedelta(hours=hours)).isoformat()}
def candidates(*titles):
    return [(2, item(t), []) for t in titles]
def mass(rows):
    out = {}
    for p, _, _, topic in rows:
        out[topic] = out.get(topic, 0) + p
    return out

class SelectionTests(unittest.TestCase):
    def test_collection_uses_same_mix_and_keeps_explicit_queries(self):
        from collectors.topics import choose_topic, QUERIES
        from collectors.douyin import Douyin
        from collectors.xiaohongshu import XHS
        from unittest.mock import Mock
        rng = Mock()
        rng.choices.return_value = ['light']
        rng.choice.return_value = QUERIES['light'][0]
        self.assertEqual(choose_topic(rng), QUERIES['light'][0])
        self.assertEqual(rng.choices.call_args.kwargs['weights'], [.5, .05, .45])
        self.assertEqual(Douyin('指定主题').topic, '指定主题')
        self.assertEqual(XHS('指定主题').topic, '指定主题')

    def test_baseline_mix_and_school_reduction(self):
        rows = ss.distribution(candidates('游戏合作', '搞笑段子', '北舞校庆', '海岛旅行', '滑雪'), [], NOW)
        bands = {}
        for probability, _, _, name in rows:
            band = ss.topic_band(name)
            bands[band] = bands.get(band, 0) + probability
        self.assertEqual(bands, {'light': .5, 'school': .05, 'other': .45})
        self.assertEqual(ss.topic(item('大学生宿舍搞笑吐槽')), '趣闻搞笑')
        self.assertEqual(ss.topic_band(ss.topic(item('未知素材'))), 'other')

    def test_resorts_same_topic(self):
        for name in ['崇礼攻略', '雪如意', '翠云山', '单板练习']:
            self.assertEqual(ss.topic(item(name)), '滑雪')

    def test_repeating_topic_capped_even_if_inventory_skewed(self):
        scored = candidates(*(['滑雪攻略']*200 + ['舞蹈排练']))
        probs = mass(ss.distribution(scored, [past('雪如意')], NOW))
        self.assertLessEqual(probs['滑雪'], .02000001)
        self.assertGreaterEqual(probs['舞蹈'], .4299999)

    def test_only_repeat_pool_leaves_skip_mass(self):
        rows = ss.distribution(candidates('滑雪1', '滑雪2'), [past('雪场')], NOW)
        self.assertAlmostEqual(sum(r[0] for r in rows), .02)
        self.assertIsNone(ss.choose(candidates('滑雪'), [past('滑雪')], NOW, random.Random(0)))

    def test_topic_count_does_not_dominate(self):
        p = mass(ss.distribution(candidates(*(['滑雪']*100+['话剧'])), [], NOW))
        self.assertAlmostEqual(p['滑雪'], .225)

    def test_recent_exposure_penalty_and_recovery(self):
        scored = candidates('滑雪', '话剧', '小猫')
        recent = [past('滑雪', 3), past('滑雪', 2), past('小猫', 1)]
        p = mass(ss.distribution(scored, recent, NOW))
        self.assertLess(p['滑雪'], p['戏剧影视'])
        p = mass(ss.distribution(scored, [past('滑雪', 200)], NOW))
        self.assertAlmostEqual(p['滑雪'], .225)

    def test_same_day_no_retry_lottery(self):
        scored = candidates('滑雪')
        history = [past('滑雪')]
        self.assertEqual(ss.choose(scored, history, NOW),
                         ss.choose(scored, history, NOW+dt.timedelta(minutes=5)))

    def test_pool_broadens_even_when_search_hits(self):
        import tempfile
        import proactive_beats as pb
        import material_store as ms
        with tempfile.TemporaryDirectory() as tmp:
            image = pathlib.Path(tmp)/'a.png'; image.write_bytes(b'pic')
            def hit(i, title):
                return {'id':i,'platform':'douyin','title':title,'source_url':f'https://douyin.com/video/{i}',
                        'media':[{'kind':'image','local_path':str(image)}]}
            state = {'proactive_beats': {'shares':[past('滑雪')]}}
            with patch.object(ms,'search',return_value=[hit(1,'滑雪攻略很好看')]), \
                 patch.object(ms,'recent',return_value=[hit(2,'舞蹈排练精彩片段')]) as recent:
                selected = pb.pick_surf_item({}, {}, platforms=['douyin'], share_state=state,
                                            now=NOW, require_media=True, rng=random.Random(1))
            self.assertEqual(selected['id'],2)
            self.assertGreaterEqual(recent.call_args.args[0],300)

if __name__ == '__main__':
    unittest.main()
