"""Compatibility provider map for the existing xiaotian implementation."""
from __future__ import annotations


LEGACY_PROVIDER_IDS = {
    "chat": "legacy_brain",
    "memory": "legacy_memory",
    "emotion": "legacy_emotion",
    "schedule": "legacy_schedule",
    "image": "legacy_imagegen",
    "voice": None,
    "campus": "legacy_campus",
    "proactive": "legacy_proactive",
    "reflection": "legacy_reflect",
}


def provider_ids() -> dict[str, str | None]:
    """Return a copy so callers cannot mutate the compatibility contract."""
    return dict(LEGACY_PROVIDER_IDS)
