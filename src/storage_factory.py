"""Filesystem artifact paths derived solely from RuntimeScope."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from persona_manifest import RuntimeScope


@dataclass(frozen=True)
class StoragePaths:
    chat_db: Path
    state_dir: Path
    qdrant_dir: Path
    outbox_db: Path
    media_dir: Path
    proactive_queue: Path

    @classmethod
    def from_scope(cls, scope: RuntimeScope) -> "StoragePaths":
        return cls(scope.path("chat.db"), scope.path("state"), scope.path("qdrant"),
                   scope.path("outbox.db"), scope.path("media"), scope.path("proactive_queue.json"))

    def create_dirs(self) -> None:
        for directory in (self.chat_db.parent, self.state_dir, self.qdrant_dir,
                          self.outbox_db.parent, self.media_dir, self.proactive_queue.parent):
            directory.mkdir(parents=True, exist_ok=True)
