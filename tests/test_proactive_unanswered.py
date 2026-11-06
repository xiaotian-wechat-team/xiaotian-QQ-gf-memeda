"""Regression: unattended full day, light budgets, receipts, retry and restart.
All transport, models, schedules and persistence are isolated; never real QQ/STATE.
"""
import os
os.environ['XIAOTIAN_TEST'] = '1'
import sys, pathlib, tempfile, unittest, datetime as dt, copy, random, contextlib, json, fcntl
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import config, memory, proactive_scheduler as ps, proactive_beats as pb
import proactive_limiter as lim, proactive_sender as sender
from channels import outbox_store
D=dt.datetime
DAY='2026-10-27'
SLOTS=[
 {'start':'00:00','end':'07:40','activity':'睡觉','bucket':'sleep'},
 {'start':'07:40','end':'08:30','activity':'刚起床赶课','bucket':'campus_life'},
 {'start':'08:30','end':'12:00','activity':'编导课','bucket':'school_class'},
 {'start':'12:00','end':'13:00','activity':'吃午饭','bucket':'campus_life'},
 {'start':'13:00','end':'17:00','activity':'电影公司实习','bucket':'work_internship'},
 {'start':'17:00','end':'22:00','activity':'排练','bucket':'school_rehearsal'},
 {'start':'22:00','end':'23:30','activity':'准备睡','bucket':'weekend_home'},
 {'start':'23:30','end':'24:00','activity':'睡觉','bucket':'sleep'}]

