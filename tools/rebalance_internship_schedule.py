#!/usr/bin/env python3
"""Apply the authorized 2–4 weekday internship policy to future plans only.
Persona SSOT: data/persona_dossier.json. No messages, models or STATE writes.
"""
import argparse,copy,datetime as dt,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import evening_plan as ep, life_calendar as lc, internship, reasonableness as rz


def work_window(day, period):
    if period=='pm':
        for s in day['slots']:
            if '12:00'<=s['start']<'17:00' and s['bucket'] in ('work_internship','school_library'):
                if ep.minutes(s['end'])-ep.minutes(s['start'])>=150:return ep.minutes(s['start']),ep.minutes(s['end'])
    available=copy.deepcopy(day)
    for s in available['slots']:
        if s['bucket']=='work_internship' and s['start']>='18:00':
            s['bucket']='weekend_home'
            s['activity']='宿舍自由时间'
    free=ep.window(available)
    if free and free[1]-free[0]>=150:return free
    return None


def replace_window(day,start,end,slots):
    day['slots']=[s for s in day['slots'] if ep.minutes(s['end'])<=start]+slots+[s for s in day['slots'] if ep.minutes(s['start'])>=end]


def rest_slots(start,end,offset=0):
    result=[]
    while start<end:
        finish=min(end,start+75)
        name,bucket=ep.QUIET[offset%len(ep.QUIET)]
        result.append(ep.slot(start,finish,name,bucket,location='学校宿舍',city='北京'))
        start=finish;offset+=1
    return result


