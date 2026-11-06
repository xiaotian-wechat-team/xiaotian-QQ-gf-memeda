"""离线测试：不读写 STATE.json、不发微信。python3.13 tests/test_nickname.py"""
import sys, json, pathlib, random
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import nickname
lib = nickname.library()
assert 200 <= len(lib) <= 1000 and len({n for n, _ in lib}) == len(lib)
assert all(len(v) == 5 and all(0 <= x <= 1 for x in v) for _, v in lib)
state = {"mood": 6, "recent_nicknames": []}
rng = random.Random(42)
for msg in ["想你了", "今天好累", "你个笨蛋", "我考试挂了", "晚安"]:
    n, v, why = nickname.pick(msg, state, rng)
    emo, _ = nickname.emotion(msg)
    if emo in ("sad", "serious"): assert v["tease"] <= .25, (msg, n, v)
    assert n not in state["recent_nicknames"]
    state["recent_nicknames"] = nickname.remember(state, n)
    print(f"{msg} -> {n} {v}\n   {why}")
print("OK")