class Regression(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
  self.path=pathlib.Path(self.tmp.name); self.q=self.path/'queue.json'
  (self.path/'contacts/demo_contact').mkdir(parents=True)
  self.full=copy.deepcopy(config.load()); self.pc=self.full['proactive']
  # Keep legacy budgets separate from production rhythm tests.
  self.pc.pop('idle_contact', None)  # Deadline integration: test_idle_contact.py.
  self.pc.pop('daily_contact_range', None)
  self.pc.pop('contact_rhythm', None)
  self.pc['image_share_min_ratio']=0
  self.pc['max_per_day']=12
  self.pc['limiter']['merge_window_sec']=600
  self.pc['limiter'].pop('spacing_range_sec',None)
  self.pc['beats']['care_checkin_enabled']=False
  self.pc.update(transport='onebot',primary_channel='qq_onebot',channel_policy='primary')
  self.full['persona']['contact_aliases']['qq_onebot:demo_contact']='demo_contact'
  self.full['persona']['channels']['qq']['allow_user_ids']=['demo_contact']
  self.pc['beats'].update(baobei_want_image=False,meal_want_image=False,activity_end_want_image=False)
  self.state={'life_today':{'date':DAY,'slots':copy.deepcopy(SLOTS)},
              'proactive_beats':{'day':DAY,'done':{}},
              'last_reply_at':'2026-10-26 22:59:56','last_inbound_at':'2026-10-26 22:59:00',
              'last_proactive_at':'2026-10-26 23:32:00','proactive_unanswered_turns':1}
  self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
  for target,value in [('memory.DL',self.path),('memory.CFG',{**memory.CFG,'db_path':str(self.path/'chat.db')}),
                       ('channels.outbox_store.DB_PATH',self.path/'outbox.db')]:
   self.stack.enter_context(patch(target,value))
  for target,kwargs in [
   ('memory.ensure_defaults',{'return_value':None}),
   ('chat_media.plan_chat_images',{'side_effect':AssertionError('legacy text-only test must not generate images')}),
   ('sleep_chat.should_suppress_proactive',{'return_value':None}),
   ('sleep_chat.refresh',{'side_effect':lambda st,now:('normal',None)}),
   ('proactive_scheduler.busy_value',{'return_value':80}),
   ('proactive_scheduler.resting',{'return_value':False}),
   ('schedule.is_sleeping',{'return_value':False}),
   ('proactive_beats.pick_surf_item',{'return_value':None}),
   ('proactive_beats._pendulum_want_image',{'return_value':False}),
   ('proactive_beats._emotion_hint',{'return_value':''}),
   ('schedule_fidelity.should_ask_his_day',{'return_value':False}),
   ('schedule_fidelity.already_asked_his_day_today',{'return_value':False}),
   ('proactive_sender._maybe_apply_unanswered_miss',{'side_effect':lambda cid,st,*a:(st,{})}),
   ('proactive_sender._onebot_peer_and_cfg',{'return_value':('demo_contact',{})})]:
   self.stack.enter_context(patch(target,**kwargs))
  self.stack.enter_context(patch.object(config,'load',return_value=self.full))
  self.stack.enter_context(patch('channels.persona_map.load',return_value=self.full))
  memory.update_state('demo_contact',**self.state)
  self.sent=[]
  self.stack.enter_context(patch.object(sender,'send_via_onebot',side_effect=self.send))
 def send(self,text,**kw):
  self.sent.append((text,kw)); return True,'onebot:confirmed',None
 def tick(self,now,st=None,**kw):
  return ps.tick(now,self.pc,st or memory.read_state('demo_contact'),[],random.Random(1),use_llm=False,**kw)
 def run_sender(self,now,**kw):
  return sender.run_once(now,cfg_full=self.full,rows=[],qpath=self.q,ui=False,
                         draft_fn=lambda:{7:'刚醒 今天得赶课',10:'编导课讲蒙太奇有点意思',12:'食堂的番茄牛腩好香',14:'在整理拍摄素材 眼睛快花了',17:'今天排练终于顺了 晚上还有一遍',22:'刷完牙准备钻被窝啦'}[now.hour],**kw)
 def test_budget_reduction_never_more_than_quarter(self):
  self.assertEqual([lim.daily_budget(self.pc,n) for n in [0,1,3,4,6,7,100]],[12,12,12,10,10,9,9])
  for base in range(1,30):
   for u in range(30):
    self.assertGreaterEqual(lim.daily_budget({'max_per_day':base},u),base*.75)
 def test_no_unanswered_interval_multiplier(self):
  now=D(2026,10,27,14)
  for after in [False,True]:
   self.assertEqual(ps.next_consider(now,{},self.pc,random.Random(2),after_send=after,unanswered_turns=0),
                    ps.next_consider(now,{},self.pc,random.Random(2),after_send=after,unanswered_turns=100))
 def test_full_day_no_reply_still_reports(self):
  for h,m in [(7,42),(10,7),(12,7),(14,7),(17,2),(22,2)]:
   result=self.run_sender(D(2026,10,27,h,m))
   self.assertEqual(result['action'],'SENT',result)
  st=memory.read_state('demo_contact'); done=st['proactive_beats']['done']
  self.assertTrue({'morning','schedule_brief','evening','pre_sleep'} <= set(done))
  self.assertEqual(st['proactive_count'],3)
  self.assertEqual(st['proactive_routine']['count'],3)
  self.assertEqual(st['proactive_unanswered_turns'],7)
 def test_busy_and_legacy_limits_do_not_hide_events(self):
  self.pc['beats'].update(max_beat_per_day=0,meal_p=0)
  st=copy.deepcopy(self.state);st.update(proactive_count=99,proactive_day=DAY)
  due=pb.due_beats(D(2026,10,27,10,30),st,self.pc)
  self.assertTrue({'morning','daily_chat','silence_nudge'} <= {b['id'] for b in due})
  due=pb.due_beats(D(2026,10,27,12,7),st,self.pc)
  self.assertIn('meal',{b['id'] for b in due})
 def test_rejected_optional_does_not_hide_reserved_report(self):
  st=copy.deepcopy(self.state);st.update(proactive_count=7,proactive_day=DAY,proactive_unanswered_turns=8)
  meal={'id':'daily_chat','done_key':'daily_chat','slot':SLOTS[3]}
  evening={'id':'evening','done_key':'evening','slot':SLOTS[5]}
  with patch.object(pb,'due_beats',return_value=[meal,evening]):
   result=self.tick(D(2026,10,27,17),st)
  self.assertEqual(result['beat'],'evening',result)
  self.assertEqual(result['events'][0]['status'],'limited')
 def test_cooldown_does_not_hide_new_event(self):
  sender.save_q({'items':[],'next_consider_at':DAY+' 23:59:00'},self.q)
  self.assertEqual(self.run_sender(D(2026,10,27,7,42))['action'],'SENT')
 def test_morning_and_plan_one_round_restart_no_duplicate(self):
  now=D(2026,10,27,7,42)
  self.assertEqual(self.run_sender(now)['action'],'SENT')
  st=memory.read_state('demo_contact')
  self.assertEqual(st['proactive_count'],0)
  self.assertEqual(st['proactive_routine']['count'],1)
  self.assertTrue({'morning','schedule_brief'} <= set(st['proactive_beats']['done']))
  self.run_sender(now+dt.timedelta(minutes=5))
  self.assertEqual(len(self.sent),1)
  q=sender.load_q(self.q); self.assertEqual(q['events']['schedule_brief']['status'],'merged')
 def test_late_wake_report_uses_current_time(self):
  res=self.tick(D(2026,10,27,12,7))
  self.assertEqual(res['beat'],'morning')
  self.assertIn('禁止说刚起床',res['task'])
 def test_optional_messages_leave_evening_sleep_budget(self):
  st=copy.deepcopy(self.state);st.update(proactive_count=7,proactive_day=DAY,proactive_unanswered_turns=100)
  st['proactive_beats']['done']={'morning':'08:00','schedule_brief':'08:00'}
  ctx={'pending_from_him':False,'unanswered_proactive_turns':100}
  now=D(2026,10,27,17)
  self.assertIsNotNone(ps.gates(now,st,self.pc,ctx,{'id':'daily_chat','slot':SLOTS[3]}))
  self.assertIsNone(ps.gates(now,st,self.pc,ctx,{'id':'evening','slot':SLOTS[5]}))
 def test_failed_send_no_debit_and_outbox_success_once(self):
  with patch.object(sender,'send_via_onebot',return_value=(False,'network unavailable',None)):
   self.assertEqual(self.run_sender(D(2026,10,27,7,42))['action'],'HOLD')
  self.assertEqual(memory.read_state('demo_contact').get('proactive_count',0),0)
  from channels import onebot_qq
  ob=onebot_qq._load_outbox(); item=ob['items'][0]
  self.assertIn('proactive_round',item)
  lim.record_outbox_success(item,D(2026,10,27,7,45))
  lim.record_outbox_success(item,D(2026,10,27,7,46))
  st=memory.read_state('demo_contact');self.assertEqual(st['proactive_count'],0)
  self.assertEqual(st['proactive_routine']['count'],1)
  self.assertIn('schedule_brief',st['proactive_beats']['done'])
 def test_ack_before_accounting_recovered_without_resend(self):
  now=D(2026,10,27,7,42)
  with patch.object(lim,'record_success',side_effect=RuntimeError('crash after delivery')):
   with self.assertRaises(RuntimeError): self.run_sender(now)
  self.assertEqual(sender.load_q(self.q)['items'][0]['status'],'sent')
  self.run_sender(now+dt.timedelta(minutes=1))
  self.assertEqual(len(self.sent),1)
  self.assertEqual(memory.read_state('demo_contact')['proactive_count'],0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_routine']['count'],1)
 def test_sleep_pause_and_pending_reply_still_defer(self):
  for patch_st,ctx,slot in [({},False,SLOTS[0]),({'proactive_paused':True},False,SLOTS[3]),({},True,SLOTS[3])]:
   self.assertIsNotNone(ps.gates(D(2026,10,27,12),{**self.state,**patch_st},self.pc,
                                {'pending_from_him':ctx}, {'id':'meal','slot':slot}))
 def test_pending_reply_expires_without_new_inbound(self):
  start=D(2026,10,27,12)
  rows=[('them','亲一下就软了',start.isoformat(),'text')]
  for seconds,blocked in [(0,True),(599,True),(600,False),(4*3600,False)]:
   with self.subTest(seconds=seconds):
    now=start+dt.timedelta(seconds=seconds)
    ctx=ps.context(rows,now,self.state)
    self.assertEqual(ctx['pending_from_him'],blocked)
    reason=ps.gates(now,self.state,self.pc,ctx,{'id':'meal','slot':SLOTS[3]})
    self.assertEqual(reason is not None,blocked)
  # A fresh incoming message starts a new short reply window.
  rows.append(('them','新的消息',(start+dt.timedelta(hours=4)).isoformat(),'text'))
  self.assertTrue(ps.context(rows,start+dt.timedelta(hours=4),self.state)['pending_from_him'])
 def test_bad_inbound_time_cannot_freeze_proactive(self):
  now=D(2026,10,27,12)
  for stamp in [None,'bad','2026-10-28 12:00:00']:
   self.assertFalse(ps.context([('them','消息',stamp,'text')],now)['pending_from_him'])
 def test_overlapping_worker_never_sends(self):
  with self.q.with_suffix('.worker.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   self.assertEqual(self.run_sender(D(2026,10,27,7,42))['action'],'WAIT')
   self.assertEqual(self.sent,[])
 def test_inbound_reset_not_overwritten_by_stale_snapshot(self):
  memory.update_state('demo_contact',proactive_unanswered_turns=0,last_inbound_at=DAY+' 07:41:00')
  item={'id':'one-round','meta':{'beat_meta':{'id':'morning','merged_keys':['schedule_brief']}}}
  lim.record_success('demo_contact',item,D(2026,10,27,7,42))
  self.assertEqual(memory.read_state('demo_contact')['proactive_unanswered_turns'],1)
 def test_existing_sent_queue_not_charged_again_after_upgrade(self):
  sender.save_q({'items':[{'id':'old-version','status':'sent','sent_at':DAY+' 07:00:00','text':'昨夜下雨的闲聊'}],
                 'next_consider_at':DAY+' 23:59:00'},self.q)
  self.run_sender(D(2026,10,27,7,42))
  self.assertEqual(memory.read_state('demo_contact')['proactive_count'],0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_routine']['count'],1)
 def test_reserved_report_survives_budget_tier_change(self):
  st={**self.state,'proactive_day':DAY,'proactive_count':10,'proactive_unanswered_turns':4}
  st['proactive_beats']={'day':DAY,'done':{'morning':'08:00','schedule_brief':'08:00'}}
  self.assertIsNone(lim.limit_reason(D(2026,10,27,17),st,self.pc,
                    {'unanswered_proactive_turns':4},{'id':'evening'}))
 def test_explicit_pause_resume_and_real_inbound_timestamp(self):
  now=D(2026,10,27,12)
  self.assertTrue(lim.inbound_patch('先别打扰我',now)['proactive_paused'])
  self.assertFalse(lim.inbound_patch('可以主动找我了',now)['proactive_paused'])
  self.assertNotIn('proactive_paused',lim.inbound_patch('他跟我说别打扰我',now))
  self.assertEqual(lim.inbound_patch('嗯',now)['proactive_unanswered_turns'],0)
 def test_retry_rechecks_budget_and_completed_receipt(self):
  item={'id':'retry','meta':{'beat_meta':{'id':'daily_chat','slot':SLOTS[3]}}}
  out={'contact':'demo_contact','proactive_round':item}
  memory.update_state('demo_contact',proactive_count=12,proactive_day=DAY)
  self.assertIn('次数',lim.retry_reason(out,D(2026,10,27,12)))
  lim.record_success('demo_contact',item,D(2026,10,27,12))
  self.assertIn('已确认',lim.retry_reason(out,D(2026,10,27,13)))
 def test_expired_outbox_never_sends_late(self):
  item={'id':'expired','expires':DAY+' 08:00:00','meta':{}}
  self.assertEqual(lim.retry_reason({'proactive_round':item},D(2026,10,27,12)),'主动草稿已过期')
 def test_concurrent_duplicate_receipts_charge_once(self):
  from concurrent.futures import ThreadPoolExecutor
  item={'id':'concurrent','meta':{'beat_meta':{'id':'morning'}}}
  with ThreadPoolExecutor(4) as pool:
   list(pool.map(lambda _:lim.record_success('demo_contact',item,D(2026,10,27,7,42)),range(10)))
  self.assertEqual(memory.read_state('demo_contact')['proactive_count'],0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_routine']['count'],1)
 def test_transport_retry_to_real_flush_accounts_and_does_not_repeat(self):
  with patch.object(sender,'send_via_onebot',return_value=(False,'network unavailable',None)):
   self.run_sender(D(2026,10,27,7,42))
  from channels import onebot_qq as qq
  messages=[]
  with patch('schedule_fidelity.outbox_item_stale',return_value=(False,'')),patch('schedule_fidelity.resolve_slot',return_value=SLOTS[1]),patch('time.sleep'):
   result=qq.flush_qq_outbox(lambda peer,text:messages.append(text),peer_id='demo_contact',now=D(2026,10,27,7,45))
   self.assertTrue(any(row.get('ok') for row in result),result)
   count=len(messages)
   qq.flush_qq_outbox(lambda peer,text:messages.append(text),peer_id='demo_contact',now=D(2026,10,27,7,46))
  self.assertEqual(len(messages),count)
  self.assertEqual(memory.read_state('demo_contact')['proactive_count'],0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_routine']['count'],1)
  self.run_sender(D(2026,10,27,7,47))
  self.assertEqual(len(self.sent),0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_count'],0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_routine']['count'],1)
 def test_legacy_heal_does_not_double_count_new_delivery_journal(self):
  memory.update_state('demo_contact',last_proactive_at=None)
  now=D(2026,10,27,7,42)
  with patch.object(lim,'record_success',side_effect=RuntimeError('crash')):
   with self.assertRaises(RuntimeError): self.run_sender(now)
  memory.heal_proactive_from_queue('demo_contact',now=now,queue_path=self.q)
  self.assertEqual(memory.read_state('demo_contact').get('proactive_count',0),0)
  self.run_sender(now+dt.timedelta(minutes=1))
  self.assertEqual(memory.read_state('demo_contact')['proactive_count'],0)
  self.assertEqual(memory.read_state('demo_contact')['proactive_routine']['count'],1)
 def test_late_receipt_cannot_undo_new_day_or_inbound_reset(self):
  memory.update_state('demo_contact',proactive_day='2026-10-28',proactive_count=2,
     last_inbound_at='2026-10-28 08:00:00',last_proactive_at='2026-10-28 08:10:00',
     proactive_unanswered_turns=0,proactive_beats={'day':'2026-10-28','done':{'morning':'08:10'}})
  lim.record_success('demo_contact',{'id':'late','meta':{'beat_meta':{'id':'evening'}}},D(2026,10,27,17))
  st=memory.read_state('demo_contact')
  self.assertEqual(st['proactive_count'],2)
  self.assertEqual(st['proactive_unanswered_turns'],0)
  self.assertEqual(st['last_proactive_at'],'2026-10-28 08:10:00')
  self.assertEqual(st['proactive_beats']['day'],'2026-10-28')
 def test_repeated_draft_skips_but_new_report_still_sends_without_reply(self):
  now=D(2026,10,27,17,2)
  rows=[('me','排练终于顺了，晚上还有一遍。',DAY+' 16:40:00','proactive')]
  for candidates in [[],[{'id':'evening','done_key':'evening','slot':SLOTS[5]}]]:
   with self.subTest(candidates=candidates), patch.object(pb,'due_beats',return_value=candidates):
    before=memory.read_state('demo_contact')
    repeated=ps.tick(now,self.pc,before,rows,random.Random(1),
                     draft_fn=lambda:'排练终于顺了 晚上还有一遍！')
    self.assertEqual(repeated['action'],'SKIP',repeated)
    self.assertEqual(memory.read_state('demo_contact'),before)
    fresh=ps.tick(now,self.pc,before,rows,random.Random(1),
                 draft_fn=lambda:'第二遍排练也结束了 这次转场顺多了')
    self.assertEqual(fresh['action'],'DRAFT',fresh)
 def test_repeat_guard_does_not_confuse_user_words_or_old_reports(self):
  now=D(2026,10,27,17)
  text='今天排练终于顺了'
  self.assertFalse(ps.repeated_draft(text,[('them',text,DAY+' 16:30:00')],now))
  self.assertFalse(ps.repeated_draft(text,[('me',text,DAY+' 08:30:00')],now))
  self.assertFalse(ps.repeated_draft('今天排练还是没顺', [('me',text,DAY+' 16:30:00')],now))

 def test_calendar_covers_all_hours(self):
  import plistlib
  pl=plistlib.loads((pathlib.Path(__file__).resolve().parents[1]/'launchd/com.xiaotian.proactive.plist').read_bytes())
  self.assertEqual(len(pl['StartCalendarInterval']),288)
  self.assertEqual({x['Hour'] for x in pl['StartCalendarInterval']},set(range(24)))

if __name__=='__main__': unittest.main()
