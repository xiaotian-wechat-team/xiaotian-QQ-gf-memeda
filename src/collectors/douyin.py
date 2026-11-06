"""抖音：视频搜索结果列表 + 打开 1 个视频页读描述/热评。视频多为 blob（无直链）→ 存封面。只读。"""
import random, urllib.parse
from collectors.base import Collector
from collectors.topics import choose_topic, QUERIES

TOPICS = [q for queries in QUERIES.values() for q in queries]
BLOCK = r"""var bt=document.body.innerText.slice(0,3000);var blocked=(/安全验证|滑块|验证码|人机验证|访问异常/.test(bt)||document.querySelector('#captcha_container,.captcha_verify_container'))?'verify':([...document.querySelectorAll('[id^=login-full-panel],.login-mask')].some(e=>e.offsetWidth>200&&e.offsetHeight>200&&/登录/.test(e.innerText))?'login-wall':null);"""
LIST_JS = r"""(function(){""" + BLOCK + r"""
var seen={};var items=[];
document.querySelectorAll('a[href*="/video/"],a[href*="/note/"]').forEach(function(a){var u=a.href.split('?')[0];if(seen[u])return;seen[u]=1;
var card=a.closest('li')||a.parentElement;var t=(card.innerText||a.innerText||'').split('\n').filter(s=>s.trim());
var im=card.querySelector('img[src^=http]');var au=card.querySelector('[class*=author],[class*=nickname]');
var title=t.filter(s=>s.length>6&&!/^[\d.:万]+$/.test(s)).sort((a,b)=>b.length-a.length)[0]||'';
var R=card.getBoundingClientRect();items.push({vis:R.top>=0&&R.bottom<=innerHeight,url:u,title:title,text:title,author:au?au.innerText.replace(/^@/,''):'',likes:(t.find(s=>/^[\d.]+万?$/.test(s))||''),images:im?[im.src]:[]})});
return JSON.stringify({blocked:blocked,items:items.filter(i=>i.title),want_scroll:1})})()"""
DETAIL_JS = r"""(function(){""" + BLOCK + r"""
var q=s=>{var x=document.querySelector(s);return x?x.innerText:''};
var cm=[...document.querySelectorAll('[data-e2e=comment-item] [class*=content], [data-e2e=comment-item] span')].map(e=>e.innerText).filter(s=>s&&s.length>3).slice(0,5);
var v=document.querySelector('video');var vs=v?(v.currentSrc||''):'';var src=v?[...v.querySelectorAll('source')].map(s=>s.src).find(s=>s.startsWith('http')):'';
var poster=v&&v.poster?[v.poster]:[];
return JSON.stringify({blocked:blocked,items:[{url:location.href.split('?')[0],text:q('[data-e2e=video-desc]')||q('h1'),author:q('[data-e2e=video-author-title]')||q('[data-e2e=user-info] a'),comments:cm,images:poster,video:(vs.startsWith('http')?vs:(src||''))}]})})()"""

class Douyin(Collector):
    platform, domain, referer = "douyin", "douyin.com", "https://www.douyin.com/"
    def __init__(self, topic=None): self.topic = topic or choose_topic()
    def plan(self, items):
        yield ("https://www.douyin.com/search/%s?type=video" % urllib.parse.quote(self.topic), LIST_JS, "list")
        for it in list(items)[:1]:
            yield (it["url"], DETAIL_JS, "detail")
