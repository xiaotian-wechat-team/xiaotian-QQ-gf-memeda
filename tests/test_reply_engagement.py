"""Reciprocal pacing with fake models/transports and temporary contact storage."""
import datetime as dt
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import brain
import memory
import reply_engagement as eng
import sleep_chat as sc
import schedule_fidelity as sf
from channels import bridge
from channels.reply_burst import combine
from channels.types import Inbound
from test_channel_review_regressions import isolated

NOW = dt.datetime(2026, 10, 29, 14)
PEER = '10001'
HUMAN_WAIT = bridge._human_wait_for_send
RESOLVE_SLOT = sf.resolve_slot
SLEEP_SPLIT_GAP = bridge._sleep_split_gap


def messages(n, now=NOW, spacing=0, channel='qq_onebot', prefix='m'):
    return [Inbound(channel, PEER if channel == 'qq_onebot' else 'demo@im.wechat', f'内容{i}', msg_id=f'{prefix}{i}',
                    ts=(now-dt.timedelta(seconds=(n-1-i)*spacing)).isoformat()) for i in range(n)]


def freeze_bridge(monkeypatch, now):
    class FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=eng.TZ).astimezone(tz)
    monkeypatch.setattr(bridge, 'datetime', SimpleNamespace(datetime=FrozenDatetime))
    # bridge scheduling paths use timeutil.now_local (Asia/Shanghai); freeze it too
    import timeutil as _tu
    monkeypatch.setattr(_tu, 'now_local', lambda cfg=None: now)


@pytest.fixture(autouse=True)
def normal_chat(monkeypatch):
    freeze_bridge(monkeypatch, NOW)
    monkeypatch.setattr(sc, 'peek_mode', lambda *a, **k: ('normal', {}))
    monkeypatch.setattr(sc, 'in_pre_sleep_window', lambda *a, **k: False)
    monkeypatch.setattr(sc, 'note_inbound', lambda *a, **k: ('normal', {}, 'normal'))
    monkeypatch.setattr(bridge, '_interruptible_sleep', lambda sec, cancel=None: bool(cancel and cancel()))


def test_quantity_and_frequency_both_matter_and_decay():
    dense = eng.record({}, messages(6), NOW)
    sparse = eng.record({}, messages(6, spacing=90), NOW)
    assert eng.profile(dense, NOW)['level'] == 'dense'
    assert eng.profile(sparse, NOW)['level'] == 'normal'
    assert eng.profile(dense, NOW+dt.timedelta(seconds=60))['level'] == 'active'
    assert eng.profile(dense, NOW+dt.timedelta(seconds=120))['level'] == 'normal'
    assert eng.profile(dense, NOW+dt.timedelta(seconds=181))['recent_messages'] == 0


def test_replayed_messages_do_not_increase_score_or_refresh_their_age():
    parts = messages(6)
    initial = eng.record({}, parts, NOW)
    later = NOW+dt.timedelta(seconds=60)
    replay = eng.record(initial, parts, later)
    assert replay == initial
    assert eng.profile(replay, later)['score'] == 3


def test_old_backlog_bad_and_future_timestamps_do_not_inflate_demand():
    parts = messages(6, NOW-dt.timedelta(hours=2))
    parts += messages(6, NOW+dt.timedelta(hours=2), prefix='future')
    parts.append(Inbound('qq_onebot', PEER, '坏时间', msg_id='bad', ts='invalid'))
    assert eng.profile(eng.record({}, parts, NOW), NOW)['level'] == 'normal'


def test_newlines_are_one_message_and_storage_is_bounded():
    one = Inbound('qq_onebot', PEER, '\n'.join(['一句话']*30), msg_id='one')
    assert eng.profile(eng.record({}, [one], NOW), NOW)['recent_messages'] == 1
    blob = eng.record({}, messages(160), NOW)
    assert len(blob['events']) == 128
    assert '内容' not in str(blob)


def test_disabled_feature_has_normal_pace():
    blob = eng.record({}, messages(10), NOW)
    assert eng.profile(blob, NOW, {'reply': {'engagement': {'enabled': False}}})['level'] == 'normal'


