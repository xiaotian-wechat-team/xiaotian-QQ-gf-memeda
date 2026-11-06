"""离线：实时回复循环（变化检测 / 退避 / 拟人打字 / 回复优先锁 / 合并重写）。全部假 io、假时钟，不碰微信、不写 DB。"""
import sys, pathlib, random, tempfile, contextlib, types, os, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import daemon as D, ui_lock as UL, proactive_sender as S

RC = dict(D.DEFAULT_RC)
class Clock:
    def __init__(s): s.t = 0.0
    def __call__(s): return s.t
    def sleep(s, d): s.t += d; s.slept.append(d) if hasattr(s, "slept") else None

class FakeIO:
    """screen: 列表 [(from,text)]；fp = 画面哈希。"""
    def __init__(s): s.screen, s.sent, s.ocr_calls, s.fp_calls, s.opened, s.in_chat = [], [], 0, 0, 0, True
    def chat_fingerprint(s): s.fp_calls += 1; return str(hash(tuple(s.screen)))
    def read_chat(s): s.ocr_calls += 1; return s.in_chat, [{"from": f, "text": t} for f, t in s.screen]
    def open_chat(s): s.opened += 1; s.in_chat = True
    def send_message(s, t): s.sent.append(t); s.screen.append(("me", t))

class FakeMem:   # 记录 DB 里已有的 (from,text)
    def __init__(s): s.rows = []
    def tail(s, c, cid, n): return [(i, f, t) for i, (f, t) in enumerate(s.rows[-n:])]
    def history(s, c, cid, n): return s.rows[-n:]
    def read_state(s, cid): return {}
    def update_state(s, cid, **k): pass
    def insert(s, c, cid, f, t, fp, commit=True): s.rows.append((f, t))
    def mark_replied(s, c, cid): pass
    def append_diary(s, cid, line): pass
    new_suffix = staticmethod(__import__("memory").new_suffix)
    fingerprint = staticmethod(lambda *a: "x")

def setup():
    m = FakeMem(); D.memory = m
    D.emotion = types.SimpleNamespace(appraise=lambda e, t: (e, None), derived=lambda e: {})
    D.ingest = lambda c, new: [m.rows.append((x["from"], x["text"])) for x in new]
    class C:
        def execute(s, *a): return types.SimpleNamespace(fetchone=lambda: (len(m.rows),))
    return m, C()

def mk(io, clk, **kw):
    m, c = setup(); locks = []
    @contextlib.contextmanager
    def lock(): locks.append(1); yield
    L = D.Loop(c, io=io, rc=RC, lock=lock, priority=contextlib.nullcontext, clock=clk, sleep=clk.sleep,
               reply_fn=kw.get("reply_fn", lambda h: "好呀"), think_fn=lambda: kw.get("think", 0.0),
               typing_fn=lambda t: kw.get("typing", 0.0), log=lambda *a, **k: None)
    return L, m, locks

# 1) 打字延时：按长度、夹在 2~15s；关掉为 0
r = random.Random(0)
assert D.typing_delay("嗯", RC, r) == 2 and D.typing_delay("啊" * 200, RC, r) == 15
assert 2 < D.typing_delay("今天排练好累呀腿都软了", RC, r) < 15
assert D.typing_delay("x" * 50, dict(RC, human_typing=False)) == 0
# 思考延时：醒着/睡着一律封顶 think_max_awake_sec（无睡觉长等待）
assert D.think_delay(RC, sleeping=False) <= 5 and D.think_delay(RC, sleeping=True) <= 5  # no sleeping long-wait overlay
# human_wait：LLM 墙钟抵扣 think 与 typing，打字不再叠在 LLM 之后
# before: max(0, think-e) + typing ; after: max(0, think-e) + max(0, typing-e)
assert D.human_wait(40, "嗯", RC, think_fn=lambda: 5, typing_fn=lambda t: 7) == 0
assert D.human_wait(3, "嗯", RC, think_fn=lambda: 5, typing_fn=lambda t: 7) == (2 + 4)
assert D.human_wait(0, "嗯", RC, think_fn=lambda: 5, typing_fn=lambda t: 7) == 12
assert D.human_wait(6, "嗯", RC, think_fn=lambda: 5, typing_fn=lambda t: 7) == 1  # think 已够，只补 typing 1s

