#!/usr/bin/env python3
"""从已登录 Chrome 当前打开的小红书/抖音/微博页面下载图片/封面/视频，入 material_store。只读：不点赞/评论/关注/发布。
用法（从仓库根目录）:
  /opt/homebrew/bin/python3.13 tools/media_download.py --platform xiaohongshu [--force] [--max 6] [--dry-run] [--lcu-fallback]
流程: 全局串行锁 -> source_poll.acquire -> osascript JS 读当前标签页媒体 URL -> 高清变体 -> 带 UA/Referer 下载
     (不带 cookie，永不打印 cookie) -> sha256 去重 -> material_store.add_item(move=True)，30 天过期由 store 负责。
     blob: 视频无法下载 -> 改存封面(poster)。全部失败且 --lcu-fallback -> LCU 截图(裁剪)作为图片。"""
import argparse, hashlib, json, os, re, sys, tempfile, time, urllib.request, pathlib, base64
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
PLAT = {"xiaohongshu": ("xiaohongshu.com", "https://www.xiaohongshu.com/"),
        "douyin": ("douyin.com", "https://www.douyin.com/"),
        "weibo": ("weibo.com", "https://weibo.com/")}
MAX_BYTES = 40_000_000

# 当前页只读抽取：标题/作者/正文 + 图片 + 视频（blob 时给 poster）
PAGE_JS = r"""(function(){var q=s=>{var x=document.querySelector(s);return x?x.innerText.trim():''};
var bt=document.body.innerText.slice(0,3000);var blocked=/安全验证|滑块|验证码|人机验证|访问异常/.test(bt)?'verify':null;
var sel={xiaohongshu:'.swiper-slide:not(.swiper-slide-duplicate) img, .media-container img, .note-slider img',
 douyin:'[data-e2e="detail-video-info"] img, .swiper-slide img, xg-poster img, img[src*="tos-cn"]',
 weibo:'.woo-picture-main img, .picture img, img[src*="sinaimg"]'}[PLATFORM];
var imgs=[...document.querySelectorAll(sel)].filter(i=>(i.naturalWidth||i.width)>=200).map(i=>{var s=i.currentSrc||i.src;
 if(i.srcset){var c=i.srcset.split(',').map(x=>x.trim().split(/\s+/)).sort((a,b)=>parseFloat(b[1]||0)-parseFloat(a[1]||0));if(c[0])s=c[0][0]}return s});
var bg=[...document.querySelectorAll('xg-poster, .poster, [class*="cover"]')].map(e=>(getComputedStyle(e).backgroundImage.match(/url\("?(.*?)"?\)/)||[])[1]).filter(Boolean);
var v=document.querySelector('video');var vs=v?(v.currentSrc||v.src||''):'';var src2=v?[...v.querySelectorAll('source')].map(s=>s.src):[];
var title=q('#detail-title')||q('[data-e2e="video-desc"]')||q('h1')||document.title;
var author=q('.author-wrapper .username')||q('[data-e2e="video-author"]')||q('.head_name_24eEB')||'';
var text=q('#detail-desc')||q('[class*="detail_wbtext"]')||'';
return JSON.stringify({blocked:blocked,url:location.href,title:title,author:author,text:text,images:imgs,covers:bg,
 video:[vs].concat(src2).filter(Boolean),poster:v?v.poster:''})})()"""

def hires(url, platform):
    """返回候选 URL 列表（高清优先，原图兜底）。不改签名参数（抖音 tos 带签名，原样）。"""
    if not url or not url.startswith("http"): return []
    c = []
    if platform == "xiaohongshu" and "!" in url.rsplit("/", 1)[-1]:
        c.append(url.split("!")[0])                    # 去掉 !nd_dft_wlteh_webp_3 等缩略样式
    if platform == "weibo":
        h = re.sub(r"/(orj\d+|mw\d+|thumb\d+|bmiddle|wap\d+|crop\.[^/]+)/", "/large/", url)
        if h != url: c.append(h)
    c.append(url)
    return list(dict.fromkeys(c))

def collect_urls(raw, platform, max_n=6):
    """raw = PAGE_JS 结果 -> [(kind, [候选url...])]；blob 视频 -> poster 封面。"""
    out, seen = [], set()
    vids = [u for u in raw.get("video") or [] if u.startswith("http")]
    if vids: out.append(("video", vids[:1]))
    elif raw.get("video") or raw.get("poster"):
        if raw.get("poster"): out.append(("cover", hires(raw["poster"], platform)))
    for u in (raw.get("images") or []) + (raw.get("covers") or []):
        cs = hires(u, platform)
        if cs and cs[0] not in seen:
            seen.add(cs[0]); out.append(("image", cs))
    return out[:max_n]