def test_channel_native_timestamps_count_real_messages():
    from channels.onebot_qq import normalize_event, make_private_event
    parts = []
    for i in range(6):
        event = make_private_event(PEER, f'最近的消息{i}')
        event.update(time=int(NOW.timestamp())-i, message_id=i+1)
        parts.append(normalize_event(event))
    assert eng.profile(eng.record({}, parts, NOW), NOW)['level'] == 'dense'
    for part in parts:
        part.ts = str(int(NOW.timestamp()*1000))
    assert eng.profile(eng.record({}, parts, NOW), NOW)['recent_messages'] == 6


def test_bridge_passes_unmerged_count_to_brain(monkeypatch):
    seen = []
    def reply(cid, hist, **kwargs):
        brain.LAST.clear()
        seen.append(kwargs['activity'])
        return '接住啦'
    monkeypatch.setattr(brain, 'reply', reply)
    bridge.handle_inbound(combine(messages(6)), dry=True)
    assert seen[0]['user_bubbles'] == 6
    assert seen[0]['level'] == 'dense'


def test_real_count_survives_burst_merge_and_alternating_replies(monkeypatch):
    def reply(cid, hist):
        brain.LAST.clear()
        return '接住你了'
    sent = []
    now = NOW
    # Every inbound has an intervening bot reply; heat must survive the turn boundary.
    for part in messages(6, now, spacing=1):
        result = bridge.handle_inbound(part, dry=False, send=sent.append, reply_fn=reply)
    assert len(sent) == 6
    assert result['reply_engagement']['level'] == 'dense'
    assert result['reply_engagement']['user_bubbles'] == 1
    assert result['reply_engagement']['recent_messages'] == 6
    batch = combine(messages(6, NOW, prefix='burst'))
    result = bridge.handle_inbound(batch, dry=False, send=sent.append, reply_fn=reply)
    assert result['reply_engagement']['user_bubbles'] == 6
    assert result['reply_engagement']['recent_messages'] == 12
    # Ordinary replies don't consume proactive contact rounds.
    assert memory.read_state('demo_contact').get('proactive_count', 0) == 0


@pytest.mark.parametrize('channel', ['qq_onebot', 'wechat_ilink'])
def test_continuous_inbound_gets_feedback_and_new_message_is_kept(monkeypatch, channel):
    monkeypatch.setattr(bridge, 'resolve_contact_id', lambda *a: 'demo_contact')
    pending, sent = [], []
    batch = combine(messages(6, channel=channel))
    def reply(cid, hist):
        brain.LAST.clear()
        pending.append(messages(1, channel=channel, prefix='next')[0])
        return '第一件我听明白了\n第二件也有意思\n你接着说'
    result = bridge.handle_inbound(batch, dry=False, send=sent.append, reply_fn=reply,
                                   should_cancel=lambda: bool(pending))
    assert not result.get('cancelled')
    assert len(sent) == 3 and len(pending) == 1
    followup = pending.pop()
    next_result = bridge.handle_inbound(followup, dry=False, send=sent.append,
        reply_fn=lambda cid, hist: '这件我也想听', should_cancel=lambda: bool(pending))
    assert next_result['reply'] == '这件我也想听'
    with memory.db() as conn:
        inputs = [text for sender, text in memory.history(conn, 'demo_contact') if sender == 'them']
    assert inputs == [batch.text, followup.text]


@pytest.mark.parametrize('case', ['normal', 'sleep', 'image', 'voice'])
def test_other_turns_still_yield_to_new_input(monkeypatch, case):
    count = 1 if case == 'normal' else 6
    if case == 'sleep':
        monkeypatch.setattr(sc, 'note_inbound', lambda *a, **k: ('pre_sleep', {}, 'pre_sleep'))
    def reply(cid, hist):
        brain.LAST.clear()
        if case == 'image':
            brain.LAST['want_image'] = '校园'
        if case == 'voice':
            brain.LAST['want_voice'] = True
        return '听到了'
    sent = []
    result = bridge.handle_inbound(combine(messages(count)), dry=False,
        send=sent.append, reply_fn=reply, should_cancel=lambda: True)
    assert result['cancelled'] and not sent


