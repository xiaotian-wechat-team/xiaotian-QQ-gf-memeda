#!/usr/bin/env python3
"""Read-only dedicated itinerary review. Persona: data/persona_dossier.json.
No service start, no schedule rewrite. Exit 1 on invalid itinerary.
"""
import argparse,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import schedule_travel_review

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--schedule',type=pathlib.Path,default=ROOT/'data/life_schedule_2026q4.json')
    p.add_argument('--report',type=pathlib.Path)
    a=p.parse_args()
    result=schedule_travel_review.review(json.loads(a.schedule.read_text())['days'])
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(text)
    print(text,end='')
    return 0 if result['ok'] else 1

if __name__=='__main__': raise SystemExit(main())
