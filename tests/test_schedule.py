"""离线：随机作息（日期种子、工作日/休息日、熬夜频率）。

schedule.current 优先 life_calendar（草稿/generate_day），故对 current() 只做
不变量检查；day_plan 层仍验经典抖动/熬夜统计。
"""
import sys, pathlib, datetime, statistics
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import schedule
D, dt = datetime.datetime, datetime.date
days = [dt(2026, 1, 5) + datetime.timedelta(days=i) for i in range(364)]
plans = [schedule.day_plan(d) for d in days]
assert schedule.day_plan(dt(2026, 10, 23)) == schedule.day_plan(dt(2026, 10, 23))
assert schedule.current(D(2026, 10, 23, 15, 7)) == schedule.current(D(2026, 10, 23, 15, 7))
assert len({p["wake"] for p in plans}) > 30 and len({p["sleep"] for p in plans}) > 30
lates = sum(p["late"] for p in plans) / 52
assert 0.8 <= lates <= 2.2, lates
assert all(p["sleep"] <= 27 * 60 for p in plans) and any(p["sleep"] >= 26 * 60 for p in plans)
wk = statistics.mean(p["wake"] for p in plans if not p["weekend"])
we = statistics.mean(p["wake"] for p in plans if p["weekend"])
assert we - wk > 90, (wk, we)
for d in days[:60]:  # sample; full year of lc.current is slow/noisy
    n = lambda h, m=0: D(d.year, d.month, d.day, h, m)
    assert schedule.is_sleeping(n(4)) and schedule.is_sleeping(n(5, 30))
    assert schedule.current(n(21))["activity"] != "睡觉"
print(f"OK 熬夜 {lates:.2f} 次/周，起床均值 工作日 {wk/60:.2f}h 休息日 {we/60:.2f}h")
