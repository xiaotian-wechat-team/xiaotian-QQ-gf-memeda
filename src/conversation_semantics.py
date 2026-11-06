"""Conservative conversational intent helpers. Persona SSOT: data/persona_dossier.json.

These pure helpers recognize explicit conversational acts, never infer a bedtime
from a topic word. Uncertain mentions leave the conversation open.
"""
from __future__ import annotations

import re

_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"\n]*\"|‘[^’]*’|'[^'\n]*'")
_CONTINUE = re.compile(
    r"(?:别|不要|不用).{0,4}(?:说晚安|睡|结束|收口)|"
    r"(?:还不|不想|不打算|不准备|不去|没想).{0,3}睡|"
    r"(?:继续|接着|陪我|还想|还要).{0,6}(?:聊|说话)|没聊够|不睡了"
)
_SLEEP = re.compile(
    r"晚安|安睡|(?:快|去)?睡吧|(?:我)?先睡(?:了|啦|咯|哦|啊)?|"
    r"(?:我)?去睡(?:了|啦|咯)|睡(?:了|啦|咯)(?:啊|哦|哈|呀)?|"
    r"困了.{0,2}睡|早点睡|早睡|休息了[啊哦]?|歇了[啊哦]?"
)
_END = re.compile(r"不聊了|先聊到这|下次再聊|先这样(?:吧|啦)?|先走了|拜拜")
_MENTION = re.compile(
    r"(?:昨天|昨晚|前天|刚才|之前|以前|明天|后天|今晚|到时候|待会|一会儿?|等会|以后|下次)|"
    r"(?:他说|她说|他们说|她们说|别人说|朋友说|室友说|导师说)|"
    r"(?:如果|假如|假设|要是|比如|例如|记得)|"
    r"(?:不是|没有|没|不|别|不要|不用).{0,4}$"
)


def clauses(text: str | None) -> list[str]:
    """Preserve punctuation and quoted spans while splitting editable clauses."""
    output, buffer = [], []
    closing_quote = None
    quotes = {"「": "」", "『": "』", "“": "”", '"': '"', "‘": "’", "'": "'"}
    for char in text or "":
        buffer.append(char)
        if closing_quote:
            if char == closing_quote:
                closing_quote = None
        elif char in quotes:
            closing_quote = quotes[char]
        elif char in "，,。.!！?？；;\n":
            output.append("".join(buffer))
            buffer = []
    if buffer:
        output.append("".join(buffer))
    return output


def unquoted(text: str) -> str:
    return _QUOTED.sub("", text)


def closing_kind(text: str | None) -> str | None:
    """Return ``sleep`` / ``end`` only for an explicit present sign-off.

    A request to keep talking overrides a bedtime mention in the same message.
    Quotes, reported speech, questions and future advice cannot create sleep
    state. An explicit later clause ("我先睡了，晚安") still works.
    """
    raw = unquoted((text or "").strip())
    if not raw:
        return None
    # "晚安，明天继续聊" ends tonight's conversation; only a present
    # request to continue overrides the sign-off.
    for part in clauses(raw):
        if any(not _MENTION.search(part[:m.start()]) for m in _CONTINUE.finditer(part)):
            return None
    found = None
    for part in clauses(raw):
        if re.search(r"[?？]|(?:吗|么|没|什么|什么意思|怎么说|两个字|这句话)\s*[。！!]*$", part):
            continue
        for kind, rx in (("sleep", _SLEEP), ("end", _END)):
            for match in rx.finditer(part):
                prefix = part[:match.start()]
                suffix = part[match.end():]
                if _MENTION.search(prefix):
                    continue
                if re.match(r"(?:是|的意思|这句话|两个字|怎么|对|有助于|能让)", suffix):
                    continue
                # Saying/hearing a word is different from saying goodbye.
                if re.search(r"(?:说|听|提|叫|写|发|梦见).{0,3}$", prefix):
                    continue
                if re.search(r"(?:他|她|别人|室友|同学|朋友|导师).{0,6}$", prefix):
                    continue
                found = kind if found != "sleep" else found
    return found


def is_explicit_goodnight(text: str | None) -> bool:
    return closing_kind(text) == "sleep"


