"""Offline actual delivery accounting, safe activity variety and image routing."""
import copy
import datetime as dt
import pathlib
import sys
from unittest.mock import patch
sys.path[:0] = [str(pathlib.Path(__file__).resolve().parents[1]/'src'), str(pathlib.Path(__file__).parent)]
import proactive_media as pm
import proactive_limiter as lim
import proactive_sender as sender
import proactive_scheduler as ps
import evening_plan as ep
import config
from test_evening_plan import base
from test_sleep_priority import SleepPriority

NOW = dt.datetime(2026, 10, 28, 18)
CFG = config.load()['proactive']


def test_prefix_ratio_for_all_daily_budgets():
    for budget in (3, 4, 5):
        state = {'proactive_day': str(NOW.date()), 'proactive_count': 0}
        decisions = []
        for n in range(budget):
            needs = pm.required(state, NOW, CFG)
            decisions.append(needs)
            item = {'meta': {'has_image': needs}}
            state.update(pm.receipt_patch(state, item, NOW))
            state['proactive_count'] += 1
            assert state['proactive_media']['images']/state['proactive_count'] >= .6
        assert sum(decisions) == {3:2, 4:3, 5:3}[budget]
    assert pm.counts(state, NOW+dt.timedelta(days=1)) == (0, 0)


def test_missing_image_never_counts_and_receipt_is_idempotent(tmp_path):
    import memory
    state = {}
    def mutate(cid, fn):
        state.update(fn(copy.deepcopy(state)));return copy.deepcopy(state)
    with patch.object(memory,'mutate_state',side_effect=mutate):
        for rid, image in [('failed-image',False),('photo',True),('photo',True)]:
            lim.record_success('demo_contact',{'id':rid,'text':'看看今天','meta':{'has_image':image}},NOW)
    assert state['proactive_count'] == 2
    assert state['proactive_media']['images'] == 1
    assert pm.required(state,NOW,CFG)


def test_care_can_share_current_life_image_but_transit_sleep_and_movie_wait():
    beat = {'id':'care_checkin','slot':{'activity':'做串珠','bucket':'city_fun','scene_id':'night-005'}}
    prepared=pm.prepare(beat,{},NOW,CFG)
    assert prepared['image_required'] and prepared['image']['mode']=='want_image'
    for sl in ({'bucket':'sleep'}, {'bucket':'city_fun','photo_ok':False}, {'leg':'outbound'}):
        assert pm.prepare({'id':'life_photo','slot':sl},{},NOW,CFG) is None
    ended={'end':'17:50','bucket':'city_fun','photo_ok':False,'outing_activity_id':'movie_01','scene_id':'night-004'}
    assert pm.prepare({'id':'activity_end','ended':ended},{},NOW,CFG)['image_slot']['photo_ok']
    ended['end']='18:30'
    assert pm.prepare({'id':'activity_end','ended':ended},{},NOW,CFG) is None


def test_retry_cannot_send_old_text_only_round_when_image_needed():
    import memory
    item={'contact':'demo_contact','proactive_round':{'id':'text','meta':{},'text':'你好'}}
    with patch.object(memory,'read_state',return_value={}),patch.object(ps,'gates',return_value=None):
        assert '图片' in lim.retry_reason(item,NOW)


def test_catalog_and_future_schedule_diversity_with_return_and_sleep():
    ids={s['id'] for s in ep.CATALOG['scenes']}
    assert len(ep.ACTIVITIES)>=52
    assert all(a['scene_id'] in ids for a in ep.ACTIVITIES)
    history=[]
    for i in range(180):
        d=NOW.date()+dt.timedelta(days=i)
        day=ep.enrich_day(base(str(d)),selected={str(d)},history=history)
        history.append(day)
        assert day['slots'][-1]['bucket']=='sleep'
        assert day['slots'][-1]['start']=='23:30'
    categories=[d['evening_plan']['category'] for d in history]
    assert {'shopping','singing','nightview','movie','light_fun'} <= set(categories)
    assert categories.count('campus_walk')/len(categories)<.06
    for old,new in zip(history,history[1:]):
        assert old['evening_plan']['outing'] != new['evening_plan']['outing']


def test_existing_night_work_has_round_trip_without_adding_workdays():
    day=base('2026-11-02');day['slots'][5]['bucket']='work_internship'
    changed=ep.enrich_day(day, selected={day['date']})
    work=[s for s in changed['slots'] if s['bucket']=='work_internship']
    assert [s['leg'] for s in work]==['outbound','activity','return']
    assert work[0]['start']==day['slots'][5]['start'] and work[-1]['end']==day['slots'][5]['end']
    assert work[1]['scene_id']==next(a['scene_id'] for a in ep.ACTIVITIES if a['id']==work[1]['outing_activity_id'])
    assert ep.enrich_night_work(copy.deepcopy(changed))==changed


class MediaTransport(SleepPriority):
    def setUp(self):
        super().setUp()
        self.full['proactive']['image_share_min_ratio'] = .6

    # Reuses isolated filesystem/state and sleep stubs, never real transports.
    def queued(self):
        q={'items':[]}
        slot=self.state['life_today']['slots'][1]
        sender.enqueue(q,'今天这个颜色好看','beat:care_checkin',self.now,
                       meta={'beat_meta':{'id':'care_checkin','slot':slot}})
        path=self.tmp/'media-queue.json';sender.save_q(q,path);return path

    def test_missing_tag_still_generates_and_records_actual_image(self):
        image=self.tmp/'photo.png';image.write_bytes(b'pretend-image')
        path=self.queued()
        with patch('chat_media.plan_chat_images',return_value={'paths':[str(image)],'texts':['给你看看']}), \
             patch.object(sender,'send_via_onebot',return_value=(False,'offline',None)) as send:
            result=sender.run_once(cfg_full=self.full,rows=[],ui=False,qpath=path)
        assert send.call_args.kwargs['image_path']==str(image)
        assert not self.state.get('proactive_count')  # enqueue/failure is not delivery
        from channels import onebot_qq
        items=onebot_qq._load_outbox()['items']
        assert items and items[0]['proactive_round']['meta']['has_image']
        lim.record_outbox_success(items[0],self.now)
        lim.record_outbox_success(items[0],self.now)
        assert self.state['proactive_count']==1
        assert self.state['proactive_media']['images']==1

    def test_generation_failure_never_degrades_to_text(self):
        with patch('chat_media.plan_chat_images',return_value={'paths':[]}),patch.object(sender,'send_via_onebot') as send:
            result=sender.run_once(cfg_full=self.full,rows=[],ui=False,qpath=self.queued())
        assert result['action']=='DROP' and '图片' in result['reason']
        send.assert_not_called()
        assert not self.state.get('proactive_count')
