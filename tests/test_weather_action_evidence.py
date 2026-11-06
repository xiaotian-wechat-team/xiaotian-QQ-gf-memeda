"""Offline weather execution provenance; never use a real model, fetch or memory."""
import datetime as dt
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import action_evidence
import brain
import chat_search
import llm
import memory
import weather

NOW = dt.datetime(2026, 10, 29, 20)
CURRENT = {"ok": True, "place": "bda", "text": "北舞现在 12°C 阴", "why": "ok"}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected live model/network/state operation")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(llm, "complete", forbidden)
    monkeypatch.setattr(weather, "call_tool", forbidden)
    monkeypatch.setattr(chat_search, "search", forbidden)
    monkeypatch.setattr(memory, "DL", tmp_path)
    monkeypatch.setattr(memory, "update_state", forbidden)
    monkeypatch.setattr(memory, "read_state", lambda *a: {})
    monkeypatch.setattr(memory, "persona", lambda: {"user": "", "soul": "", "memory": ""})
    monkeypatch.setattr(memory, "contact_ctx", lambda *a: {"profile": "", "memory": ""})
    monkeypatch.setattr(brain, "memory_block", lambda *a: "")
    monkeypatch.setattr(brain, "state_block", lambda *a, **kw: "")
    monkeypatch.setattr(brain, "intimacy_gate_block", lambda *a: "")
    monkeypatch.setattr(brain, "_nick", lambda *a: None)
    monkeypatch.setattr(brain, "CFG", {**brain.CFG, "llm": {"backend": "cursor_agent"},
                                      "life_calendar": {"sync_on_reply": False}})
    import schedule_fidelity
    monkeypatch.setattr(schedule_fidelity, "resolve_slot", lambda *a: {})


@pytest.mark.parametrize("why,status", [("ok", "ok"), ("cache", "ok"), ("cache-stale", "stale")])
def test_actual_weather_result_produces_scoped_evidence(monkeypatch, why, status):
    outputs = iter(["我查到了，北舞现在12度"])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(outputs))
    calls = []
    monkeypatch.setattr(weather, "call_tool", lambda args: calls.append(args) or {**CURRENT, "why": why})
    evidence = {}
    out = llm.complete_with_weather_tools("prompt", "查一下天气", evidence_sink=evidence)
    assert calls == [{"place": "bda"}]
    assert evidence == {"tool": "get_weather", "status": status, "place": "bda",
                        "text": CURRENT["text"], "why": why}
    assert (action_evidence.enforce(out, weather=evidence) == out) == (status == "ok")


@pytest.mark.parametrize("result", [
    {**CURRENT, "ok": False}, {**CURRENT, "text": ""}, {**CURRENT, "why": None},
])
def test_failed_or_unverified_tool_result_does_not_support_lookup(monkeypatch, result):
    outputs = iter(["我查到了"])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(outputs))
    monkeypatch.setattr(weather, "call_tool", lambda args: result)
    evidence = {}
    out = llm.complete_with_weather_tools("prompt", "查一下天气", evidence_sink=evidence)
    assert evidence["status"] == "unavailable"
    assert action_evidence.enforce(out, weather=evidence) != out


def test_model_answer_without_tool_is_not_evidence_and_clears_previous_sink(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: "我查到了")
    evidence = {"tool": "get_weather", "status": "ok", "text": "上轮天气"}
    out = llm.complete_with_weather_tools("prompt", "你好", evidence_sink=evidence)
    assert evidence == {}
    assert action_evidence.enforce(out, weather=evidence) != out


def test_lookup_precedes_single_fresh_model_call(monkeypatch):
    model_calls = []
    def model(prompt, **kw):
        assert calls == [{"place": "bda"}]
        model_calls.append((prompt, kw))
        return "我查到了，北舞现在12度"
    monkeypatch.setattr(llm, "complete", model)
    calls = []
    monkeypatch.setattr(weather, "call_tool", lambda args: calls.append(args) or CURRENT)
    evidence = {}
    llm.complete_with_weather_tools("prompt", "查一下北舞天气", evidence_sink=evidence)
    assert calls == [{"place": "bda"}] and evidence["status"] == "ok"
    assert len(model_calls) == 1
    assert model_calls[0][1]["reuse_session"] is False
    assert CURRENT["text"] in model_calls[0][0]


