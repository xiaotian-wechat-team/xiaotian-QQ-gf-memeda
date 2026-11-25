"""Lightweight language hint for 小甜 chat (no heavy model packs).

Reads data/language_pack.json. Detects EN / FR / 粤语-ish user text and returns
a one-line prompt hint. Actual generation still uses the LLM + xiaotian-chat skill.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "language_pack.json"

_FR = re.compile(
    r"\b(je|tu|vous|bonjour|merci|oui|non|ça|avec|pour|une|les|des)\b|[àâçéèêëîïôùûü]",
    re.I,
)
_EN = re.compile(
    r"\b(the|you|are|is|am|what|how|love|baby|miss|tonight|really|please|ok)\b",
    re.I,
)
# Distinctive written Cantonese markers. 好啦/点心/吓死 also occur in Mandarin.
_YUE = re.compile(r"[嘅喎咗啲嗰冇係唔哋]|咩啊")


def load():
    if not PACK.exists():
        return {}
    return json.loads(PACK.read_text(encoding="utf-8"))


def detect(text: str) -> str | None:
    if not text or not str(text).strip():
        return None
    t = str(text)
    if _YUE.search(t):
        return "yue"
    # latin-heavy
    letters = sum(c.isalpha() and ord(c) < 128 for c in t)
    if letters >= max(4, int(len(t) * 0.35)):
        if _FR.search(t):
            return "fr"
        if _EN.search(t):
            return "en"
    return None


def prompt_hint(text: str, cfg=None) -> str:
    """非默认语言才挂一行提示；默认普通话不占每轮 token（短/长路径对称调用本函数）。"""
    pack = load()
    if cfg is None:
        from config import load as config_load
        cfg = config_load()
    from personalization_runtime import option
    default = option("language.default", pack.get("default", "zh-CN"), cfg=cfg)
    follow = option("language.follow", pack.get("match_user_language", True), cfg=cfg)
    names = {"en": "英语", "fr": "法语", "yue": "粤语", "zh-CN": "普通话"}
    supported = ((cfg.get("role_dossier") or {}).get("languages") or {}).get("speaks")
    lang = detect(text) if follow else None
    if lang and supported is not None and lang not in supported and names.get(lang) not in supported:
        lang = None
    if not lang and default in ("zh-CN", "普通话", None):
        return ""
    if not lang:
        return f"语言设置：本轮使用默认语言{names.get(default, default)}，短句自然表达。"
    return f"语言包：对方这条偏{names.get(lang, lang)} → 可用同语言短回一句，别炫技。"
