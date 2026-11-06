"""离线：复制读取（右键→复制→剪贴板）全部 mock，不碰微信、不碰真实剪贴板。"""
import sys, pathlib, contextlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import pytest
m = pytest.importorskip("mac_wechat_io")

@pytest.fixture
def env(monkeypatch, tmp_path):
    st = {"pb": [[("public.utf8-plain-text", b"USER")]], "cnt": 0, "clips": [], "str": None, "menu": True, "keys": []}
    m._COPIED.clear()
    monkeypatch.setattr(m, "window", lambda: (1, 100, 200, 880, 640))
    monkeypatch.setattr(m, "activate_if_needed", lambda: None)
    monkeypatch.setattr(m, "in_chat_with", lambda *a: st.get("title", True))
    monkeypatch.setattr(m, "pb_save", lambda: list(st["pb"]))
    monkeypatch.setattr(m, "pb_restore", lambda items: st.__setitem__("restored", items))
    monkeypatch.setattr(m, "pb_count", lambda: st["cnt"])
    monkeypatch.setattr(m, "pb_string", lambda: st["str"])
    monkeypatch.setattr(m, "_key", lambda c: st["keys"].append(c))
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr(m, "_menu_find", lambda sx, sy: (sx + 30, sy + 10) if st["menu"] else None)
    def cli(a):
        st["clips"].append(a)
        if a.startswith("c:") and st.get("copy"): st["cnt"] += 1; st["str"] = st["copy"].pop(0)
    monkeypatch.setattr(m, "_cli", cli)
    return st

L = contextlib.nullcontext
def msg(t, y, frm="them"): return {"from": frm, "text": t, "y": y, "_box": (320, y, 60, 18)}

def test_copy_replaces_ocr(env):
    env["copy"] = ["干嘛呢"]
    ms = [msg("干噰呢", 300)]
    assert m.copy_refine(ms, lock=L) == 1
    assert ms[0]["text"] == "干嘛呢" and ms[0]["src"] == "copy"
    assert env["restored"] == [[("public.utf8-plain-text", b"USER")]]
    assert env["clips"][0].startswith("rc:")

def test_cache_no_reclick_and_cap(env):
    env["copy"] = ["b新2", "a新1"]
    ms = [msg("旧", 120), msg("a新1", 200), msg("我", 250, "me"), msg("b新2", 300)]
    assert m.copy_refine(ms, cap=2, lock=L) == 2
    assert sum(c.startswith("rc:") for c in env["clips"]) == 2   # 只复制最新 2 条
    env["clips"].clear()
    assert m.copy_refine(ms, cap=2, lock=L) in (0, 1) and not any(c.startswith("rc:") and "300" in c for c in env["clips"])

def test_menu_missing_falls_back(env):
    env["menu"] = False
    ms = [msg("6", 300)]
    assert m.copy_refine(ms, lock=L) == 0 and ms[0]["text"] == "6" and 53 in env["keys"]
    assert "restored" in env

def test_dissimilar_clip_rejected(env):   # 链接卡片之类
    env["copy"] = ["https://example.com/xxxxxxxx"]
    ms = [msg("某某文章标题", 300)]
    m.copy_refine(ms, lock=L); assert ms[0]["text"] == "某某文章标题"

def test_title_fail_no_click(env):
    env["title"] = False
    ms = [msg("x", 300)]
    assert m.copy_refine(ms, lock=L) == 0 and env["clips"] == []

def test_never_clicks_input_area(env):
    ms = [{"from": "them", "text": "x", "y": 600, "_box": (320, 600, 60, 18)}]
    m.copy_refine(ms, lock=L); assert env["clips"] == []

def test_ui_lock_reentrant(tmp_path):
    import ui_lock as ul
    with ul.ui_lock("reply", path=tmp_path / "l"):
        with ul.ui_lock("reply", path=tmp_path / "l"): pass
    assert ul._HELD == 0
