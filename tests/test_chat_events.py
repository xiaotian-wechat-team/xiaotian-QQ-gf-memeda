"""离线：聊天框内系统项（时间戳/撤回/拍一拍…）→ 用户侧输入。不碰微信、不写真实 chat.db。"""
import sys, pathlib, sqlite3, datetime as D
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import chat_events as ce, brain, importlib.util

def _fresh_memory():
    """独立加载一份 memory（别的测试在导入期把全局 memory.db/insert 换成了假的）。"""
    spec = importlib.util.spec_from_file_location("memory_fresh", pathlib.Path(__file__).resolve().parents[1] / "src" / "memory.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

import daemon
_PLAN, _INGEST = daemon.plan_new, daemon.ingest   # 收集期取原函数（test_daemon_loop 会全局替换 daemon.ingest）
NOW = D.datetime(2026, 10, 24, 2, 15)   # 星期六

def test_parse_formats():
    p = lambda t: ce.parse_time(t, NOW)
    assert p("18:00") == D.datetime(2026, 10, 24, 18, 0)
    assert p("晚上 6:00") == D.datetime(2026, 10, 24, 18, 0)
    assert p("下午3:05") == D.datetime(2026, 10, 24, 15, 5)
    assert p("上午 12:10") == D.datetime(2026, 10, 24, 0, 10)
    assert p("昨天 23:41") == D.datetime(2026, 10, 23, 23, 41)
    assert p("昨天 晚上 9:30") == D.datetime(2026, 10, 23, 21, 30)
    assert p("星期三 10:00") == D.datetime(2026, 10, 21, 10, 0)
    assert p("星期六 10:00") == D.datetime(2026, 10, 17, 10, 0)
    assert p("10月20日 08:30") == D.datetime(2026, 10, 20, 8, 30)
    assert p("12月31日 下午 1:00") == D.datetime(2025, 12, 31, 13, 0)
    assert p("2025年3月4日 晚上 8:15") == D.datetime(2025, 3, 4, 20, 15)
    assert p("2025年3月4日") == D.datetime(2025, 3, 4, 0, 0)
    for bad in ("对方撤回了一条消息", "25:00", "在吗", "18点见", "我12:00到"): assert p(bad) is None, bad

def test_label():
    assert ce.label(D.datetime(2026, 10, 24, 18, 0), D.datetime(2026, 10, 24, 20, 0)) == "[晚上 18:00 发]"
    assert ce.label("2026-10-23T15:20", NOW) == "[昨天 下午 15:20 发]"
    assert ce.label("2026-10-20T09:05", NOW) == "[10月20日 上午 09:05 发]"
    assert ce.label(None) == "" and ce.label("bad") == ""

def test_annotate():
    msgs = [{"from": "system", "text": "昨天 18:00", "y": 70}, {"from": "them", "text": "在吗", "y": 90},
            {"from": "them", "text": "人呢", "y": 120}, {"from": "system", "text": "“” 撤回了一条消息", "y": 150},
            {"from": "system", "text": "01:58", "y": 180}, {"from": "me", "text": "干嘛", "y": 200},
            {"from": "system", "text": "你撤回了一条消息 重新编辑", "y": 230},
            {"from": "system", "text": "小熊 拍了拍我", "y": 260}]
    out = ce.annotate(msgs, True, NOW)
    assert [m["from"] for m in out] == ["them", "them", "them", "me", "me", "them"]
    assert out[0]["sent_at"] == out[1]["sent_at"] == out[2]["sent_at"] == "2026-10-23T18:00"
    assert out[2]["text"] == "[系统提示：对方撤回了一条消息]" and out[2]["type"] == "notice"
    assert out[3]["sent_at"] == "2026-10-24T01:58"
    assert out[4]["text"] == "[系统提示：你撤回了一条消息]" and out[4]["from"] == "me"
    assert out[5]["text"] == "[系统提示：小熊 拍了拍我]"
    # 不在目标聊天：系统项一律丢弃
    assert all(m["from"] != "system" and m.get("type") != "notice" for m in ce.annotate(msgs, False, NOW))
    # 只有时间戳：不产生消息
    assert ce.annotate([{"from": "system", "text": "18:00", "y": 1}], True, NOW) == []

def test_greeting_and_me_side():
    assert ce.notice_side("以上是打招呼的消息") == "them"
    assert ce.notice_side("你已添加了小熊，现在可以开始聊天了。") == "me"
    assert ce.notice_side("我拍了拍“小熊”") == "me"

def test_db_and_prompt(tmp_path, monkeypatch):
    memory = _fresh_memory()
    monkeypatch.setitem(memory.CFG, "db_path", str(tmp_path / "c.db"))
    c = memory.db(); memory.db()   # 迁移幂等
    memory.insert(c, "demo_contact", "them", "在吗", "f1", type="text", sent_at="2026-10-24T18:00")
    memory.insert(c, "demo_contact", "them", "[系统提示：对方撤回了一条消息]", "f2", type="notice", sent_at="2026-10-24T18:00")
    h = memory.history(c, "demo_contact")
    s, t = h[0]; assert (s, t) == ("them", "在吗") and h[1].type == "notice"
    txt = brain._fmt_hist(h)
    assert "他: [" in txt and "发] " in txt and "<<<对方消息>>>[系统提示：对方撤回了一条消息]" in txt
    # 旧库（无新列）迁移
    old = tmp_path / "o.db"; sq = sqlite3.connect(old)
    sq.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, contact TEXT, sender TEXT, text TEXT, fp TEXT UNIQUE, ts TEXT, replied INTEGER DEFAULT 0)")
    sq.execute("INSERT INTO messages(contact,sender,text,fp,ts) VALUES('demo_contact','them','老','x','t')"); sq.commit(); sq.close()
    monkeypatch.setitem(memory.CFG, "db_path", str(old))
    assert memory.history(memory.db(), "demo_contact")[0].sent_at is None

def test_daemon_dedupe_and_trigger(tmp_path, monkeypatch):
    memory = _fresh_memory(); monkeypatch.setattr(daemon, "memory", memory)
    monkeypatch.setattr(daemon, "plan_new", _PLAN); monkeypatch.setattr(daemon, "ingest", _INGEST)
    monkeypatch.setitem(memory.CFG, "db_path", str(tmp_path / "d.db")); c = memory.db()
    screen = ce.annotate([{"from": "me", "text": "晚安", "y": 1}, {"from": "system", "text": "18:00", "y": 2},
                          {"from": "system", "text": "撤回了一条消息", "y": 3}], True, NOW)
    new = daemon.plan_new(c, screen); assert [m["text"] for m in new] == ["晚安", "[系统提示：对方撤回了一条消息]"]
    daemon.ingest(c, new)
    assert daemon.plan_new(c, screen) == []                       # 同一提示不重喂
    row = c.execute("SELECT type,sent_at FROM messages WHERE text LIKE '[系统%'").fetchone()
    assert row == ("notice", "2026-10-24T18:00")
    # 只多出一个时间戳 → 没有新消息
    assert daemon.plan_new(c, screen + ce.annotate([{"from": "system", "text": "18:30", "y": 9}], True, NOW)) == []
    # 自己的撤回：from=me，不触发
    mine = screen + ce.annotate([{"from": "system", "text": "你撤回了一条消息", "y": 9}], True, NOW)
    nm = daemon.plan_new(c, mine); assert [m["from"] for m in nm] == ["me"]

def test_type_note():
    tn = brain.type_note_for([("them", "[系统提示：对方撤回了一条消息]")])
    assert "[系统提示" in tn and "发]" in tn
