"""在用户现有 Chrome 配置里只读操作标签页：osascript 执行 JS、截图+OCR、前台恢复。
绝不读取/打印 cookie；不输入、不点击互动按钮。"""
import json, subprocess, time, tempfile, os

_RUN = '''on run argv
tell application "Google Chrome" to execute tab (item 2 of argv as integer) of window (item 1 of argv as integer) javascript (item 3 of argv)
end run'''

def osa(script, *args, timeout=40):
    r = subprocess.run(["osascript", "-e", script, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout.strip()

def tabs():
    out = osa('''set o to ""
tell application "Google Chrome"
 repeat with w from 1 to count of windows
  repeat with t from 1 to count of tabs of window w
   set o to o & w & "\t" & t & "\t" & (URL of tab t of window w) & linefeed
  end repeat
 end repeat
end tell
return o''')
    res = []
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) == 3: res.append((int(p[0]), int(p[1]), p[2]))
    return res

def find_tab(domain):
    for w, t, u in tabs():
        if domain in u: return w, t
    return None

def js(tab, code):
    """tab=(win,idx)。code 应返回字符串（建议 JSON.stringify）。"""
    return osa(_RUN, tab[0], tab[1], code)

def js_json(tab, code):
    s = js(tab, code)
    try: return json.loads(s)
    except Exception: return None

def goto(tab, url):
    osa('on run argv\ntell application "Google Chrome" to set URL of tab (item 2 of argv as integer) of window (item 1 of argv as integer) to (item 3 of argv)\nend run', tab[0], tab[1], url)

def wait_ready(tab, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if js(tab, "document.readyState") == "complete": return True
        except Exception: pass
        time.sleep(1)
    return False

def frontmost():
    return osa('tell application "System Events" to get bundle identifier of first process whose frontmost is true')

def restore(bundle):
    if bundle and bundle != frontmost():
        osa('on run argv\ntell application id (item 1 of argv) to activate\nend run', bundle)

def show_tab(tab):
    """OCR 路径需要标签页可见：切到该标签页并把 Chrome 提到前台（抢焦点）。"""
    osa('on run argv\ntell application "Google Chrome"\nset active tab index of window (item 1 of argv as integer) to (item 2 of argv as integer)\nset index of window (item 1 of argv as integer) to 1\nactivate\nend tell\nend run', tab[0], tab[1])

def window_id():
    import Quartz
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerName") == "Google Chrome" and w.get("kCGWindowLayer") == 0 and w["kCGWindowBounds"]["Height"] > 300:
            return w["kCGWindowNumber"]

def capture(path=None):
    path = path or tempfile.mktemp(suffix=".png", dir=os.path.expanduser("~/xiaotian-wechat/data"))
    wid = window_id()
    subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), path], check=True)
    return path

def ocr(path):
    """复用 mac_wechat_io 的 Apple Vision OCR（只读调用）。"""
    import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    import mac_wechat_io
    return mac_wechat_io.ocr(path)

LOGIN_JS = r'''(function(){var t=document.body?document.body.innerText:"";var h=t.slice(0,4000);
return JSON.stringify({url:location.href,title:document.title,
 loginWords:(h.match(/登录|立即登录|扫码登录|Log in/g)||[]).length,
 captcha:/验证码|安全验证|滑块|人机验证|访问异常/.test(h),
 hints:(h.match(/我|个人主页|消息|通知|关注|私信/g)||[]).length,
 avatars:document.querySelectorAll('img[class*=avatar],[class*=avatar] img,[class*=Avatar] img').length})})()'''
