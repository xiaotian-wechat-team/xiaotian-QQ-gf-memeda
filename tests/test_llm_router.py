"""离线：llm 路由 short→deepseek / long→cursor_agent；DeepSeek HTTP mock；不打印 key。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, json, pathlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import llm
import llm_deepseek
from llm_adapters import deepseek as llm_ds_ad


CFG = {
    "short_backend": "deepseek",
    "short_model": "deepseek-chat",
    "long_backend": "cursor_agent",
    "char_threshold": 80,
    "backend": "cursor_agent",
}


@pytest.fixture(autouse=True)
def offline_adapters(monkeypatch, tmp_path):
    """No production key file or real provider can be reached by these tests."""
    monkeypatch.setattr(llm_deepseek, "ENV_FILE", tmp_path / "deepseek.env")
    monkeypatch.setattr(llm_ds_ad, "ENV_FILE", tmp_path / "deepseek.env")
    monkeypatch.setattr(llm_ds_ad, "_KEY_CACHE", None)
    monkeypatch.setattr(llm_deepseek.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("unexpected network call"))
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda *a, **k: pytest.fail("unexpected model subprocess"))


def test_pick_short_casual(monkeypatch):
    monkeypatch.setattr(llm_ds_ad, "available", lambda: True)
    assert llm.pick_backend("想你了", CFG) == "deepseek"
    assert llm.pick_backend("在干嘛呢", CFG) == "deepseek"
    assert llm.pick_backend("哈哈哈哈哈", CFG) == "deepseek"
    assert llm.pick_backend("用 flash 回我", CFG) == "deepseek"


def test_pick_long_hard(monkeypatch):
    monkeypatch.setattr(llm_ds_ad, "available", lambda: True)
    assert llm.pick_backend("帮我分析一下这段代码为什么慢", CFG) == "cursor_agent"
    assert llm.pick_backend("帮我计划下周复习安排", CFG) == "cursor_agent"
    assert llm.pick_backend("写代码实现一个排序", CFG) == "cursor_agent"
    long = "啊" * 90
    assert llm.pick_backend(long, CFG) == "cursor_agent"
    assert llm.pick_backend("今晚吃啥？明天去哪玩？", CFG) == "cursor_agent"
    assert llm.pick_backend("[图片] 帮我看看这是啥意思", CFG) == "cursor_agent"
    assert llm.pick_backend("详细说说你的想法", CFG) == "cursor_agent"


def test_pick_no_key_falls_back(monkeypatch):
    monkeypatch.setattr(llm_deepseek, "api_key", lambda force_reload=False: None)
    monkeypatch.setattr(llm_ds_ad, "available", lambda: False)
    monkeypatch.setattr(llm_ds_ad, "api_key", lambda force_reload=False: None)
    assert llm.pick_backend("想你了", CFG) == "cursor_agent"


def test_api_key_env_and_file(monkeypatch, tmp_path):
    llm_deepseek.clear_key_cache()
    llm_ds_ad.clear_key_cache()
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    envf = tmp_path / "deepseek.env"
    monkeypatch.setattr(llm_deepseek, "ENV_FILE", envf)
    monkeypatch.setattr(llm_ds_ad, "ENV_FILE", envf)
    envf.write_text("DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
    assert llm_deepseek.api_key(force_reload=True) == "from-file"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert llm_deepseek.api_key(force_reload=True) == "from-env"
    llm_deepseek.clear_key_cache()
    llm_ds_ad.clear_key_cache()


def test_deepseek_complete_mock(monkeypatch):
    llm_deepseek.clear_key_cache()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-mock")
    captured = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "啧，就这？"}}],
            }).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(llm_deepseek.urllib.request, "urlopen", fake_urlopen)
    out = llm_deepseek.complete(
        [{"role": "system", "content": "you are 小甜"}, {"role": "user", "content": "想你了"}],
        model="deepseek-chat",
        timeout=12,
    )
    assert out == "啧，就这？"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["auth"].startswith("Bearer ")
    assert "sk-mock" not in repr(out)


def test_brain_routes_to_deepseek(monkeypatch):
    import brain
    monkeypatch.setattr(llm_ds_ad, "available", lambda: True)
    calls = {}
    monkeypatch.setattr(brain, "build_deepseek_messages", lambda *a, **k: [{"role": "user", "content": "x"}])
    monkeypatch.setattr(brain, "build_prompt", lambda *a, **k: "FULL")
    def ds(messages):
        calls["ds"] = messages
        return "想你就直说嘛"
    def ca(prompt):
        calls["ca"] = prompt
        return "不应该走这里"
    monkeypatch.setitem(brain.BACKENDS, "deepseek", ds)
    monkeypatch.setitem(brain.BACKENDS, "cursor_agent", ca)
    # force cfg path
    monkeypatch.setattr(brain, "CFG", {**brain.CFG, "llm": {**brain.CFG.get("llm", {}), **CFG}})
    monkeypatch.setattr(brain.memory, "read_state", lambda cid: {})
    monkeypatch.setattr(brain, "_nick", lambda st, last, now=None: None)
    text = brain.reply("demo_contact", [("them", "想你了")], dry=True)
    assert text
    assert "ds" in calls and "ca" not in calls
    assert brain.LAST.get("backend") == "deepseek"


def test_brain_routes_long_to_cursor(monkeypatch):
    import brain
    monkeypatch.setattr(llm_ds_ad, "available", lambda: True)
    calls = {}
    monkeypatch.setitem(brain.BACKENDS, "deepseek", lambda m: calls.setdefault("ds", 1) or "x")
    monkeypatch.setitem(brain.BACKENDS, "cursor_agent", lambda p: calls.setdefault("ca", p) or "行吧行吧，我想想方案")
    monkeypatch.setattr(brain, "CFG", {**brain.CFG, "llm": {**brain.CFG.get("llm", {}), **CFG}})
    monkeypatch.setattr(brain.memory, "read_state", lambda cid: {})
    monkeypatch.setattr(brain, "_nick", lambda st, last, now=None: None)
    monkeypatch.setattr(brain, "build_prompt", lambda *a, **k: "PROMPT")
    text = brain.reply("demo_contact", [("them", "帮我分析一下项目架构怎么改")], dry=True)
    assert text
    assert "ca" in calls and "ds" not in calls
    assert brain.LAST.get("backend") == "cursor_agent"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_kimi_adapter_registered_and_mock(monkeypatch):
    from llm_adapters import get_adapter, short_adapter_names
    from llm_adapters import kimi as kimi_ad
    import brain as _brain
    assert "kimi" in short_adapter_names()
    assert get_adapter("kimi") is kimi_ad
    assert "kimi" in _brain.BACKENDS
    kimi_ad.clear_key_cache()
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-test")
    captured = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": " 嗯嗯 "}}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["auth"] = req.headers.get("Authorization")
        return R()

    monkeypatch.setattr(kimi_ad.urllib.request, "urlopen", fake_urlopen)
    out = kimi_ad.complete([{"role": "user", "content": "hi"}], model="deepseek-chat")
    assert out == "嗯嗯"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"].startswith("kimi")
    assert captured["body"]["reasoning_effort"] == "low"
    assert captured["auth"] == "Bearer sk-kimi-test"
    cfg = dict(CFG, short_backend="kimi")
    assert llm.pick_backend("想你了", cfg) == "kimi"
    kimi_ad.clear_key_cache()


def test_doubao_adapter_registered_and_mock(monkeypatch):
    from llm_adapters import get_adapter, short_adapter_names
    from llm_adapters import doubao as doubao_ad
    import brain as _brain
    assert "doubao" in short_adapter_names()
    assert get_adapter("doubao") is doubao_ad
    assert "doubao" in _brain.BACKENDS
    doubao_ad.clear_key_cache()
    monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
    captured = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": " 豆包回 "}}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["auth"] = req.headers.get("Authorization")
        return R()

    monkeypatch.setattr(doubao_ad.urllib.request, "urlopen", fake_urlopen)
    out = doubao_ad.complete([{"role": "user", "content": "hi"}], model="deepseek-chat")
    assert out == "豆包回"
    assert "ark.cn-beijing.volces.com" in captured["url"] or captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"].startswith("doubao")
    assert captured["body"].get("thinking", {}).get("type") == "disabled"
    assert captured["body"].get("reasoning_effort") == "minimal"
    assert captured["auth"] == "Bearer ark-test-key"
    cfg = dict(CFG, short_backend="doubao")
    assert llm.pick_backend("想你了", cfg) == "doubao"
    doubao_ad.clear_key_cache()
    # no key → fall back
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setattr(doubao_ad, "available", lambda: False)
    assert llm.pick_backend("想你了", cfg) == "cursor_agent"

