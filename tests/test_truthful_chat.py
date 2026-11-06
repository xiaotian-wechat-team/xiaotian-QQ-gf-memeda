"""Search evidence, meaning and delivery accounting; no browser/network/live state."""
import datetime as dt
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import action_evidence as ae
import brain
import chat_search as search
import emotion
import memory
from preview_mode import preview

HTML = '''<html><body><li class="b_algo"><h2><a href="https://df.qq.com/">三角洲行动官网</a></h2>
<p>战术射击游戏，包含多种玩法。</p></li></body></html>'''
EVIDENCE = {"status": "ok", "sources": [{"url": "https://df.qq.com/", "title": "三角洲行动", "snippet": "射击游戏"}]}


def test_browser_extracts_real_source_and_challenge_stops():
    sources = search.parse_results(HTML)
    assert sources[0]["url"] == "https://df.qq.com/"
    assert "战术射击" in sources[0]["snippet"]
    assert search.parse_results('<h2><a href="file:///etc/passwd">秘密</a></h2>') == []
    assert search.parse_results('<h3><a href="http://127.0.0.1/">本机</a></h3>') == []
    with pytest.raises(RuntimeError, match="challenge"):
        search.parse_results('<body>Verify you are human</body>')


def test_model_prose_is_not_search_execution():
    assert search._tool_sources([{"type": "assistant", "message": {"content": json.dumps(EVIDENCE)}}]) == []
    tool = {"type": "tool_call", "subtype": "completed", "tool_call": {
        "webSearchToolCall": {"result": {"results": EVIDENCE["sources"]}}}}
    assert search._tool_sources([tool])[0]["url"] == "https://df.qq.com/"


def test_preview_never_launches_provider(monkeypatch):
    monkeypatch.setattr(search, "browser_search", lambda *a: pytest.fail("live browser"))
    with preview():
        assert search.search("查一下三角洲行动")["status"] == "unavailable"


def test_model_failure_falls_back_and_browser_default_skips_model(monkeypatch, tmp_path):
    monkeypatch.delenv("XIAOTIAN_TEST", raising=False)
    monkeypatch.setattr(search, "ROOT", tmp_path)
    calls = []
    def model(*args):
        calls.append("model")
        raise RuntimeError("no_verified_model_search_results")
    monkeypatch.setattr(search, "model_search", model)
    monkeypatch.setattr(search, "browser_search", lambda *a: calls.append("browser") or EVIDENCE["sources"])
    assert search.search("游戏介绍", cfg={"chat_search": {"provider": "model"}})["status"] == "ok"
    assert calls == ["model", "browser"]
    calls.clear()
    assert search.search("游戏介绍", cfg={"chat_search": {"provider": "browser"}})["status"] == "ok"
    assert calls == ["browser"]


def test_browser_query_is_data_and_profile_is_isolated(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "_run", lambda cmd, **kw: calls.append((cmd, kw)) or HTML)
    query = '游戏; $(touch /tmp/nope) `ls`'
    assert search.browser_search(query, {"browser_path": sys.executable, "browser_engine": "google"})
    cmd, kw = calls[0]
    assert cmd[-1].startswith("https://www.google.com/search?q=")
    assert query not in cmd[-1] and "%24" in cmd[-1]
    profile = next(x for x in cmd if x.startswith("--user-data-dir="))
    assert profile == "--user-data-dir=" + kw["cwd"]
    assert "no-sandbox" not in " ".join(cmd)


def test_search_rejects_unrelated_results_and_overlong_urls():
    assert search.relevant_sources("三角洲行动 官方 游戏介绍", [{"title": "字体网站", "snippet": "TikTok", "url": "https://example.com/"}]) == []
    assert search.relevant_sources("三角洲行动 官方 游戏介绍", EVIDENCE["sources"])
    assert search._public_url("https://example.com/" + "a"*3000) is None
    with pytest.raises(RuntimeError, match="isolation"):
        search.model_search("test", {"model_verified": True})


