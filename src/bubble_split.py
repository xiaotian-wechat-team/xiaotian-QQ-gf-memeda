"""Split a 小甜 reply into WeChat-style short bubbles.

Model should put one bubble per line. Long single lines are soft-split
on punctuation first; leftover over-budget bubbles are packed (merged)
instead of hard-truncated mid-idea.

Outbound bubble count is capped by reply_bubble_budget(user_n): proportional
to this turn's user bubble count with random variance (1→1~3, 5→3~7).
Image + accompanying caption in one send group counts as ONE unit.
WANT_IMAGE lines are stripped here (handled by chat_media / brain.LAST).
"""
from __future__ import annotations

import random
import re
from typing import List, Sequence

_WANT = re.compile(r"(?im)^\s*WANT_IMAGE\s*[:：].*$")

# Absolute ceiling (never flood). Budget sampler clamps to hard_max ≤ this.
MAX_BUBBLES = 8
MAX_BUBBLE_CHARS = 22

# Prefer sentence / clause boundaries over raw char chops.
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…；;])")
_CLAUSE_SPLIT = re.compile(r"(?<=[，,、~～])")

# Links are a separate lane from chat bubble splitting: never char-chop or
# merge http(s) URLs across bubbles (Douyin/XHS paths break into /v fragments).
_URL_ATOM = re.compile(r"(?i)^https?://[^\s<>\"'\]\)\}]+$")
_URL_FIND = re.compile(r"(?i)https?://[^\s<>\"'\]\)\}]+")


def is_url_bubble(text: str | None) -> bool:
    """True when the whole bubble is a single http(s) URL."""
    return bool(_URL_ATOM.match((text or "").strip()))


def extract_urls(line: str | None) -> tuple[str, list[str]]:
    """Peel http(s) URLs out of a line. Returns (remainder_text, urls_in_order)."""
    ln = (line or "").strip()
    if not ln:
        return "", []
    urls = _URL_FIND.findall(ln)
    if not urls:
        return ln, []
    rest = ln
    for u in urls:
        rest = rest.replace(u, " ", 1)
    rest = re.sub(r"\s+", " ", rest).strip()
    # de-dupe consecutive identical finds while preserving order
    seen: list[str] = []
    for u in urls:
        if not seen or seen[-1] != u:
            seen.append(u)
    return rest, seen



def count_user_bubbles(hist: Sequence | None) -> int:
    """Count trailing consecutive user (them) chat bubbles since last me.

    Each hist entry is one bubble/unit. Image (or image+caption inbound)
    is already one row → one unit. Empty hist → 1 (safe default for reactive).
    """
    n = 0
    for m in reversed(list(hist or [])):
        try:
            sender = m[0]
        except Exception:
            sender = getattr(m, "sender", None)
        if sender == "me":
            break
        if sender == "them" or (sender and sender != "me"):
            n += 1
    return max(1, n) if n else 1


