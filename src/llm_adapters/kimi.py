"""Kimi (Moonshot coding) OpenAI-compatible chat completions adapter."""
from __future__ import annotations
import json, os, pathlib, urllib.error, urllib.request
from config import ROOT, load

NAME = "kimi"
ENV_FILE = ROOT / "config" / "kimi.env"
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
        if k in ("KIMI_API_KEY", "MOONSHOT_API_KEY", "KEY", "API_KEY") and v:
            return v
    return None


def api_key(force_reload: bool = False) -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE is not None and not force_reload:
        return _KEY_CACHE or None
    key = (os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY") or "").strip()
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
    # brain passes generic short_model; only honour kimi-* models
    if not (model and str(model).startswith("kimi")):
        model = llm.get("kimi_model") or "kimi-k2.7-code-highspeed"
    timeout = llm.get("kimi_timeout") or timeout or llm.get("short_timeout") or 30
    base = (llm.get("kimi_base") or "https://api.kimi.com/coding/v1").rstrip("/")
    effort = llm.get("kimi_effort") or "low"
    url = f"{base}/chat/completions"
    key = api_key()
    if not key:
        raise RuntimeError("KIMI_API_KEY missing (env or config/kimi.env)")
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 1,  # model only accepts 1
        "stream": False,
        "reasoning_effort": effort,
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
        raise RuntimeError(f"kimi HTTP {e.code}: {err}") from None
    except Exception as e:
        raise RuntimeError(f"kimi request failed: {type(e).__name__}") from e
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("kimi bad response shape") from e
