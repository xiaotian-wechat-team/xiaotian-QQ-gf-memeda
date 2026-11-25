"""Provider contracts and registry for replaceable persona packages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from persona_manifest import PersonaManifest


class ChatProvider(Protocol):
    def build_context(self, event: Any) -> dict[str, Any]: ...
    def plan_reply(self, event: Any, context: dict[str, Any]) -> Any: ...


class ScheduleProvider(Protocol):
    def current(self, now: Any) -> dict[str, Any] | None: ...


class EmotionProvider(Protocol):
    def appraise(self, text: str, state: dict[str, Any]) -> dict[str, Any]: ...


class OptionalProvider(Protocol):
    def available(self) -> bool: ...


@dataclass(frozen=True)
class ProviderBundle:
    chat: ChatProvider | None = None
    schedule: ScheduleProvider | None = None
    emotion: EmotionProvider | None = None
    campus: OptionalProvider | None = None
    image: OptionalProvider | None = None
    voice: OptionalProvider | None = None
    proactive: OptionalProvider | None = None
    reflection: OptionalProvider | None = None


class ProviderRegistry:
    """Resolve providers only from an explicit manifest mapping."""

    def __init__(self):
        self._factories: dict[str, Any] = {}

    def register(self, provider_id: str, factory: Any) -> None:
        if not provider_id or provider_id in self._factories:
            raise ValueError(f"duplicate or empty provider id: {provider_id!r}")
        self._factories[provider_id] = factory

    def build(self, manifest: PersonaManifest) -> ProviderBundle:
        values = manifest.providers.values
        def make(slot: str):
            provider_id = values.get(slot)
            if provider_id is None:
                return None
            factory = self._factories.get(provider_id)
            if factory is None:
                raise KeyError(f"provider {provider_id!r} for slot {slot!r} is not registered")
            return factory(manifest)
        return ProviderBundle(chat=make("chat"), schedule=make("schedule"),
                              emotion=make("emotion"), campus=make("campus"),
                              image=make("image"), voice=make("voice"),
                              proactive=make("proactive"), reflection=make("reflection"))


class NullProvider:
    def available(self) -> bool:
        return False


def register_manifest_compat(registry: ProviderRegistry, *, legacy_factories: dict[str, Any]) -> None:
    """Register legacy implementations explicitly, without importing them here."""
    for provider_id, factory in legacy_factories.items():
        registry.register(provider_id, factory)
