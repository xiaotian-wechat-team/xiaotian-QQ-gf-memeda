"""离线：性别过滤 + 调侃称呼(role=tease)。不读写 STATE.json。python3 tests/test_nickname_rules.py"""
import sys, pathlib, random, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import nickname, config
cfg = config.load(); assert cfg["user_gender"] == "m" and cfg["persona_gender"] == "f"
nickname.library(); M = nickname.META
assert nickname.allowed_genders() == {"n", "m"}
for n in ["大冤种", "大聪明", "甲方", "熊老师", "领导", "老板", "同学", "小登"]: assert M[n]["role"] == "tease", n
for n in ["王老师", "王总"]: assert n not in M, n  # 王姓调侃已清出库
# 过时土味已删除
for n in ["皇上", "少爷", "老头子", "老头儿", "帅哥", "靓仔", "北鼻", "哈尼", "逗比", "亲"]: assert n not in M, n
# 调侃称呼不含女性向；不含人身攻击/低俗
assert all(M[n]["gender"] in ("n", "m") for n, m in M.items() if m["role"] == "tease")
for bad in ["老登", "牛马", "傻X", "死鬼", "渣"]: assert bad not in M, bad
F = {n for n, m in M.items() if m["gender"] == "f"}; assert {"靓女", "老婆"} <= F
assert "小公主" not in M  # 公主/仙女刻板已清
TEASE = {n for n, m in M.items() if m["role"] == "tease"}
rng = random.Random(1)
def run(msgs, state, k=60):
    out = []
    for i in range(k):
        n, v, why = nickname.pick(msgs[i % len(msgs)], dict(state, recent_nicknames=[]), rng)
        out.append(n)
    return out
# 1) 任何语境都不出现女性向昵称
allp = run(["想你了", "哈哈笑死", "你个笨蛋", "我好难过", "说真的问你个事", "晚安", "略略略 菜"], {"mood": 8}, 400)
assert not (set(allp) & F), set(allp) & F
# 2) 难过/认真/生气 绝不出现调侃称呼
for msg in ["我好难过", "考试挂了 好累", "说真的 我想好了 跟你说个事", "气死了 别理我"]:
    for mood in (2, 5, 9):
        got = run([msg], {"mood": mood}, 80); assert not (set(got) & TEASE), (msg, mood, set(got) & TEASE)
# 3) 心情差时即使在闹也不出现
assert not (set(run(["你个笨蛋"], {"mood": 3}, 80)) & TEASE)
# 4) tease_context 判定
t, emo, _ = nickname.target("哈哈 你个笨蛋 略略略", {"mood": 9}); assert nickname.tease_context(t, emo, {"mood": 9})
t, emo, _ = nickname.target("我好难过", {"mood": 9}); assert not nickname.tease_context(t, emo, {"mood": 9})
# 5) 调侃语境下调侃称呼可被选到（库允许时）
lib = dict(nickname.library()); t, emo, _ = nickname.target("哈哈 你个笨蛋 略略略", {"mood": 9})
assert any(nickname.cos(t, lib[n]) > 0 for n in TEASE)
# 6) user_gender=f 时反过来（接口通用）
assert nickname.allowed_genders("f") == {"n", "f"}
import brain; assert "调侃称呼" in brain.TEASE_NOTE and "情侣" in brain.TEASE_NOTE
print("OK")
import collections
r = random.Random(0); c = collections.Counter(nickname.pick("哈哈 你个笨蛋 略略略", {"mood": 9}, r)[0] for _ in range(300))
share = sum(k for n, k in c.items() if n in TEASE) / 300
assert 0 < share < .6, share  # tier 混合后略抬，仍须明显低于一半以上; print(f"调侃语境下调侃称呼占比 {share:.2f}")