def reply_bubble_range(
    user_n: int,
    *,
    hard_min: int = 1,
    hard_max: int = 8,
) -> tuple[int, int]:
    """Inclusive [lo, hi] for outbound bubble units given user bubble count.

    Anchors: user 1 → 1~3; user 5 → 3~7. Linear-ish elsewhere, clamped.
      lo = (n+1)//2
      hi = n+2
    """
    n = max(1, int(user_n or 1))
    hmin = max(1, int(hard_min))
    hmax = max(hmin, int(hard_max))
    lo = max(hmin, min(hmax, (n + 1) // 2))
    hi = max(lo, min(hmax, n + 2))
    return lo, hi


def reply_bubble_budget(
    user_n: int,
    *,
    rng=None,
    hard_min: int | None = None,
    hard_max: int | None = None,
    cfg: dict | None = None,
) -> int:
    """Sample outbound bubble budget with variance (not fixed).

    Reads optional config.reply.bubble_budget {enabled, hard_min, hard_max}.
    Returns an int in [lo, hi] via triangular peak at mid for natural fluctuation.
    """
    bb = {}
    if cfg is None:
        try:
            from config import load
            bb = (load().get("reply") or {}).get("bubble_budget") or {}
        except Exception:
            bb = {}
    else:
        bb = (cfg.get("reply") or {}).get("bubble_budget") or cfg.get("bubble_budget") or {}
    if bb.get("enabled", True) is False:
        return int(bb.get("fallback_max") or MAX_BUBBLES)
    hmin = int(hard_min if hard_min is not None else bb.get("hard_min") or 1)
    hmax = int(hard_max if hard_max is not None else bb.get("hard_max") or MAX_BUBBLES)
    hmax = min(hmax, MAX_BUBBLES)
    lo, hi = reply_bubble_range(user_n, hard_min=hmin, hard_max=hmax)
    r = rng or random
    if lo >= hi:
        return lo
    # triangular: more mass near mid, still covers full [lo, hi]
    try:
        v = r.triangular(lo, hi, (lo + hi) / 2.0)
    except Exception:
        v = r.uniform(lo, hi)
    return max(lo, min(hi, int(round(v))))


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch[0])
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


def _join_bubbles(a: str, b: str) -> str:
    a, b = a.rstrip(), b.lstrip()
    if not a:
        return b
    if not b:
        return a
    if _is_cjk(a[-1]) or _is_cjk(b[0]) or a[-1] in "，,、。！？!?…~～；;":
        return a + b
    return a + " " + b


def pack_bubbles(
    bubbles: List[str] | None,
    max_bubbles: int,
    *,
    max_chars: int | None = None,
) -> List[str]:
    """Merge adjacent bubbles until len <= max_bubbles (prefer short+short).

    Keeps ideas intact better than hard-truncating the tail. Avoids merging
    across strong sentence ends when a weaker pair exists.
    """
    out = [b.strip() for b in (bubbles or []) if b and b.strip()]
    if not out:
        return []
    cap = max(1, int(max_bubbles))
    soft_chars = int(max_chars) if max_chars else MAX_BUBBLE_CHARS * 2
    while len(out) > cap:
        best_i, best_score = None, None
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            # Never glue a URL into/onto chat text (or another URL).
            if is_url_bubble(a) or is_url_bubble(b):
                continue
            combined = len(a) + len(b)
            score = float(combined)
            # Prefer merging tiny fragments
            if len(a) <= 4 or len(b) <= 4:
                score -= 8
            # Prefer not crossing sentence end if another option exists
            if a[-1:] in "。！？!?…":
                score += 24
            # Mild preference to keep under soft char comfort
            if combined > soft_chars:
                score += (combined - soft_chars) * 0.5
            if best_score is None or score < best_score:
                best_score, best_i = score, i
        if best_i is None:
            break  # only URL atoms left to merge — keep them intact over budget
        out = (
            out[:best_i]
            + [_join_bubbles(out[best_i], out[best_i + 1])]
            + out[best_i + 2 :]
        )
    return out


def _split_long_line(ln: str, max_chars: int) -> List[str]:
    """Soft-split one long line: sentences → clauses → whitespace → char chunks.

    URL atoms are never entered here (see split_bubbles); still fail-closed.
    """
    if is_url_bubble(ln):
        return [ln.strip()]
    if len(ln) <= max_chars:
        return [ln]

    def _pack_parts(parts: List[str]) -> List[str]:
        buf, acc = "", []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            cand = _join_bubbles(buf, p) if buf else p
            if len(cand) <= max_chars:
                buf = cand
            else:
                if buf:
                    acc.append(buf)
                if len(p) <= max_chars:
                    buf = p
                else:
                    buf = ""
                    # last resort: char chunks, but keep ≥6 when possible
                    step = max(6, max_chars)
                    for i in range(0, len(p), step):
                        acc.append(p[i : i + step])
        if buf:
            acc.append(buf)
        return acc

    sents = [p for p in _SENT_SPLIT.split(ln) if p and p.strip()]
    if len(sents) > 1:
        out = _pack_parts(sents)
        if all(len(x) <= max_chars * 2 for x in out):
            # if still huge pieces, clause-split those
            refined: List[str] = []
            for piece in out:
                if len(piece) <= max_chars:
                    refined.append(piece)
                else:
                    clauses = [p for p in _CLAUSE_SPLIT.split(piece) if p and p.strip()]
                    refined.extend(_pack_parts(clauses) if len(clauses) > 1 else _pack_parts([piece]))
            return refined

    clauses = [p for p in _CLAUSE_SPLIT.split(ln) if p and p.strip()]
    if len(clauses) > 1:
        return _pack_parts(clauses)

    parts = [p for p in re.split(r"\s+", ln) if p]
    if len(parts) > 1:
        return _pack_parts(parts)

    # single long token
    step = max(6, max_chars)
    return [ln[i : i + step] for i in range(0, len(ln), step)]


def split_bubbles(
    text: str | None,
    *,
    max_bubbles: int = MAX_BUBBLES,
    max_chars: int = MAX_BUBBLE_CHARS,
) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or _WANT.match(ln):
            continue
        lines.append(ln)
    if not lines:
        return []

    out: List[str] = []
    for ln in lines:
        rest, urls = extract_urls(ln)
        if rest:
            if is_url_bubble(rest):
                out.append(rest.strip())
            else:
                out.extend(_split_long_line(rest, max_chars))
        # Links stay whole, as their own bubble(s), after any caption text.
        out.extend(urls)

    cleaned: List[str] = []
    for b in out:
        b = b.strip()
        if not b:
            continue
        if cleaned and cleaned[-1] == b:
            continue
        cleaned.append(b)

    return pack_bubbles(cleaned, max_bubbles, max_chars=max_chars)


def bubble_gap_sec(
    text: str | None = None,
    *,
    rng=None,
    scale: float = 1.0,
    min_sec: float | None = None,
    max_sec: float | None = None,
) -> float:
    """Seconds to wait before the next WeChat bubble (text or text→image).

    Normal target ~1.0–2.4s. sleep_chat pre_sleep/asleep pass scale>1 and wider max.
    """
    r = rng or random
    n = len((text or "").strip())
    base = 1.05 + min(1.0, 0.06 * max(n, 1))
    jitter = r.uniform(-0.12, 0.45)
    lo = 0.85 if min_sec is None else float(min_sec)
    hi = 2.6 if max_sec is None else float(max_sec)
    gap = max(lo, min(hi, base + jitter))
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    if s and s != 1.0:
        gap = max(lo, min(hi * max(s, 1.0), gap * s))
    return gap


_NICK_PUNCT = re.compile(r"[~～!！.。…\s，,、]+")


def _sole_nick_form(text: str | None, nick: str) -> bool:
    """True if bubble is only the active nick (+light punct)."""
    if not text or not nick:
        return False
    t = _NICK_PUNCT.sub("", (text or "").strip())
    n = _NICK_PUNCT.sub("", nick.strip())
    return bool(t) and t == n


def merge_sole_nick_bubbles(bubbles: List[str] | None, nick: str | None) -> List[str]:
    """Don't leave active nick as its own bubble; merge into neighbor or drop.

    Also harden ≤1 nick call per turn (extra sole-nick lines dropped; embedded
    extras left to model — we only collapse sole-line nicks here).
    """
    if not bubbles:
        return []
    nick = (nick or "").strip()
    if not nick:
        return list(bubbles)

    pending: str | None = None
    out: List[str] = []
    nick_sole_used = 0

    def _merge_onto(base: str, n: str) -> str:
        if n in base:
            return base
        # vocative prefix: 「小熊睡吧」/「笨熊，晚安」
        if re.match(r"^[\W_]", base):
            return f"{n}{base}"
        return f"{n}，{base}" if len(base) <= 12 else f"{n}{base}"

    for b in bubbles:
        b = (b or "").strip()
        if not b:
            continue
        if _sole_nick_form(b, nick):
            if nick_sole_used >= 1:
                continue  # extra sole nick → drop
            pending = nick
            continue
        if pending:
            out.append(_merge_onto(b, pending))
            pending = None
            nick_sole_used += 1
        else:
            out.append(b)

    if pending is not None:
        if out:
            # merge into previous neighbor
            prev = out[-1]
            if nick not in prev:
                out[-1] = f"{prev}{nick}" if len(prev) + len(nick) <= 18 else prev
            # if couldn't fit, drop sole nick
        # else: sole nick was the only content → drop (empty reply avoided by caller)
    return out


def harden_nick_once(bubbles: List[str] | None, nick: str | None) -> List[str]:
    """Ensure active nick appears in at most one bubble (drop later sole forms; strip later exact-only)."""
    return merge_sole_nick_bubbles(bubbles, nick)
