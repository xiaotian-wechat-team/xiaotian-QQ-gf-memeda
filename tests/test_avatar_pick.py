import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import avatar_pick as P
from PIL import Image


def img(tmp_path, name, color, size=64):
    p = tmp_path / name; Image.new("RGB", (size, size), color).save(p); return str(p)


def test_real_person_titles_rejected(tmp_path):
    p = img(tmp_path, "a.png", (250, 200, 210))
    assert "真人" in P.safe_reason("真人自拍头像", p, people=lambda _: 0)
    assert "小姐姐" in P.safe_reason("氛围感小姐姐", p, people=lambda _: 0)
    assert P.safe_reason("可爱小猫头像", p, people=lambda _: 0) is None


def test_brand_ad_publishers_rejected(tmp_path):
    p = img(tmp_path, "a.png", (250, 200, 210))
    assert "公司" in (P.brand_reason("可爱猫", "北京云创讯通科技有限公司") or "")
    assert "旗舰店" in (P.brand_reason("xx旗舰店 头像", "小明") or "")
    assert "广告" in (P.brand_reason("招租广告位", "插画师A") or "")
    assert "代购" in (P.brand_reason("头像", "代购小张") or "")
    assert P.brand_reason("可爱小猫插画", "于卿") is None
    assert "品牌" in (P.safe_reason("可爱猫", p, people=lambda _: 0, author="某旗舰店") or "")


def test_faces_or_no_detector_rejected(tmp_path):
    p = img(tmp_path, "a.png", (250, 200, 210))
    assert "人脸" in P.safe_reason("插画", p, people=lambda _: 1)
    assert "无法" in P.safe_reason("插画", p, people=lambda _: None)


def test_persona_scoring_prefers_sweet_cute():
    assert P.text_score("可爱小猫插画")[0] > P.text_score("暗黑系 酷 头像")[0]


def test_res_score_downranks_tiny_prefers_hires():
    assert P.res_score(120)[0] < 0 and P.res_score(640)[0] > P.res_score(400)[0] > P.res_score(200)[0]


def test_choose_skips_faces_brand_recent_and_prefers_hires(tmp_path):
    faces = {"face.png"}
    people = lambda path: 1 if pathlib.Path(path).name in faces else 0
    c = [dict(path=img(tmp_path, "face.png", (255, 180, 200), 640), title="可爱 甜 少女 插画", px=640),
         dict(path=img(tmp_path, "ad.png", (255, 190, 200), 640), title="可爱小猫", author="某某科技有限公司", px=640),
         dict(path=img(tmp_path, "tiny.png", (255, 190, 200), 64), title="可爱小猫 插画", px=120),
         dict(path=img(tmp_path, "hires.png", (255, 190, 200), 64), title="可爱小猫 插画", px=640),
         dict(path=img(tmp_path, "dog.png", (250, 200, 200), 64), title="小狗 治愈", px=640),
         dict(path=img(tmp_path, "cur.png", (255, 180, 200), 64), title="可爱 兔 甜 插画", px=640)]
    pick, rejected, scored = P.choose(c, people=people, exclude={"dog.png"}, current="x/cur.png")
    assert pathlib.Path(pick["image"]).name == "hires.png"
    assert "无真人脸" in pick["reason"] and "非品牌" in pick["reason"]
    names = " ".join(r[0] + r[1] for r in rejected)
    assert "face" in names and "公司" in names and "最近用过" in names
    # tiny 可进评分但被降权，不应压过高清同文案候选
    tiny_s = next(s for s, _, x in scored if pathlib.Path(x["path"]).name == "tiny.png")
    assert pick["score"] > tiny_s


def test_search_respects_source_poll(monkeypatch, tmp_path):
    import source_poll
    from collectors import runner
    import contextlib
    monkeypatch.setattr(P, "use_main_poll", lambda: tmp_path / "l.lock")
    monkeypatch.setattr(source_poll, "acquire", lambda *a, **k: None)
    monkeypatch.setattr(source_poll, "check", lambda *a, **k: (False, "quiet-hours"))
    monkeypatch.setattr(runner, "global_lock", lambda path=None, wait_s=0: contextlib.nullcontext())
    st, out = P.search_xhs("小猫头像", log=lambda *a: None)
    assert st == "skipped:quiet-hours" and out == []


def test_search_busy_lock(monkeypatch):
    from collectors import runner
    import contextlib
    @contextlib.contextmanager
    def busy(path=None, wait_s=0): raise runner.Busy("x"); yield
    monkeypatch.setattr(P, "use_main_poll", lambda: "x.lock")
    monkeypatch.setattr(runner, "global_lock", busy)
    assert P.search_xhs("x", log=lambda *a: None) == ("skipped:collector-busy", [])


def test_use_main_poll_points_at_main_repo(monkeypatch, tmp_path):
    import source_poll
    main = tmp_path / "xiaotian-wechat"; (main / "data").mkdir(parents=True)
    st, lk = main / "data" / "source_poll_state.json", main / "data" / "collectors.lock"
    st.write_text("{}"); lk.write_text("1")
    monkeypatch.setattr(P, "MAIN", main)
    monkeypatch.setattr(P, "MAIN_STATE", st)
    monkeypatch.setattr(P, "MAIN_LOCK", lk)
    assert P.use_main_poll() == lk
    assert source_poll.STATE == st


def test_pick_without_search_uses_local_pool(monkeypatch, tmp_path):
    d = tmp_path / "cand"; d.mkdir()
    Image.new("RGB", (64, 64), (255, 190, 200)).save(d / "xhs_9.png")
    (d / "sources.json").write_text(json.dumps([dict(file="xhs_9.png", title="可爱小猫", query="小猫头像")]))
    monkeypatch.setattr(P, "CAND", d)
    monkeypatch.setattr(P, "local_pool", lambda: [dict(path=str(d / "xhs_9.png"), title="可爱小猫", origin="local", px=640)])
    p = P.pick(search=False, people=lambda _: 0, log=lambda *a: None, history=tmp_path / "h.json")
    assert p["image"].endswith("xhs_9.png") and p["search_status"] == "not-searched"
