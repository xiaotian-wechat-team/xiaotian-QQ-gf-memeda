"""采集器解析/OCR/锁/串行测试（无网络、不碰 Chrome）。"""
import sys, os, threading, time, multiprocessing, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pytest
from collectors import base, runner
from collectors.xiaohongshu import XHS
from collectors.weibo import Weibo

XHS_RAW = {"items": [{"url": "https://www.xiaohongshu.com/search_result/abc?xsec_token=t", "title": " 北舞编导生的一天 ",
                      "author": "小舞", "likes": "1.2万", "images": ["https://sns-webpic-qc.xhscdn.com/x.webp", ""]}]}
WB_RAW = {"items": [{"url": "https://weibo.com/123/Pabc", "text": "今天 #北京舞蹈学院# 校园开放日 好多人", "author": "某某",
                     "images": ["https://wx1.sinaimg.cn/large/a.jpg"], "video": "blob:xx"},
                    {"url": "https://weibo.com/123/Pabc", "text": "dup"}]}

def test_xhs_parse():
    it = XHS("x").parse(XHS_RAW, "list")[0]
    assert it["title"] == "北舞编导生的一天" and it["author"] == "小舞" and it["images"] == ["https://sns-webpic-qc.xhscdn.com/x.webp"]
    assert it["extra"]["likes"] == "1.2万"

def test_weibo_parse_tags_video_dedupe():
    its = base.dedupe(Weibo("x").parse(WB_RAW, "list"))
    assert len(its) == 1 and "北京舞蹈学院" in its[0]["tags"] and its[0]["video"] is None
    assert its[0]["title"].startswith("今天")

def test_detail_merge():
    c = XHS("x"); items = c.parse(XHS_RAW, "list")
    d = c.parse({"items": [{"url": "u", "text": "长正文 #穿搭#", "tags": ["#校园#"], "comments": ["好看", " "]}]}, "detail")
    c.merge_detail(items, d, "https://www.xiaohongshu.com/search_result/abc?xsec_token=zz")
    assert items[0]["text"] == "长正文 #穿搭#" and items[0]["comments"] == ["好看"] and {"校园", "穿搭"} <= set(items[0]["tags"])

def test_ocr_items_and_match():
    boxes = [{"text": "发现", "x": 10, "y": 10, "w": 30, "h": 10}, {"text": "北舞编导生的", "x": 100, "y": 200, "w": 60, "h": 12},
             {"text": "一天", "x": 165, "y": 201, "w": 20, "h": 12}, {"text": "1.2万", "x": 100, "y": 230, "w": 30, "h": 10}]
    t = base.ocr_items(boxes)
    assert t == ["北舞编导生的 一天"]
    assert base.match_rate(["北舞编导生的一天"], t) == 1.0 and base.match_rate(["完全不同的标题啊"], t) == 0.0

def _hold(path, ev):
    with runner.global_lock(path): ev.set(); time.sleep(1.5)

def test_global_lock_cross_process(tmp_path):
    p = tmp_path / "l.lock"; ev = multiprocessing.Event()
    pr = multiprocessing.Process(target=_hold, args=(str(p), ev)); pr.start(); assert ev.wait(5)
    with pytest.raises(runner.Busy):
        with runner.global_lock(p, wait_s=0): pass
    pr.join()
    with runner.global_lock(p, wait_s=0): pass

def test_run_all_serial_with_gap(tmp_path):
    active, peak, log, gaps = [0], [0], [], []
    def mk(n):
        class C:
            def run(self, force, do_ocr, do_lcu=False):
                active[0] += 1; peak[0] = max(peak[0], active[0]); log.append(n); time.sleep(0.01); active[0] -= 1; return n
        return C
    reg = {n: mk(n) for n in ("xiaohongshu", "douyin", "weibo")}
    r = runner.run_all(sleep=gaps.append, factory=reg, lock_path=tmp_path / "l", gap=(5, 6))
    assert r == log == ["xiaohongshu", "douyin", "weibo"] and peak[0] == 1
    assert len(gaps) == 2 and all(5 <= g <= 6 for g in gaps)

def test_ax_texts():
    ax = "19 HTML 内容 x\n\t22 link 北舞编导生的一天记录, Value: xiaohongshu.com/explore/1\n\t34 文本  | \n\t101 文本 推荐\n\t35 link fe-video-qc.xhscdn.com/a"
    t = base.ax_texts(ax)
    assert t == ["北舞编导生的一天记录"]
    assert base.match_rate(["北舞编导生的一天记录"], t) == 1.0
