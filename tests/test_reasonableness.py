"""离线：活动池合理度闸门。不启 daemon、不调 agent CLI。"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import reasonableness as rz

pool = rz.load_pool()
assert len(pool.get("activities") or []) >= 10
assert rz.passes_rating(rz.activities_by_id()["travel_hemu"])
assert (rz.activities_by_id()["travel_hemu"]["reasonableness"]["typical_days"] == 7)
assert (rz.activities_by_id()["ski_chongli"]["reasonableness"]["typical_days"] == 4)
assert (rz.activities_by_id()["travel_sanya"]["reasonableness"]["typical_days"] == 12)

bad = [
    {"date": "2027-01-19", "activity_id": "ski_chongli", "kind": "winter_break",
     "slots": [{"start": "10:00", "end": "18:00", "activity": "崇礼滑雪", "bucket": "outdoor_outing"}]},
    {"date": "2027-01-20", "activity_id": "travel_hemu", "kind": "winter_break",
     "slots": [{"start": "10:00", "end": "18:00", "activity": "禾木", "bucket": "travel"}]},
]
ok, res = rz.may_enter_schedule(bad, log=False)
assert ok is False
assert any(i["code"] == "impossible_jump" for i in res["issues"])

sch = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "life_schedule_2026q4.json").read_text())
winter = [d for d in sch["days"] if d.get("date", "") >= "2027-01-19"]
ok2, res2 = rz.may_enter_schedule(winter, log=False)
assert ok2 and res2["score"] >= 0.9
segs = {s["activity_id"]: s["n_days"] for s in res2["segments"]}
assert segs.get("ski_chongli") == 4
assert segs.get("travel_hemu") == 7
assert segs.get("travel_sanya") == 12
assert res2["agent_hook"]["removed"] is True
hook = res2.get("agent_hook") or {}
assert hook.get("invoked") is False
print("OK reasonableness gate + winter arc", segs)
