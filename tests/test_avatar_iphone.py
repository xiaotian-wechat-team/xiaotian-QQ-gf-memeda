import sys, pathlib, base64, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import avatar_iphone as A
import pytest
from PIL import Image

R = types.SimpleNamespace
def b(x, y, w=10, h=10): return R(x=x, y=y, width=w, height=h)
RED, BLUE = (220, 30, 30), (30, 30, 220)


class El:
    def __init__(self, s, kw): self.s, self.kw = s, kw
    def _m(self): return self.s.find(self.kw)
    @property
    def exists(self): return bool(self._m())
    def tap(self):
        self.s.log.append(("tap", self.kw.get("label")))
        if self.kw.get("label") in self.s.vanish: self.s.els = [e for e in self.s.els if e.get("label") != self.kw["label"]]
    @property
    def bounds(self): return self._m()[0]["b"]
    def find_elements(self): return [R(label=e.get("label"), bounds=e["b"]) for e in self._m()]


class S:
    """静态界面的假 WDA 会话：截图是 414×736 白底，可在指定区域画色块。"""
    def __init__(self, els, paint=()): self.els, self.log, self.paint, self.vanish = els, [], list(paint), {"完成"}
    def __call__(self, **kw): return El(self, kw)
    def find(self, kw):
        kw = {k: v for k, v in kw.items() if v is not None}
        if "labelContains" in kw:
            return [e for e in self.els if kw["labelContains"] in (e.get("label") or "")]
        return [e for e in self.els if all(e.get(k) == v for k, v in kw.items())]
    def window_size(self): return (414, 736)
    def screenshot(self):
        im = Image.new("RGB", (414, 736), "white")
        for r, c in self.paint: im.paste(c, (r.x, r.y, r.x + r.width, r.y + r.height))
        return im
    def click(self, x, y): self.log.append(("click", x, y))
    def app_activate(self, b): self.log.append(("app", b))
    def send_keys(self, t): self.log.append(("keys", t))
    def tap_hold(self, *a): self.log.append(("hold",) + a)


def clock():
    t = [0.0]
    def c(): t[0] += 0.5; return t[0]
    return c


THUMB, NEW = b(346, 77, 32, 32), b(208, 66, 101, 101)
ME = b(100 + A.ME_AVATAR[0], 120 + A.ME_AVATAR[1], 64, 64)   # 「我」页头像（相对 ID 文本）

def wechat_els(acct="demo_xiaotian_wxid"):
    E = [dict(type=A.B, label=l, b=b(0, 0)) for l in ("我", "更多", "从手机相册选择", "完成")]
    E += [dict(label=f"WeChat ID: {acct}", b=b(100, 120, 200, 20)), dict(type=A.C, label="头像", b=b(0, 65, 414, 56)),
          dict(type=A.I, label="头像", b=THUMB)]
    E += [dict(type=A.I, label=f"照片{i},拍摄时间,x", b=b(2 + 103 * i, 66, 101, 101)) for i in range(3)]
    return E

def runner(s, tmp_path): return A.Runner(s, tmp_path, sleep=lambda *_: None, post=lambda *a: {}, clock=clock(), log=lambda *a: None)

def target(tmp_path, color):
    p = tmp_path / "t.png"; Image.new("RGB", (64, 64), color).save(p); return p


def test_already_applied_is_idempotent(tmp_path):
    s = S(wechat_els(), paint=[(ME, RED)])
    res = A.run(s, target(tmp_path, RED), done=tmp_path / "done.png", shot_dir=tmp_path,
                sleep=lambda *_: None, post=lambda *a: {}, clock=clock(), log=lambda *a: None)
    assert res["status"] == "already" and (tmp_path / "done.png").exists()
    assert ("tap", "更多") not in s.log and not any(x[0] == "keys" for x in s.log)


def test_wrong_account_stops(tmp_path):
    s = S(wechat_els("someone_else")); r = runner(s, tmp_path)
    with pytest.raises(A.AvatarError): r.me_tab()
    assert ("tap", "头像") not in s.log


def test_change_picks_newest_after_compare(tmp_path):
    s = S(wechat_els(), paint=[(NEW, RED)]); r = runner(s, tmp_path)
    s.els.append(dict(type=A.B, label="返回", b=b(0, 0)))
    r.change(Image.new("RGB", (64, 64), RED))
    assert ("click", int(208 + 50.5), int(66 + 50.5 + 5)) in s.log
    assert ("tap", "完成") in s.log
    assert not any("朋友圈" in str(x) or x[0] == "keys" for x in s.log)


def test_change_refuses_wrong_photo(tmp_path):
    s = S(wechat_els(), paint=[(NEW, BLUE)]); r = runner(s, tmp_path)
    with pytest.raises(A.AvatarError, match="不是目标图"): r.change(Image.new("RGB", (64, 64), RED))
    assert ("tap", "完成") not in s.log


