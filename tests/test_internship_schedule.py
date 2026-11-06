"""Offline itinerary regression: no real services, models, or memory writes."""
import sys,pathlib,datetime as dt,copy,json,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import internship as intern, schedule_travel_review as review, reasonableness as rz
import life_calendar as lc, schedule_pipeline as sp

class InternshipTests(unittest.TestCase):
 def setUp(self): self.day=intern.trip_day(dt.date(2026,11,14))
 def codes(self,days): return {i['code'] for i in review.review(days)['issues']}
 def test_complete_trip_passes_gate(self):
  self.assertTrue(review.review([self.day])['ok'])
  self.assertTrue(rz.may_enter_schedule([self.day],use_agent=False,log=False)[0])
 def test_three_cities_without_transit_rejected(self):
  day={'date':'2026-11-14','slots':[
   dict(start='08:00',end='12:00',activity='杭州取景',bucket='work_internship'),
   dict(start='13:00',end='17:00',activity='上海跟组',bucket='work_internship'),
   dict(start='19:00',end='24:00',activity='北京回宿舍',bucket='weekend_home')]}
  codes=self.codes([day])
  self.assertIn('itinerary_city_jump',codes)
  self.assertIn('itinerary_too_many_cities',codes)
  self.assertFalse(rz.may_enter_schedule([day],use_agent=False,log=False)[0])
 def test_short_transit_and_location_contradiction_rejected(self):
  self.day['slots'][2]['start']='10:30'
  self.assertIn('itinerary_transit_short',self.codes([self.day]))
  self.day['slots'][3]['city']='杭州'
  self.assertIn('itinerary_location_conflict',self.codes([self.day]))
 def test_far_trip_cannot_be_one_day(self):
  day=json.loads(json.dumps(self.day,ensure_ascii=False).replace('天津','杭州'))
  self.assertIn('itinerary_stay_short',self.codes([day]))
  self.assertIn('itinerary_transit_short',self.codes([day]))
 def test_missing_return_and_school_conflict_rejected(self):
  self.day['slots'][6].update(from_city='天津',to_city='杭州')
  self.day['slots'][5]['bucket']='school_class'
  codes=self.codes([self.day]);self.assertIn('itinerary_unknown_route',codes)
  self.assertIn('itinerary_study_conflict',codes)
  self.day['slots'][-1]['city']='天津'
  self.assertIn('itinerary_not_round_trip',self.codes([self.day]))
 def test_three_day_shanghai_with_hotel_and_return_passes(self):
  def day(date,parts):
   slots=[]
   for start,end,city,bucket in parts:
    slot=dict(start=start,end=end,city=city,location=city,bucket=bucket,
              activity='休息' if bucket=='sleep' else '工作与休息',trip_id='shanghai-work',phase='work' if bucket=='work_internship' else 'rest')
    if '→' in city:
     slot.pop('city');slot.update(phase='transit',from_city=city.split('→')[0],to_city=city.split('→')[1])
    slots.append(slot)
   return dict(date=date,kind='holiday',slots=slots)
  days=[day('2026-11-13',[('00:00','08:00','北京','sleep'),('08:00','14:00','北京→上海','travel'),('14:00','18:00','上海','work_internship'),('18:00','23:00','上海','weekend_home'),('23:00','24:00','上海','sleep')]),
        day('2026-11-14',[('00:00','08:00','上海','sleep'),('08:00','17:00','上海','work_internship'),('17:00','23:00','上海','weekend_home'),('23:00','24:00','上海','sleep')]),
        day('2026-11-15',[('00:00','08:00','上海','sleep'),('08:00','12:00','上海','work_internship'),('12:00','18:00','上海→北京','travel'),('18:00','23:00','北京','weekend_home'),('23:00','24:00','北京','sleep')])]
  self.assertTrue(review.review(days)['ok'],review.review(days))
  days[1]['date']='2026-11-17'
  self.assertIn('itinerary_broken_trip',self.codes(days))
 def test_malformed_time_rejected_without_crash(self):
  self.day['slots'][0]['end']='bad'
  self.assertIn('itinerary_invalid_time',self.codes([self.day]))

 def test_tree_variety_stable_evening_has_indoor_and_outdoor(self):
  dates=[dt.date(2026,11,1)+dt.timedelta(days=i) for i in range(30)]
  names={intern.activity_details(d)['activity'] for d in dates}
  self.assertGreater(len(names),10)
  for d in dates:
   self.assertEqual(intern.activity_details(d),intern.activity_details(d))
   item=intern.activity_details(d,'eve')
   self.assertEqual(item['local_transit_minutes'],60 if item['internship_mode']=='outdoor' else 0)
  self.assertEqual({intern.activity_details(d,'eve')['internship_mode'] for d in dates},{'indoor','outdoor'})
 def test_exam_and_holiday_do_not_get_automatic_trip(self):
  self.assertTrue(intern.trip_due(dt.date(2026,11,14),'weekend'))
  self.assertFalse(intern.trip_due(dt.date(2027,1,9),'weekend'))
  self.assertFalse(intern.trip_due(dt.date(2026,11,14),'holiday'))
  with patch.object(lc,'_material_hints',return_value=[]):
   day=lc.generate_day(dt.date(2026,11,14))
  self.assertEqual(day['activity_id'],'film_internship_trip')
  self.assertTrue(review.review([day])['ok'])
 def test_month_fill_blocks_bad_trip_before_file_write(self):
  bad=copy.deepcopy(self.day);bad['slots'][3]['city']='杭州'
  with tempfile.TemporaryDirectory() as tmp, patch.object(lc,'generate_day',side_effect=lambda d: dict(date=d.isoformat(),kind='weekend',slots=[])):
   path=pathlib.Path(tmp)/'schedule.json';path.write_text('original')
   result=sp.ensure_month_days(2026,11,doc={'days':[bad]},path=path,write=True)
   self.assertFalse(result['ok']);self.assertEqual(path.read_text(),'original')
 def test_all_saved_internship_days_pass_special_review(self):
  days=json.loads((ROOT/'data/life_schedule_2026q4.json').read_text())['days']
  self.assertTrue(review.review(days)['ok'])
 def test_reference_sync_rejects_changed_source_before_import(self):
  import sqlite3
  sys.path.insert(0,str(ROOT/'tools'));import sync_internship_refs as sync
  con=sqlite3.connect(':memory:');con.execute('CREATE TABLE images(scene_id,sha256,path)')
  manifest={'images':[dict(scene_id='work-001',sha256='0'*64,source_url='https://example.com/ref.jpg')]}
  with patch.object(sync.b,'ensure',return_value=con), patch.object(sync.b,'download_one',return_value=(b'changed','ok')), patch.object(sync.b,'save_image') as save:
   result=sync.sync(manifest)
   self.assertEqual(len(result['failed']),1);save.assert_not_called()

 def test_scene_catalog_rebuild_preserves_new_scenes(self):
  sys.path.insert(0,str(ROOT/'tools'));import gen_outfit_scenes as gen
  generated={s['id']:s for s in gen.build()[0]}
  saved={s['id']:s for s in json.loads((ROOT/'data/outfit_scenes.json').read_text())['scenes']}
  for i in range(1,10): self.assertEqual(generated[f'work-{i:03}'],saved[f'work-{i:03}'])

if __name__=='__main__': unittest.main()
