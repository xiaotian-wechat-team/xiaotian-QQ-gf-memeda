"""Offline: detail migration preserves committed timing and makes plans usable."""
import copy
import datetime as dt
import json
import pathlib
import sys
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools'),str(ROOT/'tests')]
import evening_plan as ep
import life_calendar as lc
import update_evening_schedule as migration
from test_evening_plan import base


def signature(day):
    return [(s['start'],s['end'],s['activity'],s['bucket'],s.get('outing_id')) for s in day['slots']]


def test_details_preserve_all_timing_and_do_not_draw_again():
    day=ep.enrich_day(base(),selected={'2026-11-02'})
    before=copy.deepcopy(day)
    with patch.object(ep,'choose_activity',side_effect=AssertionError('no reroll')):
        detailed=ep.detail_day(day)
    assert signature(detailed)==signature(before)
    assert detailed==ep.detail_day(detailed)
    assert day==before
    for s in detailed['slots']:
        if s.get('itinerary'):
            assert s['itinerary'][0]['start']==s['start']
            assert s['itinerary'][-1]['end']==s['end']
            assert all(a['end']==b['start'] for a,b in zip(s['itinerary'],s['itinerary'][1:]))
            assert all(p['start']<p['end'] for p in s['itinerary'])
            assert '计划' in s['plan_status']


def test_persisted_days_have_complete_detail_without_new_outings():
    doc=json.loads((ROOT/'data/life_schedule_2026q4.json').read_text())
    changed,_=migration.update(doc,dt.date(2026,10,28),include_frozen=True,details_only=True)
    for old,new in zip(doc['days'],changed['days']):
        assert signature(old)==signature(new)
        if old['date']<'2026-10-28':assert old==new
        for s in new['slots']:
            if s.get('plan_version')==3:
                assert s['route_plan']['depart']<=s['start']<s['end']<=s['route_plan']['return_by']
                assert s['scene_id'] in {x['id'] for x in ep.CATALOG['scenes']}
                assert len(s['itinerary'])>=3


def test_all_catalog_companions_are_persona_or_explicit_work_roles():
    persona=json.loads((ROOT/'data/persona_dossier.json').read_text())
    allowed={r['name'] for r in persona['roommates']}|{'自己','实习同事（按实际通知）'}
    assert len(ep.ACTIVITIES)>=52
    assert len({x['id'] for x in ep.ACTIVITIES})==len(ep.ACTIVITIES)
    for a in ep.ACTIVITIES:
        assert a['companion_plan'] in allowed
        assert len(a['steps'])==3 and len(set(a['steps']))==3
        assert a['photo_subject'] and a['fallback'] and a['purpose']


def test_movie_does_not_offer_photos_during_screening():
    a=next(a for a in ep.ACTIVITIES if a['id']=='movie_04')
    with patch.object(ep,'choose_activity',return_value=a):
        d=ep.enrich_day(base(),selected={'2026-11-02'})
    s=next(s for s in d['slots'] if s.get('leg')=='activity')
    assert not s['photo_ok']
    assert [p['photo_ok'] for p in s['itinerary']]==[True,False,True]
    assert '待确认' in s['destination_area']


def test_prompt_detail_survives_daily_summary_and_is_only_a_plan():
    day=ep.enrich_day(base(),selected={'2026-11-02'})
    now=dt.datetime(2026,11,2,17)
    with patch.object(lc,'resolve_life_now',return_value=day['slots'][3]):
        lines=lc.state_lines({'life_today':day},now=now)
    text='\n'.join(lines)
    assert '晚间计划（非已成行事实）' in text
    assert '拟同行' in text and '前回寝' in text
    assert '未确认' in text


def test_protected_user_plans_are_untouched():
    day=ep.enrich_day(base(),selected={'2026-11-02'})
    day['source']='user_override'
    assert ep.detail_day(day)==day
