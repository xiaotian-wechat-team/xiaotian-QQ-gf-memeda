"""离线：主窗口选择优先「微信」标题；send_message 激活后复核标题，失败不粘贴。"""
import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import pytest
m = pytest.importorskip("mac_wechat_io")

def test_window_prefers_main(monkeypatch):
    ws = [{"kCGWindowOwnerName": "WeChat", "kCGWindowLayer": 0, "kCGWindowName": "", "kCGWindowNumber": 9, "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 825, "Height": 866}},
          {"kCGWindowOwnerName": "WeChat", "kCGWindowLayer": 0, "kCGWindowName": "微信", "kCGWindowNumber": 7, "kCGWindowBounds": {"X": 59, "Y": 164, "Width": 880, "Height": 640}}]
    monkeypatch.setattr(m.Quartz, "CGWindowListCopyWindowInfo", lambda *a: ws)
    assert m.window()[0] == 7

def test_send_aborts_on_title(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "activate", lambda: calls.append("act"))
    monkeypatch.setattr(m, "activate_if_needed", lambda: calls.append("need"))
    monkeypatch.setattr(m, "in_chat_with", lambda *a: False)
    monkeypatch.setattr(m, "fill_input", lambda t: calls.append("fill"))
    with pytest.raises(RuntimeError): m.send_message("hi")
    assert "fill" not in calls
    assert calls == ["need", "act"]