def test_xiaohongshu_routes_to_bound_browser_skill(monkeypatch):
    import types
    calls = []
    source = {"url": "https://www.xiaohongshu.com/explore/6aabe792000000002700b353",
              "title": "三角洲不同干员", "snippet": "三角洲行动相关笔记"}
    monkeypatch.setitem(sys.modules, "bsk_search", types.SimpleNamespace(
        search=lambda q, opts: calls.append((q, opts)) or [source]))
    monkeypatch.setattr(search, "_run", lambda *a, **k: pytest.fail("unexpected headless fallback"))
    opts = {"browser_engine": "xiaohongshu", "browser_instance_id": "verified"}
    assert search.browser_search("三角洲行动", opts) == [source]
    assert calls == [("三角洲行动", opts)]
    assert "个人笔记" in search.prompt_block({"status": "ok", "sources": [source]})


@pytest.mark.parametrize("text", ["搜了", "我刚查到了", "图发给你了", "已经写入长期记忆",
                                 "嗯，我搜了", "我搜了你发的游戏", "我搜了‘三角洲行动’", "我发给你了",
                                 "我刚刚搜到了", "我看了官网"])
def test_completion_claims_need_evidence(text):
    assert ae.enforce(text) != text


def test_search_success_and_ordinary_conversation_are_preserved():
    assert ae.enforce("搜了，是个射击游戏", EVIDENCE) == "搜了，是个射击游戏"
    for text in ("你搜了吗？", "他搜了半天", "我没查到", "我会记着的", "她说‘我搜了’"):
        assert ae.enforce(text) == text


def test_actual_cursor_result_shape_is_supported():
    chunk = '<result id="1"><title>官网</title><url>https://df.qq.com/</url><content>射击游戏</content></result>'
    event = {"type": "tool_call", "subtype": "completed", "tool_call": {"webSearchToolCall": {
        "result": {"success": {"references": [{"url": "", "chunk": chunk}]}}}}}
    assert search._tool_sources([event])[0]["snippet"] == "射击游戏"


def test_clean_preserves_reassurance_and_whole_sentences():
    sentence = "这次实验没有成功不代表你能力不行，我们可以慢慢找哪里出了问题"
    assert brain.clean(sentence) == sentence
    out = brain.clean("第一件我听到了\n第二件也听明白了\n你慢慢说", max_bubbles=2)
    assert all(word in out for word in ("第一件", "第二件", "慢慢说"))
    assert len(out.splitlines()) <= 2
    assert len(brain.clean("很长" * 100, limit=40)) <= 40
    assert not brain.clean("很长" * 100).endswith("不代表")
    overflow = "你确实失败了。\n但一次实验结果不代表你的能力，我们可以一起分析哪里出了问题，我相信你有能力把它解决"
    assert brain.clean(overflow, limit=40) != "你确实失败了。"
    first = "你这次确实失败了。"
    exact = first + ("但这并不代表你没有能力" + "慢慢来"*30)[:80-len(first)]
    assert brain.clean(exact, limit=80) == exact


def test_persona_facts_and_feedback_reach_both_backends(monkeypatch):
    monkeypatch.setattr(memory, "persona", lambda: {"user": "小熊", "soul": "", "memory": ""})
    monkeypatch.setattr(memory, "contact_ctx", lambda *a: {"profile": "", "memory": ""})
    monkeypatch.setattr(brain, "memory_block", lambda *a: "")
    monkeypatch.setattr(brain, "state_block", lambda *a, **k: "")
    monkeypatch.setattr(brain, "intimacy_gate_block", lambda *a: "")
    for question, answer in (("你室友叫什么", "林晓晚"), ("你不爱吃什么", "苦瓜"),
                             ("你爸妈做什么", "小生意"), ("恋爱纪念日", "2025-02-14")):
        hist = [("them", question)]
        long = brain.build_prompt("demo_contact", hist, st={})
        short = str(brain.build_short_messages("demo_contact", hist, st={}))
        assert answer in long and answer in short
        assert "2024" in long and "2024" in short
    assert "先" in str(brain.build_short_messages("demo_contact", [("them", "你好凶")], st={}))
    assert "现代舞" in str(brain.build_short_messages("demo_contact", [("them", "你室友叫什么"), ("them", "都学什么专业")], st={}))


