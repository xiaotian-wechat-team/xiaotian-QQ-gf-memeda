"""Offline production-policy regression: one budget for shares and conversations."""
import copy
import datetime as dt
import pathlib
import sys
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import config
import proactive_limiter as lim
import proactive_beats as pb
import share_selection as ss
import share_dedupe as sd
import photo_scene

NOW = dt.datetime(2026, 10, 28, 18)
CFG = config.load()['proactive']


def test_daily_budget_is_ten_and_not_changed_by_reply():
    values = set()
    for day in range(30):
        now = NOW + dt.timedelta(days=day)
        n = lim.daily_budget(CFG, 0, now)
        assert n == lim.daily_budget(CFG, 100, now.replace(hour=23))
        values.add(n)
    assert values == {10}


def test_optional_kinds_including_must_and_retry_hit_same_cap():
    budget = lim.daily_budget(CFG, 0, NOW)
    state = {'proactive_day': str(NOW.date()), 'proactive_count': budget}
    for kind in ('surf_share', 'care_checkin', 'evening'):
        assert lim.limit_reason(NOW, state, CFG, {}, {'id': kind, 'must': True, 'bypass_quiet': True})
    import memory
    item = {'contact':'demo_contact', 'proactive_round': {'id':'old', 'meta': {'beat_meta': {'id':'surf_share'}}}}
    with patch.object(memory, 'read_state', return_value=state), patch('sleep_chat.should_suppress_proactive', return_value=None):
        assert lim.retry_reason(item, NOW)


def test_surf_gap_survives_day_rollover_and_old_done_receipts():
    state = {'proactive_beats': {'day':'2026-10-27', 'done':{}, 'shares':[
        {'shared_at':'2026-10-27 23:00:00', 'source_url':'https://example.com/1'}]}}
    assert '间隔' in lim.surf_limit_reason(dt.datetime(2026,10,28,0,59), state, CFG)
    assert lim.surf_limit_reason(dt.datetime(2026,10,28,1), state, CFG) is None
    state = {'proactive_beats': {'day':str(NOW.date()), 'done':{'surf_share@old':'17:00:00'}}}
    assert lim.surf_limit_reason(NOW, state, CFG)
    state['proactive_beats']['done'] = {f'surf_share@{i}':'10:00:00' for i in range(5)}
    assert '上限' in lim.surf_limit_reason(NOW, state, CFG)


def test_last_round_reserved_for_care_and_new_policy_gap():
    cfg=copy.deepcopy(CFG)
    cfg['contact_rhythm']['enabled']=False  # This case isolates the care reservation.
    state = {'proactive_day':str(NOW.date()), 'proactive_count':lim.daily_budget(CFG,0,NOW)-1}
    assert '关心' in lim.limit_reason(NOW,state,cfg,{}, {'id':'surf_share'})
    assert lim.limit_reason(NOW,state,cfg,{}, {'id':'care_checkin'}) is None
    state['last_proactive_at']=(NOW-dt.timedelta(minutes=29)).isoformat()
    assert '窗口' in lim.limit_reason(NOW,state,cfg,{}, {'id':'care_checkin'})


def test_care_window_and_order_are_stable_but_vary_by_day():
    orders = set()
    for day in range(15):
        now=NOW+dt.timedelta(days=day)
        slot={'start':'08:00','end':'22:00','bucket':'campus_life','activity':'休息'}
        state={'life_today':{'date':str(now.date()),'slots':[slot]}, 'life_now':slot}
        with patch.object(pb, 'pick_surf_item', return_value=None), patch.object(pb, 'meal_image_hint', return_value={}):
            a=pb.due_beats(now,state,CFG)
            b=pb.due_beats(now,state,CFG)
        ids=[v['id'] for v in a]
        assert 'care_checkin' in ids
        assert ids == [v['id'] for v in b]
        orders.add(tuple(ids))
        state['proactive_beats']={'day':str(now.date()),'done':{'care_checkin@18:00':'18:00'}}
        with patch.object(pb, 'pick_surf_item', return_value=None):
            assert 'care_checkin' not in [v['id'] for v in pb.due_beats(now,state,CFG)]
    assert len(orders)>1


def test_source_inventory_cannot_crowd_out_xhs_within_topic():
    rows=[(2, {'title':'游戏', 'platform':'douyin'}, []) for _ in range(100)]
    rows += [(2, {'title':'游戏', 'platform':'xiaohongshu'}, [])]
    dist=ss.distribution(rows,[],NOW)
    xhs=sum(p for p,i,*_ in dist if i['platform']=='xiaohongshu')
    assert abs(xhs-.25)<1e-9  # half of the 50% light topic band
    history=[{'title':'旅行','platform':'douyin','shared_at':NOW.isoformat()}]
    assert sum(p for p,i,*_ in ss.distribution(rows,history,NOW) if i['platform']=='xiaohongshu') > xhs


