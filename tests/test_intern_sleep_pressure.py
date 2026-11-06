"""离线短测：实习周频 2～4 + 模糊作息 + 指数睡眠压力。不启 daemon。"""
import sys, pathlib, datetime as dt
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import life_calendar as lc
import schedule
import proactive_scheduler as ps

# --- internship week plan: only 2..4 on eligible weeks ---
c = Counter()
monday = dt.date(2026, 10, 26)
while monday < dt.date(2027, 1, 12):
    el = lc._week_eligible_workdays(monday)
    plan = lc.internship_week_plan(monday)
    if len(el) >= 2:
        assert 2 <= len(plan) <= 4, (monday, plan, el)
        assert set(plan).issubset(set(el))
        assert all(v in ("pm", "eve") for v in plan.values())
        c[len(plan)] += 1
    monday += dt.timedelta(days=7)
assert set(c) <= {2, 3, 4} and sum(c.values()) >= 8
print("OK internship week plan", dict(sorted(c.items())))

# --- generate_day: fuzzy minutes + prep sleep + intern on planned days ---
d = dt.date(2026, 10, 27)  # Tuesday in a 4-intern week
day = lc.generate_day(d)
sb = day["sleep_bounds"]
assert sb["wake"] and ":" in sb["wake"]
# at least some slots have non-:00 minutes across a sample week
nons = 0
for i in range(7):
    dd = lc.generate_day(dt.date(2026, 10, 26) + dt.timedelta(days=i))
    for s in dd["slots"]:
        if s["start"] not in ("00:00",) and not s["start"].endswith(":00"):
            nons += 1
        if not s["end"].endswith(":00"):
            nons += 1
assert nons >= 10, nons
assert any("准备睡" in (s.get("activity") or "") for s in day["slots"])
plan = lc.internship_week_plan(d)
has = any(s.get("bucket") == "work_internship" for s in day["slots"])
assert has == (d.weekday() in plan)
print("OK fuzzy slots + prep sleep + intern match plan")

# --- sleep_pressure monotonic in evening ---
anchor = schedule.sleep_pressure(dt.datetime(2026, 10, 27, 21, 40))
p1 = schedule.sleep_pressure(dt.datetime(2026, 10, 27, 22, 17))
p2 = schedule.sleep_pressure(dt.datetime(2026, 10, 27, 23, 30))
p3 = schedule.sleep_pressure(dt.datetime(2026, 10, 28, 0, 45))
assert abs(anchor) < 1e-9
assert 0 < p1 < p2 < p3 <= 1.0
assert p3 >= 0.55  # soft-gate threshold region
# delay stretches
d_early = schedule.reply_delay(dt.datetime(2026, 10, 27, 15, 0), rng=__import__("random").Random(0))
d_late = schedule.reply_delay(dt.datetime(2026, 10, 28, 1, 30), rng=__import__("random").Random(0))
# same rng stream position differs because current() path differs; just check late pressure>0
assert schedule.sleep_pressure(dt.datetime(2026, 10, 28, 1, 30)) > 0.5
print(f"OK sleep_pressure 22:17={p1:.3f} 23:30={p2:.3f} 00:45={p3:.3f}")

# --- quiet bounds follow wake (not fixed 10:00) ---
cfg = {"quiet_hours": ["00:30", "09:00"], "quiet_hours_weekend": ["00:30", "10:00"]}
qs, qe = ps._quiet_bounds(dt.datetime(2026, 10, 25, 8, 0), cfg)
wake = schedule.day_plan(dt.date(2026, 10, 25))["wake"]
assert qe == f"{wake // 60:02d}:{wake % 60:02d}"
assert qe != "10:00" or wake == 10 * 60  # only equal if wake literally 10:00
print("OK quiet end aligns wake", qs, qe)

# --- draft density after regen ---
import json
doc = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "life_schedule_2026q4.json").read_text())
import reasonableness as rz
issues = rz.check_persona_realism(doc["days"])
assert not any(i.get("code") == "weekday_missing_internship" for i in issues)
print("OK draft realism no missing-internship;", len(issues), "other issues")
print("ALL OK")