# 2) 退避：默认 poll_idle_max==poll_active（紧循环）；显式放大 idle 时仍可退避，变化立刻回 active
b = D.Backoff(RC); t = 0; seq = []
for _ in range(40): seq.append(b.update(False, t)); t += seq[-1]
assert seq[0] == RC["poll_active_sec"] and max(seq) == RC["poll_idle_max_sec"]
assert b.update(True, t) == RC["poll_active_sec"]
# 兼容旧行为：idle_max > active 时静默后上抬
slow = dict(RC, poll_active_sec=1.0, poll_idle_max_sec=4.0, idle_after_sec=20, backoff_factor=1.5)
b2 = D.Backoff(slow); t = 0; seq2 = []
for _ in range(40): seq2.append(b2.update(False, t)); t += seq2[-1]
assert seq2[0] == 1 and max(seq2) == 4 and seq2[-1] == 4 and min(seq2[25:]) > 1
assert b2.update(True, t) == 1

# 3) 无变化不 OCR、不拿锁
io, clk = FakeIO(), Clock(); io.screen = [("me", "在吗")]
L, m, locks = mk(io, clk); m.rows = [("me", "在吗")]
L.step(); o = io.ocr_calls                      # 第一轮必做一次全检
for _ in range(10): L.step()
assert io.ocr_calls == o and len(locks) == o and io.sent == []

# 4) 他发消息 → 下一轮就检测到 → 回复；检测延迟 ≤ 1 个活跃轮询间隔
io.screen.append(("them", "下课了吗")); d = L.step()
assert io.sent == ["好呀"] and d == RC["poll_active_sec"], (io.sent, d)
assert m.rows[-1] == ("me", "好呀")
# 回过之后同一画面不重复回
for _ in range(5): L.step()
assert io.sent == ["好呀"]

# 5) 打字等待期间他又发 → 合并后重写，只发一条
io, clk = FakeIO(), Clock(); calls = []
def rf(h): calls.append(list(h)); return f"回{len(calls)}"
L, m, _ = mk(io, clk, reply_fn=rf, typing=6.0)
io.screen = [("them", "a")]
orig = clk.sleep
def sleep_inject(d, n=[0]):
    orig(d); n[0] += 1
    if n[0] == 3: io.screen.append(("them", "b"))   # 打字到一半又来一条
clk.sleep = sleep_inject; L.sleep = sleep_inject
L.step()
assert io.sent == ["回2"] and ("them", "b") in calls[-1], (io.sent, calls)

# 6) 不在目标聊天 → open_chat 后再读
io, clk = FakeIO(), Clock(); io.in_chat = False; io.screen = [("them", "hi")]
L, m, _ = mk(io, clk); L.step(); assert io.opened == 1 and io.sent == ["好呀"]

# 7) 截图失败 → 不崩，按全检处理
class BadFP(FakeIO):
    def chat_fingerprint(s): raise PermissionError("no screen")
io, clk = BadFP(), Clock(); io.screen = [("them", "hi")]
L, m, _ = mk(io, clk); L.step(); assert io.sent == ["好呀"]

# 8) 回复优先锁：挂起 reply_pending 时主动发送拿不到锁；回复方持锁时主动发送也拿不到（不阻塞）
tmp = pathlib.Path(tempfile.mkdtemp()); lk, pend = tmp / "ui.lock", tmp / "pending"
with UL.reply_priority(pend):
    assert UL.reply_pending(pend)
    try:
        with UL.ui_lock("proactive", lk, pend): raise SystemExit("proactive should yield")
    except UL.LockBusy: pass
