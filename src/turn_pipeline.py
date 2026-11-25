"""Transport-neutral turn pipeline.

The pipeline is intentionally provider based.  It can run in ``shadow`` mode
to produce a trace without sending or persisting anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class InboundEvent:
    scope: Any
    sender_id: str
    text: str
    event_id: str
    channel: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnPlan:
    reply: str | None
    media: tuple[str, ...] = ()
    should_send: bool = True
    reason: str = ""


class DedupStore(Protocol):
    def seen(self, event: InboundEvent) -> bool: ...


class PersonaPolicy(Protocol):
    def plan(self, event: InboundEvent, context: dict[str, Any]) -> TurnPlan: ...


class Persistence(Protocol):
    def commit(self, event: InboundEvent, plan: TurnPlan) -> None: ...


class Delivery(Protocol):
    def send(self, event: InboundEvent, plan: TurnPlan) -> None: ...


class TraceRecorder(Protocol):
    def record(self, stage: str, values: dict[str, Any]) -> None: ...


class TurnPipeline:
    def __init__(self, *, dedup: DedupStore, policy: PersonaPolicy,
                 persistence: Persistence, delivery: Delivery,
                 context_builder=None, mode: str = "legacy"):
        if mode not in {"legacy", "shadow", "generic"}:
            raise ValueError("mode must be legacy, shadow, or generic")
        self.dedup, self.policy = dedup, policy
        self.persistence, self.delivery = persistence, delivery
        self.context_builder = context_builder or (lambda event: {})
        self.mode = mode
        self.trace: TraceRecorder | None = None

    def with_trace(self, recorder: TraceRecorder) -> "TurnPipeline":
        self.trace = recorder
        return self

    def _record(self, stage: str, **values: Any) -> None:
        if self.trace is not None:
            self.trace.record(stage, values)

    def run(self, event: InboundEvent) -> TurnPlan:
        event.scope.assert_peer(event.sender_id)
        self._record("route", channel=event.channel, sender_id=event.sender_id)
        if self.dedup.seen(event):
            self._record("dedup", result="duplicate", event_id=event.event_id)
            return TurnPlan(None, should_send=False, reason="duplicate")
        self._record("dedup", result="new", event_id=event.event_id)
        context = self.context_builder(event)
        plan = self.policy.plan(event, context)
        self._record("policy", reply=plan.reply, media=list(plan.media), reason=plan.reason)
        if self.mode == "shadow":
            self._record("shadow", send=False, persist=False)
            return plan
        if plan.should_send and plan.reply:
            self.delivery.send(event, plan)
            self._record("delivery", sent=True)
        self.persistence.commit(event, plan)
        self._record("persistence", committed=True)
        return plan
