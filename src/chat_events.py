"""聊天框内系统项 → 用户侧输入（只作用于已核对为 demo_contact 的聊天面板内，不处理全局通知/横幅/标题栏/其他聊天）。
- 时间戳（HH:MM、昨天 HH:MM、星期X HH:MM、MM月DD日 HH:MM、yyyy年M月D日 HH:MM，可带 上午/下午/晚上…）不是消息，
  而是元数据：解析成 datetime，挂到它之后、下一个时间戳之前的每条 me/them 消息的 sent_at 上。单独的时间戳不产生消息，不触发回复。
- 撤回 / 拍一拍 / 加好友提示 / 打招呼提示等面板内系统提示 → 一条消息 text=「[系统提示：…]」、type=notice：
  他那侧（对方撤回、他拍了拍我、打招呼提示…）from=them，可像普通消息一样触发回复；
  小甜自己引起的（你撤回了…、我拍了拍…、你已添加了…）from=me，只作上下文，不触发。
去重靠 daemon 现有的「屏幕 vs 库尾」对齐：提示作为普通行入库，同一条不会每轮重喂；时间戳不入库，只随消息的 sent_at 存。"""
import re, datetime

PERIODS = {"凌晨": 0, "早上": 0, "早晨": 0, "上午": 0, "中午": 12, "下午": 12, "傍晚": 12, "晚上": 12, "夜里": 12, "半夜": 0}
WEEK = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_P = "(?P<p>凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|半夜)?\\s*"
_T = "(?P<H>\\d{1,2})[:：](?P<M>\\d{2})"
TS_RES = [
    ("today", re.compile(f"^{_P}{_T}$")),
    ("yday", re.compile(f"^昨天\\s*{_P}{_T}$")),
    ("byday", re.compile(f"^前天\\s*{_P}{_T}$")),
    ("week", re.compile(f"^(?:星期|周)(?P<w>[一二三四五六日天])\\s*{_P}{_T}$")),
    ("md", re.compile(f"^(?P<mo>\\d{{1,2}})月(?P<d>\\d{{1,2}})日\\s*{_P}{_T}$")),
    ("ymd", re.compile(f"^(?P<y>\\d{{4}})年(?P<mo>\\d{{1,2}})月(?P<d>\\d{{1,2}})日\\s*{_P}(?:{_T})?$")),
    ("ymd2", re.compile(f"^(?P<y>\\d{{4}})[/-](?P<mo>\\d{{1,2}})[/-](?P<d>\\d{{1,2}})\\s*{_P}(?:{_T})?$")),
]

def _hour(h, p):
    h = int(h)
    if p in ("下午", "傍晚", "晚上", "夜里") and h < 12: h += 12
    if p == "中午" and h < 11: h += 12
    if p in ("凌晨", "半夜", "早上", "早晨", "上午") and h == 12: h = 0
    return h

def parse_time(text, now=None):
    """WeChat 时间戳文本 → datetime；不是时间戳返回 None。"""
    now = now or datetime.datetime.now()
    t = re.sub(r"\s+", " ", (text or "").strip())
    for kind, rx in TS_RES:
        m = rx.match(t)
        if not m: continue
        g = m.groupdict()
        H = _hour(g["H"], g.get("p")) if g.get("H") else 0
        M = int(g["M"]) if g.get("M") else 0
        if H > 23 or M > 59: return None
        d = now.date()
        try:
            if kind == "yday": d -= datetime.timedelta(1)
            elif kind == "byday": d -= datetime.timedelta(2)
            elif kind == "week":
                back = (d.weekday() - WEEK[g["w"]]) % 7 or 7   # 微信「星期X」指过去一周内、不含今天/昨天
                d -= datetime.timedelta(back)
            elif kind == "md":
                d = datetime.date(d.year, int(g["mo"]), int(g["d"]))
                if d > now.date(): d = d.replace(year=d.year - 1)
            elif kind in ("ymd", "ymd2"):
                d = datetime.date(int(g["y"]), int(g["mo"]), int(g["d"]))
        except ValueError:
            return None
        return datetime.datetime.combine(d, datetime.time(H, M))
    return None

ME_RE = re.compile(r"^(你|我)(撤回|拍了拍|已添加|已经添加|通过了|开启了|关闭了|修改|邀请|将|发起|取消|已)")
def _clean(t):
    t = re.sub(r"\s*重新编辑\s*$", "", (t or "").strip())
    return re.sub(r"\s+", " ", t)

def notice_side(text):
    """系统提示是谁那侧：'me'（小甜自己引起）或 'them'（他那侧 / 中性提示，作为用户侧输入）。"""
    return "me" if ME_RE.match(_clean(text)) else "them"

def notice_text(text):
    t = _clean(text)
    t = re.sub(r'^[“"「『]?[^“”"」』]*[”"」』]\s*(?=撤回了)', "对方", t)       # “昵称” 撤回了… → 对方撤回了…（昵称渲染为空白）
    if re.match(r"^\s*撤回了", t): t = "对方" + t.strip()
    return f"[系统提示：{t}]"

def label(dt, now=None):
    """sent_at → 提示词前缀，如 '[晚上 18:00 发]' / '[昨天 下午 15:20 发]' / '[10月20日 上午 09:05 发]'。"""
    if not dt: return ""
    if isinstance(dt, str):
        try: dt = datetime.datetime.fromisoformat(dt)
        except ValueError: return ""
    now = now or datetime.datetime.now()
    h = dt.hour
    p = "凌晨" if h < 5 else "早上" if h < 8 else "上午" if h < 12 else "中午" if h < 13 else "下午" if h < 18 else "晚上"
    days = (now.date() - dt.date()).days
    day = "" if days == 0 else "昨天 " if days == 1 else "前天 " if days == 2 else \
        (f"{dt.month}月{dt.day}日 " if dt.year == now.year else f"{dt.year}年{dt.month}月{dt.day}日 ")
    return f"[{day}{p} {dt:%H:%M} 发]"

def annotate(msgs, in_chat, now=None):
    """read_chat() 结果（已按 y 排序）→ 去掉 system 项：时间戳变成后续消息的 sent_at，提示变成 type=notice 的消息。
    in_chat=False（窗口/标题没核对通过）时 system 项一律丢弃，绝不当输入。"""
    out, cur = [], None
    for m in msgs:
        if m.get("from") != "system":
            out.append({**m, "sent_at": cur.isoformat(timespec="minutes")} if cur else dict(m)); continue
        if not in_chat: continue
        dt = parse_time(m.get("text"), now)
        if dt: cur = dt; continue
        t = _clean(m.get("text"))
        if len(t) < 2: continue
        nm = {**m, "from": notice_side(t), "type": "notice", "text": notice_text(t), "raw": t}
        if cur: nm["sent_at"] = cur.isoformat(timespec="minutes")
        out.append(nm)
    return out
