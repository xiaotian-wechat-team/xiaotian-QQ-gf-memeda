"""Validate capability declarations before a persona runtime is started."""
from __future__ import annotations

from persona_manifest import PersonaManifest

SLOTS = {
    "chat": "chat", "memory": "memory", "emotion": "emotion",
    "schedule": "schedule", "campus": "campus", "image": "image",
    "voice": "voice", "proactive": "proactive", "reflection": "reflection",
}


def validate_capabilities(manifest: PersonaManifest) -> None:
    errors = []
    values = manifest.providers.values
    caps = manifest.capabilities
    for capability, slot in SLOTS.items():
        enabled = getattr(caps, capability)
        provider = values.get(slot)
        if enabled and provider is None:
            errors.append(f"{capability}: enabled but provider is null")
        if not enabled and provider is not None:
            errors.append(f"{capability}: disabled but provider {provider!r} is configured")
    if errors:
        raise ValueError("capability/provider mismatch: " + "; ".join(errors))
