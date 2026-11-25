"""Explicit Codex-quota adapter for the location Agent; no provider fallback.

Persona facts: data/persona_dossier.json. Credentials use the existing Magpie
auth helper, while messages and usage belong to this independent Agent.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class MagpieCodex:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.model = self.settings.get("model", "codex/gpt-6.1-sol")
        if not self.model.startswith("codex/"):
            raise ValueError("location Agent requires an explicit codex/ model")
        self.calls = []

    def complete(self, messages, tools):
        from llm_adapters.grok import api_key
        key = api_key()
        if not key:
            raise RuntimeError("Magpie authorization unavailable")
        base = self.settings.get("base_url", "http://127.0.0.1:3425/v1").rstrip("/")
        body = dict(model=self.model, messages=messages, tools=tools, stream=False,
                    reasoning_effort=self.settings.get("effort", "medium"),
                    max_tokens=6000)
        request = urllib.request.Request(base + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.settings.get("timeout", 90)) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            # Provider error bodies can contain account details. Keep them out of UI/logs.
            raise RuntimeError(f"Magpie Codex HTTP {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Magpie Codex unavailable ({type(exc).__name__})") from None
        returned_model = value.get("model", "")
        if not returned_model.startswith("codex/"):
            raise RuntimeError("Magpie returned a non-Codex provider")
        self.calls.append({"model": returned_model, "seconds": round(time.monotonic() - started, 3),
                           "usage": value.get("usage")})
        try:
            message = value["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError()
            return message
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Invalid Magpie Codex response") from None