def fetch(url, referer, dest_dir, opener=None):
    """只带 UA + Referer，不带 cookie。返回 (path, sha256) 或 None。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer, "Accept": "*/*"})
    op = opener or urllib.request.urlopen
    with op(req, timeout=25) as r:
        ct = r.headers.get("Content-Type", "") if hasattr(r, "headers") else ""
        data = r.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES or "text/html" in ct: return None
    ext = ".mp4" if "video" in ct or url.split("?")[0].endswith(".mp4") else ".png" if "png" in ct else \
          ".webp" if "webp" in ct or "webp" in url else ".jpg"
    fd, p = tempfile.mkstemp(suffix=ext, dir=dest_dir); os.write(fd, data); os.close(fd)
    return p, hashlib.sha256(data).hexdigest()

def download_all(cands, referer, dest_dir, opener=None, log=print):
    media, shas = [], set()
    for kind, urls in cands:
        for u in urls:
            try: got = fetch(u, referer, dest_dir, opener)
            except Exception as e: log("fail %s: %s" % (kind, str(e)[:80])); got = None
            if not got: continue
            p, sha = got
            if sha in shas: os.unlink(p)                 # 本次内 sha256 去重（库内去重由 store 再做一次）
            else: shas.add(sha); media.append({"path": p, "source_url": u, "kind": "video" if kind == "video" else "image"})
            break
    return media

def lcu_screenshot(dest_dir, crop=None):
    """兜底：LCU 截 Chrome 窗口（需 lcu apps allow "Google Chrome"），可选裁剪 (x,y,w,h)。"""
    sys.path.insert(0, str(ROOT / "tools")); from lcu_client import LCU
    c = LCU()
    try:
        c.js('let app = await cua.getApp("Google Chrome");', timeout=60)
        r = c.js('const s = await app.getScreenshot(); await nodeRepl.emitImage(Array.isArray(s) ? s[0].bytes : s)', timeout=60)
    finally: c.close()
    img = next((x for x in r.get("result", {}).get("content", []) if x.get("type") == "image"), None)
    if not img: return None
    fd, p = tempfile.mkstemp(suffix=".png", dir=dest_dir); os.write(fd, base64.b64decode(img["data"])); os.close(fd)
    if crop:
        try:
            from PIL import Image
            Image.open(p).crop((crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3])).save(p)
        except Exception: pass
    return p

class _SessionGate:
    """流水线内复用：同一平台采集会话刚通过 source_poll 且已计数，这里只读当前页一次，不再额外计数。"""
    pages = 0
    def page(self): self.pages += 1
    def done(self, **k): pass

def run(platform, force=False, max_n=6, dry=False, lcu_fallback=False, store=None, log=print, in_session=False):
    """in_session=True 仅供 collectors.pipeline：调用方已持全局锁、本平台采集刚经 source_poll 放行。"""
    import source_poll, contextlib
    from collectors import chrome
    from collectors.runner import global_lock
    domain, referer = PLAT[platform]
    with (contextlib.nullcontext() if in_session else global_lock(wait_s=0)):
        gate = _SessionGate() if in_session else source_poll.acquire(platform, force=force)
        if not gate: return {"status": "skipped:" + source_poll.check(platform, force=force)[1]}
        try:
            tab = chrome.find_tab(domain)
            if not tab: gate.done(error=True); return {"status": "error:no-tab"}
            gate.page()
            raw = chrome.js_json(tab, PAGE_JS.replace("PLATFORM", json.dumps(platform))) or {}
            if raw.get("blocked"): gate.done(captcha=True); return {"status": "captcha"}
            cands = collect_urls(raw, platform, max_n)
            if dry: gate.done(ok=True); return {"status": "dry", "url": raw.get("url"), "candidates": cands}
            tmp = ROOT / "data" / "dl_tmp"; tmp.mkdir(parents=True, exist_ok=True)
            media = download_all(cands, referer, str(tmp), log=log)
            if not media and lcu_fallback:
                p = lcu_screenshot(str(tmp))
                if p: media = [{"path": p, "kind": "image", "source_url": None}]
            import material_store
            st = store or material_store._s()
            iid = st.add_item(platform, raw.get("url"), author=raw.get("author") or None, title=(raw.get("title") or "")[:200],
                              text=(raw.get("text") or "")[:4000], extra={"via": "media_download"}, media=media, move=True)
            gate.done(ok=bool(media))
            return {"status": "ok", "item": iid, "media": len(media), "url": raw.get("url")}
        except Exception as e:
            gate.done(error=True); return {"status": "error:%s" % str(e)[:200]}

if __name__ == "__main__":
    a = argparse.ArgumentParser(); a.add_argument("--platform", required=True, choices=list(PLAT))
    a.add_argument("--force", action="store_true"); a.add_argument("--max", type=int, default=6)
    a.add_argument("--dry-run", action="store_true"); a.add_argument("--lcu-fallback", action="store_true")
    o = a.parse_args()
    print(json.dumps(run(o.platform, o.force, o.max, o.dry_run, o.lcu_fallback), ensure_ascii=False, indent=1))
