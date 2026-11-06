"""Offline regression: temp contact STATE only; no network or real messages."""
import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import memory
import share_dedupe as sd
import proactive_beats as pb


class ShareDedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'contacts/demo_contact').mkdir(parents=True)
        self.p = patch.object(memory, 'DL', self.root)
        self.p.start()
        self.now = dt.datetime(2026, 10, 27, 17)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def item(self, owner='a', url='https://www.xiaohongshu.com/explore/abc?xsec_token=old'):
        return {'id': owner, 'meta': {'beat_meta': {'id': 'surf_share',
                'material': {'id': 7, 'source_url': url}}}}

    def test_link_variants_and_distinct_works(self):
        self.assertEqual(sd.canonical_url('https://www.xiaohongshu.com/explore/ABC?xsec_token=x'),
                         sd.canonical_url('https://www.xiaohongshu.com/discovery/item/abc?utm_source=y'))
        self.assertEqual(sd.canonical_url('https://www.douyin.com/video/123'),
                         sd.canonical_url('https://www.douyin.com/?modal_id=123'))
        self.assertNotEqual(sd.canonical_url('https://example.com/?id=1'), sd.canonical_url('https://example.com/?id=2'))

    def test_restart_and_cross_day_reservation(self):
        item = self.item()
        self.assertTrue(sd.reserve('demo_contact', item, self.now))
        self.assertFalse(sd.reserve('demo_contact', item, self.now))  # crashed worker must not replay
        self.assertTrue(sd.reserve('demo_contact', item, self.now, retry=True))  # existing suffix only
        other = self.item('b', 'https://www.xiaohongshu.com/discovery/item/abc?xsec_token=new')
        sd.material_of(other)['id'] = 999
        self.assertFalse(sd.reserve('demo_contact', other, self.now + dt.timedelta(days=100)))
        st = memory.read_state('demo_contact')
        memory.update_state('demo_contact', **sd.receipt_patch(st, item, self.now))
        self.assertFalse(sd.reserve('demo_contact', item, self.now, retry=True))

    def test_same_image_different_url_and_deleted_file(self):
        image = self.root / 'a.png'; image.write_bytes(b'identical image')
        a = self.item(); sd.material_of(a)['image_path'] = str(image)
        sd.material_of(a)['dedupe_keys'] = sorted(sd.keys(sd.material_of(a)))
        self.assertTrue(sd.reserve('demo_contact', a, self.now))
        copy = self.root / 'b.png'; copy.write_bytes(image.read_bytes())
        b = self.item('b', 'https://www.douyin.com/video/456')
        sd.material_of(b)['image_path'] = str(copy)
        self.assertFalse(sd.reserve('demo_contact', b, self.now))
        image.unlink()
        self.assertTrue(sd.duplicate(sd.material_of(a), memory.read_state('demo_contact')))

    def test_migrate_history_and_uncertain_queue(self):
        a = self.item(); a['status'] = 'hold'
        memory.update_state('demo_contact', proactive_beats={'shares': [{'source_url': 'https://www.douyin.com/video/99'}]})
        st = sd.migrate('demo_contact', [a])
        self.assertTrue(sd.duplicate(sd.material_of(a), st))
        self.assertTrue(sd.duplicate({'source_url': 'https://www.douyin.com/?modal_id=99'}, st))
        self.assertTrue(sd.reserve('demo_contact', a, self.now, retry=True))
        memory.update_state('demo_contact', proactive_beats=pb._beats_state(st, self.now + dt.timedelta(days=1)))
        self.assertTrue(sd.duplicate(sd.material_of(a), memory.read_state('demo_contact')))

    def test_selection_skips_duplicate_even_with_new_id(self):
        import material_store
        image = self.root / 'pic.png'; image.write_bytes(b'pic')
        hit = {'id': 99, 'platform': 'xiaohongshu', 'title': '有趣的校园故事',
               'source_url': 'https://www.xiaohongshu.com/discovery/item/abc?xsec_token=new',
               'media': [{'kind': 'image', 'local_path': str(image)}]}
        self.assertTrue(sd.reserve('demo_contact', self.item(), self.now))
        with patch.object(material_store, 'search', return_value=[hit]), \
             patch.object(material_store, 'recent', return_value=[]):
            self.assertIsNone(pb.pick_surf_item({}, {}, require_media=True, share_state=memory.read_state('demo_contact')))

    def test_queued_duplicate_is_dropped_before_transport(self):
        import proactive_sender as sender
        import config
        a = self.item()
        a.setdefault('meta', {})['has_image'] = True
        self.assertTrue(sd.reserve('demo_contact', a, self.now))
        q = {'items': []}
        sender.enqueue(q, '换一种配文再分享', 'surf_share', self.now,
                       meta=self.item('b')['meta'])
        path = self.root / 'queue.json'
        sender.save_q(q, path)
        st = memory.read_state('demo_contact')
        with patch.object(sender.ps, 'gates', return_value=None), \
             patch.object(sender, 'send_via_onebot') as send:
            result = sender.run_once(self.now, dry=True, cfg_full=config.load(),
                                     qpath=path, st=st, rows=[])
        self.assertEqual(result['action'], 'DROP')
        send.assert_not_called()

    def test_outbox_allows_only_original_unsent_suffix(self):
        import proactive_limiter as limiter
        import proactive_scheduler
        a = self.item()
        a.setdefault('meta', {})['has_image'] = True
        self.assertTrue(sd.reserve('demo_contact', a, self.now))
        with patch.object(proactive_scheduler, 'gates', return_value=None):
            self.assertIsNone(limiter.retry_reason({'contact': 'demo_contact', 'proactive_round': a}, self.now))
            b = self.item('b')
            b.setdefault('meta', {})['has_image'] = True
            self.assertEqual(limiter.retry_reason({'contact': 'demo_contact', 'proactive_round': b}, self.now),
                             '分享内容重复，禁止补发')

    def test_no_interference_with_ordinary_chat(self):
        self.assertEqual(sd.material_of({'id': 'chat', 'text': 'same topic'}), {})
        self.assertFalse(sd.duplicate({}, memory.read_state('demo_contact')))


if __name__ == '__main__':
    unittest.main()
