"""Empty provider skeleton for jisoo until the user supplies facts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Unavailable:
    capability: str

    def available(self) -> bool:
        return False

    def reason(self) -> str:
        return f"{self.capability} provider has no confirmed persona data"


class JisooProviderSkeleton:
    """Explicit slots; intentionally contains no guessed identity facts."""
    chat = Unavailable("chat")
    memory = Unavailable("memory")
    emotion = Unavailable("emotion")
    schedule = Unavailable("schedule")
    campus = Unavailable("campus")
    image = Unavailable("image")
    voice = Unavailable("voice")
    proactive = Unavailable("proactive")
    reflection = Unavailable("reflection")
