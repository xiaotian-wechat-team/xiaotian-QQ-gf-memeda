"""离线：freeze / override / study-priority / inbound / inject。不启 daemon、不调 cursor-agent。"""
import json, sys, pathlib, datetime as dt
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import schedule_pipeline as sp

TMP = ROOT / "data" / "devtest" / "life_schedule_pipeline_test.json"
TMP.parent.mkdir(parents=True, exist_ok=True)
base = {
    "version": 1,
    "tz": "Asia/Shanghai",
    "range": {"start": "2026-11-01", "end": "2026-11-30", "days": 30},
    "days": [
        {
            "date": "2026-11-01",
            "weekday": 6,
            "weekday_zh": "日",
            "kind": "weekend",
            "slots": [
                {"start": "00:00", "end": "10:00", "activity": "睡觉", "bucket": "sleep"},
                {"start": "13:00", "end": "18:00", "activity": "自己逛街", "bucket": "city_fun"},
                {"start": "18:30", "end": "22:00", "activity": "宿舍刷剧", "bucket": "weekend_home"},
            ],
        },
        {
            "date": "2026-11-07",
            "weekday": 5,
            "weekday_zh": "六",
            "kind": "weekend",
            "slots": [
                {"start": "00:00", "end": "10:00", "activity": "睡觉", "bucket": "sleep"},
                {"start": "13:00", "end": "18:00", "activity": "周末闲逛", "bucket": "city_fun"},
            ],
        },
    ],
    "frozen": None,
    "overrides": [],
}
TMP.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n")

fr = sp.freeze_month(2026, 11, path=TMP)
assert fr["active"]
doc = sp.load_schedule(TMP)
assert sp.is_frozen(doc, on=dt.date(2026, 11, 15))
sp.unfreeze(path=TMP)

assert sp.detect_user_override_intent("下周陪我出去玩")
assert sp.detect_user_override_intent("你好呀") is None

pend_path = ROOT / "data" / "devtest" / "override_pending_test.json"
old_pend = sp.OVERRIDE_PENDING
sp.OVERRIDE_PENDING = pend_path
try:
    # inbound hook
    ov = sp.note_inbound_user_message("下周陪我出去玩", cid="test")
    assert ov["triggered"] and not ov["refused"]
    assert ov["pending"]["status"] == "awaiting_user_confirm"
    yld = sp.review_agents_must_yield(sp.load_schedule(TMP))
    assert yld["yield"] and "user_override_pending" in yld["reasons"]

    sp.confirm_override_from_user_reply(
        "周六下午三点三里屯见",
        date="2026-11-01",
        start="15:00",
        end="19:00",
        activity="和他三里屯逛街",
        route="三里屯",
    )
    res = sp.apply_override_slot(path=TMP)
    assert res["ok"] and res["others_unchanged"]
    doc2 = sp.load_schedule(TMP)
    day = next(d for d in doc2["days"] if d["date"] == "2026-11-01")
    acts = [s["activity"] for s in day["slots"]]
    assert "和他三里屯逛街" in acts
    assert "宿舍刷剧" in acts

    # study hard-block: exam week date
    sp.save_override_pending(None)
    sp.begin_override_confirm("下周陪我出门")
    pend = sp.confirm_override_from_user_reply(
        "周六见", date="2027-01-15", start="15:00", end="19:00",
        activity="出门", route="三里屯",
    )
    assert pend["status"] == "study_refused"
    exam = {"date": "2027-01-15", "kind": "exam_week",
            "slots": [{"activity": "期末复习·图书馆", "bucket": "school_library"}]}
    blk = sp.study_priority_block("2027-01-15", day=exam, user_ask_outing=True)
    assert blk["block"] and blk.get("blocks_override") and not blk.get("user_override_still_wins")
    defense = {"date": "2027-05-20", "kind": "thesis_defense",
               "slots": [{"activity": "毕业答辩", "bucket": "school_class"}], "notes": "毕业答辩"}
    blk2 = sp.study_priority_block("2027-05-20", day=defense, user_ask_outing=True)
    assert blk2["reason"] == "thesis_defense" and blk2.get("blocks_override")
finally:
    sp.OVERRIDE_PENDING = old_pend
    if pend_path.exists():
        pend_path.unlink()

# inject research into month (uses real research file if present)
inj = sp.inject_research_into_month(2026, 11, doc=sp.load_schedule(TMP), path=TMP, max_inject=2, write=True)
assert inj["ok"]
print("OK schedule_pipeline freeze/override/study/inbound/inject", "injected", inj.get("injected"))
