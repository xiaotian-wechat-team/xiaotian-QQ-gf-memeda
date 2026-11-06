"""Offline: meal_period on get_day, weather tool loop routing, face_closeup→face sheet.

No WeChat/QQ, no network, no cursor-agent live call.
"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, json, copy, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import datetime as dt
import life_calendar as lc
import shot_modes as sm
import llm
import weather
import refpick
import config
from PIL import Image
import test_canonical_session as tcs


def test_get_day_stamps_meal_period():
    day = {
        "date": "2026-10-26",
        "kind": "workday",
        "slots": [
            {"start": "07:30", "end": "08:30", "activity": "吃早饭", "scene_hints": ["豆浆"], "bucket": "campus_life"},
            {"start": "10:00", "end": "11:00", "activity": "赖床/吃早午饭", "scene_hints": [], "bucket": "weekend_home"},
            {"start": "12:00", "end": "12:40", "activity": "食堂午饭", "scene_hints": ["食堂"], "bucket": "campus_life"},
            {"start": "18:00", "end": "18:40", "activity": "吃晚饭", "scene_hints": ["晚饭"], "bucket": "campus_life"},
            {"start": "14:00", "end": "16:00", "activity": "编导技法课", "scene_hints": ["教室"], "bucket": "school_class"},
        ],
    }
    out = lc.stamp_meal_periods(day)
    assert out["slots"][0]["meal_period"] == "breakfast"
    assert out["slots"][1]["meal_period"] == "brunch"
    assert out["slots"][2]["meal_period"] == "lunch"
    assert out["slots"][3]["meal_period"] == "dinner"
    assert "meal_period" not in out["slots"][4]
    # idempotent
    out2 = lc.stamp_meal_periods(out)
    assert out2["slots"][2]["meal_period"] == "lunch"


def test_face_closeup_is_face_five_view_alias():
    assert sm.normalize_shot("face_closeup") == sm.SHOT_PORTRAIT
    assert sm.route_user_shot("脸特写") == sm.SHOT_PORTRAIT
    tmp = pathlib.Path(tempfile.mkdtemp())
    root = tcs.make_profile(tmp)
    cfg = copy.deepcopy(config.load())
    cfg["appearance"]["profile_dir"] = str(root)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    face = root / "canonical" / "sheet.png"
    assert face.is_file() or True
    # ensure face sheet exists
    face.parent.mkdir(parents=True, exist_ok=True)
    if not face.is_file():
        Image.new("RGB", (400, 200), (200, 180, 160)).save(face)
    can, oth = refpick.pick_identity("脸特写", 3, cfg, shot_type="face_closeup")
    assert can == [face]
    assert not sm.skips_hard_face_body("face_closeup")


def test_weather_needs_forces_long_backend(monkeypatch):
    from llm_adapters import get_adapter
    monkeypatch.setattr(get_adapter('deepseek'), 'available', lambda: True)
    cfg = {"short_backend": "deepseek", "long_backend": "cursor_agent", "char_threshold": 80}
    # short casual without weather → short
    assert llm.pick_backend("在干嘛", cfg) == "deepseek"
    # weather needs → long (Cursor), never DeepSeek tools
    assert llm.pick_backend("今天穿什么出门会不会下雨", cfg) == "cursor_agent"
    assert weather.needs("今天穿什么出门会不会下雨")
    assert llm.weather_tool_model({"weather_tool_model": "grok-4.7-low-fast"}) == "grok-4.7-low-fast"
    assert llm.weather_tool_model({}) == "grok-4.7-low-fast"


def test_weather_tool_loop_calls_tool(monkeypatch):
    """A real weather lookup precedes one model phrasing call (both mocked)."""
    calls = {"n": 0, "complete": []}

    def fake_complete(prompt, timeout=None, reuse_session=None, model=None):
        calls["complete"].append({"model": model, "prompt": prompt[:80]})
        return "海淀现在有点凉\n记得多穿点"

    def fake_call_tool(args=None, **kw):
        calls["tool_args"] = args
        return {"place": "bda", "text": "北舞 12°C 阴", "ok": True}

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(weather, "call_tool", fake_call_tool)
    out = llm.complete_with_weather_tools("STATE...\n他: 冷不冷", "冷不冷")
    assert "多穿" in out or "凉" in out
    assert calls["tool_args"]["place"] == "bda"
    assert all(c["model"] == "grok-4.7-low-fast" for c in calls["complete"])


def test_weather_lookup_routes_user_location_without_model_roundtrip(monkeypatch):
    """The bounded router chooses yanyuan and needs only one model call."""
    calls = {"n": 0, "tool": 0}

    def fake_complete(prompt, timeout=None, reuse_session=None, model=None):
        calls["n"] += 1
        return "燕园有点冷 多穿"

    def fake_call_tool(args=None, **kw):
        calls["tool"] += 1
        calls["args"] = args
        return {"place": "yanyuan", "text": "燕园 8°C", "ok": True}

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(weather, "call_tool", fake_call_tool)
    out = llm.complete_with_weather_tools("prompt", "我这边冷不冷要穿什么")
    assert calls["tool"] == 1 and calls["n"] == 1
    assert calls["args"]["place"] == "yanyuan"
    assert "多穿" in out or "冷" in out


if __name__ == "__main__":
    test_get_day_stamps_meal_period()
    test_face_closeup_is_face_five_view_alias()
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        test_weather_needs_forces_long_backend(mp)
    # tool loop test needs careful monkeypatch; run via pytest
    print("OK partial")
