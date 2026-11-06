"""离线：场景向量、推断、混合、映射文字。不读微信、不调 codex。"""
import sys, pathlib, random, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import outfit as o

need = ["上课/排练", "见老师/学生会正式场合", "朋友聚会", "约会", "准备见他", "和他在一起(居家/独处)", "宅宿舍/睡前", "外出日常"]
assert all(s in o.SCENES and len(o.SCENES[s]) == len(o.DIMS) and all(0 <= x <= 1 for x in o.SCENES[s]) for s in need)
V = lambda s: dict(zip(o.DIMS, o.SCENES[s]))
assert V("见老师/学生会正式场合")["formality"] > 0.8 and V("见老师/学生会正式场合")["exposure"] < 0.1
assert V("约会")["exposure"] > V("外出日常")["exposure"] > V("见老师/学生会正式场合")["exposure"]
assert V("上课/排练")["sportiness"] == max(o.SCENES[s][3] for s in o.LEGACY)
# 显式覆盖 + 别名
r = random.Random(1)
assert o.plan(scene="正式", rng=r)["weights"] == {"见老师/学生会正式场合": 1.0}
f = o.plan(scene="见老师/学生会正式场合", jitter=0); assert f["level"] == 0 and "规矩得体" in f["text"]
d = o.plan(scene="约会", jitter=0); assert d["level"] == 3 and "更放开" in d["text"] and "更显身材" in d["text"] and "精致" in d["text"]
assert o.plan(scene="和他在一起", jitter=0)["level"] == 3 and "居家" in o.plan(scene="居家", jitter=0)["text"]
assert "练功" in o.plan(scene="排练", jitter=0)["text"]
try: o.plan(scene="不存在"); assert False
except ValueError: pass
# 所有等级的文字都保留分寸
for lv in range(4): t = o.describe(V("约会"), lv); assert "不暴露" in t and "不性感化" in t
assert o.describe(V("约会"), 9) == o.describe(V("约会"), 3) and o.describe(V("约会"), -1) == o.describe(V("约会"), 0)
# 抖动：有界、可复现
a = o.blend({"约会": 1.0}, 0.05, random.Random(3)); b = o.blend({"约会": 1.0}, 0.05, random.Random(3)); assert a == b
assert all(abs(a[k] - V("约会")[k]) <= 0.05 + 1e-9 for k in o.DIMS)
# 混合是线性的
m = o.blend({"约会": .5, "宅宿舍/睡前": .5}, 0); assert abs(m["coziness"] - (.1 + .95) / 2) < 1e-6
# 推断：作息时段
mon10 = datetime.datetime(2026, 10, 19, 10, 0)  # 周一
top = lambda w: next(iter(w))
assert top(o.infer_weights("", mon10, activity="编导技法课")) == "上课/排练"
assert top(o.infer_weights("", mon10, activity="学生会文艺部例会")) == "见老师/学生会正式场合"
assert top(o.infer_weights("", mon10, activity="准备睡觉")) == "宅宿舍/睡前"
assert top(o.infer_weights("", mon10, activity="和室友出去玩")) == "朋友聚会"
assert abs(sum(o.infer_weights("", mon10, activity="x").values()) - 1) < 1e-9
# 画面描述/聊天
assert top(o.infer_weights("晚上和他去约会看电影", mon10, activity="自由时间")) == "约会"
assert top(o.infer_weights("", mon10, activity="自由时间", chat=["我马上到，等我", "来找你啦"])) == "准备见他"
assert top(o.infer_weights("窝在一起看电影，在你家", mon10, activity="")) == "和他在一起(居家/独处)"
# 周末晚上更可能见他；工作日上午不会
sat = datetime.datetime(2026, 10, 24, 20, 0)
assert o.infer_weights("", sat, activity="自由时间").get("约会", 0) > o.infer_weights("", mon10, activity="自由时间").get("约会", 0)
# 校园事件：参与晚会 -> 正式权重上升
base = o.infer_weights("", mon10, activity="吃午饭")
ev = o.infer_weights("", mon10, activity="吃午饭", campus_life=["在忙：学生会迎新晚会筹备"])
assert ev.get("见老师/学生会正式场合", 0) > base.get("见老师/学生会正式场合", 0)
# 情绪：开心+亲密 -> 见他；低落 -> 宅
happy = {"pad": {"pleasure": .8}, "intimacy": 70, "last_label": "coquet"}
sad = {"pad": {"pleasure": -.6}, "intimacy": 70}
assert o.infer_weights("", mon10, activity="吃午饭", emo=happy).get("准备见他", 0) > base.get("准备见他", 0)
assert o.infer_weights("", mon10, activity="吃午饭", emo=sad).get("宅宿舍/睡前", 0) > base.get("宅宿舍/睡前", 0)
# plan 全部上下文可注入（不读 STATE/聊天库）
p = o.plan("在排练厅", now=mon10, activity="剧目排练", emo=None, campus_life=[], chat=[], rng=random.Random(0))
assert top(p["weights"]) == "上课/排练" and p["text"].startswith("穿搭：")

