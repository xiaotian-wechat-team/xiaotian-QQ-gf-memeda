"""Generic persona and deployment contracts (schema v2).

This module contains data-only contracts.  It deliberately does not import
brain, channels, or any persona-specific provider, so a new character can be
added without changing the production transport path.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{path}: expected stable id")
    return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


@dataclass(frozen=True)
class CapabilitySet:
    chat: bool = True
    memory: bool = True
    emotion: bool = True
    schedule: bool = True
    campus: bool = False
    image: bool = False
    voice: bool = False
    proactive: bool = False
    reflection: bool = True

    @classmethod
    def from_dict(cls, raw: Any, path: str = "capabilities") -> "CapabilitySet":
        obj = _mapping(raw, path)
        fields = cls.__dataclass_fields__
        unknown = set(obj) - set(fields)
        if unknown:
            raise ValueError(f"{path}: unknown fields {sorted(unknown)}")
        vals = {}
        for name in fields:
            value = obj.get(name, getattr(cls, name))
            if not isinstance(value, bool):
                raise ValueError(f"{path}.{name}: expected boolean")
            vals[name] = value
        return cls(**vals)


@dataclass(frozen=True)
class RouteRule:
    channel: str
    peer_type: str = "private"
    allow_user_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Any, path: str) -> "RouteRule":
        obj = _mapping(raw, path)
        channel = obj.get("channel")
        if not isinstance(channel, str) or not channel:
            raise ValueError(f"{path}.channel: required")
        peers = obj.get("allow_user_ids", [])
        if not isinstance(peers, list) or any(not isinstance(p, str) or not p for p in peers):
            raise ValueError(f"{path}.allow_user_ids: expected string array")
        return cls(channel, obj.get("peer_type", "private"), tuple(peers))


@dataclass(frozen=True)
class ProviderRegistry:
    values: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any, path: str = "providers") -> "ProviderRegistry":
        obj = _mapping(raw, path)
        for key, value in obj.items():
            _id(key, f"{path}.{key}")
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{path}.{key}: expected provider id or null")
        return cls(dict(obj))


@dataclass(frozen=True)
class PersonaManifest:
    persona_id: str
    persona_name: str
    relationship_kind: str | None
    language_policy: dict[str, Any]
    style_policy: dict[str, Any]
    memory_namespace: str
    providers: ProviderRegistry
    capabilities: CapabilitySet
    facts: dict[str, Any]
    routes: tuple[RouteRule, ...] = ()
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, raw: Any) -> "PersonaManifest":
        obj = _mapping(raw, "manifest")
        if obj.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("manifest.schema_version: expected 2")
        pid = _id(obj.get("persona_id"), "manifest.persona_id")
        name = obj.get("persona_name")
        if not isinstance(name, str) or not name:
            raise ValueError("manifest.persona_name: required")
        namespace = _id(obj.get("memory_namespace", pid), "manifest.memory_namespace")
        facts = _mapping(obj.get("facts", {}), "manifest.facts")
        routes = tuple(RouteRule.from_dict(v, f"manifest.routes[{i}]") for i, v in enumerate(obj.get("routes", [])))
        return cls(pid, name, obj.get("relationship_kind"),
                   _mapping(obj.get("language_policy", {}), "manifest.language_policy"),
                   _mapping(obj.get("style_policy", {}), "manifest.style_policy"), namespace,
                   ProviderRegistry.from_dict(obj.get("providers", {})),
                   CapabilitySet.from_dict(obj.get("capabilities", {})), facts, routes)


@dataclass(frozen=True)
class DeploymentProfile:
    deployment_id: str
    environment: str
    persona_id: str
    channel: str
    bot_account_id: str
    peer_id: str
    peer_type: str = "private"
    endpoints: dict[str, str] = field(default_factory=dict)
    token_env: str | None = None
    launchd_label: str | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> "DeploymentProfile":
        obj = _mapping(raw, "deployment")
        did = _id(obj.get("deployment_id"), "deployment.deployment_id")
        pid = _id(obj.get("persona_id"), "deployment.persona_id")
        required = ("environment", "channel", "bot_account_id", "peer_id")
        for key in required:
            if not isinstance(obj.get(key), str) or not obj[key]:
                raise ValueError(f"deployment.{key}: required")
        endpoints = _mapping(obj.get("endpoints", {}), "deployment.endpoints")
        if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in endpoints.items()):
            raise ValueError("deployment.endpoints: values must be non-empty strings")
        return cls(did, obj["environment"], pid, obj["channel"], obj["bot_account_id"], obj["peer_id"],
                   obj.get("peer_type", "private"), dict(endpoints), obj.get("token_env"), obj.get("launchd_label"))

    def resolve_environment(self, environ: dict[str, str] | None = None) -> "DeploymentProfile":
        """Resolve ``${VAR}`` placeholders without storing credentials in manifests."""
        env = os.environ if environ is None else environ
        def resolve(value: str) -> str:
            if value.startswith("${") and value.endswith("}"):
                key = value[2:-1]
                if key not in env or not env[key]:
                    raise ValueError(f"deployment environment variable is missing: {key}")
                return env[key]
            return value
        return DeploymentProfile(self.deployment_id, self.environment, self.persona_id,
                                 self.channel, resolve(self.bot_account_id), resolve(self.peer_id),
                                 self.peer_type, {k: resolve(v) for k, v in self.endpoints.items()},
                                 self.token_env, self.launchd_label)

    def bind_manifest(self, manifest: PersonaManifest) -> None:
        if manifest.persona_id != self.persona_id:
            raise ValueError(f"deployment persona {self.persona_id!r} does not match manifest {manifest.persona_id!r}")
        if not any(route.channel == self.channel and route.peer_type == self.peer_type
                   and self.peer_id in route.allow_user_ids for route in manifest.routes):
            raise PermissionError("deployment peer is not explicitly allowed by persona manifest")


@dataclass(frozen=True)
class RuntimeScope:
    persona_id: str
    contact_id: str
    channel: str
    peer_id: str
    runtime_root: Path

    @classmethod
    def generic(cls, persona_id: str, contact_id: str, channel: str,
                peer_id: str, runtime_root: str | Path) -> "RuntimeScope":
        """Create an isolated namespace for a new persona."""
        _id(persona_id, "runtime.persona_id")
        if not contact_id or not channel or not peer_id:
            raise ValueError("runtime contact, channel and peer are required")
        return cls(persona_id, str(contact_id), str(channel), str(peer_id), Path(runtime_root))

    @classmethod
    def xiaotian_legacy(cls, repo_root: str | Path, digital_life: str | Path,
                        contact_id: str = "demo_contact", channel: str = "legacy",
                        peer_id: str = "demo_contact") -> "RuntimeScope":
        """Explicit compatibility mapping; never used for other personas."""
        if str(contact_id) != "demo_contact":
            raise PermissionError("xiaotian legacy scope only permits contact demo_contact")
        root = Path(repo_root).expanduser().resolve()
        scope = cls("xiaotian", contact_id, channel, peer_id, root / ".legacy-runtime")
        object.__setattr__(scope, "_legacy_paths", {
            "chat.db": root / "data" / "chat.db",
            "state": Path(digital_life).expanduser().resolve() / "contacts" / contact_id,
        })
        return scope

    def path(self, name: str) -> Path:
        if name not in {"chat.db", "state", "qdrant", "outbox.db", "media", "proactive_queue.json"}:
            raise ValueError(f"unsupported runtime artifact: {name}")
        legacy = getattr(self, "_legacy_paths", {})
        if name in legacy:
            return legacy[name]
        return self.runtime_root.expanduser().resolve() / self.persona_id / name

    def assert_peer(self, peer_id: str) -> None:
        if str(peer_id) != self.peer_id:
            raise PermissionError(f"peer {peer_id!r} is outside runtime scope")

    @property
    def memory_namespace(self) -> str:
        return f"{self.persona_id}:{self.contact_id}"


def load_manifest(path: str | Path) -> PersonaManifest:
    return PersonaManifest.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_deployment(path: str | Path) -> DeploymentProfile:
    return DeploymentProfile.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
