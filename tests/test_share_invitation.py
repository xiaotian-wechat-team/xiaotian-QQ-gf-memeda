"""Offline invitation lottery and prompt/receipt wiring."""
import datetime as dt
import pathlib
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'src'))
import share_invitation as si
import proactive_beats as pb
NOW = dt.datetime(2026, 10, 27, 18)
class InvitationTests(unittest.TestCase):
    def test_outing_draw_and_non_outing(self):
        for title in ('三亚海岛旅行', '崇礼滑雪', '周末逛展'):
            self.assertTrue(si.plan({'title':title}, [], NOW, Mock(random=lambda: .1))['include'])
        for title in ('北舞校庆', '游戏攻略', '搞笑段子'):
            self.assertFalse(si.plan({'title':title}, [], NOW, Mock(random=lambda: 0))['include'])
        self.assertFalse(si.plan({'title':'旅游'}, [], NOW, Mock(random=lambda:.9))['include'])

    def test_cooldown_and_decay(self):
        def history(hours):
            return [{'invitation':{'include':True},'shared_at':(NOW-dt.timedelta(hours=hours)).isoformat()}]
        self.assertEqual(si.plan({'title':'旅游'},history(1),NOW)['probability'],0)
        self.assertLess(si.plan({'title':'旅游'},history(24),NOW)['probability'],.35)
        self.assertEqual(si.plan({'title':'旅游'},history(200),NOW)['probability'],.35)

    def test_stable_and_no_write(self):
        m={'title':'旅行', 'source_url':'https://example.com/a'}
        self.assertEqual(si.plan(m,[],NOW),si.plan(m,[],NOW+dt.timedelta(minutes=5)))
        self.assertNotIn('invitation',m)

    def test_prompt_and_success_history(self):
        decision={'eligible':True,'include':True,'probability':.35}
        material={'id':9,'title':'海岛旅行','source_url':'https://example.com/a','invitation':decision}
        task=pb.build_beat_task({'id':'surf_share','material':material},{},NOW)
        self.assertIn('本次抽中出游邀约',task)
        saved=pb.mark_beat_done({},'surf_share@9',NOW,share=material)
        self.assertEqual(saved['shares'][0]['invitation'],decision)
        self.assertIn('不加一起去',si.prompt({'eligible':True,'include':False}))

if __name__=='__main__':
    unittest.main()
