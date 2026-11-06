"""Grok short-path via Magpie gateway (Cursor subscription), not Cursor desktop/CLI.

Uses local magpie OpenAI-compatible /v1/chat/completions (default 127.0.0.1:3425).
Auth: MAGPIE_API_KEY / config/magpie.env / ~/.config/magpie/settings.json lanKey.
Model default: cursor/grok-4.6 with effort=low.
"""
from __future__ import annotations
import json, os, pathlib, re, urllib.error, urllib.request
from config import ROOT, load

NAME = "grok"
DEFAULT_MODEL = "cursor/grok-4.6"
DEFAULT_BASE = "http://127.0.0.1:3425/v1"
ENV_FILE = ROOT / "config" / "magpie.env"
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
        if k in ("MAGPIE_API_KEY", "MAGPIE_LAN_KEY", "KEY", "API_KEY") and v:
            return v
    return None


def _lan_key_from_settings() -> str | None:
    p = pathlib.Path.home() / ".config" / "magpie" / "settings.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    key = (data.get("lanKey") or "").strip()
    return key or None


def api_key(force_reload: bool = False) -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE is not None and not force_reload:
        return _KEY_CACHE or None
    key = (os.environ.get("MAGPIE_API_KEY") or os.environ.get("MAGPIE_LAN_KEY") or "").strip()
    if not key:
        key = (_parse_env_file(ENV_FILE) or "").strip()
    if not key:
        key = (_lan_key_from_settings() or "").strip()
    _KEY_CACHE = key
    return key or None


def clear_key_cache():
    global _KEY_CACHE
    _KEY_CACHE = None


def available() -> bool:
    if not api_key():
        return False
    llm = load().get("llm") or {}
    base = (llm.get("magpie_base") or DEFAULT_BASE).rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key()}"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def complete(messages, *, model=None, timeout=None, temperature=0.7) -> str:
    llm = load().get("llm") or {}
    model = model or llm.get("grok_model") or (llm.get("short_model") if "grok" in str(llm.get("short_backend") or "") else None) or DEFAULT_MODEL
    timeout = timeout or llm.get("short_timeout") or llm.get("grok_timeout") or 45
    base = (llm.get("magpie_base") or DEFAULT_BASE).rstrip("/")
    url = f"{base}/chat/completions"
    key = api_key()
    if not key:
        raise RuntimeError("MAGPIE_API_KEY missing (env, config/magpie.env, or magpie lanKey)")
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    effort = llm.get("grok_effort")
    if effort is None:
        effort = "low"
    if effort:
        body["effort"] = effort
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"magpie/grok HTTP {e.code}: {err}") from None
    except Exception as e:
        raise RuntimeError(f"magpie/grok request failed: {type(e).__name__}") from e
    try:
        text = (payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("magpie/grok bad response shape") from e
    text = re.sub(r"\\confidence\{[^}]*\}", "", text)
    return text.strip()
