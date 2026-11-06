"""离线：晚间选择、往返、旅行隔离、旧注入修复、review Agent 移除。"""
import copy
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
import evening_plan as ep
import life_calendar as lc
import reasonableness as rz
import schedule_pipeline as sp
import update_evening_schedule as migration


def base(date='2026-11-02'):
    return dict(date=date, kind='workday', slots=[
        ep.slot(0, 480, '睡觉', 'sleep'),
        ep.slot(480, 720, '上课', 'school_class'),
        ep.slot(720, 780, '午饭', 'campus_life'),
        ep.slot(780, 1020, '自习', 'school_library'),
        ep.slot(1020, 1110, '吃晚饭', 'campus_life'),
        ep.slot(1110, 1380, '宿舍追剧', 'weekend_home'),
        ep.slot(1380, 1410, '回宿舍洗漱/准备睡', 'weekend_home'),
    ])


class EveningPlanTest(unittest.TestCase):
    def setUp(self):
        self.materials = patch.object(lc, '_material_hints', return_value=[])
        self.materials.start()
        self.addCleanup(self.materials.stop)

    def test_weekday_frequency_and_all_weekdays_eligible(self):
        weekdays = set()
        monday = dt.date(2026, 11, 2)
        for week in range(24):
            start = monday + dt.timedelta(weeks=week)
            if len(lc._week_eligible_workdays(start)) != 5:
                continue
            days = [lc.generate_day(start + dt.timedelta(days=i)) for i in range(5)]
            outings = [d for d in days if d.get('evening_plan', {}).get('outing')]
            self.assertIn(len(outings), (1, 2), start)
            weekdays.update(dt.date.fromisoformat(d['date']).weekday() for d in outings)
            self.assertEqual(days, [lc.generate_day(start + dt.timedelta(days=i)) for i in range(5)])
        self.assertEqual(weekdays, {0, 1, 2, 3, 4})

    def test_fatigue_reduces_selection_and_eve_work_blocks_it(self):
        counts = Counter()
        with patch.object(lc, '_week_eligible_workdays', return_value=list(range(5))), patch.object(lc, 'internship_week_plan', return_value={}):
            for week in range(150):
                monday = dt.date(2026, 11, 2) + dt.timedelta(weeks=week)
                days = [base((monday + dt.timedelta(days=i)).isoformat()) for i in range(5)]
                days[0]['slots'][3]['bucket'] = 'work_internship'
                days[4]['slots'][5]['bucket'] = 'work_internship'
                picked = ep.week_plan(monday, days)
                self.assertNotIn(days[4]['date'], picked)
                counts.update(dt.date.fromisoformat(key).weekday() for key in picked)
        self.assertLess(counts[0], sum(counts[i] for i in (1, 2, 3)) / 3)

    def test_weekend_usually_has_one_outing_and_keeps_a_rest_night(self):
        count = 0
        for week in range(8):
            saturday = dt.date(2026, 11, 7) + dt.timedelta(weeks=week)
            days = [lc.generate_day(saturday + dt.timedelta(days=i)) for i in range(2)]
            outings = [d for d in days if d.get('evening_plan', {}).get('outing')]
            self.assertLessEqual(len(outings), 1)
            count += len(outings)
        self.assertGreaterEqual(count, 4)

    def test_outings_have_duration_transport_and_no_adjacent_repeat(self):
        previous = None
        found = Counter()
        for i in range(100):
            date = dt.date(2026, 11, 2) + dt.timedelta(days=i)
            key = date.isoformat()
            day = ep.enrich_day(base(key), selected={key})
            aid = day['evening_plan']['outing']
            self.assertIsNotNone(aid)
            self.assertNotEqual(aid, previous)
            previous = aid
            legs = [s for s in day['slots'] if s.get('outing_id')]
            self.assertEqual([s['leg'] for s in legs], ['outbound', 'activity', 'return'])
            duration = ep.minutes(legs[1]['end']) - ep.minutes(legs[1]['start'])
            short = aid in {a[0] for a in ep.SHORT_OUTINGS}
            self.assertTrue(30 <= duration <= 90 if short else 90 <= duration <= 150)
            found['short' if short else 'long'] += 1
            self.assertLessEqual(legs[-1]['end'], '23:00')
            self.assertEqual(rz.check_slot_safety([day]), [])
        self.assertGreater(found['short'], found['long'] * 2)
        self.assertGreater(found['long'], 0)

    def test_quiet_evening_is_not_one_four_hour_label(self):
        day = ep.enrich_day(base(), selected=set())
        activities = [s['activity'] for s in day['slots'] if '18:30' <= s['start'] < '23:00']
        self.assertEqual(len(activities), 2)
        self.assertEqual(len(set(activities)), 2)
        self.assertEqual(day['slots'][-1]['bucket'], 'sleep')
        self.assertEqual(day['slots'][-1]['end'], '24:00')

    def test_exam_trip_and_user_appointments_are_preserved(self):
        for marker in ({'kind': 'exam_week'}, {'trip_id': 'confirmed-trip'}, {'activity_id': 'travel_hemu'}):
            day = base(); day.update(marker)
            self.assertEqual(ep.enrich_day(day, selected={day['date']}), day)
        day = base(); day['slots'][5]['source'] = 'user_override'
        self.assertEqual(ep.enrich_day(day), day)

    def test_no_outing_over_evening_internship(self):
        day = base(); day['slots'][5]['bucket'] = 'work_internship'
        changed = ep.enrich_day(day, selected={day['date']})
        work = [s for s in changed['slots'] if s['bucket'] == 'work_internship']
        self.assertEqual(work[0]['start'], day['slots'][5]['start'])
        self.assertEqual(work[-1]['end'], day['slots'][5]['end'])
        self.assertFalse(any(s.get('outing_id') and s['bucket'] != 'work_internship' for s in changed['slots']))

    def test_single_day_holiday_generator_never_invents_long_trip(self):
        for i in range(14):
            day = lc.generate_day(dt.date(2027, 2, 1) + dt.timedelta(days=i))
            self.assertFalse(any(s.get('bucket') == 'travel' for s in day['slots']))
            self.assertEqual(rz.check_slot_safety([day]), [])

    def test_safety_catches_missing_return_and_hidden_long_trip(self):
        day = ep.enrich_day(base(), selected={'2026-11-02'})
        day['slots'] = [s for s in day['slots'] if s.get('leg') != 'return']
        self.assertIn('local_outing_incomplete', {i['code'] for i in rz.check_slot_safety([day])})
        day = base(); day['slots'][-1].update(activity='梅里雪山', activity_id='travel_hemu')
        self.assertIn('unplanned_long_distance_slot', {i['code'] for i in rz.check_slot_safety([day])})

    def test_research_never_replaces_meals_prep_or_activities(self):
        day = ep.enrich_day(base(), selected={'2026-11-02'})
        before = copy.deepcopy(day)
        ideas = [{'title':'新疆禾木/喀纳斯冬游'}, {'title':'citywalk'}]
        with patch.object(sp, 'load_research_idea_titles', return_value=ideas):
            result = sp.inject_research_into_month(2026, 11, doc={'days':[day]})
        after = result['doc']['days'][0]
        for old, new in zip(before['slots'], after['slots']):
            self.assertEqual(old, {k:v for k,v in new.items() if k != 'research_inspiration'})
            self.assertNotIn('禾木', str(new.get('research_inspiration', '')))

    def test_removed_agent_cannot_be_reenabled_by_old_env_or_flag(self):
        with patch.dict(os.environ, {'REASONABLENESS_AGENT':'1'}), patch.object(rz.subprocess, 'run') as run, patch.object(rz, '_agent_bin') as binary:
            result = rz.review([base()], use_agent=True)
            self.assertTrue(result['agent_hook']['removed'])
            self.assertFalse(result['agent_hook']['invoked'])
            run.assert_not_called(); binary.assert_not_called()

    def test_returning_to_dorm_does_not_trigger_premature_goodnight(self):
        import proactive_beats as pb
        day = ep.enrich_day(base(), selected={'2026-11-02'})
        state = {'life_today': day, 'proactive_beats': {'day': day['date'], 'done': {}}}
        with patch.object(pb, 'pick_surf_item', return_value=None):
            for s in day['slots']:
                if '回宿舍' not in s['activity'] and '洗漱' not in s['activity']:
                    continue
                minute = ep.minutes(s['start'])
                now = dt.datetime(2026, 11, 2, minute // 60, minute % 60)
                state['life_now'] = {**s, 'date': day['date'], 'kind': day['kind']}
                beats = pb.due_beats(now, state, {'beats': {'enabled': True}})
                ids = {b['id'] for b in beats}
                if '洗漱' in s['activity']:
                    self.assertIn('pre_sleep', ids)
                else:
                    self.assertNotIn('pre_sleep', ids)

    def test_month_rejects_teleport_before_any_write(self):
        day = base(); day['slots'][-1].update(activity='新疆禾木', activity_id='travel_hemu')
        with tempfile.TemporaryDirectory() as tmp, patch.object(lc, 'generate_day', side_effect=lambda date: base(date.isoformat())):
            path = pathlib.Path(tmp) / 'schedule.json'; path.write_text('original')
            result = sp.ensure_month_days(2026, 11, doc={'days':[day]}, path=path, write=True)
            self.assertFalse(result['ok'])
            self.assertEqual(path.read_text(), 'original')

    def test_saved_repairs_and_full_schedule_pass(self):
        doc = json.loads(lc.DRAFT_FILE.read_text())
        self.assertTrue(rz.review(doc['days'])['ok'])
        for key in ('2026-11-01', '2026-11-07'):
            day = next(d for d in doc['days'] if d['date'] == key)
            self.assertFalse(any(s.get('source') == 'research_inject' for s in day['slots']))
            self.assertTrue(any(s.get('leg') == 'return' for s in day['slots']))
            self.assertTrue(any('洗漱' in s['activity'] for s in day['slots']))

    def test_migration_preserves_past_freeze_record_and_confirmed_trip(self):
        past = base('2026-10-27')
        future = base('2026-11-02')
        trip = base('2026-11-14'); trip['source'] = 'user_override'
        for s in trip['slots']: s['source'] = 'user_override'
        doc = {'days':[past, future, trip], 'frozen':{'active':True,'start':'2026-11-01','end':'2026-11-30'}}
        out, _ = migration.update(doc, dt.date(2026,10,28), include_frozen=True)
        self.assertEqual(out['days'][0], past)
        self.assertEqual(out['days'][-1], trip)
        self.assertEqual(out['frozen'], doc['frozen'])

if __name__ == '__main__':
    unittest.main()
