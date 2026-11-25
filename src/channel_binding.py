"""Small adapter between deployment profiles and generic runtime scopes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from persona_manifest import DeploymentProfile, PersonaManifest, RuntimeScope


@dataclass(frozen=True)
class ChannelBinding:
    deployment: DeploymentProfile
    scope: RuntimeScope

    @classmethod
    def create(cls, deployment: DeploymentProfile, manifest: PersonaManifest,
               runtime_root: str | Path, *, environ: dict[str, str] | None = None) -> "ChannelBinding":
        resolved = deployment.resolve_environment(environ)
        resolved.bind_manifest(manifest)
        scope = RuntimeScope.generic(manifest.persona_id, resolved.peer_id,
                                     resolved.channel, resolved.peer_id, runtime_root)
        return cls(resolved, scope)

    def authorize(self, *, channel: str, peer_id: str, peer_type: str = "private") -> None:
        if channel != self.deployment.channel or peer_id != self.deployment.peer_id or peer_type != self.deployment.peer_type:
            raise PermissionError("inbound route is outside exact deployment binding")
        self.scope.assert_peer(peer_id)
