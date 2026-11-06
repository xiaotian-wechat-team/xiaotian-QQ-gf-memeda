"""Compat shim: DeepSeek short adapter + shared compact skill body.

Prefer llm_adapters.deepseek for new code. skill_system() stays here so both
DeepSeek and Grok short paths share SKILL.deepseek.md.
"""
from __future__ import annotations
import json, os, pathlib, urllib.error, urllib.request
import urllib  # tests monkeypatch llm_deepseek.urllib.request
from config import ROOT, load
from llm_adapters import deepseek as _ad

API_URL = "https://api.deepseek.com/chat/completions"
ENV_FILE = ROOT / "config" / "deepseek.env"
SKILL_PATH = ROOT / ".cursor" / "skills" / "xiaotian-chat" / "SKILL.md"
SKILL_DEEPSEEK = ROOT / ".cursor" / "skills" / "xiaotian-chat" / "SKILL.deepseek.md"
_SKILL_CACHE = None


def api_key(force_reload: bool = False):
    _ad.ENV_FILE = ENV_FILE
    return _ad.api_key(force_reload=force_reload)


def clear_key_cache():
    _ad.clear_key_cache()


def available() -> bool:
    _ad.ENV_FILE = ENV_FILE
    return _ad.available()


def complete(messages, *, model=None, timeout=None, temperature=0.7) -> str:
    """POST via adapter; use this module's urllib so tests can monkeypatch."""
    _ad.ENV_FILE = ENV_FILE
    llm = load().get("llm") or {}
    model = model or llm.get("deepseek_model") or llm.get("short_model") or "deepseek-chat"
    timeout = timeout or llm.get("short_timeout") or llm.get("deepseek_timeout") or 30
    base = (llm.get("deepseek_base") or "https://api.deepseek.com").rstrip("/")
    url = f"{base}/chat/completions"
    key = api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY missing (env or config/deepseek.env)")
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"deepseek HTTP {e.code}: {err}") from None
    except Exception as e:
        raise RuntimeError(f"deepseek request failed: {type(e).__name__}") from e
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("deepseek bad response shape") from e


def skill_system() -> str:
    global _SKILL_CACHE
    if _SKILL_CACHE is not None:
        return _SKILL_CACHE
    path = SKILL_DEEPSEEK if SKILL_DEEPSEEK.is_file() else SKILL_PATH
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        body = parts[2] if len(parts) >= 3 else raw
    else:
        body = raw
    _SKILL_CACHE = body.strip()
    return _SKILL_CACHE
