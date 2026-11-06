"""Part-time internship variety. Persona SSOT: data/persona_dossier.json.

Local half-days rotate independently of random draws for the rest of the day.
Business trips are whole-day plans, never a label squeezed into an evening slot.
"""
import datetime as dt

import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def tree():
    return json.loads((ROOT / 'data/internship_tree.json').read_text())


def activity_details(date, period='pm'):
    branches = [b for b in tree()['branches'] if period in b['periods']]
    import life_calendar as lc
    week = lc.internship_week_plan(date)
    ordered = sorted(week, key=lambda wd: (week[wd] != 'eve', wd))
    rank = ordered.index(date.weekday()) if date.weekday() in ordered else date.toordinal()
    outside = rank % 2 == 0
    if outside and period == 'eve':
        import evening_plan
        tasks = [a for a in evening_plan.ACTIVITIES if a['category']=='night_work']
        task = tasks[date.toordinal() % len(tasks)]
        return dict(activity='电影公司实习·'+task['activity'], bucket='work_internship',
                    scene_hints=task['scene_hints'], activity_id='film_internship_scout',
                    city='北京', location=task['location'], internship_mode='outdoor',
                    local_transit_minutes=60, tone='含往返接驳，工作间隙回')
    matches = [b for b in branches if b['outside'] == outside]
    if matches:
        branches = matches
    index = date.toordinal()
    branch = branches[index % len(branches)]
    task = branch['tasks'][(index // len(branches)) % len(branch['tasks'])]
    place = branch['locations'][(index // (len(branches)*len(branch['tasks']))) % len(branch['locations'])]
    return dict(activity='电影公司实习·'+place+'·'+task['name'],
                bucket='work_internship',scene_hints=task['scene_hints']+[place],
                activity_id=branch['activity_id'],city='北京',location=place,
                internship_mode='outdoor' if outside else 'indoor',
                local_transit_minutes=60 if branch['outside'] else 0,
                tone='含往返接驳，工作间隙回' if branch['outside'] else '实习中、回得慢')


def activity(date, period='pm'):
    item=activity_details(date,period)
    return item['activity'],item['bucket'],item['scene_hints'],item['activity_id']


def trip_due(date, kind):
    # At most one local-city day trip/month, outside holiday/review/exam periods.
    studies=json.loads((ROOT/'data/study_blocks.json').read_text())
    key=date.isoformat()
    if (studies.get('blocks') or studies.get('dates') or {}).get(key):
        return False
    if any(e.get('review_start','9999') <= key <= e.get('exam_date','0000') for e in studies.get('exams',[])):
        return False
    return kind == 'weekend' and date.weekday() == 5 and 8 <= date.day <= 14


def trip_day(date):
    """Fictional planned Tianjin work day with generous door-to-door buffers.

    Times are schedule budgets, not train timetables or booked arrangements.
    No company/client/project identities are invented.
    """
    destination = next(d for d in tree()['destinations'] if d['id']=='tianjin')
    place = destination['locations'][(date.month-1) % len(destination['locations'])]
    plan = [
        ('00:00','07:20','睡觉','sleep',[], '北京'),
        ('07:20','08:00','出差前吃早饭/检查取景清单','campus_life',['早餐','背包'],'北京'),
        ('08:00','11:00','实习出差·北京赴天津/市内接驳','travel',['实习出差交通','高铁车厢','车窗'],'北京→天津'),
        ('11:00','12:30',f'电影公司实习·天津{place}室外取景踩点','work_internship',['室外取景踩点','天津街景'],'天津'),
        ('12:30','13:30','出差午饭/休息','campus_life',['午饭'],'天津'),
        ('13:30','16:00',f'电影公司实习·天津{place}外景机位记录/协助现场收尾','work_internship',['外景跟组','场记板'],'天津'),
        ('16:00','19:00','实习出差·天津返京/返回宿舍','travel',['实习出差交通','高铁车厢','车窗'],'天津→北京'),
        ('19:00','20:00','返京吃晚饭','campus_life',['晚饭'],'北京'),
        ('20:00','22:10','回宿舍休息/简单整理出差笔记','weekend_home',['宿舍','笔记'],'北京'),
        ('22:10','23:00','回宿舍洗漱/准备睡','weekend_home',['宿舍','洗漱'],'北京'),
        ('23:00','24:00','睡觉','sleep',[],'北京'),
    ]
    slots = []
    for start,end,name,bucket,hints,city in plan:
        slot = dict(start=start,end=end,activity=name,bucket=bucket,scene_hints=hints,
                    location=city,tone='在路上或工作间隙回' if bucket in ('travel','work_internship') else '放松',
                    photo_ok=bucket!='sleep',energy=-1,delay=[10,120])
        slot['trip_id']='internship-'+date.isoformat()
        slot['phase']='transit' if bucket=='travel' else 'work' if bucket=='work_internship' else 'rest'
        if '→' in city:
            slot['from_city'],slot['to_city']=city.split('→')
        else:
            slot['city']=city
        if bucket=='work_internship': slot['activity_id']='film_internship_trip' 
        slots.append(slot)
    return dict(date=date.isoformat(),weekday=5,weekday_zh='六',kind='weekend',holiday=None,
                activity_id='film_internship_trip',photo_bias='work_internship',slots=slots,
                sleep_bounds=dict(wake='07:20',prep_start='22:10',prep_end='23:00'),
                notes='实习短途出差计划；往返各预留3小时含接驳，不代表实际车次或预订',material_hints=[])