assert not UL.reply_pending(pend) and not pend.exists()
import subprocess
holder = subprocess.Popen([sys.executable, "-c", f"import fcntl,time;f=open({str(lk)!r},'a');fcntl.flock(f,fcntl.LOCK_EX);print('ok',flush=True);time.sleep(3)"], stdout=subprocess.PIPE)
holder.stdout.readline(); t0 = time.time()
try:
    with UL.ui_lock("proactive", lk, pend): raise SystemExit("should be busy")
except UL.LockBusy: pass
assert time.time() - t0 < 0.5      # 非阻塞
holder.kill(); holder.wait()
with UL.ui_lock("proactive", lk, pend): pass   # 空闲时可拿
with UL.ui_lock("reply", lk, pend): pass
# 过期挂起标记（daemon 崩溃遗留、pid 不存在）不挡主动发送
pend.write_text("999999"); assert not UL.reply_pending(pend)
pend.write_text(str(os.getpid())); assert not UL.reply_pending(pend, now=time.time() + UL.STALE_SEC + 1)

# 9) 主动发送遇到回复优先 → HOLD，不发
import copy, datetime, config
on = copy.deepcopy(config.load()); on["proactive"].update(probability=1, transport="ui", image_share_min_ratio=0)
# Public snapshot has no appearance assets; this section tests UI-lock priority,
# not image generation. Disable the image floor so missing refs cannot DROP first.
import proactive_media as _pmedia
_orig_pm_required = _pmedia.required
_orig_pm_prepare = _pmedia.prepare
_pmedia.required = lambda *a, **k: False
def _prep_no_img(beat, *a, **k):
    b = dict(beat or {})
    b.pop("image_required", None)
    return b
_pmedia.prepare = _prep_no_img
class PIO(FakeIO):
    SIDEBAR_W, HEADER_H = 300, 60
    norm = staticmethod(lambda t: "".join((t or "").split()))
    def capture(s): pass
    def ocr(s): return [{"text": "小熊", "x": 400, "y": 20}]
    def read_messages(s): return [{"from": "me", "text": "x"}]
def busy(role="proactive", path=None): raise UL.LockBusy("回复优先：有待回复消息")
S.ui_lock = busy; pio = PIO()
r = S.run_once(datetime.datetime(2026, 10, 24, 20, 0), dry=False, io=pio, cfg_full=on, rows=[], st={}, rng=random.Random(0),
               draft_fn=lambda: "下课啦", qpath=tmp / "q.json")
assert r["action"] == "HOLD" and "回复优先" in r["reason"] and pio.sent == [], r
# 核对期间来了新消息（reply_pending 变真）→ HOLD
S.ui_lock = lambda role="proactive", path=None: contextlib.nullcontext()
real = UL.reply_pending; UL.reply_pending = lambda *a, **k: True
r = S.run_once(datetime.datetime(2026, 10, 24, 20, 0), dry=False, io=pio, cfg_full=on, rows=[], st={}, rng=random.Random(0),
               draft_fn=lambda: "下课啦", qpath=tmp / "q2.json")
UL.reply_pending = real
assert r["action"] == "HOLD" and pio.sent == [], r
_pmedia.required = _orig_pm_required
_pmedia.prepare = _orig_pm_prepare

# 10) 连发放弃后 DB 已有未回 them：下一轮 theirs 为空但 need=True，仍须回复
io, clk = FakeIO(), Clock(); calls = []
def rf_ab(h): calls.append([tuple(x) if not isinstance(x, tuple) else x for x in h]); return f"回{len(calls)}"
L, m, _ = mk(io, clk, reply_fn=rf_ab, typing=2.0)
L.rc = dict(RC, max_regenerate=0, poll_active_sec=1.0)  # 只许一轮尝试
io.screen = [("them", "连发1")]
orig = clk.sleep
def sleep_ab(d, n=[0]):
    orig(d); n[0] += 1
    if n[0] == 1: io.screen.append(("them", "连发2"))  # 打字等待中又来一条 → 本轮放弃