def test_dry_preview_keeps_real_state_unchanged(tmp_path):
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = bridge.handle_inbound(combine(messages(6)), dry=True,
        reply_fn=lambda cid, hist: '好呀')
    assert result['reply_engagement']['user_bubbles'] == 6
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('backend', ['deepseek', 'cursor_agent'])
def test_model_sees_budget_before_generation_and_all_burst_content(monkeypatch, backend):
    import weather
    from bubble_split import MAX_BUBBLES
    captured = []
    monkeypatch.setattr(brain, 'CFG', {**brain.CFG, 'llm': {'backend': backend},
                                     'life_calendar': {'sync_on_reply': False}})
    monkeypatch.setattr(brain, '_nick', lambda *a, **k: None)
    monkeypatch.setattr(brain, 'memory_block', lambda *a, **k: '')
    monkeypatch.setattr(brain, 'state_block', lambda *a, **k: '')
    monkeypatch.setattr(weather, 'needs', lambda *a: False)
    raw = '\n'.join(f'内容{i}我听到了' for i in range(6))
    monkeypatch.setitem(brain.BACKENDS, backend, lambda prompt: captured.append(prompt) or raw)
    activity = {**eng.profile(eng.record({}, messages(6), NOW), NOW), 'user_bubbles': 6}
    answer = brain.reply('demo_contact', [('them', combine(messages(6)).text)], dry=True, now=NOW, activity=activity)
    assert 6 <= brain.LAST['reply_bubble_budget'] <= MAX_BUBBLES
    assert brain.LAST['user_bubbles'] == 6
    assert len(answer.splitlines()) == 6
    prompt = str(captured[0])
    assert '本轮他发了6条消息' in prompt and '120字' in prompt
    assert '自然' in prompt and all(f'内容{i}' in prompt for i in range(6))


def test_dense_turn_still_obeys_sleep_budget(monkeypatch):
    import weather
    monkeypatch.setattr(sc, 'peek_mode', lambda *a, **k: ('pre_sleep', {}))
    monkeypatch.setattr(brain, 'CFG', {**brain.CFG, 'llm': {'backend': 'test_stub'}})
    monkeypatch.setattr(brain, '_nick', lambda *a, **k: None)
    monkeypatch.setattr(brain, 'build_prompt', lambda *a, **k: '')
    monkeypatch.setattr(weather, 'needs', lambda *a: False)
    monkeypatch.setitem(brain.BACKENDS, 'test_stub', lambda prompt: '听着呢\n我也想聊\n慢慢说\n接着呢\n还有呢\n说吧')
    answer = brain.reply('demo_contact', [('them', '想聊聊')], dry=True, now=NOW,
                         activity={'level': 'dense', 'user_bubbles': 6})
    assert brain.LAST['reply_bubble_budget'] <= 3
    assert len(answer.splitlines()) <= 3


def test_active_and_dense_waits_are_shorter_and_model_time_counts(monkeypatch):
    import schedule
    monkeypatch.setattr(schedule, 'reply_delay', lambda now: 30)
    normal = HUMAN_WAIT(0, '现在想聊好多好多事情', now=NOW)
    assert normal > HUMAN_WAIT(0, '现在想聊好多好多事情', now=NOW, activity={'level': 'active'})
    assert HUMAN_WAIT(0, '现在想聊好多好多事情', now=NOW, activity={'level': 'dense'}) <= 1
    assert HUMAN_WAIT(30, '现在想聊好多好多事情', now=NOW, activity={'level': 'dense'}) == 0


def test_active_bubble_spacing_stays_short_with_natural_variation():
    import random
    from bubble_split import bubble_gap_sec
    for level, lo, hi in [('active', .5, 1.2), ('dense', .35, .85)]:
        rng = random.Random(17)
        gaps = [bubble_gap_sec('听着呢', rng=rng, **eng.pacing({'level': level})['gap']) for _ in range(20)]
        assert all(lo <= gap <= hi for gap in gaps)
        assert len(set(gaps)) > 1


def internship_state(now=NOW, status=None):
    slot = {'start': '09:00', 'end': '23:59', 'activity': '实习整理素材', 'bucket': 'work_internship'}
    if status:
        slot['execution_status'] = status
    return {'life_today': {'date': now.date().isoformat(), 'slots': [slot]}}


def select(activity=None, st=None, now=NOW, cfg=None, text='还想和你聊聊'):
    return eng.select_response(activity or {'level': 'dense'}, st or {}, 'demo_contact', text, now, cfg)


