"""Daily reports bypass optional quotas, never sleep or duplicate protection.
Uses isolated temporary databases and mocked transports; no real sends/models.
"""
import datetime as dt
from unittest.mock import patch
import pytest
import test_proactive_unanswered as regression
import proactive_limiter as lim
import proactive_media as media
import proactive_beats as beats
import proactive_sender as sender
import config, memory


@pytest.fixture
def h():
    case=regression.Regression('runTest')
    case.setUp()
    # Restore production limits while retaining isolated transport configuration.
    case.pc.update(daily_contact_range=[10,10], image_share_min_ratio=.6,
                   contact_rhythm={'enabled':True,'cumulative_caps':[[24,10]]})
    yield case
    case.doCleanups()


def test_goodnight_sends_at_full_quota_without_photo_and_only_once(h):
    now=dt.datetime(2026,10,27,22,2)
    slot={**regression.SLOTS[-2], 'photo_ok':False}
    beat={'id':'pre_sleep','done_key':'pre_sleep','slot':slot,'must':True}
    memory.update_state('demo_contact',proactive_day=regression.DAY,proactive_count=10)
    with patch.object(beats,'due_beats',return_value=[beat]):
        assert h.run_sender(now)['action']=='SENT'
        st=memory.read_state('demo_contact')
        assert st['proactive_count']==10
        assert st['proactive_routine']['count']==1
        assert st['proactive_media']['images']==0
        assert h.run_sender(now+dt.timedelta(minutes=5))['action']=='SKIP'
    assert len(h.sent)==1


def test_routine_retry_survives_quota_and_image_floor(h):
    now=dt.datetime(2026,10,27,12,7)
    beat={'id':'meal','done_key':'meal@12:00','slot':regression.SLOTS[3]}
    item={'id':'meal-retry','meta':{'beat_meta':beat,'has_image':False}}
    ob={'contact':'demo_contact','proactive_round':item}
    memory.update_state('demo_contact',proactive_day=regression.DAY,proactive_count=10)
    assert lim.retry_reason(ob,now) is None
    lim.record_outbox_success(ob,now)
    lim.record_outbox_success(ob,now)
    st=memory.read_state('demo_contact')
    assert st['proactive_count']==10 and st['proactive_routine']['count']==1
    assert lim.retry_reason(ob,now+dt.timedelta(minutes=5))
    assert lim.limit_reason(now+dt.timedelta(minutes=5),st,h.pc,{},beat)
    assert lim.limit_reason(now+dt.timedelta(hours=6),st,h.pc,{},
                            {**beat,'done_key':'meal@18:00'}) is None


def test_all_routines_exempt_but_plain_text_cannot_bypass(h):
    now=dt.datetime(2026,10,27,12)
    st={'proactive_day':regression.DAY,'proactive_count':10,
        'last_proactive_at':(now-dt.timedelta(minutes=3)).isoformat()}
    for bid in lim.ROUTINE_BEATS:
        beat={'id':bid,'image_required':True,'slot':{'photo_ok':False}}
        assert lim.limit_reason(now,st,h.pc,{},beat) is None
        assert not media.required(st,now,h.pc,beat)
        assert 'image_required' not in media.prepare(beat,st,now,h.pc)
    assert lim.limit_reason(now,st,h.pc,{}, {'id':'care_checkin','text':'晚安'})
    st['last_proactive_at']=(now-dt.timedelta(seconds=60)).isoformat()
    assert lim.limit_reason(now,st,h.pc,{}, {'id':'meal'})


def test_routine_does_not_restart_optional_cooldown_and_images_include_reports(h):
    now=dt.datetime(2026,10,27,12)
    memory.update_state('demo_contact',proactive_day=regression.DAY,proactive_count=2,
                        last_proactive_at=regression.DAY+' 10:00:00')
    lim.record_success('demo_contact',{'id':'meal1','meta':{'beat_meta':{'id':'meal'}}},now)
    st=memory.read_state('demo_contact')
    assert st['last_budgeted_proactive_at']==regression.DAY+' 10:00:00'
    assert media.counts(st,now)==(3,0)
    assert media.required(st,now,h.pc,{'id':'care_checkin'})
    lim.record_success('demo_contact',{'id':'care1','meta':{'beat_meta':{'id':'care_checkin'}}},now+dt.timedelta(hours=1))
    st=memory.read_state('demo_contact')
    assert st['proactive_count']==3 and st['proactive_routine']['count']==1
    tomorrow=now+dt.timedelta(days=1)
    lim.record_success('demo_contact',{'id':'next-morning','meta':{'beat_meta':{'id':'morning'}}},tomorrow)
    st=memory.read_state('demo_contact')
    assert st['proactive_count']==0 and st['proactive_routine']['count']==1
    assert media.counts(st,tomorrow)==(1,0)


def test_reports_still_obey_actual_sleep_and_explicit_pause(h):
    now=dt.datetime(2026,10,27,23,45)
    beat={'id':'pre_sleep','slot':regression.SLOTS[-1],'must':True}
    st=memory.read_state('demo_contact')
    assert regression.ps.gates(now,st,h.pc,{'pending_from_him':False},beat)
    beat['slot']=regression.SLOTS[-2]
    assert regression.ps.gates(now,{**st,'proactive_paused':True},h.pc,{'pending_from_him':False},beat)
    with patch('sleep_chat.should_suppress_proactive',return_value='双方已道晚安'):
        assert regression.ps.gates(now,st,h.pc,{'pending_from_him':False},beat)=='双方已道晚安'
