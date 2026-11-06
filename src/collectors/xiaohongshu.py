"""小红书：搜索页列表 + 打开前 2 篇笔记详情（正文/标签/热评）。只读。"""
import random, urllib.parse
from collectors.base import Collector
from collectors.topics import choose_topic, QUERIES

TOPICS = [q for queries in QUERIES.values() for q in queries]
BLOCK = r"""var bt=document.body.innerText.slice(0,3000);var blocked=/安全验证|滑块|验证码|人机验证|访问异常/.test(bt)?'verify':([...document.querySelectorAll('.login-container,.login-modal')].some(e=>e.offsetWidth>200)?'login-wall':null);"""
LIST_JS = r"""(function(){""" + BLOCK + r"""
var items=[...document.querySelectorAll('section.note-item')].map(function(e){var a=e.querySelector('a.cover');var q=s=>{var x=e.querySelector(s);return x?x.innerText:''};var im=e.querySelector('a.cover img');
var R=e.getBoundingClientRect();return {vis:R.top>=0&&R.bottom<=innerHeight,url:a?a.href:'',title:q('.title'),author:q('.author .name')||q('.name'),likes:q('.like-wrapper .count')||q('.count'),images:im?[im.src]:[]}}).filter(i=>i.url&&i.url.indexOf('/explore/')>0||i.url.indexOf('/search_result/')>0);
return JSON.stringify({blocked:blocked,items:items,want_scroll:1})})()"""
DETAIL_JS = r"""(function(){""" + BLOCK + r"""
var q=s=>{var x=document.querySelector(s);return x?x.innerText:''};
var tags=[...document.querySelectorAll('#detail-desc a.tag, .note-text a.tag')].map(a=>a.innerText);
var cm=[...document.querySelectorAll('.comment-item .content .note-text, .parent-comment .note-text')].slice(0,5).map(e=>e.innerText);
var imgs=[...document.querySelectorAll('.swiper-slide:not(.swiper-slide-duplicate) img, .media-container img')].map(i=>i.src).filter((v,i,a)=>a.indexOf(v)==i);
var v=document.querySelector('.player-container video, video');var vs=v?(v.currentSrc||v.src||''):'';
return JSON.stringify({blocked:blocked,items:[{url:location.href,title:q('#detail-title'),text:q('#detail-desc'),author:q('.author-wrapper .username')||q('.author .name'),tags:tags,comments:cm,images:imgs,video:vs}]})})()"""

class XHS(Collector):
    platform, domain, referer = "xiaohongshu", "xiaohongshu.com", "https://www.xiaohongshu.com/"
    def __init__(self, topic=None): self.topic = topic or choose_topic()
    def plan(self, items):
        yield ("https://www.xiaohongshu.com/search_result?keyword=%s&source=web_search_result_notes" % urllib.parse.quote(self.topic), LIST_JS, "list")
        for it in list(items)[:2]:
            yield (it["url"], DETAIL_JS, "detail")