@pytest.mark.parametrize('probability,expected', [(0., 'usual'), (1., 'enthusiastic')])
def test_daytime_actual_internship_has_two_content_and_pacing_branches(monkeypatch, probability, expected):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    choice = select(st=internship_state(), cfg={'reply': {'engagement': {
        'internship_enthusiastic_probability': probability}}})
    assert choice['selector_applied'] and choice['response_mode'] == expected
    pace = eng.pacing(choice)
    assert pace['max_chars'] == (120 if probability else 80)
    assert ('wait_cap_sec' in pace) == bool(probability)
    prompt = eng.prompt_block({**choice, 'user_bubbles': 1, 'max_bubbles': 8, 'max_chars': pace['max_chars']})
    assert ('本轮必须热情陪聊' in prompt) == bool(probability)
    assert ('沿用常规回复节奏' in prompt) != bool(probability)


def test_internship_probability_is_thirty_percent(monkeypatch):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    assert eng.options()['internship_enthusiastic_probability'] == .3
    choices = [select({'level': 'active', 'session_started_at': NOW.timestamp()+i}, internship_state())
               for i in range(1000)]
    assert all(c['selector_applied'] and c['enthusiastic_probability'] == .3 for c in choices)
    assert 250 < sum(c['response_mode'] == 'enthusiastic' for c in choices) < 350


def test_selector_stays_stable_as_events_expire_during_continuous_chat(monkeypatch):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    blob = eng.record({}, messages(6), NOW)
    first = select(eng.profile(blob, NOW), internship_state())
    # 10 minutes of continuous chat crosses several score-window boundaries.
    for minute in range(1, 11):
        now = NOW+dt.timedelta(minutes=minute)
        blob = eng.record(blob, messages(6, now, prefix=f'b{minute}'), now)
        choice = select(eng.profile(blob, now), internship_state(), now)
        assert choice['selector_draw'] == first['selector_draw']
        assert choice['response_mode'] == first['response_mode']
    later = now+dt.timedelta(seconds=181)
    blob = eng.record(blob, messages(6, later, prefix='new'), later)
    new_choice = select(eng.profile(blob, later), internship_state(), later)
    assert new_choice['selector_draw'] != first['selector_draw']


@pytest.mark.parametrize('kind', ['campus_life', 'school_class', 'weekend_home', 'missing', 'pending', 'cancelled', 'stale'])
def test_only_current_executable_internship_enters_selector(monkeypatch, kind):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    st = internship_state()
    if kind in ('pending', 'cancelled'):
        st['life_today']['slots'][0]['execution_status'] = kind
    elif kind == 'missing':
        import life_calendar
        monkeypatch.setattr(life_calendar, 'slot_at', lambda now: None)
        st = {}
    else:
        if kind == 'stale':
            st['life_now'] = {**st['life_today']['slots'][0], 'date': '2026-10-28'}
        st['life_today']['slots'][0].update(bucket='weekend_home' if kind == 'stale' else kind,
                                          activity='日常事务，比较忙')
    choice = select(st=st)
    assert not choice['selector_applied'] and choice['response_mode'] == 'enthusiastic'


def test_low_frequency_daytime_does_not_roll(monkeypatch):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    choice = select({'level': 'normal'}, internship_state())
    assert not choice['selector_applied'] and choice['response_mode'] == 'usual'


@pytest.mark.parametrize('now,night', [
    (NOW.replace(hour=16, minute=59), False), (NOW.replace(hour=17), True),
    (NOW.replace(hour=23), True), (NOW.replace(hour=0), True),
    (NOW.replace(hour=6, minute=59), True), (NOW.replace(hour=7), False),
    (NOW.replace(hour=9, tzinfo=dt.timezone.utc), True),  # 17:00 Shanghai
])
def test_evening_precedes_work_lottery_and_low_message_count(monkeypatch, now, night):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    choice = select({'level': 'normal'}, internship_state(), now)
    assert not choice['selector_applied']
    assert (choice['response_mode'] == 'enthusiastic') == night
    if night:
        assert eng.effective_sleep_style('asleep', choice) == 'normal'
        assert eng.pacing(choice)['budget_floor'] == 8


