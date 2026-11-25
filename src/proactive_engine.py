"""Provider-driven proactive scheduler core."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProactiveDecision:
    status: str
    task: str | None = None
    reason: str = ""

    @classmethod
    def skip(cls, reason: str) -> "ProactiveDecision":
        return cls("skip", reason=reason)


class BeatProvider(Protocol):
    def next_beat(self, now: Any, context: dict[str, Any]) -> str | None: ...


class TopicProvider(Protocol):
    def topic(self, beat: str, context: dict[str, Any]) -> str | None: ...


class ProactiveGate(Protocol):
    def allow(self, now: Any, context: dict[str, Any]) -> tuple[bool, str]: ...


class ProactiveEngine:
    def __init__(self, *, beats: BeatProvider | None, topics: TopicProvider | None,
                 gate: ProactiveGate, enabled: bool = True):
        self.beats, self.topics, self.gate, self.enabled = beats, topics, gate, enabled

    def tick(self, now: Any, context: dict[str, Any] | None = None) -> ProactiveDecision:
        context = context or {}
        if not self.enabled:
            return ProactiveDecision.skip("capability disabled")
        allowed, reason = self.gate.allow(now, context)
        if not allowed:
            return ProactiveDecision.skip(reason or "gate rejected")
        if self.beats is None or self.topics is None:
            return ProactiveDecision.skip("beat or topic provider unavailable")
        beat = self.beats.next_beat(now, context)
        if not beat:
            return ProactiveDecision.skip("no beat")
        topic = self.topics.topic(beat, context)
        if not topic:
            return ProactiveDecision.skip("no topic")
        return ProactiveDecision("draft", task=f"{beat}: {topic}")