def test_weather_success_does_not_authorize_unrelated_web_claims():
    evidence = {"tool": "get_weather", "status": "ok", "text": CURRENT["text"]}
    for text in ("我搜到了三角洲行动", "我看了官网", "我查到了三角洲行动"):
        assert action_evidence.enforce(text, weather=evidence) != text
    for text in ("我查到了", "我查了天气", "我查到了北舞现在12度"):
        assert action_evidence.enforce(text, weather=evidence) == text


@pytest.mark.parametrize("ok", [True, False])
def test_brain_passes_actual_weather_evidence_and_uses_one_protocol(monkeypatch, ok):
    prompts = []
    def model(prompt, **kwargs):
        prompts.append(prompt)
        return "我查到了，北舞现在12度"
    monkeypatch.setattr(llm, "complete", model)
    monkeypatch.setattr(weather, "call_tool", lambda args: {**CURRENT, "ok": ok})
    out = brain.reply("demo_contact", [("them", "查一下北舞天气")], dry=True, now=NOW)
    assert len(prompts) == 1
    assert CURRENT["text"] in prompts[0] and "SEARCH_QUERY" not in prompts[0]
    assert brain.LAST["weather_evidence"]["status"] == ("ok" if ok else "unavailable")
    assert ("我查到了" in out) == ok


def test_brain_does_not_trust_successful_helper_return_without_tool_evidence(monkeypatch):
    monkeypatch.setattr(llm, "complete_with_weather_tools", lambda *a, **kw: "我查到了")
    out = brain.reply("demo_contact", [("them", "查一下天气")], dry=True, now=NOW)
    assert "我查到了" not in out and "weather_evidence" not in brain.LAST


def test_tool_exception_does_not_grant_evidence_to_fallback(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: 'CALL_TOOL:get_weather{"place":"bda"}')
    def failure(args):
        raise RuntimeError("weather unavailable")
    monkeypatch.setattr(weather, "call_tool", failure)
    monkeypatch.setitem(brain.BACKENDS, "cursor_agent", lambda prompt: "我查到了")
    out = brain.reply("demo_contact", [("them", "查一下天气")], dry=True, now=NOW)
    assert "我查到了" not in out and "weather_evidence" not in brain.LAST


def test_model_failure_after_tool_keeps_actual_evidence_in_fallback_prompt(monkeypatch):
    calls = []
    def model(prompt, **kwargs):
        calls.append(prompt)
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(llm, "complete", model)
    monkeypatch.setattr(weather, "call_tool", lambda args: CURRENT)
    fallback = []
    monkeypatch.setitem(brain.BACKENDS, "cursor_agent", lambda prompt: fallback.append(prompt) or "我查到了，北舞现在12度")
    out = brain.reply("demo_contact", [("them", "查一下天气")], dry=True, now=NOW)
    assert "我查到了" in out and brain.LAST["weather_evidence"]["status"] == "ok"
    assert CURRENT["text"] in fallback[0] and "SEARCH_QUERY" not in fallback[0]


def test_new_topic_does_not_replay_cancelled_weather_from_history(monkeypatch):
    prompts = []
    monkeypatch.setitem(brain.BACKENDS, "cursor_agent",
                        lambda prompt: prompts.append(prompt) or "晚上安排还没定")
    hist = [("them", "你学校冷不冷"), ("them", "今天晚上什么安排")]
    out = brain.reply("demo_contact", hist, dry=True, now=NOW, user_text=hist[-1][1])
    assert out and not brain.LAST['weather_tools']
    assert len(prompts) == 1
    current = prompts[0].split('# 本轮新收到的消息（当前回复对象）')[1]
    assert '今天晚上什么安排' in current and '你学校冷不冷' not in current
    assert '不把一句话硬拆' in prompts[0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