def test_model_resolves_search_topic_before_browser(monkeypatch):
    import weather
    monkeypatch.setattr(brain, "CFG", {**brain.CFG, "llm": {"backend": "deepseek"}, "life_calendar": {"sync_on_reply": False}})
    monkeypatch.setattr(memory, "read_state", lambda *a: {})
    monkeypatch.setattr(memory, "persona", lambda: {"user": "", "soul": "", "memory": ""})
    monkeypatch.setattr(memory, "contact_ctx", lambda *a: {"profile": "", "memory": ""})
    monkeypatch.setattr(brain, "memory_block", lambda *a: "")
    monkeypatch.setattr(brain, "state_block", lambda *a, **k: "")
    monkeypatch.setattr(brain, "_nick", lambda *a: None)
    monkeypatch.setattr(weather, "needs", lambda *a: False)
    prompts, queries = [], []
    def model(messages):
        prompts.append(str(messages))
        return "SEARCH_QUERY:三角洲行动 官方 游戏介绍" if len(prompts) == 1 else "搜了，是个战术射击游戏"
    monkeypatch.setitem(brain.BACKENDS, "deepseek", model)
    monkeypatch.setattr(search, "search", lambda q, **kw: queries.append(q) or EVIDENCE)
    answer = brain.reply("demo_contact", [("them", "这个游戏叫三角洲行动"), ("me", "我还没玩过"), ("them", "你搜一下")],
                         dry=True, now=dt.datetime(2026, 10, 29, 20))
    assert queries == ["三角洲行动 官方 游戏介绍"] and len(prompts) == 2
    assert "理解指代" in prompts[0] and "三角洲行动" in prompts[0]
    assert "实际搜索结果" in prompts[1] and "搜了" in answer


def test_persona_counters_commit_once_only_after_body_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DL", tmp_path)
    (tmp_path / "contacts" / "demo_contact").mkdir(parents=True)
    now = dt.datetime(2026, 10, 29, 20)
    memory.update_state("demo_contact", emotion=emotion.default(), proactive_count=7)
    pending = {"sweet_mode": {"mode": "warm"}, "nickname": "小熊", "bias": {}}
    assert not brain.commit_delivery("demo_contact", pending, delivery_id="a", sent_text="", now=now)
    assert emotion.daily_sweet_count(memory.read_state("demo_contact")["emotion"], now) == 0
    assert brain.commit_delivery("demo_contact", pending, delivery_id="a", sent_text="小熊，听着呢", now=now)
    assert not brain.commit_delivery("demo_contact", pending, delivery_id="a", sent_text="再说两句", now=now)
    state = memory.read_state("demo_contact")
    assert emotion.daily_sweet_count(state["emotion"], now) == 1
    assert state["active_nickname"] == "小熊" and state["proactive_count"] == 7
    assert not brain.commit_delivery("demo_contact", {**pending, "preview": True}, delivery_id="b", sent_text="嗯", now=now)
    assert not brain.commit_delivery("demo_contact", pending, delivery_id="c", sent_text="[图片:校园]", now=now)
    assert brain.commit_delivery("demo_contact", pending, delivery_id="d", sent_text="我听着呢", now=now)
    assert brain.commit_delivery("demo_contact", pending, delivery_id="d", sent_text="小熊慢慢说", now=now)
    assert not brain.commit_delivery("demo_contact", pending, delivery_id="d", sent_text="小熊慢慢说", now=now)
    assert emotion.daily_sweet_count(memory.read_state("demo_contact")["emotion"], now) == 2


def test_user_frequency_and_evening_parameters_preserved():
    cfg = json.loads((ROOT / "config.json").read_text())
    p, e = cfg["proactive"], cfg["reply"]["engagement"]
    assert p["max_per_day"] == 10 and p["no_double_text"] is False
    assert p["contact_rhythm"]["cumulative_caps"] == [[12, 2], [17, 4], [20, 7], [24, 10]]
    assert p["contact_rhythm"]["day_spacing_sec"] == [4500, 7200]
    assert p["contact_rhythm"]["evening_spacing_sec"] == [2100, 3900]
    assert e["evening_hour"] == 17 and e["day_start_hour"] == 7
    assert e["internship_enthusiastic_probability"] == .3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