def test_set_pasteboard_activates_wda_first_and_retries():
    calls, n = [], [0]
    def post(p, body):
        calls.append(p)
        if p.endswith("getPasteboard"):
            n[0] += 1; return {"value": "eA==" if n[0] > 1 else ""}
        return {}
    A.set_pasteboard("data:x", post=post, sleep=lambda *_: None)
    assert calls[:3] == ["/wda/apps/activate", "/wda/setPasteboard", "/wda/getPasteboard"] and len(calls) == 6


def test_set_pasteboard_empty_raises():
    with pytest.raises(A.AvatarError):
        A.set_pasteboard("x", post=lambda p, b: {"value": ""}, sleep=lambda *_: None)


def test_wait_until_times_out():
    assert A.wait_until(lambda: False, 2, sleep=lambda *_: None, clock=clock()) is None
    assert A.wait_until(lambda: 5, 2, sleep=lambda *_: None, clock=clock()) == 5


def test_img_diff():
    a, c = Image.new("RGB", (40, 40), RED), Image.new("RGB", (80, 60), RED)
    assert A.img_diff(a, c) < 1 and A.img_diff(a, Image.new("RGB", (40, 40), BLUE)) > 100


def test_data_url(tmp_path):
    p = tmp_path / "a.png"; Image.new("RGB", (8, 8)).save(p)
    u = A.data_url(p); assert u.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(u.split(",", 1)[1])[:2] == b"\xff\xd8"


def test_main_requires_yes():
    assert A.main([]) == 2


def test_already_checked_on_me_tab_without_opening_profile(tmp_path):
    s = S(wechat_els(), paint=[(ME, RED)])
    res = A.run(s, target(tmp_path, RED), done=tmp_path / "d.png", shot_dir=tmp_path,
                sleep=lambda *_: None, post=lambda *a: {}, clock=clock(), log=lambda *a: None)
    assert res["status"] == "already" and not any(x[0] == "click" for x in s.log)   # 没点昵称区进个人资料


def test_picker_uses_last_element_as_newest(tmp_path):
    s = S(wechat_els(), paint=[(b(2 + 103 * 2, 66, 101, 101), RED)]); r = runner(s, tmp_path)
    s.els.append(dict(type=A.B, label="返回", b=b(0, 0)))
    r.change(Image.new("RGB", (64, 64), RED))
    assert ("click", int(208 + 50.5), int(66 + 50.5 + 5)) in s.log


def test_data_url_is_small(tmp_path):
    import random
    p = tmp_path / "n.png"; im = Image.new("RGB", (640, 640))
    im.putdata([tuple(random.randrange(256) for _ in range(3)) for _ in range(640 * 640)]); im.save(p)
    assert len(A.data_url(p)) < len(A.data_url(p, size=640, quality=90))


def test_rate_limit_14_days(tmp_path):
    import datetime as dt
    h = tmp_path / "h.json"; now = dt.datetime(2026, 10, 24, 12)
    assert A.rate_limited(now, h) is None
    A.append_history(dict(ts="2026-10-20T10:00:00", status="changed", image="a.png"), h)
    assert A.rate_limited(now, h).startswith("2026-11-03")
    assert A.rate_limited(dt.datetime(2026, 11, 4), h) is None
    A.append_history(dict(ts="2026-10-23T10:00:00", status="changed", image="b.png", test=True), h)
    assert A.rate_limited(dt.datetime(2026, 11, 4), h) is None   # 测速运行不计


def _fake_run(calls):
    def run(s, image, *a, **k):
        calls.append(image); return dict(status="changed", done="d", total=30.0, timings=[], shots=[])
    return run


def test_main_auto_records_reason_and_rate_limits(tmp_path, monkeypatch):
    import datetime as dt
    h = tmp_path / "h.json"; calls = []
    monkeypatch.setattr(A, "run", _fake_run(calls))
    picker = lambda force=False: dict(image="data/avatar/candidates/xhs_5.png", reason="猫+3 …", score=10.4)
    now = dt.datetime(2026, 10, 24, 12)
    assert A.main(["--auto"], connect=lambda: None, picker=picker, now=now, history=h) == 0
    e = A.load_history(h)[-1]
    assert calls == ["data/avatar/candidates/xhs_5.png"] and e["reason"] == "猫+3 …" and e["auto"] and e["status"] == "changed"
    assert A.main(["--auto"], connect=lambda: None, picker=picker, now=now + dt.timedelta(days=3), history=h) == 3
    assert A.main(["--auto", "--force"], connect=lambda: None, picker=picker, now=now + dt.timedelta(days=3), history=h) == 0
    assert len(calls) == 2


def test_main_auto_no_pick(tmp_path, monkeypatch):
    calls = []; monkeypatch.setattr(A, "run", _fake_run(calls))
    assert A.main(["--auto"], connect=lambda: None, picker=lambda force=False: None, history=tmp_path / "h.json") == 4
    assert not calls
