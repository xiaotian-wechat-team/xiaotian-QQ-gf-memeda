"""media_download：高清变体/候选/下载去重/入库（mock HTTP，不碰 Chrome/网络）。"""
import sys, os, io, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import media_download as md

class Resp(io.BytesIO):
    def __init__(self, data, ct): super().__init__(data); self.headers = {"Content-Type": ct}
    def __enter__(self): return self
    def __exit__(self, *a): pass

def opener_for(table, seen):
    def op(req, timeout=0):
        seen.append(req)
        if req.full_url not in table: raise OSError("404")
        return Resp(*table[req.full_url])
    return op

def test_hires():
    assert md.hires("https://sns-webpic-qc.xhscdn.com/a/b!nd_dft_wlteh_webp_3", "xiaohongshu")[0] == "https://sns-webpic-qc.xhscdn.com/a/b"
    assert md.hires("https://wx1.sinaimg.cn/orj360/x.jpg", "weibo") == ["https://wx1.sinaimg.cn/large/x.jpg", "https://wx1.sinaimg.cn/orj360/x.jpg"]
    u = "https://p3.douyinpic.com/tos-cn-i/x~tplv-dy.jpeg?x-expires=1&sign=a"
    assert md.hires(u, "douyin") == [u]
    assert md.hires("blob:https://x", "douyin") == []

def test_blob_video_to_cover():
    c = md.collect_urls({"video": ["blob:https://www.douyin.com/1"], "poster": "https://p3.douyinpic.com/c.jpg", "images": []}, "douyin")
    assert c == [("cover", ["https://p3.douyinpic.com/c.jpg"])]
    c = md.collect_urls({"video": ["https://v.douyin.com/v.mp4"], "images": ["https://a/1.jpg", "https://a/1.jpg"]}, "douyin")
    assert [k for k, _ in c] == ["video", "image"]

def test_download_headers_fallback_and_dedupe(tmp_path):
    seen = []
    table = {"https://wx1.sinaimg.cn/orj360/x.jpg": (b"IMG1", "image/jpeg"),   # large 404 -> 兜底原图
             "https://wx1.sinaimg.cn/large/y.jpg": (b"IMG1", "image/jpeg"),    # 同内容 -> sha 去重
             "https://wx1.sinaimg.cn/large/z.jpg": (b"<html>", "text/html")}  # 登录页 -> 丢弃
    cands = md.collect_urls({"images": ["https://wx1.sinaimg.cn/orj360/x.jpg", "https://wx1.sinaimg.cn/large/y.jpg",
                                        "https://wx1.sinaimg.cn/large/z.jpg"]}, "weibo")
    media = md.download_all(cands, "https://weibo.com/", str(tmp_path), opener=opener_for(table, seen), log=lambda *a: None)
    assert len(media) == 1 and media[0]["source_url"].endswith("orj360/x.jpg")
    assert len(os.listdir(tmp_path)) == 1
    h = dict(seen[0].header_items())
    assert h["Referer"] == "https://weibo.com/" and "Chrome" in h["User-agent"] and "Cookie" not in h

def test_store_roundtrip(tmp_path):
    import material_store
    st = material_store.Store(tmp_path / "store")
    p = tmp_path / "a.jpg"; p.write_bytes(b"\xff\xd8x")
    iid = st.add_item("weibo", "https://weibo.com/1/A", title="t", media=[{"path": str(p), "kind": "image"}], move=True)
    iid2 = st.add_item("weibo", "https://weibo.com/1/B", title="t2", media=[{"path": str(tmp_path / "b.jpg")}] if (tmp_path / "b.jpg").write_bytes(b"\xff\xd8x") else [], move=True)
    assert st.db.execute("SELECT count(*) FROM media").fetchone()[0] == 1 and iid != iid2
