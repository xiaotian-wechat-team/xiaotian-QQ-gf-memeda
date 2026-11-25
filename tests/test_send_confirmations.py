"""Offline receipts and contact boundaries; temp storage, no model/network calls."""
import copy
import json
import pathlib
import sqlite3
import sys
import threading
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from channels.send_receipts import AckTracker, ReceiptJournal, DeliveryFailed, DeliveryUnknown
from channels import bridge, persona_map, onebot_qq as qq
from channels.types import Inbound
import brain
import memory


def receipt(echo, *, ok=True):
    return {'echo': echo, 'status': 'ok' if ok else 'failed', 'retcode': 0 if ok else 1200,
            'data': {'message_id': 42} if ok else None}


class Socket:
    def __init__(self, tracker, ack=True, ok=True):
        self.tracker, self.ack, self.ok, self.sent = tracker, ack, ok, []
    def send(self, raw):
        payload = json.loads(raw)
        self.sent.append(payload)
        if self.ack:
            self.tracker.resolve(receipt(payload['echo'], ok=self.ok))


def test_unique_echo_confirmed_and_explicit_failure(tmp_path):
    tracker = AckTracker(timeout=.01, journal=ReceiptJournal(tmp_path / 'acks.db'))
    ws = Socket(tracker)
    called = []
    for i in range(3):
        tracker.send(ws, {'action': 'send_private_msg'}, operation_id=str(i), on_confirmed=lambda: called.append(1))
    assert len({p['echo'] for p in ws.sent}) == 3
    assert called == [1, 1, 1]
    ws.ok = False
    with pytest.raises(DeliveryFailed):
        tracker.send(ws, {}, operation_id='reject', on_confirmed=lambda: called.append(0))
    assert called == [1, 1, 1]
    with tracker.journal.connect() as db:
        assert dict(db.execute('SELECT operation_id,status FROM receipts')) == {'0':'confirmed','1':'confirmed','2':'confirmed','reject':'failed'}


def test_timeout_blocks_repeat_and_late_duplicate_ack_finishes_once(tmp_path):
    tracker = AckTracker(timeout=.01, journal=ReceiptJournal(tmp_path / 'acks.db'))
    ws = Socket(tracker, ack=False)
    called = threading.Event()
    calls = []
    def confirmed():
        calls.append(1)
        called.set()
    with pytest.raises(DeliveryUnknown):
        tracker.send(ws, {}, operation_id='round', on_confirmed=confirmed)
    with pytest.raises(DeliveryUnknown):
        tracker.send(ws, {}, operation_id='round', on_confirmed=confirmed)
    assert len(ws.sent) == 1 and not calls
    ack = receipt(ws.sent[0]['echo'])
    assert tracker.resolve(ack)
    assert called.wait(1)
    assert not tracker.resolve(ack)
    assert calls == [1]
    tracker.send(ws, {}, operation_id='round')
    assert len(ws.sent) == 1


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import image_caption, voice_asr, reply_engagement, bubble_split
    monkeypatch.setattr('sleep_chat.scheduled_sleep', lambda *a, **k: False)
    cfg = {'contact': {'id': 'demo_contact'}, 'persona': {'primary_contact_id': 'demo_contact',
        'contact_aliases': {'qq_onebot:123': 'demo_contact', 'wechat_ilink:wx-peer': 'demo_contact'},
        'channels': {'qq': {'allow_user_ids': ['123']}}, 'classmate': {'enabled': True}}}
    monkeypatch.setattr(persona_map, 'load', lambda: cfg)
    import config
    monkeypatch.setattr(config, 'load', lambda: cfg)
    monkeypatch.setattr(memory, 'CFG', {**memory.CFG, 'db_path': str(tmp_path / 'chat.db')})
    monkeypatch.setattr(memory, 'DL', tmp_path / 'life')
    (memory.DL / 'contacts' / 'demo_contact').mkdir(parents=True)
    monkeypatch.setattr(brain, 'LAST', {})
    monkeypatch.setattr(brain, 'commit_delivery', Mock())
    monkeypatch.setattr(bridge, '_mem_release', lambda: None)
    monkeypatch.setattr(bridge, '_human_wait_for_send', lambda *a, **k: 0)
    monkeypatch.setattr(bridge, '_interruptible_sleep', lambda *a, **k: False)
    monkeypatch.setattr(image_caption, 'enrich_inbound', lambda m: (m, {}))
    monkeypatch.setattr(voice_asr, 'enrich_inbound', lambda m: (m, {}))
    monkeypatch.setattr(reply_engagement, 'select_response', lambda p, *a: p)
    monkeypatch.setattr(bubble_split, 'split_bubbles', lambda t, **k: t.split('\n') if t else [])
    monkeypatch.setattr(qq.urllib.request, 'urlopen', Mock(side_effect=AssertionError('network forbidden')))
    return tmp_path


