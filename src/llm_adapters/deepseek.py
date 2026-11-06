"""DeepSeek OpenAI-compatible chat completions adapter."""
from __future__ import annotations
import json, os, pathlib, urllib.error, urllib.request
from config import ROOT, load

NAME = "deepseek"
ENV_FILE = ROOT / "config" / "deepseek.env"
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
        if k in ("DEEPSEEK_API_KEY", "KEY", "API_KEY") and v:
            return v
    return None


def api_key(force_reload: bool = False) -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE is not None and not force_reload:
        return _KEY_CACHE or None
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
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
