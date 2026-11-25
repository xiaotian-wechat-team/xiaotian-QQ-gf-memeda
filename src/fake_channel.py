"""In-memory channel for deterministic generic runtime acceptance tests."""
from __future__ import annotations

from dataclasses import dataclass

from turn_pipeline import InboundEvent, TurnPlan


@dataclass(frozen=True)
class FakeOutbound:
    event_id: str
    text: str


class FakeChannel:
    def __init__(self, binding):
        self.binding = binding
        self.outbound: list[FakeOutbound] = []
        self.acked: set[str] = set()

    def inbound(self, *, event_id: str, peer_id: str, text: str) -> InboundEvent:
        self.binding.authorize(channel=self.binding.deployment.channel, peer_id=peer_id)
        return InboundEvent(self.binding.scope, peer_id, text, event_id,
                            self.binding.deployment.channel)

    def send(self, event: InboundEvent, plan: TurnPlan) -> None:
        self.outbound.append(FakeOutbound(event.event_id, plan.reply or ""))

    def ack(self, event_id: str) -> None:
        if not any(item.event_id == event_id for item in self.outbound):
            raise ValueError("cannot ack unknown outbound event")
        self.acked.add(event_id)
