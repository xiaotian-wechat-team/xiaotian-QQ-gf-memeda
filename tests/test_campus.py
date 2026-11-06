import sys, pathlib, random, datetime
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import campus, schedule

page = '<a href="/a.html">头条  原创舞剧《锦鲤》即将首演 发布时间：2026/10/05</a><a href="x">联系我们</a>' \
       '<a href="https://mp.weixin.qq.com/s/1">学生会迎新晚会筹备工作启动</a>'
ev = campus.extract(page, "https://news.bda.edu.cn/")
assert [e["title"] for e in ev] == ["原创舞剧《锦鲤》即将首演", "学生会迎新晚会筹备工作启动"], ev
assert ev[0]["url"] == "https://news.bda.edu.cn/a.html" and not ev[0]["mine"] and ev[1]["mine"]
db = {"events": []}
assert len(campus.merge(db, ev, "t")) == 2 and campus.merge(db, [dict(e) for e in ev], "t") == []
assert campus.life_event(ev[0])["kind"] == "听说"
assert campus.life_event(ev[1], random.Random(1))["kind"] == "听说"
mon = datetime.datetime(2026, 10, 26, 10, 0); tue_eve = datetime.datetime(2026, 10, 27, 19, 30)
# These are fallback timetable assertions, not today's mutable life calendar.
with patch("life_calendar.slot_at", return_value=None):
    assert schedule.current(mon)["activity"] in ("编导技法课", "刚起床"), schedule.current(mon)
    assert schedule.current(tue_eve)["activity"] == "学生会文艺部例会", schedule.current(tue_eve)
print("ok")
