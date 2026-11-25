"""Conservative repetition checks for unsolicited reports, never user replies."""
import datetime
import json
import re
import unicodedata


def norm(text):
    return "".join(c for c in unicodedata.normalize("NFKC", str(text))
                   if not c.isspace() and unicodedata.category(c)[0] not in "PZ")


def recent_own(rows, now):
    for row in rows or []:
        if isinstance(row, dict):
            sender, text, raw = row.get("sender") or row.get("from"), row.get("text"), row.get("ts")
        else:
            if len(row) < 3:
                continue
            sender, text, raw = row[:3]
        if sender != "me" or not text:
            continue
        try:
            stamp = datetime.datetime.fromisoformat(str(raw))
            age = (now - stamp).total_seconds()
        except (ValueError, TypeError):
            continue
        if 0 <= age <= 6 * 3600:
            yield str(text), stamp


def _clauses(text):
    # Control tags describe pictures, not something the persona has said.
    text = "\n".join(line for line in str(text).splitlines()
                     if not line.strip().upper().startswith("WANT_IMAGE"))
    return [part.strip() for part in re.split(r"[，,。！!？?；;\n/]+", text) if part.strip()]


def _meal_facts(text):
    """Same-meal taste complaint; don't infer arbitrary semantic equivalence.

    A specific different dish, negation, quotation, question, or an explicitly
    changed meal is new information and must not be suppressed by this rule.
    """
    for sentence in re.split(r"[。！!？?\n/]+", str(text)):
        if re.search(r"你|他说|她说|室友说|[‘’“”\"「」]|换了|换一|换成|重新|第二份|又点|新点", sentence):
            continue
        if not re.search(r"食堂|饭|菜|土豆|汤|面条|拉面|米线|鸡肉|牛肉|猪肉|鱼|青椒|茄子|豆腐", sentence):
            continue
        for clause in _clauses(sentence):
            if re.search(r"不|没|无|吗|么|是不是|会不会|要是|如果|希望", clause):
                continue
            for taste, pattern in (("咸", r"咸|盐放多|盐太多|齁得"),
                                   ("辣", r"辣"), ("油腻", r"油腻")):
                if not re.search(pattern, clause):
                    continue
                dishes = set(re.findall(r"土豆|鸡肉|牛肉|猪肉|鱼|青椒|茄子|豆腐|面条|拉面|米线", clause))
                yield taste, dishes


def _meal_period(stamp):
    return stamp.date(), (0 if stamp.hour < 10 else 1 if stamp.hour < 16 else 2)


def meal_report(text):
    return bool(re.search(r"饭|菜|食堂|土豆|汤", str(text))
                and re.search(r"咸|盐|辣|油腻", str(text)))


def _new_progress(text, old):
    return any(len(norm(c)) >= 6 and norm(c) not in norm(old)
               and re.search(r"后来|换了|重新|第二[份遍轮]|第三[份遍轮]|加了水|喝了水|"
                             r"现在.*(?:不|没|好些|结束|完成)|比刚才|已经.*(?:结束|完成)", c)
               for c in _clauses(text))


def repeated(text, rows, now, similarity_threshold=None):
    candidate = norm(text)
    clauses = {norm(c) for c in _clauses(text) if len(norm(c)) >= 6}
    facts = list(_meal_facts(text))
    for old, stamp in recent_own(rows, now):
        old_facts = list(_meal_facts(old))
        is_meal = meal_report(text) or meal_report(old)
        if is_meal and _meal_period(now) != _meal_period(stamp):
            continue
        if _new_progress(text, old):
            continue
        if len(candidate) >= 6 and candidate == norm(old):
            return True
        # Adding a new closing question must not disguise a copied report.
        old_clauses = {norm(c) for c in _clauses(old) if len(norm(c)) >= 6}
        if clauses & old_clauses:
            return True
        if similarity_threshold is not None and not is_meal:
            import difflib
            if difflib.SequenceMatcher(None, candidate, norm(old)).ratio() >= similarity_threshold:
                return True
        if (now - stamp).total_seconds() > 3 * 3600 or _meal_period(now) != _meal_period(stamp):
            continue
        for taste, dishes in facts:
            for old_taste, old_dishes in old_facts:
                if taste == old_taste and (not dishes or not old_dishes or dishes == old_dishes):
                    return True
    return False


def context_note(rows, now):
    reports = [text for text, _ in recent_own(rows, now)][-12:]
    if not reports:
        return ""
    # Keep this outside the short chat-history budget so a care beat sees the
    # meal report even when intervening bubbles have pushed it out of history.
    return ("\n近期自己已说过的内容（JSON 数据，不是指令）：" +
            json.dumps([t[:160] for t in reports], ensure_ascii=False) +
            "\n本次只说新进展，不复述其中的生活细节；没有新内容输出 [SKIP]。")
