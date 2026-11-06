import sys, os, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import material_store as ms


def _f(d, name, data):
    p = pathlib.Path(d) / name; p.write_bytes(data); return p


def test_add_dedupe_refresh_purge_search(tmp_path):
    s = ms.Store(tmp_path / "m")
    a = _f(tmp_path, "a.jpg", b"img-A"); v = _f(tmp_path, "v.mp4", b"vid-V")
    iid = s.add_item("xiaohongshu", "https://x/1", title="秋季穿搭分享", text="燕园校园的银杏很好看", tags=["穿搭", "秋天"], media=[a, v])
    it = s.recent(1)[0]
    assert it["id"] == iid and len(it["media"]) == 2 and {m["kind"] for m in it["media"]} == {"image", "video"}
    for m in it["media"]:
        assert os.path.exists(m["local_path"]) and "/media/xiaohongshu/" in m["local_path"]
    assert a.exists()  # copy by default
    # sha dedupe: same bytes on another item -> no second file/row
    a2 = _f(tmp_path, "a2.jpg", b"img-A")
    s.add_item("weibo", "https://w/2", title="另一条", media=[a2])
    assert s.stats()["media"]["image"]["n"] == 1
    # url dedupe + refresh replaces old version
    old_paths = [m["local_path"] for m in it["media"]]
    b = _f(tmp_path, "b.jpg", b"img-B")
    iid2 = s.add_item("xiaohongshu", "https://x/1", title="秋季穿搭更新版", text="新版本", media=[b])
    assert iid2 == iid and s.stats()["items"] == 2
    new = [x for x in s.recent(5) if x["id"] == iid][0]
    assert new["title"] == "秋季穿搭更新版" and len(new["media"]) == 1
    vid_old = [p for p in old_paths if p.endswith(".mp4")][0]
    assert not os.path.exists(vid_old)
    # FTS
    assert [x["id"] for x in s.search("穿搭更新")] == [iid]
    assert s.search("燕园校园") == []  # old text replaced
    assert s.search("穿搭", platform="weibo") == []
    assert len(s.search("另一条")) == 1
    # expiry
    newpath = new["media"][0]["local_path"]
    r = s.purge_expired(now=time.time() + 31 * 86400)
    assert r["items"] == 2 and s.stats()["items"] == 0 and not os.path.exists(newpath)
    assert s.stats()["media"] == {}


def test_auto_purge_on_add(tmp_path):
    s = ms.Store(tmp_path / "m")
    p = _f(tmp_path, "o.png", b"old")
    s.add_item("douyin", "https://d/old", title="旧素材", media=[p], fetched_at=time.time() - 31 * 86400)
    assert s.stats()["items"] == 0  # already expired, purged immediately
    s.add_item("bilibili", "https://b/new", title="新素材")
    assert s.stats()["by_platform"] == {"bilibili": 1}
    assert s.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_get_by_id_and_url(tmp_path):
    s = ms.Store(tmp_path / "m")
    a = _f(tmp_path, "g.jpg", b"img-G")
    iid = s.add_item("douyin", "https://www.douyin.com/video/9", title="有图", media=[a],
                     extra={"top_comments": ["赞"]})
    got = s.get(iid)
    assert got and got["source_url"].endswith("/9") and got["extra"]["top_comments"] == ["赞"]
    assert s.get_by_url("https://www.douyin.com/video/9")["id"] == iid
    assert s.get(999999) is None
