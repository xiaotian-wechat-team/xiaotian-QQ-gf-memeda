"""Generic media contracts with explicit unavailable results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class MediaRequest:
    text: str
    explicit: bool = False
    max_refs: int = 5


@dataclass(frozen=True)
class MediaResult:
    status: str
    paths: tuple[str, ...] = ()
    reason: str = ""

    @classmethod
    def unavailable(cls, reason: str) -> "MediaResult":
        return cls("unavailable", reason=reason)


class ImageIntentParser(Protocol):
    def parse(self, text: str) -> MediaRequest | None: ...


class ReferenceProvider(Protocol):
    def references(self, request: MediaRequest) -> Sequence[str]: ...


class ImageGenerator(Protocol):
    def generate(self, request: MediaRequest, references: Sequence[str]) -> MediaResult: ...


class VoiceProvider(Protocol):
    def synthesize(self, text: str) -> MediaResult: ...


class MediaPlanner:
    def __init__(self, *, parser: ImageIntentParser, refs: ReferenceProvider,
                 generator: ImageGenerator | None, max_refs: int = 5):
        self.parser, self.refs, self.generator = parser, refs, generator
        self.max_refs = max_refs

    def plan_image(self, text: str) -> MediaResult:
        request = self.parser.parse(text)
        if request is None or not request.explicit:
            return MediaResult.unavailable("no explicit image request")
        if self.generator is None:
            return MediaResult.unavailable("image capability disabled")
        refs = tuple(self.refs.references(request))[:min(request.max_refs, self.max_refs)]
        return self.generator.generate(request, refs)


class NullReferenceProvider:
    def references(self, request: MediaRequest) -> Sequence[str]:
        return ()


class UnavailableImageGenerator:
    def generate(self, request: MediaRequest, references: Sequence[str]) -> MediaResult:
        return MediaResult.unavailable("image provider unavailable")
