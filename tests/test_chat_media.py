"""离线：parse_image_intent + mock generate/send；不碰微信、不调 codex。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, tempfile, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import chat_media, brain, config

# Isolate module-level scene assertions from the real wall clock and
# live life_schedule (e.g. 09:08–12:02 剧目排练, meal hints at 08/12/19).
# Production code is not changed; patches are restored at end of file.
import datetime as _iso_dt
import random as _iso_rnd
import life_calendar as _iso_lc
import schedule_fidelity as _iso_sf
import timeutil as _iso_tu

_ISO_FIXED_NOW = _iso_dt.datetime(2026, 10, 24, 15, 30)
_ISO_NEUTRAL_SLOT = {
    "bucket": "campus_life",
    "activity": "校园日常",
    "hints": ["校园日常"],
    "scene_hints": ["校园日常"],
    "kind": "workday",
    "photo_ok": True,
    "meal_period": "any",
}
_ISO_SAVED = {
    "lc_photo_context": _iso_lc.photo_context,
    "lc_now_local": getattr(_iso_lc, "now_local", None),
    "lc_resolve_life_now": _iso_lc.resolve_life_now,
    "lc_slot_at": _iso_lc.slot_at,
    "sf_resolve_slot": _iso_sf.resolve_slot,
    "sf_force_scene": _iso_sf.force_scene_for_slot,
    "sf_now_local": _iso_sf.now_local,
    "tu_now_local": _iso_tu.now_local,
    "random_Random": _iso_rnd.Random,
}
try:
    import proactive_beats as _iso_pb
    _ISO_SAVED["pb_meal_period_for_slot"] = _iso_pb.meal_period_for_slot
    _iso_pb.meal_period_for_slot = lambda slot=None, now=None: "any"
except Exception:
    _iso_pb = None

_iso_lc.now_local = lambda: _ISO_FIXED_NOW
_iso_lc.photo_context = lambda st=None, now=None: dict(_ISO_NEUTRAL_SLOT)
_iso_lc.resolve_life_now = lambda st=None, now=None: dict(_ISO_NEUTRAL_SLOT)
_iso_lc.slot_at = lambda now=None: {"meal_period": "any"}
_iso_sf.now_local = lambda now=None: now or _ISO_FIXED_NOW
_iso_sf.resolve_slot = lambda st=None, now=None: dict(_ISO_NEUTRAL_SLOT)
_iso_sf.force_scene_for_slot = lambda slot, scene=None, *, imagined=False: scene
_iso_tu.now_local = lambda cfg=None: _ISO_FIXED_NOW
_iso_rnd.seed(0)

class _IsoDetRandom(_ISO_SAVED["random_Random"]):
    """Unseeded Random() stays deterministic; explicit seeds (0, 1, …) unchanged."""
    def __init__(self, x=None):
        super().__init__(0 if x is None else x)

_iso_rnd.Random = _IsoDetRandom

# --- parse ---
assert chat_media.parse_image_intent("就是普通回复") is None
r = chat_media.parse_image_intent("给你看一眼\nWANT_IMAGE:图书馆窗边看书")
assert r == {"scene": "图书馆窗边看书", "caption": "给你看一眼"}, r
r = chat_media.parse_image_intent("WANT_IMAGE：宿舍化妆镜自拍\n啧 凑合看")
assert r["scene"] == "宿舍化妆镜自拍" and "凑合看" in r["caption"]
r = chat_media.parse_image_intent('{"want_image":"操场散步","caption":"下课啦"}')
assert r == {"scene": "操场散步", "caption": "下课啦"}, r
# 裸 scene JSON 不误伤
assert chat_media.parse_image_intent('{"scene":"x","caption":"y"}') is None
assert chat_media.parse_image_intent("") is None
assert chat_media.parse_image_intent("WANT_IMAGE:") is None

# --- brain.clean 与 LAST：mock 后端 ---
calls = []
def fake_cursor(prompt):
    calls.append(prompt)
    return "给你看\nWANT_IMAGE:咖啡店窗边"
brain.BACKENDS = dict(brain.BACKENDS, cursor_agent=fake_cursor, deepseek=lambda m: "WANT_IMAGE:操场\n走啦")
# force cursor path
cfg_llm = dict(config.load().get("llm") or {})
# monkey via brain.CFG
old_cfg = brain.CFG
brain.CFG = {**old_cfg, "llm": {**cfg_llm, "short_backend": None, "backend": "cursor_agent"}}
try:
    # memory/nickname may touch disk; dry=True skips state write
    # 闲聊「在干嘛」：模型自发 WANT_IMAGE 必须被底层门闩丢掉
    text = brain.reply("demo_contact", [("them", "在干嘛")], dry=True)
finally:
    brain.CFG = old_cfg
assert brain.LAST.get("want_image") in (None, ""), brain.LAST
assert "WANT_IMAGE" not in (text or "")
assert text == "给你看" or "给你看" in text

# 明确要图：保留 WANT_IMAGE
brain.CFG = {**old_cfg, "llm": {**cfg_llm, "short_backend": None, "backend": "cursor_agent"}}
try:
    text_ask = brain.reply("demo_contact", [("them", "发张图看看")], dry=True)
finally:
    brain.CFG = old_cfg
assert brain.LAST.get("want_image") == "咖啡店窗边", brain.LAST
assert "给你看" in (text_ask or "")

# deepseek path：闲聊同样不得自发图
brain.CFG = {**old_cfg, "llm": {**cfg_llm, "short_backend": "deepseek", "backend": "cursor_agent", "char_threshold": 9999}}
# pick_backend: short text → deepseek if short_backend set; force by patching pick
import llm
real_pick = llm.pick_backend
llm.pick_backend = lambda *a, **k: "deepseek"
try:
    text2 = brain.reply("demo_contact", [("them", "嗨")], dry=True)
finally:
    llm.pick_backend = real_pick
    brain.CFG = old_cfg
assert brain.LAST.get("want_image") in (None, ""), brain.LAST
assert text2 == "走啦" or (text2 or "").startswith("走")

# deepseek + 明确要图
brain.CFG = {**old_cfg, "llm": {**cfg_llm, "short_backend": "deepseek", "backend": "cursor_agent", "char_threshold": 9999}}
llm.pick_backend = lambda *a, **k: "deepseek"
try:
    text2b = brain.reply("demo_contact", [("them", "来张自拍")], dry=True)
finally:
    llm.pick_backend = real_pick
    brain.CFG = old_cfg
assert brain.LAST.get("want_image") == "操场", brain.LAST

# --- mock generate + send ---
tmp = pathlib.Path(tempfile.mkdtemp())
img = tmp / "out.png"; img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

class FakeIO:
    def __init__(s): s.opened, s.msgs, s.imgs = 0, [], []
    def open_chat(s): s.opened += 1
    def send_message(s, t): s.msgs.append(t)
    def send_image(s, p): s.imgs.append(str(p))

def fake_gen(scene, **kw):
    assert scene == "图书馆窗边看书"
    return ["codex"], [img]

io = FakeIO()
# whitelist
chat_media.assert_allowed_contact(config.load())
out = chat_media.send_generated_image(
    "图书馆窗边看书", caption="给你看", dry=False, io=io, generate_fn=fake_gen, open_chat_first=True, n_images=1
)
assert out["ok"] and out["sent_image"] and out["sent_caption"]
assert io.opened == 1 and io.msgs == ["给你看"] and io.imgs == [str(img)], (io.msgs, io.imgs)

# path passthrough / no-generate
io2 = FakeIO()
out2 = chat_media.send_generated_image(str(img), caption=None, io=io2, no_generate=True, open_chat_first=False)
assert out2["sent_image"] and io2.msgs == [] and io2.imgs == [str(img.resolve())], (out2, io2.imgs)

# dry
d = chat_media.send_generated_image("操场", caption="x", dry=True, io=FakeIO(), n_images=1)
assert d["dry"] and d["would_send_image"] and not d["sent_image"] and d["scene"] == "操场" and d["n_images"] == 1

# deliver_model_output
io3 = FakeIO()
def gen2(scene, **kw):
    assert scene == "宿舍"
    return ["c"], [img]
r = chat_media.deliver_model_output("配文\nWANT_IMAGE:宿舍", io=io3, generate_fn=gen2, n_images=1)
assert r["kind"] == "image" and io3.msgs == ["配文"] and io3.imgs

r2 = chat_media.deliver_model_output("纯文字", io=FakeIO())
assert r2["kind"] == "text" and r2["text"] == "纯文字"

# 非白名单
bad = {"contact": {"id": "999", "remark": "别人"}}
try:
    chat_media.send_generated_image(str(img), io=FakeIO(), cfg=bad, no_generate=True)
    raise SystemExit("whitelist failed")
except PermissionError:
    pass

# prompt teaches marker
assert "WANT_IMAGE" in brain.CHAT_TIPS and "WANT_IMAGE" in brain.REPLY_TASK

print("OK")


# --- multi-image 1..5 ---
import random
cfg_n = {"imagegen": {"chat_image_count_p": [0.0, 1.0, 0.0]}, "contact": config.load()["contact"]}
# sample bounds + lane caps（demand/proactive max=5，绝对顶 5）
assert chat_media.MAX_CHAT_IMAGES_ABS == 5
assert chat_media.image_count_max({"imagegen": {}}, lane="demand") == 5
assert chat_media.image_count_max({"imagegen": {}}, lane="proactive") == 5
assert chat_media.clamp_image_count(99, {"imagegen": {}}, lane="demand") == 5
assert chat_media.clamp_image_count(99, {"imagegen": {}}, lane="proactive") == 5
assert chat_media.clamp_image_count(0, {"imagegen": {}}, lane="demand") == 1
for _ in range(20):
    assert chat_media.sample_image_count({"imagegen": {"chat_image_count_p": [1, 0, 0, 0, 0]}}, random.Random(0)) == 1
assert chat_media.sample_image_count({"imagegen": {"chat_image_count_p": [0, 0, 0, 0, 1]}}, random.Random(1)) == 5
# proactive 可 >1：强制第 3 档
assert chat_media.sample_image_count(
    {"imagegen": {"proactive_image_count_p": [0, 0, 1, 0, 0]}}, random.Random(2), lane="proactive"
) == 3
# proactive 默认可抽到 >1（偏 1–2，但分布非恒 1）
nsp = [chat_media.sample_image_count({"imagegen": {}}, random.Random(i), lane="proactive") for i in range(400)]
assert set(nsp) <= {1, 2, 3, 4, 5}
assert max(nsp) > 1, "proactive must be able to sample N>1"
assert 5 in set(nsp) or any(
    chat_media.sample_image_count(
        {"imagegen": {"proactive_image_count_p": [0, 0, 0, 0, 1]}}, random.Random(i), lane="proactive"
    ) == 5
    for i in range(5)
)
# 旧更长分布截到 max=5 后仍合法
assert chat_media.sample_image_count({"imagegen": {"chat_image_count_p": [0, 0, 0, 0, 0, 1]}}, random.Random(3)) in (1, 2, 3, 4, 5)
ns = [chat_media.sample_image_count({"imagegen": {"chat_image_count_p": [0.40, 0.30, 0.18, 0.08, 0.04]}}, random.Random(i)) for i in range(500)]
assert set(ns) <= {1, 2, 3, 4, 5} and ns.count(1) > ns.count(5)

texts = chat_media.multi_interaction_texts(3, "给你看", random.Random(0))
assert len(texts) == 3 and texts[0] == "给你看" and texts[-1]

imgs = [tmp / f"o{i}.png" for i in range(3)]
for im in imgs:
    im.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
calls = {"n": 0, "last_n_images": None}
def gen_multi(scene, **kw):
    calls["n"] += 1
    n_req = int(kw.get("n_images") or 1)
    calls["last_n_images"] = n_req
    # N>1：一次调用返回 N 张；禁止被测路径串行多轮
    if n_req > 1:
        return ["c"], imgs[:n_req]
    return ["c"], [imgs[0]]
io_m = FakeIO()
out_m = chat_media.send_generated_image(
    "宿舍化妆镜自拍", caption="多看几张", io=io_m, generate_fn=gen_multi,
    open_chat_first=True, n_images=3, rng=random.Random(0),
)
assert out_m["n_images"] == 3 and out_m["sent_images"] == 3
assert calls["n"] == 1 and calls["last_n_images"] == 3 and len(io_m.imgs) == 3
assert io_m.msgs[0] == "多看几张"
assert any("哪张" in m or "挑" in m or "张" in m for m in io_m.msgs)

# plan + send_prepared
calls["n"] = 0
plan = chat_media.plan_chat_images("操场", caption="走", n_images=2, rng=random.Random(1), generate_fn=gen_multi)
assert len(plan["paths"]) == 2
assert calls["n"] == 1 and calls["last_n_images"] == 2
io_p = FakeIO()
sent = chat_media.send_prepared_images(plan, io=io_p, open_chat_first=False)
assert sent["sent_images"] == 2

# N=5 one-shot（无串行）；proactive lane 同样一次 generate
imgs5 = [tmp / f"p{i}.png" for i in range(5)]
for im in imgs5:
    im.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
calls["n"] = 0
calls["last_n_images"] = None
def gen_five(scene, **kw):
    calls["n"] += 1
    n_req = int(kw.get("n_images") or 1)
    calls["last_n_images"] = n_req
    return ["c"], imgs5[:n_req]
plan5 = chat_media.plan_chat_images(
    "食堂晚饭", caption="多拍了", n_images=5, rng=random.Random(0),
    generate_fn=gen_five, image_lane="proactive",
)
assert plan5["n_images"] == 5 and len(plan5["paths"]) == 5
assert calls["n"] == 1 and calls["last_n_images"] == 5
print("multi ok")

# --- photo_scene 日程地点关键词 + brain.clean 去括号旁白 ---
import photo_scene
class _St(dict):
    pass
# suggest_scene：模型只写「看展」时，hints 有 798 → 拼成 798看展
# Deterministic at any wall-clock hour: pin now, suppress meal hints, seed rng.
import datetime as _dt_ps
import random as _rnd_ps
import life_calendar as lc
_fixed_ps = _dt_ps.datetime(2026, 10, 24, 15, 30)
_real = lc.photo_context
_real_now = getattr(lc, "now_local", None)
_real_rln = getattr(lc, "resolve_life_now", None)
_real_slot = getattr(lc, "slot_at", None)
_ctx798 = {"bucket": "city_fun", "activity": "798/美术馆夜场逛展", "hints": ["798", "展览"], "kind": "weekend", "photo_ok": True}
lc.photo_context = lambda st=None, now=None: dict(_ctx798)
lc.now_local = lambda: _fixed_ps
lc.resolve_life_now = lambda st=None, now=None: {**_ctx798, "meal_period": "any"}
lc.slot_at = lambda now=None: {"meal_period": "any"}
try:
    s = photo_scene.suggest_scene(model_scene="看展", force_schedule=False, now=_fixed_ps, rng=_rnd_ps.Random(0), st={})
    assert s.startswith("798"), s
finally:
    lc.photo_context = _real
    if _real_now is not None:
        lc.now_local = _real_now
    if _real_rln is not None:
        lc.resolve_life_now = _real_rln
    if _real_slot is not None:
        lc.slot_at = _real_slot

assert brain.clean("刚拍好～（刚才卡了一下补发）") == "刚拍好～"
assert "补发" not in brain.clean("给你（系统延迟补发）看一眼")
assert "卡了" not in brain.clean("啧（刚才卡了）等下")

# 已够细的地点场景不得再塌
lc.photo_context = lambda st=None, now=None: dict(_ctx798)
lc.now_local = lambda: _fixed_ps
lc.resolve_life_now = lambda st=None, now=None: {**_ctx798, "meal_period": "any"}
lc.slot_at = lambda now=None: {"meal_period": "any"}
try:
    assert photo_scene.suggest_scene(model_scene="798/美术馆夜场逛展", now=_fixed_ps, rng=_rnd_ps.Random(0), st={}) == "798/美术馆夜场逛展"
    s_fs = photo_scene.suggest_scene(model_scene=None, force_schedule=True, now=_fixed_ps, rng=_rnd_ps.Random(0), st={})
    assert "798" in s_fs, s_fs  # 日程只作地点约束，不整段 activity 当 prompt
finally:
    lc.photo_context = _real
    if _real_now is not None:
        lc.now_local = _real_now
    if _real_rln is not None:
        lc.resolve_life_now = _real_rln
    if _real_slot is not None:
        lc.slot_at = _real_slot

# restore wall-clock isolation patches
_iso_lc.photo_context = _ISO_SAVED["lc_photo_context"]
if _ISO_SAVED["lc_now_local"] is not None:
    _iso_lc.now_local = _ISO_SAVED["lc_now_local"]
if _ISO_SAVED["lc_resolve_life_now"] is not None:
    _iso_lc.resolve_life_now = _ISO_SAVED["lc_resolve_life_now"]
if _ISO_SAVED["lc_slot_at"] is not None:
    _iso_lc.slot_at = _ISO_SAVED["lc_slot_at"]
_iso_sf.resolve_slot = _ISO_SAVED["sf_resolve_slot"]
_iso_sf.force_scene_for_slot = _ISO_SAVED["sf_force_scene"]
_iso_sf.now_local = _ISO_SAVED["sf_now_local"]
_iso_tu.now_local = _ISO_SAVED["tu_now_local"]
_iso_rnd.Random = _ISO_SAVED["random_Random"]
if _iso_pb is not None and _ISO_SAVED.get("pb_meal_period_for_slot") is not None:
    _iso_pb.meal_period_for_slot = _ISO_SAVED["pb_meal_period_for_slot"]
