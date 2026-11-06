"""离线：昵称锁定、清洗 <|eos|>、类型说明跟最新一条。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, random
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import nickname, brain


def test_sticky_nickname():
    rng = random.Random(0)
    st = {"mood": 7, "recent_nicknames": [], "active_nickname": "笨熊"}
    # 确保库里有 笨熊；若无则用库里已有的一个甜昵称
    lib = dict(nickname.library())
    if "笨熊" not in lib:
        name = next(n for n, v in lib.items() if nickname.META.get(n, {}).get("role") != "tease")
        st["active_nickname"] = name
    active = st["active_nickname"]
    for msg in ["想你了", "在干嘛呢", "哈哈哈哈", "给我发个照片看看"]:
        n, _, why = nickname.pick(msg, st, rng)
        assert n == active, (msg, n, why)
        assert "锁定" in why


def test_sticky_drops_tease_when_sad():
    lib = dict(nickname.library())
    tease = next(n for n, m in nickname.META.items() if m.get("role") == "tease" and n in lib)
    st = {"mood": 8, "active_nickname": tease, "recent_nicknames": []}
    n, v, why = nickname.pick("我好难过 考试挂了", st, random.Random(1))
    assert n != tease
    assert v["tease"] <= 0.25


def test_clean_strips_eos():
    assert "<|eos|>" not in brain.clean("想你了<|eos|>")
    assert brain.clean("行吧行吧 <|endoftext|>") == "行吧行吧"
    assert "eos" not in brain.clean("啧</s>").lower() or brain.clean("啧</s>") == "啧"


def test_type_note_follows_latest_text():
    # 纯文字不挂类型说明；最新是表情包才解释表情包
    hist = [("them", "[表情包]"), ("me", "又来"), ("them", "你干嘛呢")]
    tn = brain.type_note_for(hist)
    assert tn == ""
    tn2 = brain.type_note_for([("them", "嗨"), ("them", "[表情包]")])
    assert "表情包" in tn2 and "最新一条" in tn2


def test_prompt_has_nick_and_latest_hard_rules():
    # 最新一条是纯文字 → 无 TYPE 说明；称呼仍注入。媒体轮才带「最新一条」硬规则。
    hist = [("them", "[图片]"), ("me", "嗯"), ("them", "给我发个照片看看")]
    p = brain.build_prompt("demo_contact", hist, nick="笨熊", st={})
    assert "只用这一个" in p or "这次称呼对方为：笨熊" in p
    assert "禁止称呼" not in p
    assert "这次称呼对方为：笨熊" in p
    p_img = brain.build_prompt("demo_contact", [("them", "[图片]")], nick="笨熊", st={})
    assert "最新一条" in p_img and "图片" in p_img


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