def test_xhs_search_detail_accepted_and_alias_deduped(tmp_path):
    import material_store as ms
    image=tmp_path/'a.png';image.write_bytes(b'image')
    note='abcdef0123456789abcdef01'
    url=f'https://www.xiaohongshu.com/search_result/{note}?xsec_token=example'
    assert sd.canonical_url(url)==sd.canonical_url(f'https://www.xiaohongshu.com/explore/{note}')
    item={'id':1,'title':'这个游戏太好玩了','platform':'xiaohongshu','source_url':url,
          'media':[{'kind':'image','local_path':str(image)}]}
    import random
    with patch.object(ms,'search',return_value=[item]),patch.object(ms,'recent',return_value=[]):
        assert pb.pick_surf_item({}, {}, now=NOW,require_media=True,rng=random.Random(1))
        item['source_url']='https://www.xiaohongshu.com/search_result?keyword=游戏'
        assert pb.pick_surf_item({}, {}, now=NOW,require_media=True,rng=random.Random(1)) is None


def test_waiting_prompt_knows_recent_and_rejects_reworded_light_cliche():
    seen=[]
    def fake(messages):
        seen.append(messages)
        return '别急 我找个光线' if len(seen)==1 else '好呀 给你看看今天这身'
    out=photo_scene.hold_on_line(user_text='看看ootd',recent_outbound=['别急 等我找好光'],complete_fn=fake)
    assert out=='好呀 给你看看今天这身'
    assert '别急 等我找好光' in seen[0][1]['content']
    assert photo_scene._near_dup_hold('等会儿啊，我拍一下','等会儿啊 我拍一下')


def test_bridge_remembers_successful_waiting_bubble_across_requests(tmp_path, monkeypatch):
    import types
    import memory, brain
    from channels import bridge
    from channels.types import Inbound
    monkeypatch.setattr(memory, 'DL', tmp_path)
    monkeypatch.setattr(memory, 'CFG', {**memory.CFG, 'db_path':str(tmp_path/'chat.db')})
    monkeypatch.setattr(memory, 'ensure_defaults', lambda *a:None)
    monkeypatch.setattr(bridge, '_human_wait_for_send', lambda *a,**k:0)
    monkeypatch.setattr(bridge, '_interruptible_sleep', lambda *a,**k:False)
    monkeypatch.setattr(bridge, '_mem_release', lambda:None)
    (tmp_path/'contacts/demo_contact').mkdir(parents=True)
    seen=[]
    def hold(**kw):
        seen.append(kw['recent_outbound'])
        return '好呀 等我一下' if len(seen)==1 else '给你瞅瞅'
    cm=types.ModuleType('chat_media')
    cm.hold_on_line=hold
    # Image failure must not discard an acknowledgement that was already sent.
    cm.plan_chat_images=lambda *a,**kw:None
    monkeypatch.setitem(sys.modules,'chat_media',cm)
    def reply(*a):
        brain.LAST={'want_image':'第一视角穿搭'}
        return '这身你觉得咋样'
    sent=[]
    for i in range(2):
        bridge.handle_inbound(Inbound(channel='qq_onebot',peer_id='10001',text='看看第一视角',msg_id=str(i)),
                              dry=False,persist=True,send=sent.append,reply_fn=reply)
    assert '好呀 等我一下' in seen[1]
    assert memory.read_state('demo_contact')['recent_photo_holds']==['好呀 等我一下','给你瞅瞅']
    c=memory.db()
    try:
        assert c.execute("SELECT count(*) FROM messages WHERE type='photo_hold'").fetchone()[0]==2
    finally:
        c.close()


def test_spacing_draw_stable_with_shorter_evening_intervals():
    waits=set()
    with patch.object(lim,'rhythm_reason',return_value=None):
        for hour,lo,hi in ((10,75,120),(18,35,65)):
            for day in range(10):
                last=(NOW+dt.timedelta(days=day)).replace(hour=hour)
                state={'last_proactive_at':last.isoformat()}
                early=lim.limit_reason(last+dt.timedelta(minutes=lo-2),state,CFG,{}, {'id':'care_checkin'})
                repeated=lim.limit_reason(last+dt.timedelta(minutes=lo-1),state,CFG,{}, {'id':'care_checkin'})
                assert early == repeated and '窗口' in early
                waits.add(early)
                assert lim.limit_reason(last+dt.timedelta(minutes=hi),state,CFG,{}, {'id':'care_checkin'}) is None
    assert len(waits)>1