# ---------- 500+ 场景库 ----------
import json, os, collections, re
L = o.SCENE_LIST; assert len(L) >= 500, len(L)
cats = collections.Counter(d["category"] for d in L)
for c in ("体育", "娱乐", "约会", "旅游", "旅游国家", "旅游景点", "校园日常"): assert cats[c] >= 30, (c, cats)
assert len({d["name"] for d in L}) == len(L) and len({d["id"] for d in L}) == len(L)
for d in L:
    assert set(d["vec"]) == set(o.XDIMS) and all(0 <= x <= 1 for x in d["vec"].values()) and 0 <= d["cap"] <= 3 and d["hint"], d
    assert not re.search(r"性感|暴露|比基尼|内衣|低胸|透视", d["hint"] + d.get("hint_safe", "")), d
import re
for n in ("见家长", "寺庙", "见老师/学生会正式场合", "灵隐寺", "迪拜" if "迪拜" in o.BY_NAME else "阿联酋迪拜"): assert o.BY_NAME[n]["cap"] == 0, n
assert o.BY_NAME["游泳"]["cap"] <= 2 and "泳衣" in o.BY_NAME["游泳"]["hint"] and o.BY_NAME["游泳"].get("hint_safe")
for n in ("爬山", "打网球", "游泳", "滑雪", "瑜伽", "跑步", "KTV", "剧本杀", "演唱会", "游乐园", "看展", "烛光晚餐", "看电影约会", "逛街",
          "海边", "露营", "古镇", "自驾", "日本", "泰国", "法国", "冰岛", "三里屯", "故宫", "长城", "外滩", "洱海", "富士山",
          "宅宿舍/睡前", "准备见他", "和他在一起(居家/独处)"): assert n in o.BY_NAME, n
# 显式覆盖：名称/id/别名
assert o.resolve("spot-001") == "三里屯" and o.resolve("去游泳") == "游泳" and o.resolve("太古里") == "三里屯"
assert o.plan(scene="爬山", jitter=0)["scene"] == "爬山" and o.plan(scene="见家长", jitter=0)["level"] == 0
# 选择（向量 + 别名；带作息先验）
sat20 = datetime.datetime(2026, 10, 24, 20, 0); sat10 = datetime.datetime(2026, 10, 24, 10, 0)
C = dict(emo=None, campus_life=[], chat=[])
def top1(t, now, act, **kw): return o.rank(t, now, activity=act, **C, **kw)[0][0]
for emb in (True, False):   # 有模型 / 无模型（别名+字符匹配兜底）都要对
    assert top1("周六晚上在三里屯", sat20, "准备睡觉", use_embed=emb) == "三里屯"
    assert top1("周六晚上在三里屯", sat20, "自由时间", use_embed=emb) != "宅宿舍/睡前"
    assert top1("周末去爬香山", sat10, "自由时间", use_embed=emb) == "爬山"
    assert top1("见老师", mon10, "", use_embed=emb) == "见老师/学生会正式场合"
    assert top1("在泳池游泳", sat10, "", use_embed=emb) == "游泳"
    assert top1("去冰岛看极光", sat10, "", use_embed=emb) in ("冰岛", "极光")
p = o.plan("周六晚上在三里屯", now=sat20, activity="准备睡觉", **C, rng=random.Random(0))
assert p["scene"] == "三里屯" and p["top"][0]["name"] == "三里屯" and "三里屯" in p["text"] and p["level"] >= 2
p = o.plan("见老师", now=mon10, activity="", **C, rng=random.Random(0)); assert p["level"] == 0 and "规矩得体" in p["text"]
assert o.plan("周末去爬香山", now=sat10, activity="", **C, rng=random.Random(0))["scene"] == "爬山"
# 季节/天气：冬天户外更保暖；目的地自带气候
jan = datetime.datetime(2027, 1, 9, 14, 0); jul = datetime.datetime(2026, 7, 9, 14, 0)
w1 = o.plan(scene="爬山", now=jan, jitter=0)["vector"]["warmth"]; w2 = o.plan(scene="爬山", now=jul, jitter=0)["vector"]["warmth"]
assert w1 < 0.25 < 0.7 < w2 and "羽绒服" in o.plan(scene="爬山", now=jan, jitter=0)["text"]
assert o.plan(scene="泰国", now=jan, jitter=0)["vector"]["warmth"] > 0.8 and o.season_warmth(jul, "北京 晴 -3°C") < 0.1
# 游泳降到 0 级用 hint_safe
v = o.plan(scene="游泳", jitter=0)["vector"]; assert "泳衣" not in o.describe(v, 0, "游泳") and "泳衣" in o.describe(v, 2, "游泳")
# 采样：温度高时也只在 top-k 里选；权重和为 1
r = o.rank("和他去海边", sat10, activity="", **C); names = [n for n, _ in r[:8]]
for seed in range(10):
    pr, w, info = o.choose(r, 8, 0.2, random.Random(seed)); assert pr in names and abs(sum(w.values()) - 1) < 0.01