def test_evening_overrides_daytime_internship_usual_choice(monkeypatch):
    monkeypatch.setattr(sf, 'resolve_slot', RESOLVE_SLOT)
    cfg = {'reply': {'engagement': {'internship_enthusiastic_probability': 0}}}
    before = select(st=internship_state(), now=NOW.replace(hour=16, minute=59), cfg=cfg)
    after = select(st=internship_state(), now=NOW.replace(hour=17), cfg=cfg)
    assert before['selector_applied'] and before['response_mode'] == 'usual'
    assert not after['selector_applied'] and after['response_mode'] == 'enthusiastic'


@pytest.mark.parametrize('text,expected', [
    ('晚安，我先睡了', 'closing'), ('今天先聊到这', 'closing'),
    ('别说晚安，我还想聊', 'enthusiastic'), ('先别睡，继续聊', 'enthusiastic'),
    ('她刚才说晚安，我有点难过', 'enthusiastic'), ('你为什么总跟我说晚安？', 'enthusiastic'),
    ('他发了“先睡了”，好突然', 'enthusiastic'),
])
def test_explicit_signoff_is_distinct_from_wanting_to_keep_chatting(text, expected):
    choice = select(now=NOW.replace(hour=23), text=text)
    assert choice['response_mode'] == expected
    assert not choice['selector_applied']


def test_disabling_engagement_preserves_legacy_evening_sleep_style():
    choice = select(now=NOW.replace(hour=23), cfg={'reply': {'engagement': {'enabled': False}}})
    assert choice['response_mode'] == 'usual'
    assert eng.effective_sleep_style('asleep', choice) == 'asleep'


@pytest.mark.parametrize('backend', ['deepseek', 'cursor_agent'])
def test_evening_single_message_gets_substantive_prompt_and_no_sleep_truncation(monkeypatch, backend):
    import weather
    captured = []
    monkeypatch.setattr(sc, 'peek_mode', lambda *a, **k: ('asleep', {}))
    monkeypatch.setattr(brain, 'CFG', {**brain.CFG, 'llm': {'backend': backend}})
    monkeypatch.setattr(brain, '_nick', lambda *a, **k: None)
    monkeypatch.setattr(brain, 'memory_block', lambda *a, **k: '')
    monkeypatch.setattr(weather, 'needs', lambda *a: False)
    raw = '这事听着真有意思\n你那个想法我喜欢\n我想到另一个玩法\n咱们可以接着琢磨\n细节也说给我听\n我还想听后面的'
    monkeypatch.setitem(brain.BACKENDS, backend, lambda prompt: captured.append(prompt) or raw)
    answer = brain.reply('demo_contact', [('them', '还想和你聊聊刚才的事')], dry=True,
                         now=NOW.replace(hour=23), activity={'level': 'normal', 'user_bubbles': 1})
    assert brain.LAST['reply_bubble_budget'] == 8 and len(answer.splitlines()) == 6
    assert brain.LAST['sleep_chat_style'] == 'normal'
    prompt = str(captured[0])
    assert '本轮必须热情陪聊' in prompt and '不要只把同一句拆多条冒充热情' in prompt
    assert '睡聊：已睡（少说话' not in prompt and 'gate=idle 已睡极简' not in prompt
    assert '# 睡聊' not in prompt


@pytest.mark.parametrize('channel', ['qq_onebot', 'wechat_ilink'])
def test_evening_bridge_does_not_reapply_sleep_clamp_or_cancel_for_new_inbound(monkeypatch, channel):
    now = NOW.replace(hour=23)
    freeze_bridge(monkeypatch, now)
    monkeypatch.setattr(bridge, 'resolve_contact_id', lambda *a: 'demo_contact')
    monkeypatch.setattr(bridge, '_sleep_split_gap', SLEEP_SPLIT_GAP)
    monkeypatch.setattr(sc, 'note_inbound', lambda *a, **k: ('asleep', {}, 'asleep'))
    def reply(cid, hist):
        brain.LAST.clear()
        brain.LAST['sleep_chat_style'] = 'asleep'
        return '我听到了\n这事挺有意思\n也想听后面的\n你接着说'
    sent = []
    result = bridge.handle_inbound(messages(1, now, channel=channel)[0], dry=False,
        send=sent.append, reply_fn=reply, should_cancel=lambda: True)
    assert not result.get('cancelled') and len(sent) == 4
    assert result['reply_engagement']['response_mode'] == 'enthusiastic'


