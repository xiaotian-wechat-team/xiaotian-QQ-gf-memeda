"""微博：热搜榜（取 Top 若干）+ 用第一个合适热搜/校园舞蹈话题做一次搜索读微博正文/图片。只读。"""
import random, urllib.parse
from collectors.base import Collector

TOPICS = ["北京舞蹈学院", "北舞", "舞蹈生", "大学生穿搭"]
BLOCK = r"""var bt=document.body.innerText.slice(0,3000);var blocked=/安全验证|滑块|验证码|人机验证|访问异常/.test(bt)?'verify':(/passport\.weibo|login\.sina/.test(location.href)?'login-wall':null);"""
HOT_JS = r"""(function(){""" + BLOCK + r"""
var items=[...document.querySelectorAll('#pl_top_realtimehot tbody tr')].map(function(tr){var a=tr.querySelector('td.td-02 a');var r=tr.querySelector('td.td-01');var h=tr.querySelector('td.td-02 span');
return a?{url:a.href,title:a.innerText,text:a.innerText,rank:r?r.innerText:'',heat:h?h.innerText:'',tags:['热搜']}:null}).filter(i=>i&&/^\d+$/.test(i.rank)).slice(0,6);
return JSON.stringify({blocked:blocked,items:items})})()"""
SEARCH_JS = r"""(function(){""" + BLOCK + r"""
var items=[...document.querySelectorAll('#pl_feedlist_index .card-wrap[mid]')].map(function(c){var q=s=>{var x=c.querySelector(s);return x?x.innerText:''};
var full=c.querySelector('p.txt[node-type=feed_list_content_full]');var t=c.querySelector('a[href*="weibo.com/"][href*="?refer"], .from a');
var imgs=[...c.querySelectorAll('.media img, .m3 img')].map(i=>i.src.replace('/orj360/','/large/').replace('/thumb150/','/large/'));
var pv=c.querySelector('video');
var R=c.getBoundingClientRect();return {vis:R.top>=0&&R.bottom<=innerHeight,url:t?t.href.split('?')[0]:('https://weibo.com/detail/'+c.getAttribute('mid')),text:(full||c.querySelector('p.txt')||{innerText:''}).innerText,author:q('a.name'),images:imgs,likes:q('.card-act li:last-child'),video:pv&&pv.src&&pv.src.startsWith('http')?pv.src:''}}).filter(i=>i.text);
return JSON.stringify({blocked:blocked,items:items,want_scroll:1})})()"""

class Weibo(Collector):
    platform, domain, referer = "weibo", "weibo.com", "https://weibo.com/"
    def __init__(self, topic=None): self.topic = topic or random.choice(TOPICS)
    def plan(self, items):
        yield ("https://s.weibo.com/top/summary?cate=realtimehot", HOT_JS, "hot")
        yield ("https://s.weibo.com/weibo?q=%s" % urllib.parse.quote(self.topic), SEARCH_JS, "list")
