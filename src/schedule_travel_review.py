"""Dedicated internship itinerary review; config/schedule_review.json is policy.
Persona SSOT: data/persona_dossier.json. Read-only, no model calls or file writes.
"""
import datetime as dt
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def config():
    return json.loads((ROOT/'config/schedule_review.json').read_text())


def minutes(value):
    h,m=map(int,value.split(':'))
    if not 0<=h<=24 or not 0<=m<60 or (h==24 and m): raise ValueError(value)
    return h*60+m


def review(days):
    cfg=config()
    tree=json.loads((ROOT/cfg['activity_tree']).read_text())
    destinations={x['city']:x for x in tree['destinations']}
    home=cfg['home_city']; known=[home,*destinations]
    issues=[]; trips={}; previous_city=None; previous_date=None
    def error(code,day,msg):
        issues.append(dict(code=code,severity='error',segment=day.get('date'),msg=msg))
    for day in sorted(days,key=lambda x:x['date']):
        slots=day.get('slots') or []
        relevant=any(s.get('trip_id') or s.get('bucket')=='work_internship' or '出差' in s.get('activity','') for s in slots)
        if not relevant:
            previous_city=None; previous_date=None
            continue
        trip_day=any(s.get('trip_id') or '出差' in s.get('activity','') or
                     (s.get('bucket')=='work_internship' and (s.get('city') not in (None,home) or any(c in str(s.get('location',''))+s.get('activity','') for c in destinations)))
                     for s in slots)
        if trip_day:
            studies=json.loads((ROOT/'data/study_blocks.json').read_text())
            if day.get('kind') not in ('weekend','holiday','winter_break','summer_break') or any(e.get('review_start','9999') <= day['date'] <= e.get('exam_date','0000') for e in studies.get('exams',[])):
                error('itinerary_study_conflict',day,'仅在无课空闲日安排出差，复习考试窗不可排')
        current=previous_city if previous_date and (dt.date.fromisoformat(day['date'])-previous_date).days==1 else None
        end=0; cities=set(); sleep_minutes=0
        for s in slots:
            text=s.get('activity',''); loc=s.get('location','')
            if trip_day and (not s.get('trip_id') or s.get('phase') not in ('rest','work','transit')):
                error('itinerary_missing_location',day,'出差每槽必须有trip_id与有效phase')
            if isinstance(loc,dict): loc=loc.get('city','')
            try: start,finish=minutes(s['start']),minutes(s['end'])
            except (KeyError,ValueError,TypeError):
                error('itinerary_invalid_time',day,text); continue
            if finish<=start or start<end or (trip_day and start!=end):
                error('itinerary_overlap_or_gap',day,f'{s.get("start")}–{s.get("end")} {text}')
            if s.get('local_transit_minutes'):
                if finish-start < int(s['local_transit_minutes'])+120:
                    error('itinerary_local_work_short',day,'本市外出需接驳预算及至少2小时工作')
            end=finish
            if s.get('bucket')=='sleep': sleep_minutes+=max(0,finish-start)
            transit=s.get('phase')=='transit' or bool(s.get('from_city') or s.get('to_city'))
            if transit:
                origin,dest=s.get('from_city'),s.get('to_city')
                if not origin or not dest or origin not in known or dest not in known:
                    error('itinerary_unknown_route',day,'交通必须写明已审线路起终城市');continue
                if current and current!=origin: error('itinerary_city_jump',day,f'{current}突然从{origin}出发')
                route=destinations.get(dest if origin==home else origin if dest==home else '')
                if route is None: error('itinerary_unknown_route',day,f'{origin}→{dest}尚无交通预算，需review')
                elif finish-start < route['transit_hours_from_beijing']*60:
                    error('itinerary_transit_short',day,f'{origin}→{dest}不足{route["transit_hours_from_beijing"]}小时交通预算')
                cities.update([origin,dest]);current=dest
            else:
                found=[c for c in known if c in text or c in loc]
                city=s.get('city') or (found[0] if len(found)==1 else home if s.get('bucket','').startswith('school_') or '回宿舍' in text else None)
                if s.get('city') and any(c!=s['city'] for c in found): error('itinerary_location_conflict',day,text)
                if trip_day and (not city or not s.get('trip_id') or not s.get('phase')):
                    error('itinerary_missing_location',day,'出差每槽须有trip_id、city/location与phase')
                if city:
                    if city not in known: error('itinerary_unknown_route',day,f'{city}没有已审交通与停留预算')
                    if current and city!=current: error('itinerary_city_jump',day,f'{current}→{city}缺交通槽')
                    current=city;cities.add(city)
                if trip_day and (s.get('bucket','').startswith('school_') or day.get('kind') in ('exam_week','exam_review','finals','thesis_defense')):
                    error('itinerary_study_conflict',day,'出差不得覆盖课程或复习考试')
            tid=s.get('trip_id')
            if tid:
                trip=trips.setdefault(tid,{'dates':set(),'cities':set(),'first':None,'last':None})
                trip['dates'].add(day['date']);trip['cities'].update(cities)
                if trip['first'] is None: trip['first']=s.get('from_city') if transit else s.get('city')
                trip['last']=current
        if trip_day:
            if end!=1440: error('itinerary_incomplete_day',day,'出差日应覆盖至24:00含住宿休息')
            if len(cities)>cfg['max_cities_per_day']: error('itinerary_too_many_cities',day,'同一天城市过多，需拆分出差行程')
            if sleep_minutes<cfg['min_night_rest_hours']*60: error('itinerary_rest_short',day,'睡眠休息不足')
        previous_city=current;previous_date=dt.date.fromisoformat(day['date'])
    for tid,trip in trips.items():
        day={'date':min(trip['dates'])}
        span=(dt.date.fromisoformat(max(trip['dates']))-dt.date.fromisoformat(min(trip['dates']))).days+1
        if span!=len(trip['dates']): error('itinerary_broken_trip',day,'同一出差行程的日期不连续')
        if trip['first']!=home or trip['last']!=home: error('itinerary_not_round_trip',day,f'{tid}缺从北京出发或回京的完整行程')
        for city in trip['cities']-{home}:
            dest=destinations.get(city)
            if dest and len(trip['dates'])<dest['min_trip_days']:
                error('itinerary_stay_short',day,f'{city}出差至少{dest["min_trip_days"]}天，不能塞进半天/一天')
    return {'ok':not issues,'issues':issues,'policy':'config/schedule_review.json','n_trips':len(trips)}