# 有本地模型时验证缓存；干净环境则验证词法回退，不依赖未提交的缓存。
if o._load_model() is None:
    assert o.scene_embeddings() is None
else:
    assert o.scene_embeddings() is not None and o.EMB_FILE.exists()
print("OK")


def test_library_reading_not_home_scene():
    import outfit
    top = outfit.rank("在图书馆低头看书", use_embed=False, activity="", k=3)
    assert top[0][0] == "图书馆自习"
    names = [n for n, _ in outfit.rank("在图书馆低头看书", use_embed=False, activity="")]
    assert names.index("看书") > names.index("图书馆自习")


def test_place_adjust_penalizes_home_scene_in_public_place():
    import outfit
    i = [d["name"] for d in outfit.SCENE_LIST].index("看书")
    assert outfit.place_adjust("在教室看书")[i] < 0
    assert outfit.place_adjust("在家窝沙发看书")[i] > 0
    assert outfit.place_adjust("看书")[i] == 0

# ---------- 地点别名压过粗粒度场景（798 > 看展）----------
sat2033 = datetime.datetime(2026, 10, 24, 20, 33)
assert o.prefer_place_scene("看展", "798/美术馆夜场逛展", ["在798看展呢"]) == "798艺术区"
p798 = o.plan("看展", now=sat2033, activity="798/美术馆夜场逛展", chat=["在798看展呢"], emo=None, campus_life=[], rng=random.Random(0))
assert p798["scene"] == "798艺术区", p798["scene"]

# 细地点不得塌成「看展」
assert o.prefer_place_scene("798/美术馆夜场逛展", "798/美术馆夜场逛展", []) == "798艺术区"
p_keep = o.plan("798/美术馆夜场逛展", now=sat2033, activity="798/美术馆夜场逛展", chat=[], emo=None, campus_life=[], rng=random.Random(0))
assert p_keep["scene"] == "798艺术区", p_keep["scene"]

# 日程地点优先于聊天旧话：排练残留不得压过当前长城
assert o.prefer_place_scene("长城", "古北水镇/长城冬日半日", ["下课啦 今天排练排到腿软"]) is None
p_wall = o.plan("长城", now=sat2033, activity="古北水镇/长城冬日半日",
                chat=["下课啦 今天排练排到腿软"], emo=None, campus_life=[], rng=random.Random(0))
assert p_wall["scene"] == "长城", p_wall["scene"]

# bool / non-iterable must not crash prefer_place_scene
assert o.prefer_place_scene("操场", True, True) is None or isinstance(o.prefer_place_scene("操场", True, True), (str, type(None)))
assert o.prefer_place_scene("长城", False, None) is None or True
assert o.prefer_place_scene("", None, True) is None


def test_mid_cold_pantyhose_branch_rates():
    """偏冷 warmth 文案：长裤 50% / 厚连裤袜 30% / 丝袜类 20%。"""
    import collections
    counts = collections.Counter()
    for i in range(1000):
        t = o.warmth_text(0.3, random.Random(i))
        assert t.startswith("偏冷，外套加针织层次，下装")
        bottom = t.split("下装", 1)[1]
        counts[bottom] += 1
    assert set(counts) == {"长裤", "厚连裤袜", "丝袜类"}
    # 允许抽样误差：约 ±5pp
    assert 450 <= counts["长裤"] <= 550, counts
    assert 250 <= counts["厚连裤袜"] <= 350, counts
    assert 150 <= counts["丝袜类"] <= 250, counts
    assert "长裤或厚连裤袜" not in o.warmth_text(0.3, random.Random(0))