@pytest.mark.parametrize('channel', ['qq_onebot', 'wechat_ilink'])
def test_slow_weather_yields_to_new_topic_even_in_enthusiastic_evening(monkeypatch, channel):
    now = NOW.replace(hour=18)
    freeze_bridge(monkeypatch, now)
    monkeypatch.setattr(bridge, 'resolve_contact_id', lambda *a: 'demo_contact')
    pending, sent, current = [], [], []
    def reply(cid, hist, **kwargs):
        current.append(kwargs['user_text'])
        brain.LAST.clear()
        brain.LAST['weather_tools'] = True
        pending.append('看看你的穿搭\n今天晚上什么安排')
        return '外面十三度，体感十一'
    monkeypatch.setattr(brain, 'reply', reply)
    inbound = messages(1, now, channel=channel)[0]
    inbound.text = '你学校冷不冷'
    result = bridge.handle_inbound(inbound, dry=False, send=sent.append,
                                  should_cancel=lambda: bool(pending))
    assert result['cancelled'] and sent == []
    assert current == ['你学校冷不冷']
    assert result['reply_engagement']['response_mode'] == 'enthusiastic'
    with memory.db() as conn:
        assert not any(sender == 'me' for sender, _ in memory.history(conn, 'demo_contact'))


@pytest.mark.parametrize('text,expected', [('晚安，我先睡了', 'closing'),
                                           ('别说晚安，我还想聊', 'enthusiastic')])
def test_evening_signoff_and_continuation_control_actual_generated_budget(monkeypatch, text, expected):
    import weather
    captured = []
    monkeypatch.setattr(sc, 'peek_mode', lambda *a, **k: ('pre_sleep', {}))
    monkeypatch.setattr(brain, 'CFG', {**brain.CFG, 'llm': {'backend': 'test_stub'}})
    monkeypatch.setattr(brain, '_nick', lambda *a, **k: None)
    monkeypatch.setattr(brain, 'state_block', lambda *a, **k: '')
    monkeypatch.setattr(brain, 'memory_block', lambda *a, **k: '')
    monkeypatch.setattr(weather, 'needs', lambda *a: False)
    monkeypatch.setitem(brain.BACKENDS, 'test_stub', lambda prompt: captured.append(prompt) or '听到了\n我也是\n好呀\n在呢')
    answer = brain.reply('demo_contact', [('them', text)], dry=True, now=NOW.replace(hour=23),
                         activity={'level': 'dense', 'user_bubbles': 6})
    assert brain.LAST['reply_engagement']['response_mode'] == expected
    if expected == 'closing':
        assert brain.LAST['reply_bubble_budget'] <= 3 and len(answer.splitlines()) <= 3
        assert '自然回应收口' in str(captured[0])
    else:
        assert brain.LAST['reply_bubble_budget'] == 8 and len(answer.splitlines()) == 4
        assert '# 睡聊' not in str(captured[0])


def test_proactive_task_keeps_its_sleep_policy(monkeypatch):
    import weather
    captured = []
    monkeypatch.setattr(sc, 'peek_mode', lambda *a, **k: ('asleep', {}))
    monkeypatch.setattr(brain, 'CFG', {**brain.CFG, 'llm': {'backend': 'test_stub'}})
    monkeypatch.setattr(brain, '_nick', lambda *a, **k: None)
    monkeypatch.setattr(brain, 'state_block', lambda *a, **k: '')
    monkeypatch.setattr(brain, 'memory_block', lambda *a, **k: '')
    monkeypatch.setattr(weather, 'needs', lambda *a: False)
    monkeypatch.setitem(brain.BACKENDS, 'test_stub', lambda prompt: captured.append(prompt) or '[SKIP]')
    brain.reply('demo_contact', [], dry=True, now=NOW.replace(hour=23), task='主动分享日常')
    assert brain.LAST['sleep_chat_style'] == 'asleep'
    assert 'reply_engagement' not in brain.LAST
    assert '本轮必须热情陪聊' not in str(captured[0])


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