def test_nonallowlisted_never_reads_media_generates_or_sends(isolated, monkeypatch):
    from channels import ilink_weixin as iw
    reply = Mock(side_effect=AssertionError('must not generate'))
    send = Mock(side_effect=AssertionError('must not send'))
    assert qq.normalize_event(qq.make_private_event('999', '[图片]')) is None
    out = bridge.handle_inbound(Inbound('qq_onebot', '999', 'hi'), dry=False, send=send, reply_fn=reply)
    assert out['blocked']
    with pytest.raises(PermissionError):
        qq.send_private_text_http('http://unused', '999', 'hi')
    runner = iw.ILinkWeixinRunner(dry=False)
    with pytest.raises(PermissionError):
        runner._send_text_checked('stranger', 'hi', 'token')
    assert not reply.called and not send.called


def test_partial_send_records_only_acknowledged_bubbles(isolated):
    pending = {'nickname': '小熊'}
    def reply(*args):
        brain.LAST.update(pending_delivery=copy.deepcopy(pending))
        return '第一句\n第二句'
    sent = []
    def send(out):
        sent.append(out.text)
        if len(sent) == 2:
            raise DeliveryFailed('rejected')
    out = bridge.handle_inbound(Inbound('qq_onebot', '123', '聊聊', msg_id='m1'), dry=False, send=send, reply_fn=reply)
    assert out['sent_bubbles'] == ['第一句'] and out['delivery_status'] == 'failed'
    with memory.db() as db:
        assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall() == [('第一句',)]
        assert db.execute("SELECT replied FROM messages WHERE sender='them'").fetchone()[0] == 0
    diary = next((memory.DL / 'contacts/demo_contact/diary').glob('*.md')).read_text()
    assert '第一句' in diary and '第二句' not in diary
    assert brain.commit_delivery.call_count == 1


def test_late_ack_records_only_once_and_completes_inbound(isolated):
    tracker = AckTracker(timeout=.01, journal=ReceiptJournal(isolated / 'ack.db'))
    ws = Socket(tracker, ack=False)
    def send(out):
        return tracker.send(ws, {}, operation_id=out.meta['delivery_id'], on_confirmed=out.meta['_on_confirmed'])
    out = bridge.handle_inbound(Inbound('qq_onebot', '123', '你好', msg_id='m2'), dry=False, send=send, reply_fn=lambda *a:'收到')
    assert out['delivery_status'] == 'unknown' and not out['sent_bubbles']
    with memory.db() as db:
        assert db.execute("SELECT count(*) FROM messages WHERE sender='me'").fetchone()[0] == 0
    tracker.resolve(receipt(ws.sent[0]['echo']))
    tracker._late.join()
    with memory.db() as db:
        assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall() == [('收到',)]
        assert db.execute("SELECT replied FROM messages WHERE sender='them'").fetchone()[0] == 1


def test_failed_image_does_not_reuse_success_caption(isolated, monkeypatch):
    monkeypatch.setattr(bridge, '_plan_want_image', lambda *a, **k: None)
    def reply(*args):
        brain.LAST['want_image'] = '自拍'
        return '图发了，给你看看'
    sent=[]
    out=bridge.handle_inbound(Inbound('qq_onebot','123','发张自拍',msg_id='photo'),dry=False,send=lambda o:sent.append(o),reply_fn=reply)
    assert out['image_generation_failed']
    assert not sent and out['reply'] == ''