def test_cold_plan_no_summer_shorts():
    """冷天户外（网球 hint 原有短裤短裙）不得残留夏季下装。"""
    jan = datetime.datetime(2027, 1, 9, 14, 0)
    C = dict(emo=None, campus_life=[], chat=[])
    p = o.plan(scene="打网球", now=jan, jitter=0, weather="北京 晴 -3°C", **C, rng=random.Random(0))
    assert p["vector"]["warmth"] < 0.4
    # 场景 hint 原「运动短裙或短裤」须被换成长裤；禁令句本身可含「短裤」字样
    assert "运动短裙" not in p["text"] and "运动短裤" not in p["text"]
    assert "短裙或短裤" not in p["text"]
    assert "长裤" in p["text"] or "连裤袜" in p["text"] or "丝袜" in p["text"]
    assert "勿短裤短裙光腿" in p["text"]
    # 室内偏冷也要挂 warmth（原先 outdoor 门槛会漏）
    dorm = o.plan(scene="宅宿舍/睡前", now=jan, jitter=0, weather="北京 晴 -3°C", **C, rng=random.Random(1))
    assert dorm["vector"]["warmth"] < 0.4
    assert "偏冷" in dorm["text"] or "天冷" in dorm["text"]


def test_season_warmth_reads_disk_cache(monkeypatch, tmp_path):
    """无进程缓存且 now≈现在时，读 BDA（非燕园）weather 落盘，不联网。"""
    import weather as w
    import time
    cache = tmp_path / "weather_cache.json"
    # key 必须与 weather._key(places()['bda']) 一致
    cache.write_text(
        __import__("json").dumps({"39.9459,116.3130": {"ts": time.time(), "text": "北京海淀北舞 晴 2°C（体感0°C）"}})
    )
    monkeypatch.setattr(w, "CACHE_FILE", cache)
    monkeypatch.setattr(w, "_cache", {})
    monkeypatch.setattr(w, "_fetch", lambda u: (_ for _ in ()).throw(RuntimeError("net")))
    sw = o.season_warmth(datetime.datetime.now(), weather=None)
    assert sw == round((2 + 5) / 38, 3)
    # 拨到严冬且无显式 weather → 月份先验，不串今日缓存
    jan = datetime.datetime(2027, 1, 9, 14, 0)
    assert o.season_warmth(jan, weather=None) == o.SEASON[1]
    # 显式 place=yanyuan 不应误用 BDA 缓存键以外的数据：无燕园缓存 → 月份
    assert o.season_warmth(datetime.datetime.now(), weather=None, place="yanyuan") == o.SEASON[datetime.datetime.now().month]


def test_rank_accepts_shot_type_no_typeerror():
    """imagegen 透传 shot_type 不得打爆 rank。"""
    top = o.rank("宿舍看看腿", use_embed=False, activity="", k=3, shot_type="legs_focus")
    assert top and isinstance(top[0][0], str)


def test_plan_shot_type_and_user_text_heisi_reaches_prompt():
    """手动要图：shot_type + user_text=黑丝 不得跌进保守兜底，且黑丝进穿搭句。"""
    import imagegen
    now = datetime.datetime(2026, 10, 26, 22, 0)
    p = imagegen.outfit_plan(
        "宿舍看看腿",
        chat=True,
        shot_type="legs_focus",
        shot_bits={"activity": "休息"},
        skip_face_fallback=True,
        slot={"bucket": "evening"},
        today={"date": "2026-10-26"},
        date="2026-10-26",
        user_text="来张黑丝腿部特写",
        now=now,
        emo=None,
        campus_life=[],
        activity="",
    )
    assert p.get("text")
    assert "穿着日常、得体、保守" not in p["text"]
    assert "黑丝" in p["text"] or "黑色丝袜" in p["text"]
    # 直接 plan 路径同样吃 user_text
    p2 = o.plan(
        "宿舍看看腿",
        now=now,
        rng=random.Random(0),
        use_embed=False,
        emo=None,
        campus_life=[],
        chat=[],
        activity="",
        shot_type="legs_focus",
        user_text="来张黑丝",
    )
    assert "黑丝" in p2["text"] or "黑色丝袜" in p2["text"]


def test_rank_ctx_strips_unknown_kwargs():
    """slot/today/user_text 等不得再以 TypeError 泄漏进 rank。"""
    now = datetime.datetime(2026, 10, 26, 22, 0)
    p = o.plan(
        "图书馆看书",
        now=now,
        rng=random.Random(1),
        emo=None,
        campus_life=[],
        chat=["来张照片"],
        activity="",
        shot_type="portrait",
        shot_bits={},
        skip_face_fallback=True,
        slot={"bucket": "x"},
        today={"date": "2026-10-26"},
        date="2026-10-26",
        user_text="发张自拍",
    )
    assert p.get("scene")
    assert "穿着日常、得体、保守" not in p["text"]
