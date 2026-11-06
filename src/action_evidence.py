"""Keep completion claims grounded. Persona SSOT: data/persona_dossier.json."""
import re
from urllib.parse import urlsplit

NOTE = ("动作如实：只有本轮工具结果支持才说搜了/查到了；图片发送前只说给你看看，"
        "不提前声称发成功；不宣称已写入长期记忆。可以自然说会记着。\n")


def _weather_lookup_claim(part):
    """A weather lookup cannot support claims about web searches or other topics."""
    match = re.match(r"^(?:嗯+|行|哦|好)?(?:我)?(?:刚刚|刚|已经|刚才)?"
                     r"(?:查了一下|查完了|查到了?|查过了|查了)(.*)$", part)
    if not match:
        return False
    subject = match.group(1).strip()
    return not subject or bool(re.search(r"天气|气温|温度|下雨|下雪|降雨|降雪|晴|阴|冷|暖|风|雾|霾|[℃°]|\d+\s*度", subject))


def _reading_claim(part, *, search_context=False):
    """Positive first-person web-reading claims; ordinary reading stays untouched."""
    prefix = r"^(?:嗯+|行|哦|好)?(?:我)?(?:也|还)?(?:刚刚|刚|已经|刚才)?(?:仔细|认真)?"
    read = r"(?:看|读)(?:完了?|过了?|了)"
    page = r"(?:(?:这|那|整|全|一)(?:一)?篇|相关|这些|那些)?(?:笔记|文章|帖子|全文|正文)"
    official = (re.match(prefix + r"(?:去|上网)?" + read + r"(?:一下)?(?:官网|官方网站)", part)
                or re.match(prefix + r"(?:去|在|上)(?:官网|官方网站)(?:上)?(?:看|读|查)(?:了|过|完|到)", part))
    return bool(official or search_context and (
        re.match(prefix + read + r"(?:整篇|全部|完整)?" + page, part)
        or re.match(prefix + read + r"(?:整篇|全文)(?:了|啦|呢|啊)?$", part)
        or re.match(prefix + r"(?:把)?(?:整篇|全文|正文|" + page + r")(?:都|已经)?" + read, part)))


def _reading_evidence_phrase(search):
    """Search hits never prove a visit to an official site or a complete read."""
    sources = search.get("sources") if search and search.get("status") == "ok" else None
    if not sources:
        return "这会儿还没查到可靠结果"
    def xhs(source):
        try:
            return isinstance(source, dict) and urlsplit(source.get("url", "")).hostname == "www.xiaohongshu.com"
        except (TypeError, ValueError):
            return False
    if all(xhs(source) for source in sources):
        if any(source.get("content_scope") == "visible_note_body"
               and isinstance(source.get("snippet"), str) and source["snippet"].strip()
               and source["snippet"].strip() not in ("[图片]", "[视频]", "[图集]") for source in sources):
            return "我看了相关笔记"
        return "我看到相关笔记标题"
    return "我看到相关搜索结果"


def _media_reading_claim(part):
    """A completed media view, excluding its text description and future plans."""
    prefix = r"^(?:嗯+|行|哦|好)?(?:我)?(?:也|还)?(?:刚刚|刚|已经|刚才)?(?:仔细|认真)?"
    read = r"(?:看|观看|刷)(?:完了?|过了?|了)"
    media = r"(?:你发的|刚搜到的|相关的)?(?:这|那|整|全)?(?:个|条|段|组|套)?(?:视频|图集)"
    match = (re.match(prefix + read + media, part.lstrip())
             or re.match(prefix + r"(?:把)?" + media + r"(?:都|已经)?" + read, part.lstrip()))
    if not match:
        return False
    after = part.lstrip()[match.end():]
    # “看了视频的文案” is a text read; “看完视频再聊” is a future sequence.
    return not re.match(r"(?:的)?(?:标题|简介|文案|文字|标签|说明|介绍)|(?:之后|以后|后)?(?:再|就|才)", after)


def _inside_quote(prefix):
    """Keep a quoted media claim quoted even after an internal comma/newline."""
    pairs = {"“": "”", "「": "」", "‘": "’", '"': '"', "'": "'"}
    closing = None
    for char in prefix:
        if closing:
            if char == closing:
                closing = None
        elif char in pairs:
            closing = pairs[char]
    return closing is not None


def enforce(text, search=None, *, weather=None):
    """Narrow first-person assertions only; preserve questions, quotes and negation."""
    valid_search = bool(search and search.get("status") == "ok" and search.get("sources"))
    valid_weather = bool(weather and weather.get("tool") == "get_weather"
                         and weather.get("status") == "ok" and weather.get("text"))
    text_only_search = search is not None and (not valid_search or all(
        isinstance(source, dict) and source.get("content_scope") in (None, "search_title", "visible_note_body")
        for source in search["sources"]))
    parts = re.split(r"([\n。！？!?；;，,])", text or "")
    for i in range(0, len(parts), 2):
        part = parts[i]
        if i + 1 < len(parts) and parts[i + 1] in ("？", "?"):
            continue
        # A quoted assertion is not the speaker's assertion. A quoted search
        # subject (我搜了“三角洲行动”) still is. Don't skip an entire mixed clause.
        if re.match(r"\s*[“\"「‘']", part):
            continue
        if text_only_search and _media_reading_claim(part) and not _inside_quote("".join(parts[:i])):
            phrase = _reading_evidence_phrase(search)
            part = {"我看了相关笔记": "我只看到了笔记里的文字",
                    "我看到相关笔记标题": "我只看到了相关笔记标题",
                    "我看到相关搜索结果": "我只看到了搜索结果里的文字"}.get(phrase, phrase)
        if _reading_claim(part, search_context=search is not None):
            # Weather, a result title, or a personal note cannot authorize
            # “看了官网/读完整篇”. Keep only the scope actually read this turn.
            part = _reading_evidence_phrase(search)
        if not valid_search and not (valid_weather and _weather_lookup_claim(part)):
            if re.match(r"^(?:嗯+|行|哦|好)?(?:我)?(?:刚刚|刚|已经|刚才)?(?:去|上网)?"
                        r"(?:搜了一下|搜过了|搜完了|搜到了?|搜了|查了一下|查完了|查到|查过了|查了|看过官网|看了官网)", part):
                part = "这会儿还没查到可靠结果"
        if re.match(r"^(?:我)?(?:已经|刚刚|刚才)?(?:把)?(?:图|图片|照片)(?:已经)?"
                    r"(?:发给你|发过去|发好|发送成功)[了啦]?", part):
            part = "给你看看"
        elif re.match(r"^我(?:已经|刚刚|刚才)?发给你[了啦]", part):
            part = "我发给你看"
        if re.match(r"^(?:我)?(?:已经|已)(?:写入|存入|保存到)(?:了)?长期记忆", part):
            part = "我会记着"
        parts[i] = part
    return "".join(parts)
