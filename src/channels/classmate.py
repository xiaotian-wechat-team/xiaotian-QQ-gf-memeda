"""同学人设：陌生人私聊。禁止情侣语气 / 发图；称呼「X同学」；记忆隔离。"""
from __future__ import annotations

import re
import sys
from typing import Optional

from .persona_map import (
    classmate_address,
    classmate_display_name,
    is_classmate_contact,
    resolve_contact_id,
)


CLASSMATE_SYSTEM = (
    "你是大学生小甜。对方是普通同学/熟人，不是恋人。"
    "用礼貌、轻松、短句中文回复；称呼对方为指定「X同学」（可偶尔省略称呼）。"
    "禁止撒娇、情侣昵称、亲密暗示、发自拍/照片、分享情侣日程或私人恋爱记忆。"
    "不知道就说不知道；一次最多问一个问题；一两句即可。"
)

# Light filter: strip couple-intimate nicknames that leak into classmate replies
_COUPLE_INTIMATE_RE = re.compile(
    r"(亲爱的(?:老公|老婆|宝贝)?|honey|宝贝儿?|小宝贝儿?|大宝贝|乖宝贝|臭宝贝|"
    r"宝宝|乖宝宝|小宝宝|大宝宝|臭宝宝|好宝宝|憨宝宝|傻宝宝|笨宝宝|"
    r"老婆(?:儿|大人|宝贝)?|老公(?:宝贝)?|我的(?:宝贝|宝宝|小宝贝|老婆)|"
    r"小熊|维尼熊|维尼|么么哒?|"
    r"🐻|"
    r"想你了|想我了|好想你|想你啦|想我想)",
    re.IGNORECASE,
)


def scrub_couple_nicknames(text: str, address_as: str = "") -> str:
    """Optional light filter: replace couple-intimate nicknames with classmate address."""
    if not text:
        return text
    repl = (address_as or "同学").strip() or "同学"
    out = _COUPLE_INTIMATE_RE.sub(repl, text)
    out = re.sub(r"(同学){2,}", "同学", out)
    return out


def reply_classmate(
    cid: str,
    hist: list,
    *,
    address_as: str,
    dry: bool = True,
) -> str:
    """Generate a short classmate-mode reply. Isolated from couple memory writes."""
    import brain

    brain.LAST = {
        "persona_mode": "classmate",
        "want_image": None,
        "want_voice": None,
        "nickname": address_as,
        "memory_source": "classmate_local",
    }
    lines = []
    for h in hist[-8:]:
        s, t = h[0], h[1]
        who = "小甜" if s == "me" else "对方"
        lines.append(f"{who}: {t}")
    chat = "\n".join(lines) if lines else "（无历史）"
    user = (
        f"这次称呼对方为：{address_as}\n"
        f"# 最近聊天\n{chat}\n"
        "只输出气泡正文，一行一条，不要引号，不要 WANT_IMAGE。"
    )
    messages = [
        {"role": "system", "content": CLASSMATE_SYSTEM},
        {"role": "user", "content": user},
    ]
    text = ""
    try:
        from brain import _short_adapter
        text = str(_short_adapter(messages) or "").strip()
    except Exception as e:
        print(f"[classmate] short llm fail: {e!r}", file=sys.stderr, flush=True)
        try:
            import llm
            text = str(llm.complete(
                CLASSMATE_SYSTEM + "\n" + user,
                reuse_session=False,
            ) or "").strip()
        except Exception as e2:
            print(f"[classmate] llm fallback fail: {e2!r}", file=sys.stderr, flush=True)
            text = f"{address_as}，在的，有事说～"
    out_lines = []
    for ln in text.replace("\r", "").split("\n"):
        s = ln.strip().strip('"').strip("'")
        if not s:
            continue
        if s.upper().startswith("WANT_IMAGE"):
            continue
        out_lines.append(s)
    text = "\n".join(out_lines[:3]).strip() or f"{address_as}，嗯？"
    text = scrub_couple_nicknames(text, address_as)
    brain.LAST["want_image"] = None
    brain.LAST["want_voice"] = None
    return text


def handle_classmate_inbound(
    msg,
    *,
    dry: bool = True,
    send=None,
    reply_fn=None,
    persist: Optional[bool] = True,
) -> dict:
    """Bridge branch for non-couple peers."""
    import classmate_memory as cm
    persist = False if dry else (True if persist is None else persist)
    if persist:
        cm.ensure()

    from .persona_map import classmate_contact_id, is_couple_contact
    cid = resolve_contact_id(msg.channel, msg.peer_id)
    if is_couple_contact(cid):
        raise ValueError(f"refusing couple contact in classmate handler: {cid}")
    if not is_classmate_contact(cid):
        cid = classmate_contact_id(msg.channel, msg.peer_id)
    addr = classmate_address(msg.peer_id, getattr(msg, "raw", None) or {})
    disp = classmate_display_name(msg.peer_id, getattr(msg, "raw", None) or {})
    if persist is None:
        persist = not dry
    if persist:
        cm.upsert_profile(cid, display_name=disp, address_as=addr)
        cm.insert(cid, "them", msg.text, msg_id=msg.msg_id or "", msg_type=msg.msg_type or "text")
        hist = cm.history(cid, 20)
    else:
        hist = [("them", msg.text)]
    if reply_fn is not None:
        text = (reply_fn(cid, hist) or "").strip()
        import brain
        brain.LAST = {
            "persona_mode": "classmate",
            "want_image": None,
            "want_voice": None,
            "nickname": addr,
            "memory_source": "classmate_local",
        }
    else:
        text = reply_classmate(cid, hist, address_as=addr, dry=dry)
    text = scrub_couple_nicknames(text, addr)
    out = {
        "contact_id": cid,
        "reply": text,
        "dry": dry,
        "channel": msg.channel,
        "peer_id": msg.peer_id,
        "persist": bool(persist),
        "persona_mode": "classmate",
        "want_image": None,
        "want_voice": False,
        "address_as": addr,
    }
    if dry or send is None or not text:
        return out
    from .types import Outbound
    send(Outbound(channel=msg.channel, peer_id=msg.peer_id, text=text, meta={}))
    if persist:
        cm.insert(cid, "me", text, msg_type="text")
    return out
