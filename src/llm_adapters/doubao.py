"""Doubao (Volcengine Ark) OpenAI-compatible chat completions adapter.

Ordinary Ark API Key + dialogue models (e.g. doubao-seed-*-lite), NOT Coding Plan.
Key: env ARK_API_KEY / DOUBAO_API_KEY, or config/doubao.env.
"""
from __future__ import annotations
import json, os, pathlib, urllib.error, urllib.request
from config import ROOT, load

NAME = "doubao"
ENV_FILE = ROOT / "config" / "doubao.env"
_KEY_CACHE = None


def _parse_env_file(path: pathlib.Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in ("ARK_API_KEY", "DOUBAO_API_KEY", "VOLC_ARK_API_KEY", "KEY", "API_KEY") and v:
            return v
    return None


def api_key(force_reload: bool = False) -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE is not None and not force_reload:
        return _KEY_CACHE or None
    key = (
        os.environ.get("ARK_API_KEY")
        or os.environ.get("DOUBAO_API_KEY")
        or os.environ.get("VOLC_ARK_API_KEY")
        or ""
    ).strip()
    if not key:
        key = (_parse_env_file(ENV_FILE) or "").strip()
    _KEY_CACHE = key
    return key or None


def clear_key_cache():
    global _KEY_CACHE
    _KEY_CACHE = None


def available() -> bool:
    return bool(api_key())


def complete(messages, *, model=None, timeout=None, temperature=0.7) -> str:
    llm = load().get("llm") or {}
    # honour doubao-* / ep-* only; else use configured doubao_model
    m = str(model or "")
    if not (m.startswith("doubao") or m.startswith("ep-")):
        model = llm.get("doubao_model") or "doubao-seed-2-1-lite-260915"
    else:
        model = m
    timeout = llm.get("doubao_timeout") or timeout or llm.get("short_timeout") or 30
    base = (llm.get("doubao_base") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    url = f"{base}/chat/completions"
    key = api_key()
    if not key:
        raise RuntimeError("ARK_API_KEY missing (env or config/doubao.env)")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    # thinking off + minimal effort (2026-10-24 latency notes)
    if llm.get("doubao_thinking", False) is False:
        payload["thinking"] = {"type": "disabled"}
    effort = llm.get("doubao_reasoning_effort") or "minimal"
    if effort:
        payload["reasoning_effort"] = effort
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"doubao HTTP {e.code}: {err}") from None
    except Exception as e:
        raise RuntimeError(f"doubao request failed: {type(e).__name__}") from e
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("doubao bad response shape") from e
