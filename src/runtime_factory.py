"""Build an isolated persona runtime without starting transports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from capability_guard import validate_capabilities
from channel_binding import ChannelBinding
from persona_manifest import load_deployment, load_manifest
from persona_providers import ProviderBundle, ProviderRegistry
from storage_factory import StoragePaths


@dataclass(frozen=True)
class PersonaRuntime:
    binding: ChannelBinding
    providers: ProviderBundle
    storage: StoragePaths


def build_runtime(*, manifest_path: str | Path, deployment_path: str | Path,
                  runtime_root: str | Path, provider_registry: ProviderRegistry,
                  environ: dict[str, str] | None = None) -> PersonaRuntime:
    manifest = load_manifest(manifest_path)
    validate_capabilities(manifest)
    deployment = load_deployment(deployment_path)
    binding = ChannelBinding.create(deployment, manifest, runtime_root, environ=environ)
    providers = provider_registry.build(manifest)
    storage = StoragePaths.from_scope(binding.scope)
    storage.create_dirs()
    return PersonaRuntime(binding, providers, storage)