def test_outbox_late_ack_updates_real_sent_prefix_without_retransmit(isolated, monkeypatch):
    import datetime as dt
    from channels import outbox_store
    import schedule_fidelity
    monkeypatch.setattr(outbox_store, 'DB_PATH', isolated / 'outbox.db')
    monkeypatch.setattr(outbox_store, 'LEGACY_JSON', {})
    monkeypatch.setattr(schedule_fidelity, 'resolve_slot', lambda **kw: {})
    monkeypatch.setattr(schedule_fidelity, 'outbox_item_stale', lambda *a, **kw: (False, ''))
    tracker = AckTracker(timeout=.01, journal=ReceiptJournal(isolated / 'acks.db'))
    ws = Socket(tracker, ack=False)
    def send(peer, text, **kw):
        return tracker.send(ws, {}, **kw)
    send.ack_aware = True
    qq._save_outbox({'items': [{'id':'delivery-1','peer':'123','contact':'demo_contact','text':'收到', 'status':'pending'}]})
    qq.flush_qq_outbox(send, peer_id='123', now=dt.datetime(2026,10,29,12))
    assert qq._load_outbox()['items'][0]['status'] == 'unknown'
    qq.flush_qq_outbox(send, peer_id='123', now=dt.datetime(2026,10,29,12))
    assert len(ws.sent) == 1
    tracker.resolve(receipt(ws.sent[0]['echo']))
    tracker._late.join()
    item = qq._load_outbox()['items'][0]
    assert item['status'] == 'sent' and item['sent_bubbles'] == ['收到']
    with memory.db() as db:
        assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall() == [('收到',)]


@pytest.mark.parametrize('status', ['failed', 'unknown'])
def test_proactive_partial_counts_one_round_and_only_confirmed_content(status, monkeypatch):
    import datetime as dt
    import test_proactive_unanswered as harness
    import proactive_sender as sender
    h = harness.Regression()
    h.setUp()
    try:
        now = dt.datetime(2026,10,27,17)
        q = {'items': []}
        item = sender.enqueue(q, '第一句\n第二句\n第三句', 'daily_chat', now,
            meta={'pending_delivery': {'nickname': '小熊'}, 'has_image': True})
        sender.save_q(q, h.q)
        committed = Mock()
        monkeypatch.setattr(brain, 'commit_delivery', committed)
        def send(text, **kw):
            # Reservation must exist before the first network operation.
            assert sender.load_q(h.q)['items'][0]['status'] == 'sending'
            kw['on_confirmed']('第一句')
            kw['on_confirmed']('第二句')
            return False, 'third not confirmed', {'delivery_status':status, 'sent_bubbles':['第一句','第二句']}
        with patch.object(sender, 'send_via_onebot', send):
            result = h.run_sender(now)
        assert result['delivery_status'] == ('unknown' if status=='unknown' else 'partial')
        saved = sender.load_q(h.q)['items'][0]
        assert saved['id'] == item['id'] and sender.due({'items':[saved]}, now) is None
        state = memory.read_state('demo_contact')
        assert state['proactive_count'] == 1
        assert (state.get('proactive_media') or {}).get('images', 0) == 0
        if status == 'failed':
            from channels import outbox_store
            queued = outbox_store.load('onebot')['items']
            assert queued[0]['text'] == '第三句'
        with memory.db() as db:
            assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall() == [('第一句',),('第二句',)]
        assert committed.call_count == 2
    finally:
        h.doCleanups()


def test_proactive_unknown_without_ack_has_no_memory_or_budget(monkeypatch):
    import datetime as dt
    import test_proactive_unanswered as harness
    import proactive_sender as sender
    h=harness.Regression(); h.setUp()
    try:
        now=dt.datetime(2026,10,27,17)
        q={'items':[]}; sender.enqueue(q,'今天有点忙','daily_chat',now); sender.save_q(q,h.q)
        with patch.object(sender,'send_via_onebot',lambda *a,**k:(False,'timeout',{'delivery_status':'unknown'})):
            result=h.run_sender(now)
        assert result['delivery_status']=='unknown'
        assert memory.read_state('demo_contact').get('proactive_count',0)==0
        with memory.db() as db:
            assert db.execute("SELECT count(*) FROM messages WHERE sender='me'").fetchone()[0]==0
        from channels import outbox_store
        assert not outbox_store.load('onebot')['items']
    finally:
        h.doCleanups()


