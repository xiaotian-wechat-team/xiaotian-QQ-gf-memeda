"""Offline regression for weekly work and planned-vs-executed activity gates."""
import copy
import datetime as dt
import json
import pathlib
import sys
from collections import Counter
from unittest.mock import patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools')]
import evening_plan as ep
import life_calendar as lc
import proactive_media as media
import schedule_execution as execution
import sleep_chat
from channels.outbox_send import check_proactive_sleep
import rebalance_internship_schedule as migration


def test_generated_weeks_balance_indoor_outdoor_and_include_evening():
    counts = set()
    with patch.object(lc, '_material_hints', return_value=[]):
        for i in range(9):
            monday = dt.date(2026, 11, 2) + dt.timedelta(weeks=i)
            days = [lc.generate_day(monday+dt.timedelta(days=j)) for j in range(5)]
            working = [d for d in days if any(s['bucket']=='work_internship' for s in d['slots'])]
            counts.add(len(working))
            assert 2 <= len(working) <= 4
            modes = Counter()
            for d in working:
                work = [s for s in d['slots'] if s['bucket']=='work_internship']
                mode = {s['internship_mode'] for s in work}
                assert len(mode)==1
                modes.update(mode)
                assert all(s['end'] <= d['sleep_bounds']['prep_start'] for s in work)
            assert abs(modes['indoor']-modes['outdoor'])<=1
            assert any(s.get('leg')=='activity' and s['start']>='18:00'
                       and s['bucket']=='work_internship' for d in working for s in d['slots'])
    assert counts=={2,3,4}


def test_saved_workdays_match_report_and_keep_round_trips():
    doc=json.loads((ROOT/'data/life_schedule_2026q4.json').read_text())
    for report in doc['enrichment']['internship_rebalance']['weeks']:
        monday=dt.date.fromisoformat(report['week'])
        days=[d for d in doc['days'] if str(monday)<=d['date']<=str(monday+dt.timedelta(days=4))]
        work=[d for d in days if any(s['bucket']=='work_internship' for s in d['slots'])]
        assert len(work)==report['actual']<=4
        if len(lc._week_eligible_workdays(monday))>=2:
            assert len(work)>=2
        for day in work:
            if day['date']<'2026-10-29':continue
            for s in day['slots']:
                if s.get('internship_mode')=='outdoor':
                    group=[x for x in day['slots'] if x.get('outing_id')==s.get('outing_id')]
                    assert {x['leg'] for x in group}=={'outbound','activity','return'}


def test_migration_preserves_past_and_confirmed_days():
    doc=json.loads((ROOT/'data/life_schedule_2026q4.json').read_text())
    day=next(d for d in doc['days'] if d['date']=='2026-11-02')
    day['slots'][-2]['execution_status']='confirmed'
    changed,_=migration.update(doc,dt.date(2026,10,29))
    for old,new in zip(doc['days'],changed['days']):
        if old['date']<'2026-10-29' or old['date']=='2026-11-02':assert old==new
        fixed=lambda d:[s for s in d['slots'] if s['bucket'] in ('sleep','school_class','school_rehearsal')]
        assert fixed(old)==fixed(new)


@pytest.mark.parametrize('status', ['pending','cancelled'])
def test_pending_and_cancelled_plans_cannot_send_or_retry(status):
    slot=dict(start='19:00',end='21:00',activity='影院观影',bucket='city_fun',
              outing_id='movie',execution_status=status,photo_ok=True)
    day=dict(date='2026-11-02',kind='workday',slots=[slot])
    now=dt.datetime(2026,11,2,19,30)
    stale=copy.deepcopy(slot);stale.pop('execution_status')
    beat={'id':'activity_end','ended':stale}
    item={'beat_id':'activity_end','proactive_round':{'meta':{'beat_meta':beat}}}
    with patch.object(lc,'get_day',return_value=day), \
         patch('memory.read_state',return_value={'life_today':day}), \
         patch.object(sleep_chat,'should_suppress_proactive',return_value=None):
        assert execution.beat_blocked(beat,now)
        assert lc.slot_at(now)['bucket']=='weekend_home'
        assert not execution.beat_blocked({'slot':lc.slot_at(now)},now)
        assert lc.resolve_life_now({'life_now':dict(stale,date=day['date'])},now)['bucket']=='weekend_home'
        assert media.photo_slot({'slot':slot},now) is None
        with pytest.raises(sleep_chat.ProactiveSleepBlocked, match='未确认|取消'):
            sleep_chat.check_proactive_send({},now,beat)
        with pytest.raises(sleep_chat.ProactiveSleepBlocked, match='未确认|取消'):
            check_proactive_sleep(item,now)
        # Explicitly confirmed plans resume normal execution.
        slot['execution_status']='confirmed'
        assert not execution.beat_blocked(beat,now)
        assert lc.slot_at(now)['bucket']=='city_fun'


def test_detail_budgets_stay_inside_short_slots_and_keep_confirmation():
    a=next(a for a in ep.ACTIVITIES if a['category']=='shopping')
    for duration in (3,15,30,75):
        day={'date':'2026-11-02','kind':'workday','slots':[
            dict(start='19:00',end=ep.hm(1140+duration),activity=a['activity'],
                 bucket='city_fun',outing_activity_id=a['id'],outing_id='a',leg='activity',execution_status='confirmed')]}
        s=ep.detail_day(day)['slots'][0]
        assert s['execution_status']=='confirmed'
        assert s['itinerary'][0]['start']==s['start']
        assert s['itinerary'][-1]['end']==s['end']
        assert all(p['start']<p['end'] for p in s['itinerary'])
        assert all(x['end']==y['start'] for x,y in zip(s['itinerary'],s['itinerary'][1:]))
