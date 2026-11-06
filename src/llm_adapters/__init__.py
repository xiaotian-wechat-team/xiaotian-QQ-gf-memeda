"""Short-path chat adapters (DeepSeek / Grok / Kimi / Doubao).

brain / llm.pick_backend talk to get_adapter(name); each adapter exposes
available() + complete(messages).
"""
from __future__ import annotations

def get_adapter(name: str):
    n = (name or "").strip().lower()
    if n in ("deepseek", "ds"):
        from llm_adapters import deepseek as m
        return m
    if n in ("grok", "grok46", "cursor_grok", "cursor-grok"):
        from llm_adapters import grok as m
        return m
    if n in ("kimi", "moonshot"):
        from llm_adapters import kimi as m
        return m
    if n in ("doubao", "ark", "volc", "volcengine", "豆包"):
        from llm_adapters import doubao as m
        return m
    raise KeyError(f"unknown short adapter: {name!r}")


def short_adapter_names():
    return ("deepseek", "grok", "kimi", "doubao")


def is_short_adapter(name: str) -> bool:
    try:
        get_adapter(name)
        return True
    except KeyError:
        return False
