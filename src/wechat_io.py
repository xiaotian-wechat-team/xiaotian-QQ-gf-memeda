"""WeChat I/O on iPhone via WebDriverAgent.
Chat page: XCUIElementTypeTable > Cell > Other(label="发送者,文本").
发送者 "我" = 自己；联系人昵称（含 U+20D5 等组合符）= 对方；无逗号的 Cell 是时间/系统提示。"""
import sys, time, re, unicodedata, xml.etree.ElementTree as ET
from config import load
CFG = load()
sys.path.insert(0, CFG["wda_helper_dir"])
import wda_fast as w

def norm(t: str) -> str:
    t = unicodedata.normalize("NFC", t or "")
    t = "".join(c for c in t if unicodedata.category(c) not in ("Mn", "Me", "Cf"))
    return re.sub(r"\s+", "", t)

_s = None
def session():
    global _s
    if _s is None:
        _s = w.connect(CFG["wda_url"]); w.ensure_unlocked(_s)
    return _s

def _src():
    return ET.fromstring(session().source(format="xml"))

def in_chat_with(nick) -> bool:
    root = _src()
    for el in root.iter("XCUIElementTypeTable"):
        for o in el.iter("XCUIElementTypeOther"):
            lab = o.get("label") or ""
            if "," in lab and norm(lab.split(",", 1)[0]) == norm(nick):
                return True
    return False

def open_chat(nick=None):
    nick = nick or CFG["contact"]["nickname"]
    s = session(); s.app_activate(CFG["wechat_bundle"]); time.sleep(1.5)
    if in_chat_with(nick): return True
    for _ in range(4):  # back to main list
        b = s(label="返回")
        if not b.exists: break
        b.click(); time.sleep(0.8)
    s(label="微信").click(); time.sleep(1)
    root = _src()
    for c in root.iter("XCUIElementTypeCell"):
        lab = c.get("label") or c.get("name") or ""
        if norm(lab.split(",", 1)[0]) == norm(nick):
            x = int(c.get("x")) + int(c.get("width")) // 2
            y = int(c.get("y")) + int(c.get("height")) // 2
            s.click(x, y); time.sleep(1.5); return True
    raise RuntimeError(f"chat with {nick} not found in list")

def read_messages(nick=None):
    """Return visible messages: [{'from':'me'|'them'|'system','text':..,'y':..}]"""
    nick = nick or CFG["contact"]["nickname"]
    out = []
    for tbl in _src().iter("XCUIElementTypeTable"):
        for cell in tbl.findall("XCUIElementTypeCell"):
            for o in cell:
                lab = o.get("label") or ""
                if not lab: continue
                if "," in lab:
                    who, text = lab.split(",", 1)
                    if who == "我": frm = "me"
                    elif norm(who) == norm(nick): frm = "them"
                    else: frm = "system"
                else:
                    frm, text = "system", lab
                out.append({"from": frm, "text": text, "y": int(cell.get("y"))})
    return out

def send_message(text):
    s = session()
    tv = s(className="XCUIElementTypeTextView")
    tv.click(); time.sleep(0.5)
    s.send_keys(text + "\n")  # 微信回车即发送
    time.sleep(1)

if __name__ == "__main__":
    open_chat()
    for m in read_messages(): print(m)
