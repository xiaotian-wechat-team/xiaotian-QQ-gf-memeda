"""Runtime mode selection with legacy as the safe default."""
from __future__ import annotations

import os


MODES = {"legacy", "shadow", "generic"}


def selected_mode(config: dict | None = None, environ: dict[str, str] | None = None) -> str:
    config = config or {}
    env = os.environ if environ is None else environ
    value = env.get("PERSONA_RUNTIME_MODE") or config.get("runtime_mode") or "legacy"
    if value not in MODES:
        raise ValueError(f"unsupported persona runtime mode: {value!r}")
    return value


def can_send(mode: str) -> bool:
    if mode not in MODES:
        raise ValueError(f"unsupported persona runtime mode: {mode!r}")
    return mode != "shadow"
