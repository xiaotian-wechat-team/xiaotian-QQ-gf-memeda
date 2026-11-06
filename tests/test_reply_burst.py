"""Offline burst timing, transport ingestion, and full-turn model input."""
import pathlib
import sys
import threading
from dataclasses import replace
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from channels import reply_burst as burst, onebot_qq as qq, bridge
from channels.types import Inbound
from test_channel_review_regressions import isolated


def simulate(arrivals, **kwargs):
    now = [0.]
    pending = list(arrivals)
    def drain():
        out = []
        while pending and pending[0][0] <= now[0] + 1e-8:
            out.append(pending.pop(0)[1])
        return out
    items = burst.collect('first', drain, clock=lambda: now[0],
                          sleep=lambda n: now.__setitem__(0, now[0] + n), **kwargs)
    return items, now[0]


def test_single_and_sliding_quiet_window():
    assert simulate([]) == (['first'], 6)
    items, elapsed = simulate([(4, 'second'), (8, 'third')])
    assert items == ['first', 'second', 'third']
    assert 10 <= elapsed < 10.3


def test_continuous_input_has_hard_deadline():
    items, elapsed = simulate([(i, str(i)) for i in range(1, 61)])
    assert elapsed == 12
    assert len(items) >= 12
    assert '13' not in items


def test_active_conversation_collects_next_single_quickly():
    assert simulate([], activity={'level': 'active'}) == (['first'], 2)


def test_disabled_engagement_keeps_legacy_collection():
    items, elapsed = simulate([(i, str(i)) for i in range(1, 61)],
        cfg={'reply': {'engagement': {'enabled': False}}})
    assert elapsed == 45 and len(items) >= 45


def test_qq_callback_receives_all_messages_while_worker_waits(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def collect(first, drain, **kwargs):
        entered.set()
        assert release.wait(3)
        return [first] + drain()
    monkeypatch.setattr(burst, 'collect', collect)
    runner = qq.OneBotQQRunner('ws://unused', dry=True)
    received = []
    monkeypatch.setattr(runner, '_handle_event', lambda ev, inbound: received.append(inbound))
    import json
    for i in range(3):
        runner.on_message(None, json.dumps(qq.make_private_event('10001', f'part{i}')))
    assert entered.wait(1)
    worker = runner._burst_worker
    assert runner._has_newer('10001')
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert len(received) == 1
    assert received[0].text == 'part0\npart1\npart2'


def test_batch_keeps_each_media_and_passes_all_text_to_model(monkeypatch):
    import image_caption, voice_asr
    parts = [Inbound('qq_onebot', '10001', f'part{i}', msg_id=str(i)) for i in range(25)]
    captioned, heard = [], []
    def caption(m):
        captioned.append(m.msg_id)
        return replace(m, text=m.text + '-image'), {}
    def asr(m):
        heard.append(m.msg_id)
        return replace(m, text=m.text + '-voice'), {}
    monkeypatch.setattr(image_caption, 'enrich_inbound', caption)
    monkeypatch.setattr(voice_asr, 'enrich_inbound', asr)
    seen = []
    result = bridge.handle_inbound(burst.combine(parts), dry=False, persist=False, send=None,
        reply_fn=lambda cid, hist: seen.extend(hist) or '收到')
    assert result['reply'] == '收到'
    assert captioned == heard == [str(i) for i in range(25)]
    assert seen[-1][1] == '\n'.join(f'part{i}-image-voice' for i in range(25))


def test_qq_new_inbound_cancels_old_draft(monkeypatch):
    runner = qq.OneBotQQRunner('ws://unused', dry=True)
    event = qq.make_private_event('10001', 'new')
    runner._burst_events.append(event)
    def process(inbound, **kwargs):
        assert kwargs['should_cancel']()
        return {'cancelled': True}
    monkeypatch.setattr(qq, 'process_inbound', process)
    runner._handle_event(qq.make_private_event('10001', 'old'))


def test_ilink_live_batch_preserves_order_and_other_peer(monkeypatch):
    from channels import ilink_weixin as iw
    runner = iw.ILinkWeixinRunner(dry=True, typing=False)
    runner._ensure_inbox()
    monkeypatch.setattr(iw, 'resolve_contact_id', lambda *a: 'demo_contact')
    parts = [Inbound('wechat_ilink', 'demo@im.wechat', f'part{i}', msg_id=str(i)) for i in range(3)]
    other = Inbound('wechat_ilink', 'other-peer', 'not this conversation')
    runner._inbox.put(parts[1])
    runner._inbox.put(other)
    runner._inbox.put(parts[2])
    monkeypatch.setattr(burst, 'collect', lambda first, drain, **kwargs: [first] + drain())
    seen = []
    def handle(msg, **kwargs):
        seen.append(msg)
        return {'reply': '收到'}
    monkeypatch.setattr(bridge, 'handle_inbound', handle)
    runner.handle_one(parts[0], batch=True)
    assert [m.text for m in seen] == ['part0\npart1\npart2']
    assert runner._inbox.get_nowait() is other


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))