clk.sleep = sleep_ab; L.sleep = sleep_ab
d0 = L.step()
assert io.sent == [], ("should abandon", io.sent, d0)
assert ("them", "连发1") in m.rows and ("them", "连发2") in m.rows
assert L.pending_unreplied is True
# 下一轮：plan_new 空，但 pending_unreplied/need → 必须回
clk.sleep = orig; L.sleep = orig
d = L.step()
assert io.sent == ["回2"], (io.sent, m.rows, calls, d)
assert m.rows[-1] == ("me", "回2") and L.pending_unreplied is False



# 11) hold_on 后末条是 me：已有 img_plan.paths 仍须 send_prepared_images（勿当「已被回过」）
import brain as _brain
io, clk = FakeIO(), Clock()
io.imgs = []
_orig_send = FakeIO.send_message
def _send_img(s, path):
    s.imgs.append(str(path))
    s.screen.append(("me", f"[img:{path}]"))
FakeIO.send_image = _send_img
tmp_img = pathlib.Path(tempfile.mkdtemp())
p1, p2 = tmp_img / "a.png", tmp_img / "b.png"
p1.write_bytes(b"\x89PNG"); p2.write_bytes(b"\x89PNG")
plan = {"scene": "测试场景", "n_images": 2, "paths": [p1, p2], "texts": ["图1", None], "dry": False}

import chat_media as _cm
real_hold, real_plan, real_send = _cm.hold_on_line, _cm.plan_chat_images, _cm.send_prepared_images
_cm.hold_on_line = lambda *a, **k: "稍等我拍一下～"
_cm.plan_chat_images = lambda scene, caption=None, **kw: plan
def _fake_send(pl, *, io=None, open_chat_first=True, **kw):
    for i, path in enumerate(pl["paths"]):
        line = (pl.get("texts") or [None])[i] if i < len(pl.get("texts") or []) else None
        if line: io.send_message(line)
        io.send_image(path)
    return {"ok": True, "n_images": len(pl["paths"]), "paths": [str(x) for x in pl["paths"]]}
_cm.send_prepared_images = _fake_send
old_last = getattr(_brain, "LAST", None)
_brain.LAST = {"want_image": "测试场景"}
# CFG.imagegen.hold_on 走真实配置；确保开着
old_cfg = D.CFG
D.CFG = {**old_cfg, "imagegen": {**(old_cfg.get("imagegen") or {}), "hold_on_before_generate": True}}
try:
    L, m, _ = mk(io, clk, reply_fn=lambda h: "刚拍的", typing=0.0, think=0.0)
    L.rc = dict(RC, human_typing=False)
    # 关掉 human 延时，避免 wait_watch 路径干扰断言（指纹因 hold_on 会变，但 need=False 不 continue）
    io.screen = [("them", "发张自拍呗")]
    d = L.step()
finally:
    _cm.hold_on_line, _cm.plan_chat_images, _cm.send_prepared_images = real_hold, real_plan, real_send
    _brain.LAST = old_last
    D.CFG = old_cfg
    if hasattr(FakeIO, "send_image"):
        delattr(FakeIO, "send_image")

assert "稍等我拍一下～" in io.sent, io.sent
assert "图1" in io.sent, io.sent
assert [str(p1), str(p2)] == getattr(io, "imgs", []), getattr(io, "imgs", None)
# 关键：末条曾是 me(hold_on) 时没有整轮取消、图已发出
assert L.pending_unreplied is False, (L.pending_unreplied, io.sent, d)
assert any("图片" in (f"{r}") for r in m.rows[-1:]), m.rows[-3:]

print("test_daemon_loop OK")