def is_explicit_closing(text: str | None) -> bool:
    return closing_kind(text) is not None


def is_nap_signoff(text: str | None) -> bool:
    """An explicit present nap, not advice, quoted speech or yesterday's sleep."""
    raw = unquoted(text or "")
    if _CONTINUE.search(raw):
        return False
    for part in clauses(raw.replace(" / ", "，")):
        if re.fullmatch(r"\s*(?:嗯[，,]?\s*)?(?:我)?(?:先|再|去|要)?"
                        r"(?:睡(?:一|个)?(?:会儿?|小觉|午觉)|补(?:个)?觉)"
                        r"(?:了|啦|吧|啊|哈)?[。！!，,\s]*", part):
            return True
    return False


def is_imagined_request(text: str | None) -> bool:
    """Explicitly requested fiction; drawing/generation alone proves nothing.

    Caller still checks image intent. This flag cannot be supplied by the
    model's generated scene, quoted speech, or a mention of one's own fantasy.
    """
    raw = unquoted((text or "").strip())
    if not raw:
        return False
    marker = r"想象|幻想|假如|假设|假装|虚构|架空|脑补"
    if re.search(rf"(?:不是|不要|不用|别|不许|无需|不想|并非).{{0,5}}(?:{marker})", raw):
        return False
    if re.search(r"(?:要|看|发|拍).{0,5}(?:实拍|真实发生)|不是.{0,6}(?:虚构|幻想)", raw):
        return False
    for part in clauses(raw):
        for match in re.finditer(marker, part):
            prefix, suffix = part[:match.start()], part[match.end():]
            if re.search(r"(?:昨天|昨晚|之前|刚才)|(?:他|她|别人|朋友).{0,6}(?:说|在)|"
                         r"我(?:在|正在|一直|曾经)|你(?:喜欢|总爱|爱|经常)|"
                         r"什么是|什么意思|提到|谈到", prefix):
                continue
            if re.match(r"(?:是什么|是什么意思|这个词|的意思|吗|么|呢)", suffix):
                continue
            if match.group() in ("假如", "假设") and re.match(r"(?:我(?!们)|他|她)", suffix):
                # A hypothetical about the user's day does not turn a later
                # request for her current photo into fiction.
                continue
            return True
    return False


_NONCURRENT = re.compile(
    r"昨天|昨晚|前天|上周|以前|之前|曾经|当时|那天|明天|后天|下周|"
    r"待会|一会儿?|等会|回头|以后|到时候|今晚|明早|今天(?:早上|上午)|"
    r"等(?:我|咱|到|下)|如果|假如|假设|要是|比如|例如|想象|"
    r"(?:打算|准备|想|要|计划|会)(?:再|先|去)|结束后|下班后|下课后"
)
_SUBJECT = re.compile(r"你们|他们|她们|我们|咱们|我|你|您|他|她|室友|导师|同学|朋友|小熊|小熊|小熊")


def is_current_self_claim(clause: str, match_start: int) -> bool:
    """Whether a matched activity is asserted as the speaker's present action.

    Colloquial subjectless updates ("刚下课啦") count; other people's actions,
    future/past/conditional/negated statements and questions do not. This is a
    conservative guard, not a full language parser.
    """
    if (unquoted(clause) != clause
            or re.search(r"[?？]|是不是|有没有|(?:吗|么)[。！!]*$", clause)):
        return False
    prefix = clause[:match_start]
    if _NONCURRENT.search(prefix):
        return False
    if re.search(r"(?:他|她|室友|导师|同学|朋友|你).{0,4}(?:说|问|觉得|以为)", prefix):
        return False
    if re.search(r"(?:不|没|没有|不是|不在|并非|别|不要|不用|不去|没去)(?:正|正在|在|去)?$", prefix):
        return False
    subjects = list(_SUBJECT.finditer(prefix))
    if subjects and subjects[-1].group() not in ("我", "我们", "咱们"):
        return False
    # "想上课/要回宿舍/能下班" is a wish/plan, not a current-location claim.
    if re.search(r"(?:想|要|会|能|该|应该|可以|得|等|再|准备|打算)(?:去|先|再)?$", prefix):
        return False
    return True