def test_bridge_unknown_is_durable_across_a_replayed_event(isolated):
    calls=[]
    def send(out):
        calls.append(out.text)
        raise DeliveryUnknown('HTTP acknowledgement lost')
    msg=Inbound('wechat_ilink','wx-peer','你好',msg_id='stable-inbound')
    reply = Mock(return_value='收到')
    first=bridge.handle_inbound(msg,dry=False,send=send,reply_fn=reply)
    assert first['delivery_status']=='unknown'
    assert first['delivery_error']=='HTTP acknowledgement lost'
    reply.return_value='换个新草稿\n再补一句'
    result=bridge.handle_inbound(msg,dry=False,send=send,reply_fn=reply)
    assert result['duplicate'] and result['delivery_status']=='recovery_needed'
    assert not result['inbound_replied'] and result['sent_bubbles']==[]
    assert 'delivery_error' not in result
    assert reply.call_count==1
    assert calls==['收到']
    with memory.db() as db:
        assert db.execute("SELECT count(*) FROM messages WHERE sender='them'").fetchone()[0]==1
    assert not memory.read_state('demo_contact').get('last_reply_at')
    _assert_database_unlocked(isolated / 'chat.db')


def _assert_database_unlocked(path):
    # A held UNIQUE-conflict transaction must not turn the next receipt write
    # into a misleading timeout/unknown result. Probe a separate real connection.
    db=sqlite3.connect(path, timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE')
        db.rollback()
    finally:
        db.close()


def _make_recovery_due():
    with memory.db() as db:
        db.execute('UPDATE inbound_turns SET next_attempt=0,lease=0')


def test_failed_model_turn_recovers_without_new_inbound_or_duplicate_emotion(isolated):
    from channels.turn_store import TurnStore
    import emotion
    send = Mock()
    model = Mock(side_effect=[TimeoutError('provider offline'), '恢复啦'])
    msg = Inbound('wechat_ilink', 'wx-peer', '在吗', msg_id='model-retry')
    with patch.object(emotion, 'appraise', wraps=emotion.appraise) as appraisal:
        with pytest.raises(TimeoutError):
            bridge.handle_inbound(msg, dry=False, send=send, reply_fn=model)
        assert not send.called
        _make_recovery_due()
        assert len(TurnStore().pending('wechat_ilink')) == 1
        recovered = bridge.recover_pending('wechat_ilink', send=send, reply_fn=model)
        assert recovered[0]['delivery_status'] == 'confirmed'
        assert appraisal.call_count == 1
    assert model.call_count == 2 and send.call_count == 1
    assert not TurnStore().pending('wechat_ilink')
    with memory.db() as db:
        assert db.execute("SELECT text,replied FROM messages WHERE sender='them'").fetchall() == [('在吗', 1)]
    _assert_database_unlocked(isolated / 'chat.db')


@pytest.mark.parametrize('failed_step', ['database', 'diary', 'state'])
def test_confirmed_delivery_replays_bookkeeping_and_immutable_unsent_suffix(isolated, failed_step):
    from channels.turn_store import TurnStore
    msg = Inbound('wechat_ilink', 'wx-peer', '聊聊', msg_id='bookkeeping-retry')
    model = Mock(return_value='收到\n继续聊')
    sent = []
    def send(out):
        sent.append(out.text)
    original_insert = memory.insert
    original_diary = memory.append_delivery_diary
    original_mutate = memory.mutate_state
    def insert(db, cid, sender, *a, **kw):
        if sender == 'me' and failed_step == 'database':
            raise sqlite3.OperationalError('temporary disk failure')
        return original_insert(db, cid, sender, *a, **kw)
    def diary(*a, **kw):
        if failed_step == 'diary':
            raise OSError('temporary diary failure')
        return original_diary(*a, **kw)
    def mutate(cid, change):
        if failed_step == 'state' and change.__name__ == 'state_patch':
            raise OSError('temporary state failure')
        return original_mutate(cid, change)
    with patch.object(memory, 'insert', insert), patch.object(memory, 'append_delivery_diary', diary), \
            patch.object(memory, 'mutate_state', mutate):
        first = bridge.handle_inbound(msg, dry=False, send=send, reply_fn=model)
    assert first['delivery_status'] == 'bookkeeping_pending' and sent == ['收到']
    _make_recovery_due()
    recovered = bridge.recover_pending('wechat_ilink', send=send,
                                      reply_fn=Mock(side_effect=AssertionError('must reuse plan')))
    assert recovered[0]['delivery_status'] == 'confirmed'
    assert sent == ['收到', '继续聊'] and model.call_count == 1
    assert not TurnStore().pending('wechat_ilink')
    with memory.db() as db:
        assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall() == [('收到',), ('继续聊',)]
        assert db.execute("SELECT replied FROM messages WHERE sender='them'").fetchone() == (1,)
    contents = ''.join(p.read_text() for p in (memory.DL/'contacts/demo_contact/diary').glob('*.md'))
    assert contents.count('我回「收到」') == contents.count('我回「继续聊」') == 1
    before = memory.read_state('demo_contact')
    bridge.handle_inbound(msg, dry=False, send=send, reply_fn=model)
    assert memory.read_state('demo_contact') == before and len(sent) == 2


def test_crash_after_transport_confirmation_recovers_without_resending(isolated):
    from channels.turn_store import TurnStore
    msg = Inbound('wechat_ilink', 'wx-peer', '你好', msg_id='crash-after-ack')
    send = Mock()
    with patch.object(memory, 'append_delivery_diary', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            bridge.handle_inbound(msg, dry=False, send=send, reply_fn=lambda *a: '收到')
    assert send.call_count == 1
    _make_recovery_due()  # another process acquires the expired worker lease
    result = bridge.recover_pending('wechat_ilink', send=send)
    assert result[0]['delivery_status'] == 'confirmed' and send.call_count == 1
    assert not TurnStore().pending('wechat_ilink')


def test_recovered_bookkeeping_uses_receipt_time_not_restart_time(isolated):
    import datetime as dt
    msg = Inbound('wechat_ilink', 'wx-peer', '你好', msg_id='receipt-time')
    send = Mock()
    with patch.object(memory, 'append_delivery_diary', side_effect=OSError('disk unavailable')):
        bridge.handle_inbound(msg, dry=False, send=send, reply_fn=lambda *a: '收到')
    actual_send = dt.datetime.now().replace(microsecond=0)-dt.timedelta(hours=2)
    with memory.db() as db:
        db.execute("UPDATE receipts SET updated=? WHERE status='confirmed'", (actual_send.timestamp(),))
    _make_recovery_due()
    bridge.recover_pending('wechat_ilink', send=send)
    assert send.call_count == 1
    assert memory.read_state('demo_contact')['last_reply_at'] == actual_send.strftime('%Y-%m-%d %H:%M:%S')


def test_unknown_delivery_is_never_automatically_recovered(isolated):
    from channels.turn_store import TurnStore
    send = Mock(side_effect=DeliveryUnknown('response lost'))
    msg = Inbound('wechat_ilink', 'wx-peer', '你好', msg_id='unknown-no-resend')
    bridge.handle_inbound(msg, dry=False, send=send, reply_fn=lambda *a: '收到')
    _make_recovery_due()
    assert TurnStore().pending('wechat_ilink') == []
    assert bridge.recover_pending('wechat_ilink', send=send) == []
    assert send.call_count == 1


def test_retry_generation_can_finish_with_skip(isolated):
    from channels.turn_store import TurnStore
    msg = Inbound('wechat_ilink', 'wx-peer', '在吗', msg_id='retry-then-skip')
    with pytest.raises(TimeoutError):
        bridge.handle_inbound(msg, dry=False, send=Mock(), reply_fn=Mock(side_effect=TimeoutError))
    _make_recovery_due()
    send = Mock()
    bridge.recover_pending('wechat_ilink', send=send, reply_fn=lambda *a: '[SKIP]')
    _make_recovery_due()
    assert not TurnStore().pending('wechat_ilink') and not send.called


def test_late_ack_bookkeeping_failure_recovers_without_resending(isolated):
    tracker = AckTracker(timeout=.01, journal=ReceiptJournal(isolated/'ack.db'))
    socket = Socket(tracker, ack=False)
    def send(out):
        return tracker.send(socket, {}, operation_id=out.meta['delivery_id'], on_confirmed=out.meta['_on_confirmed'])
    msg = Inbound('qq_onebot', '123', '你好', msg_id='late-bookkeeping')
    bridge.handle_inbound(msg, dry=False, send=send, reply_fn=lambda *a: '收到')
    with patch.object(memory, 'append_delivery_diary', side_effect=OSError('disk unavailable')):
        tracker.resolve(receipt(socket.sent[0]['echo']))
        tracker._late.join()
    _make_recovery_due()
    result = bridge.recover_pending('qq_onebot', send=send)
    assert result[0]['delivery_status'] == 'confirmed' and len(socket.sent) == 1
    with memory.db() as db:
        assert db.execute("SELECT replied FROM messages WHERE sender='them'").fetchone() == (1,)


def test_confirmed_photo_bookkeeping_is_repaired_even_after_slot_expires(isolated, monkeypatch):
    import photo_schedule
    image = isolated/'photo.png'
    image.write_bytes(b'fixture')
    monkeypatch.setattr(bridge, '_plan_want_image', lambda *a, **k: {
        'paths': [str(image)], 'texts': [], 'photo_schedule': {'fixture': True}})
    def reply(*a):
        brain.LAST['want_image'] = '自拍'
        return ''
    send = Mock()
    gate = Mock()
    monkeypatch.setattr(photo_schedule, 'check', gate)
    msg = Inbound('wechat_ilink', 'wx-peer', '发张自拍', msg_id='photo-ack-before-expiry')
    with patch.object(memory, 'append_delivery_diary', side_effect=OSError('disk unavailable')):
        first = bridge.handle_inbound(msg, dry=False, send=send, reply_fn=reply)
    assert first['delivery_status'] == 'bookkeeping_pending'
    gate.side_effect = ValueError('photo slot expired')
    _make_recovery_due()
    result = bridge.recover_pending('wechat_ilink', send=send)
    assert result[0]['delivery_status'] == 'confirmed'
    assert send.call_count == gate.call_count == 1


def test_held_but_confirmed_unbookkept_turn_is_reconciled_after_restart(isolated):
    msg = Inbound('wechat_ilink', 'wx-peer', '你好', msg_id='held-ack-before-crash')
    send = Mock()
    with patch.object(memory, 'append_delivery_diary', side_effect=OSError('disk unavailable')):
        bridge.handle_inbound(msg, dry=False, send=send, reply_fn=lambda *a: '收到')
    # Reproduce timeout/late-ACK crash ordering: transport confirmed while the
    # turn still has the persisted unknown/held status from its timeout.
    with memory.db() as db:
        db.execute("UPDATE inbound_turns SET phase='held',lease=0,next_attempt=0")
    result = bridge.recover_pending('wechat_ilink', send=send)
    assert result[0]['delivery_status'] == 'confirmed' and send.call_count == 1


def test_recovery_repairs_bookkeeping_but_drops_suffix_covered_by_new_reply(isolated):
    sent = []
    def send(out):
        sent.append(out.text)
    old = Inbound('wechat_ilink', 'wx-peer', '聊聊', msg_id='older-plan')
    with patch.object(memory, 'append_delivery_diary', side_effect=OSError('disk unavailable')):
        bridge.handle_inbound(old, dry=False, send=send, reply_fn=lambda *a: '收到\n旧的后半句')
    bridge.handle_inbound(Inbound('wechat_ilink', 'wx-peer', '不用了', msg_id='newer-plan'),
                          dry=False, send=send, reply_fn=lambda *a: '好呀')
    _make_recovery_due()
    bridge.recover_pending('wechat_ilink', send=send)
    assert sent == ['收到', '好呀']
    diary = ''.join(p.read_text() for p in (memory.DL/'contacts/demo_contact/diary').glob('*.md'))
    assert diary.count('我回「收到」') == 1 and '旧的后半句' not in diary


def test_turn_claim_is_exclusive_and_stale_owner_cannot_overwrite(isolated):
    from concurrent.futures import ThreadPoolExecutor
    from channels.turn_store import TurnStore
    msg = Inbound('wechat_ilink', 'wx-peer', '你好', msg_id='exclusive')
    store = TurnStore()
    with ThreadPoolExecutor(4) as workers:
        turns = list(workers.map(lambda _: store.claim('same-key', msg, create=True), range(4)))
    claimed = [turn for turn in turns if turn]
    assert len(claimed) == 1
    _make_recovery_due()
    replacement = TurnStore().claim('same-key', msg)
    replacement.update('complete')
    claimed[0].update('retry_generate')
    with memory.db() as db:
        assert db.execute("SELECT phase FROM inbound_turns WHERE key='same-key'").fetchone() == ('complete',)


@pytest.mark.parametrize('transport', ['ws', 'http'])
def test_onebot_worker_recovers_failed_model_without_new_event(isolated, transport):
    msg = Inbound('qq_onebot', '123', '在吗', msg_id='runner-recovery', bot_account_id='bot-1')
    with pytest.raises(TimeoutError):
        bridge.handle_inbound(msg, dry=False, send=Mock(), reply_fn=Mock(side_effect=TimeoutError))
    _make_recovery_due()
    if transport == 'ws':
        runner = qq.OneBotQQRunner('ws://unused', dry=False, reply_fn=lambda *a: '恢复啦')
        runner._login_info = {'user_id':'bot-1'}
    else:
        runner = qq.OneBotHttpRunner('127.0.0.1:0', 'http://unused', dry=False, reply_fn=lambda *a: '恢复啦')
    with patch.object(runner, '_send_outbound', Mock()) as send:
        runner._recover_pending()
        assert send.call_count == 1
        runner.dry = True
        with patch.object(bridge, 'recover_pending') as recovery:
            runner._recover_pending()
            assert not recovery.called


def test_ilink_worker_recovers_on_restart_without_new_event(isolated, monkeypatch):
    from types import SimpleNamespace
    from channels import ilink_weixin as iw
    msg = Inbound('wechat_ilink', 'wx-peer', '在吗', msg_id='ilink-runner-restart')
    with pytest.raises(TimeoutError):
        bridge.handle_inbound(msg, dry=False, send=Mock(), reply_fn=Mock(side_effect=TimeoutError))
    _make_recovery_due()
    runner = iw.ILinkWeixinRunner(dry=False)
    bot = SimpleNamespace(messages=lambda: iter(()), stop=Mock())
    monkeypatch.setattr(runner, '_build_bot', lambda: bot)
    monkeypatch.setattr(iw, '_warmup_mem_async', lambda: None)
    monkeypatch.setattr(brain, 'reply', lambda *a, **k: '恢复啦')
    send = Mock()
    monkeypatch.setattr(runner, '_send', send)
    runner.run()
    assert send.call_count == 1
    with memory.db() as db:
        assert db.execute("SELECT replied FROM messages WHERE sender='them'").fetchone() == (1,)


def test_bridge_search_diagnostics_contain_only_summary_and_unsigned_links(isolated):
    def reply(*a):
        brain.LAST['search']={'status':'ok','provider':'browser','query':'private query',
            'sources':[{'url':'https://www.xiaohongshu.com/explore/note1?xsec_token=secret#fragment',
                        'title':'private title','snippet':'full source text','bsk_signature':'secret'}]}
        return '查到了'
    msg=Inbound('wechat_ilink','wx-peer','帮我搜一下',msg_id='search-summary')
    out=bridge.handle_inbound(msg,dry=True,reply_fn=reply)
    assert out['search_evidence']=={'status':'ok','provider':'browser',
        'source_urls':['https://www.xiaohongshu.com/explore/note1']}
    brain.LAST['search']={'status':'unavailable','provider':'browser','reason':'browser_skill_timeout'}
    out=bridge.handle_inbound(msg,dry=True,reply_fn=lambda *a:'暂时没查到')
    assert out['search_evidence']['reason']=='browser_skill_timeout'
    brain.LAST.clear()
    out=bridge.handle_inbound(msg,dry=True,reply_fn=lambda *a:'嗯')
    assert 'search_evidence' not in out


@pytest.mark.parametrize('partial_status', [None, 'failed', 'unknown'])
def test_duplicate_inbound_never_generates_or_appends_a_different_draft(isolated, partial_status):
    reply=Mock(return_value='第一句' if partial_status is None else '第一句\n第二句')
    sent=[]
    def send(out):
        sent.append(out.text)
        if len(sent)==2 and partial_status:
            error=DeliveryUnknown if partial_status=='unknown' else DeliveryFailed
            raise error('deliberate second-bubble failure')
    msg=Inbound('wechat_ilink','wx-peer','聊聊',msg_id='duplicate-different-draft')
    first=bridge.handle_inbound(msg,dry=False,send=send,reply_fn=reply)
    assert first['delivery_status']==(partial_status or 'confirmed')
    assert first['sent_bubbles']==['第一句']
    if partial_status:
        assert first['delivery_error']=='deliberate second-bubble failure'
    before_sent=list(sent)
    before_commits=brain.commit_delivery.call_count
    before_state=memory.read_state('demo_contact')
    reply.return_value='另一个新句子\n新续句\n又一个新续句'
    duplicate=bridge.handle_inbound(msg,dry=False,send=send,reply_fn=reply)
    assert duplicate['duplicate']
    assert duplicate['delivery_status']==('recovery_needed' if partial_status else 'confirmed')
    assert duplicate['inbound_replied'] is (partial_status is None)
    assert duplicate['sent_bubbles']==[] and duplicate['reply']==''
    assert reply.call_count==1 and sent==before_sent
    assert brain.commit_delivery.call_count==before_commits
    assert memory.read_state('demo_contact')==before_state
    with memory.db() as db:
        assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall()==[('第一句',)]
        assert db.execute("SELECT replied FROM messages WHERE sender='them'").fetchall()==[(0 if partial_status else 1,)]
    _assert_database_unlocked(isolated / 'chat.db')


def test_ilink_audio_needs_a_media_receipt(isolated,monkeypatch):
    from channels import ilink_weixin as iw
    import weixin_ilink.api as api
    runner=iw.ILinkWeixinRunner(dry=False)
    monkeypatch.setattr(api,'send_message',lambda *a,**k:{'ret':0,'message_id':9})
    runner._checked_media_call(4,lambda:api.send_message(None,{'item_list':[{'type':4}]}))
    with pytest.raises(DeliveryUnknown):
        runner._checked_media_call(4,lambda:api.send_message(None,{'item_list':[{'type':1}]}))
    monkeypatch.setattr(api,'send_message',lambda *a,**k:{'ret':-2,'errmsg':'rejected'})
    with pytest.raises(DeliveryFailed):
        runner._checked_media_call(3,lambda:api.send_message(None,{'item_list':[{'type':3}]}))


def test_proactive_restart_replays_confirmed_bookkeeping_never_the_send(monkeypatch):
    import datetime as dt
    import test_proactive_unanswered as harness
    import proactive_sender as sender
    import proactive_beats
    h=harness.Regression(); h.setUp()
    try:
        now=dt.datetime(2026,10,27,17)
        q={'items':[], 'next_consider_at':'2026-10-27 18:00:00'}
        sender.enqueue(q,'第一句\n未发送的第二句','daily_chat',now)
        sender.save_q(q,h.q)
        def send(text,**kw):
            kw['on_confirmed']('第一句')
            raise AssertionError('simulated crash must stop before next bubble')
        with patch.object(sender,'send_via_onebot',send), patch.object(sender,'_persist_confirmed_piece',side_effect=RuntimeError('simulated disk interruption')):
            with pytest.raises(RuntimeError):
                h.run_sender(now)
        assert sender.load_q(h.q)['items'][0]['status']=='sending'
        with patch.object(proactive_beats,'due_beats',return_value=[]):
            h.run_sender(now+dt.timedelta(minutes=1))
            h.run_sender(now+dt.timedelta(minutes=2))
        assert not h.sent
        saved=sender.load_q(h.q)['items'][0]
        assert saved['status']=='unknown' and saved['accounted']
        assert memory.read_state('demo_contact')['proactive_count']==1
        with memory.db() as db:
            assert db.execute("SELECT text FROM messages WHERE sender='me'").fetchall()==[('第一句',)]
    finally:
        h.doCleanups()