def update(doc,start):
    result=copy.deepcopy(doc);days=result['days'];by={d['date']:d for d in days}
    fixed=lambda d:ep.protected(d) or any(s.get('execution_status')=='confirmed' for s in d['slots'])
    protected=lambda d:fixed(d) or d['kind']!='workday'
    weeks=sorted({lc._week_monday(dt.date.fromisoformat(d['date'])) for d in days if d['date']>=str(start) and not protected(d)})
    report=[]
    for monday in weeks:
        week=[by.get(str(monday+dt.timedelta(days=i))) for i in range(5)]
        week=[d for d in week if d]
        candidates=[d for d in week if d['date']>=str(start) and not protected(d) and work_window(d,'eve')]
        previous=[d for d in week if d not in candidates and any(s['bucket']=='work_internship' for s in d['slots'])]
        desired=lc.internship_week_plan(monday)
        # Partial first week includes already-lived days in the target.
        target=max(len(previous),len(desired));remaining=max(0,target-len(previous))
        preferred=[d for d in candidates if dt.date.fromisoformat(d['date']).weekday() in desired]
        rest=[d for d in candidates if d not in preferred]
        r=ep.rng('intern-migrate-'+str(monday));r.shuffle(rest)
        selected=(preferred+rest)[:remaining]
        order=sorted(selected,key=lambda d:(desired.get(dt.date.fromisoformat(d['date']).weekday())!='eve',d['date']))
        # Remove future old work only; class, rehearsal, user plans and sleep stay.
        for d in candidates:
            old_groups={s.get('outing_id') for s in d['slots'] if s['bucket']=='work_internship' and s.get('outing_id')}
            out=[]
            for s in d['slots']:
                if s['bucket']=='work_internship' or s.get('outing_id') in old_groups:
                    a,b=ep.minutes(s['start']),ep.minutes(s['end'])
                    out.append(ep.slot(a,b,'整理课程作业/自习' if a<1080 else '宿舍休息', 'school_library' if a<1080 else 'weekend_home',location='学校宿舍',city='北京'))
                else:out.append(s)
            d['slots']=out
        for rank,d in enumerate(order):
            date=dt.date.fromisoformat(d['date']);period='eve' if rank==0 else desired.get(date.weekday(),'pm')
            bounds=work_window(d,period)
            if not bounds:continue
            a,b=bounds;actual_period='eve' if a>=1080 else 'pm';outside=rank%2==0
            duration=min(180,b-a);end=a+duration
            info=internship.activity_details(date,actual_period)
            # Make mode explicit in persisted plans; balance by selected workdays.
            info['internship_mode']='outdoor' if outside else 'indoor'
            if not outside:
                branch=internship.tree()['branches'][0];task=branch['tasks'][date.toordinal()%len(branch['tasks'])]
                info.update(activity='电影公司实习·室内工作间·'+task['name'],scene_hints=task['scene_hints'],location='公司工作间',activity_id='film_internship',local_transit_minutes=0)
            if outside and actual_period=='pm':
                # Local daytime scouting uses daylight scene hints, not night scenes.
                info.update(activity='电影公司实习·本地取景踩点/机位与环境声记录',location='海淀校区周边公共街段',scene_hints=['白天街道','取景笔记','相机'],local_transit_minutes=60,activity_id='film_internship_scout')
            work=ep.slot(a,end,info.pop('activity'), 'work_internship')
            work.update(info);work['bucket']='work_internship'
            parts=[work]+rest_slots(end,b,date.toordinal())
            replace_window(d,a,b,parts)
            if actual_period=='eve':
                d.pop('evening_plan',None);d.pop('evening_followup',None)
                d['photo_bias']='work_internship'
            d=ep.enrich_night_work(d)
            # Daytime outside work also gets explicit round-trip slots.
            for idx,s in list(enumerate(d['slots'])):
                if s['bucket']=='work_internship' and s.get('internship_mode')=='outdoor' and s['start']<'18:00' and not s.get('leg'):
                    x,y=ep.minutes(s['start']),ep.minutes(s['end']);common=dict(outing_id='intern-local-'+d['date'],internship_mode='outdoor',city='北京')
                    core=dict(s,start=ep.hm(x+30),end=ep.hm(y-30),leg='activity',**common)
                    core['local_transit_minutes']=0
                    core['transit_in_separate_slots']=True
                    d['slots'][idx:idx+1]=[ep.slot(x,x+30,'实习外出去程','work_internship',leg='outbound',**common),core,ep.slot(y-30,y,'实习外出返校','work_internship',leg='return',**common)]
            by[d['date']].update(d)
        report.append(dict(week=str(monday),target=target,actual=len(previous)+len(selected),days=[d['date'] for d in selected]))
    # Rotate at most one existing occurrence per category into a newly added
    # template. Dates, categories and slot boundaries remain fixed.
    swapped=set()
    originals={a['id']:a for a in ep.ACTIVITIES}
    for d in days:
        if d['date']<str(start) or fixed(d):continue
        for s in d['slots']:
            old=originals.get(s.get('outing_activity_id'));cat=(old or {}).get('category')
            if s.get('leg')!='activity' or not old or cat in swapped or cat in ('night_work','campus_walk'):continue
            new=next((a for a in ep.ACTIVITIES if a['category']==cat and int(a['id'].rsplit('_',1)[1])>=6),None)
            if new:
                oid=s['outing_id'];s['activity']=new['activity'];s['scene_hints']=new['scene_hints'];s['scene_id']=new['scene_id']
                for member in d['slots']:
                    if member.get('outing_id')==oid:member['outing_activity_id']=new['id']
                d.setdefault('evening_plan',{}).update(outing=new['id'],category=cat);swapped.add(cat)
        # Only overlong free evening slots are split; sleep/prep remains intact.
        out=[]
        for s in d['slots']:
            a,b=ep.minutes(s['start']),ep.minutes(s['end'])
            if s.get('source')=='evening_plan' and s['bucket']=='weekend_home' and a>=1080 and b-a>90 and '洗漱' not in s['activity']:
                out.extend(rest_slots(a,b,dt.date.fromisoformat(d['date']).toordinal()))
            else:out.append(s)
        d['slots']=out
        detailed=ep.detail_day(d,history=[x for x in days if x['date']<d['date']]);d.update(detailed)
    ok,review=rz.may_enter_schedule(days,use_agent=False,log=False)
    if not ok:raise ValueError(review['issues'])
    result.setdefault('enrichment',{})['internship_rebalance']={'from_date':str(start),'weeks':report,'authorized':'用户要求工作日随机2–4天，均衡室内外；其余最小修复','local_checks_ok':ok}
    result['rules']={k:v.replace('3～5','2～4') if isinstance(v,str) else v for k,v in result.get('rules',{}).items()}
    return result,report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--from-date',required=True);p.add_argument('--apply',action='store_true');args=p.parse_args()
    path=ROOT/'data/life_schedule_2026q4.json';doc,report=update(json.loads(path.read_text()),dt.date.fromisoformat(args.from_date))
    if args.apply:path.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
