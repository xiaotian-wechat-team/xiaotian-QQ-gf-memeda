"""Production rhythm: deterministic offline simulations, no messages/models/STATE."""
import datetime as dt
import pathlib
import sys
from collections import Counter
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
import config
import proactive_beats as beats
import proactive_limiter as lim
import proactive_scheduler as scheduler

CFG=config.load()['proactive']
DAY=dt.datetime(2026,10,29)


def test_probability_rises_toward_evening_without_polling_rerolls():
    probabilities=[lim.rhythm_probability(DAY.replace(hour=h),CFG) for h in (7,10,12,15,17,20,22)]
    assert probabilities==sorted(probabilities)
    assert probabilities[-1]>.9 and probabilities[1]<.25
    allowed=Counter()
    for i in range(365):
        day=DAY+dt.timedelta(days=i)
        for hour in (10,21):
            now=day.replace(hour=hour)
            reason=lim.rhythm_reason(now,{},CFG,{'id':'care_checkin'})
            assert all(lim.rhythm_reason(now+dt.timedelta(minutes=m),{},CFG,{'id':'surf_share'})==reason for m in (5,10,20,29))
            allowed[hour]+=reason is None
    assert allowed[21]>allowed[10]*3


def test_daytime_cannot_exhaust_the_ten_rounds_even_for_must_events():
    for hour,count in ((11,2),(16,4),(19,7),(23,10)):
        now=DAY.replace(hour=hour)
        state={'proactive_day':str(now.date()),'proactive_count':count}
        for bid in ('care_checkin','surf_share','evening'):
            assert lim.limit_reason(now,state,CFG,{}, {'id':bid,'must':True,'bypass_quiet':True})
    # Genuine successful counts, including today's existing ten, are never reset by policy.
    assert lim.daily_budget(CFG,99,DAY)==10
    tomorrow=DAY+dt.timedelta(days=1)
    state={'proactive_day':str(DAY.date()),'proactive_count':10}
    assert lim.limit_reason(tomorrow.replace(hour=23),state,CFG,{}, {'id':'pre_sleep'}) is None


def test_full_days_favor_evening_and_never_catch_up_missed_slots():
    totals=Counter()
    for i in range(90):
        day=DAY+dt.timedelta(days=i)
        st={'proactive_day':str(day.date()),'proactive_count':0}
        for minute in range(8*60,23*60,5):
            now=day+dt.timedelta(minutes=minute)
            if lim.limit_reason(now,st,CFG,{}, {'id':'care_checkin'}) is None:
                st['proactive_count']+=1
                st['last_proactive_at']=now.isoformat()
                totals['evening' if now.hour>=17 else 'day']+=1
            assert st['proactive_count']<=10
            if now.hour<17:assert st['proactive_count']<=4
    assert totals['evening']>totals['day']*1.25,totals


def test_care_is_once_per_window_and_more_likely_than_other_single_candidates():
    wins=Counter()
    with patch.object(beats,'pick_surf_item',return_value=None), patch.object(beats,'_pendulum_want_image',return_value=False), patch('schedule_fidelity.should_ask_his_day',return_value=False):
        for i in range(100):
            now=(DAY+dt.timedelta(days=i)).replace(hour=18)
            slot={'start':'08:00','end':'23:00','activity':'宿舍听歌','bucket':'weekend_home','photo_ok':True}
            st={'life_today':{'date':str(now.date()),'slots':[slot]},'proactive_beats':{'day':str(now.date()),'done':{}}}
            due=beats.due_beats(now,st,CFG)
            wins[due[0]['id']]+=1
            care=next(b for b in due if b['id']=='care_checkin')
            assert care['done_key']=='care_checkin@18:00'
            st['proactive_beats']['done'][care['done_key']]='18:00:00'
            assert not any(b['id']=='care_checkin' for b in beats.due_beats(now,st,CFG))
            later=beats.due_beats(now.replace(hour=20),st,CFG)
            assert next(b for b in later if b['id']=='care_checkin')['done_key']=='care_checkin@20:00'
            assert not any(b.get('done_key')=='care_checkin@09:00' for b in later)
        assert wins['care_checkin']>wins['life_photo']


def test_share_ceiling_one_to_five_stable_and_retries_obey_rhythm():
    values=set()
    for i in range(60):
        now=DAY+dt.timedelta(days=i)
        values.add(beats.surf_day_target(now,CFG['beats']))
        assert beats.surf_day_target(now,CFG['beats'])==beats.surf_day_target(now.replace(hour=23),CFG['beats'])
    assert values=={1,2,3,4,5}
    st={'proactive_day':str(DAY.date()),'proactive_count':4}
    item={'contact':'demo_contact','proactive_round':{'id':'pending','meta':{'beat_meta':{'id':'care_checkin'}}}}
    with patch('memory.read_state',return_value=st), patch('sleep_chat.should_suppress_proactive',return_value=None):
        assert '分时额度' in lim.retry_reason(item,DAY.replace(hour=16))
    with patch('sleep_chat.should_suppress_proactive',return_value='小甜在睡觉'):
        assert scheduler.gates(DAY.replace(hour=23),{},CFG,{'pending_from_him':False}, {'id':'pre_sleep','must':True})=='小甜在睡觉'
