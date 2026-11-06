"""写实修饰库：分组覆盖、互斥、硬禁（正式/寺庙不闪光）、加权采样概率界（固定种子）。离线，不生图。"""
import sys, pathlib, random, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import realism, outfit, imagegen

FLASHY = {m["id"] for m in realism.MODS if "flash" in m.get("exclusive", []) or "闪光" in m["zh"]}

def test_library_shape():
    assert 80 <= len(realism.MODS) <= 150
    c = collections.Counter(m["group"] for m in realism.MODS)
    assert set(c) == set(realism.GROUPS) and len(c) == 7 and min(c.values()) >= 5
    for m in realism.MODS:
        assert m["en"] and m["zh"] and m["weight"] > 0 and m["time"] in ("any", "day", "night")
        assert not any(w in m["en"].lower() for w in ("sexy", "seductive", "nude", "lingerie", "cleavage"))
        if m["group"] == "blur_focus" and "blur" in m["en"]: assert "face" in m["en"] or "background" in m["en"] or "foreground" in m["en"]

def test_no_flash_formal_sacred():
    for name in ("见老师/学生会正式场合", "寺庙", "灵隐寺", "教堂", "话剧"):
        assert BAN & realism.scene_tags(name), name
        for seed in range(300):
            s = realism.pick(name, name, hour=21, seed=seed)
            assert not FLASHY & set(s["ids"]), (name, s["ids"])
        assert not FLASHY & set(realism.probs(realism.scene_tags(name), night=True))
BAN = {"formal", "sacred", "theater", "museum"}

def test_exclusive_and_one_per_group():
    for seed in range(2000):
        sc = outfit.SCENE_LIST[seed % len(outfit.SCENE_LIST)]
        s = realism.pick(sc["name"], hour=seed % 24, seed=seed)
        assert 0 <= len(s["ids"]) <= 3 and len(s["ids"]) <= s["n"]
        assert len(set(s["groups"])) == len(s["groups"])
        xs = [x for i in s["ids"] for x in realism.BY_ID[i]["exclusive"]]; assert len(xs) == len(set(xs)), s["ids"]

def test_seed_reproducible_and_count_distribution():
    assert realism.pick("KTV", seed=7, hour=22) == realism.pick("KTV", seed=7, hour=22)
    rng = random.Random(42); ns = collections.Counter(len(realism.pick("外出日常", rng=rng, hour=15)["ids"]) for _ in range(4000))
    assert 0.25 < ns[0] / 4000 < 0.35 and 0.6 < (ns[1] + ns[2] + ns[3]) / 4000 < 0.76 and ns[3] > 0

def test_scene_biases_not_rules():
    ktv = realism.probs(realism.scene_tags("KTV") | {"night"}, outfit.BY_NAME["KTV"]["vec"], night=True)
    beach = realism.probs(realism.scene_tags("三亚") | realism.scene_tags("海边"), {"outdoor": 1, "water": 1, "warmth": .9}, night=False)
    assert ktv["direct_flash"] > beach.get("direct_flash", 0) * 3
    assert beach["lens_flare"] > ktv["lens_flare"] * 3
    sport = realism.probs(realism.scene_tags("篮球"), {"sportiness": .9, "outdoor": .6})
    assert sport["motion_blur_limbs"] > realism.probs({"campus"}, {"sportiness": .1})["motion_blur_limbs"]
    # 不是硬规则：海边白天也有小概率出现直闪；没有修饰概率为 1
    assert 0 < beach["direct_flash"] < 0.05 and max(ktv.values()) < 0.3
    dorm = realism.probs(realism.scene_tags("宅宿舍/睡前"), outfit.BY_NAME["宅宿舍/睡前"]["vec"], night=True)
    assert dorm["mirror_selfie"] > ktv["mirror_selfie"]

def test_prompt_wiring():
    plan = imagegen.add_realism({"scene": "KTV", "vector": outfit.BY_NAME["KTV"]["vec"], "text": "穿搭：x。"}, "在KTV唱歌", seed=1)
    for seed in range(50):
        plan = imagegen.add_realism({"scene": "KTV", "vector": {}, "text": "t"}, "在KTV唱歌", seed=seed)
        if plan["realism"]["ids"]: break
    p = imagegen.build_prompt("在KTV唱歌", "黑色长直发", plan["text"], plan["realism_text"])
    assert "写实细节" in p and "脸始终清晰" in p and "不性感化" in p and "同一个女生" in p and p.rstrip().endswith("$imagegen")
    _, inner = imagegen.macbook_cmd("x", {"imagegen": {}}, "o", plan["realism_text"]); assert "REALISM_TEXT=" in inner
    import os; os.environ["REALISM"] = "0"
    try: assert imagegen.add_realism({"scene": "KTV"}, "x")["realism_text"] == ""
    finally: os.environ.pop("REALISM")

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f()
    print("ok")
