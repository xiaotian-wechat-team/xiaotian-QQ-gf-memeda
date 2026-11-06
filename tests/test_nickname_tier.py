"""离线：nickname tier（core/extension）、禁词、清理项。python3 tests/test_nickname_tier.py"""
import sys, pathlib, random, collections, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import nickname

nickname.library()
M = nickname.META
lib_names = {n for n, _ in nickname.library()}

# 1) 禁错误姓氏称呼：库与 META 皆无；即使写进 json 也被 library 过滤
assert "王某某" not in lib_names and "王某某" not in M
assert "王某某" in nickname.BANNED_NAMES

# 2) tier 字段完备；core / extension 分界
assert all(m.get("tier") in ("core", "extension") for m in M.values())
cores = {n for n, m in M.items() if m["tier"] == "core"}
exts = {n for n, m in M.items() if m["tier"] == "extension"}
assert cores and exts
for must in ("小熊", "🐻"):
    assert must in cores, must
for sweet in ("宝贝", "honey", "宝宝"):
    assert sweet in exts, sweet
# 羊系谐音保留为 extension
for sheep in ("小羊", "羊羊", "小绵羊"):
    assert sheep in exts, sheep

# 3) weight：core 显著高于 extension
core_w = [M[n]["weight"] for n in cores]
ext_w = [M[n]["weight"] for n in exts]
assert min(core_w) >= 1.15
assert max(ext_w) <= nickname.EXTENSION_WEIGHT_CAP + 1e-9
assert min(core_w) > max(ext_w)
assert 0.05 <= nickname.EXTENSION_PICK_P <= 0.25

# 4) 已清除不符拓展库的项
purged = [
    "小鱼", "鱼鱼", "猪猪", "小猫", "狗勾", "兔兔", "小狗", "小猪",
    "小公主", "小仙女", "我的王子", "女王大人", "王老师", "王总", "小狐狸",
]
for n in purged:
    assert n not in M and n not in lib_names, n

# 5) 日常语境 core 主导（无 sticky）
rng = random.Random(7)
c = collections.Counter(
    nickname.pick("今天怎么样", {"mood": 7, "energy": 6, "recent_nicknames": []}, rng, sticky=False)[0]
    for _ in range(300)
)
core_share = sum(v for k, v in c.items() if k in cores) / 300
ext_share = sum(v for k, v in c.items() if k in exts) / 300
assert core_share >= 0.80, core_share
assert 0.02 <= ext_share <= 0.25, ext_share
print(f"日常 core 占比 {core_share:.2f} ext {ext_share:.2f}; top={c.most_common(5)}")

# 6) json 行带 tier
raw = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "nicknames.json").read_text())["nicknames"]
assert all(n.get("tier") in ("core", "extension") for n in raw)
assert all(n["name"] not in nickname.BANNED_NAMES for n in raw)
print("OK")
