"""自己相册选图 + QQ 头像 album 限流豁免；album 放行人像，xhs 仍拒真人。"""
import datetime as dt
import json
import pathlib
import sys

import pytest
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import avatar_pick as P
import avatar_qq as Q


def _img(d, name, color=(240, 200, 210), size=80):
    p = d / name
    Image.new("RGB", (size, size), color).save(p)
    return p


def test_album_pool_recursive(tmp_path):
    raw = tmp_path / "raw"
    (raw / "sub").mkdir(parents=True)
    _img(raw, "a.jpg")
    _img(raw / "sub", "b.png")
    (raw / "note.txt").write_text("x")
    pool = P.album_pool(raw)
    names = {pathlib.Path(c["path"]).name for c in pool}
    assert names == {"a.jpg", "b.png"}
    assert all(c["origin"] == "album" for c in pool)


def test_pick_album_allows_faces(tmp_path, monkeypatch):
    """album 人像包：Vision 检出人脸仍应选出候选（不再 album-no-safe）。"""
    raw = tmp_path / "raw"
    raw.mkdir()
    face = _img(raw, "face0.jpg", (250, 180, 190), 120)
    hist = tmp_path / "h.json"
    hist.write_text("[]")
    monkeypatch.setattr(P, "ALBUM_CACHE", tmp_path / "cache")
    p = P.pick_album(history=hist, album_dir=raw, log=lambda *a: None, people=lambda _: 1)
    assert p and p.get("image") and p["source"] == "album"
    assert pathlib.Path(p["source_image"]).name == "face0.jpg"
    assert p.get("search_status") == "album"
    assert "允许人像" in p["reason"]
    assert P.album_face_reason("任意", face, people=lambda _: 9) is None


def test_pick_album_allows_title_real_words(tmp_path, monkeypatch):
    """album：标题含真人类词也不挡。"""
    raw = tmp_path / "raw"
    raw.mkdir()
    _img(raw, "真人自拍好看.jpg")
    hist = tmp_path / "h.json"
    hist.write_text("[]")
    monkeypatch.setattr(P, "ALBUM_CACHE", tmp_path / "cache")
    p = P.pick_album(history=hist, album_dir=raw, log=lambda *a: None, people=lambda _: 2)
    assert p and p.get("image")
    assert pathlib.Path(p["source_image"]).name == "真人自拍好看.jpg"
    assert p.get("search_status") == "album"


def test_xhs_safe_reason_still_rejects_real_face(tmp_path):
    """小红书/本地池路径：safe_reason 仍禁标题真人词与 Vision 人脸。"""
    p = _img(tmp_path, "illust.png")
    assert "真人" in P.safe_reason("真人自拍头像", p, people=lambda _: 0)
    assert "人脸" in P.safe_reason("插画", p, people=lambda _: 1)
    assert "无法" in P.safe_reason("插画", p, people=lambda _: None)
    assert P.safe_reason("可爱小猫头像", p, people=lambda _: 0) is None


def test_pick_album_excludes_recent_source_image(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    a = _img(raw, "only_a.jpg")
    b = _img(raw, "only_b.jpg")
    hist = tmp_path / "h.json"
    hist.write_text(json.dumps([
        {"status": "changed", "image": "new_avatar.png", "source_image": str(a), "test": False}
    ]))
    monkeypatch.setattr(P, "ALBUM_CACHE", tmp_path / "cache")
    people = lambda _: 0
    picks = {pathlib.Path(P.pick_album(history=hist, album_dir=raw, log=lambda *a: None, people=people)["source_image"]).name
             for _ in range(8)}
    assert picks == {"only_b.jpg"}


def test_rate_limited_album_ignores_xhs_history(tmp_path):
    h = tmp_path / "h.json"
    now = dt.datetime(2026, 10, 25, 1, 0)
    h.write_text(json.dumps([
        {"ts": "2026-10-25T01:00:00", "status": "changed", "channel": "qq", "test": False},
    ]))
    assert Q.rate_limited(now, h, days=14, for_source="xhs") is not None
    assert Q.rate_limited(now, h, days=14, for_source="album") is None
    assert Q.rate_limited(now, h, days=0, for_source="album") is None


def test_rate_limited_album_respects_own_days(tmp_path):
    h = tmp_path / "h.json"
    now = dt.datetime(2026, 10, 25, 1, 0)
    h.write_text(json.dumps([
        {"ts": "2026-10-24T01:00:00", "status": "changed", "channel": "qq", "source": "album", "test": False},
    ]))
    assert Q.rate_limited(now, h, days=3, for_source="album") is not None
    assert Q.rate_limited(now, h, days=0, for_source="album") is None
