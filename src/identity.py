"""Fixed persona/partner context and runtime paths for independent workers."""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _inside(path: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    path = path.expanduser().resolve()
    root = root.expanduser().resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"identity path escapes runtime root: {path}")
    return path


@dataclass(frozen=True)
class PartnerConfig:
    id: str
    name: str
    display_name: str
    address: str
    gender: str | None
    city: str | None = None
    occupation: str | None = None


@dataclass(frozen=True)
class RouteConfig:
    persona_id: str
    partner_id: str
    channel: str
    bot_account_id: str
    peer_id: str
    peer_type: str = "private"

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.persona_id, self.partner_id, self.channel,
                self.bot_account_id, self.peer_id)


@dataclass(frozen=True)
class RuntimePaths:
    root: pathlib.Path
    chat_db: pathlib.Path
    memory_root: pathlib.Path
    mem0_qdrant: pathlib.Path
    mem0_history: pathlib.Path
    llm_session: pathlib.Path
    llm_workspace: pathlib.Path
    codex_session: pathlib.Path
    schedule_overrides: pathlib.Path
    schedule_memory: pathlib.Path
    proactive_queue: pathlib.Path
    outbox_db: pathlib.Path
    receipts_db: pathlib.Path
    media_root: pathlib.Path
    generated_root: pathlib.Path

    @classmethod
    def from_root(cls, root: pathlib.Path, generated_root: pathlib.Path) -> "RuntimePaths":
        root = root.expanduser().resolve()
        runtime = root / "runtime"
        return cls(
            root=root,
            chat_db=runtime / "chat.db",
            memory_root=runtime / "memory",
            mem0_qdrant=runtime / "mem0_qdrant",
            mem0_history=runtime / "mem0_history.db",
            llm_session=runtime / "llm_session.json",
            llm_workspace=runtime / "llm_ws",
            codex_session=runtime / "codex_session.json",
            schedule_overrides=runtime / "schedule_overrides.json",
            schedule_memory=runtime / "schedule_memory.json",
            proactive_queue=runtime / "proactive_queue.json",
            outbox_db=runtime / "outbox.db",
            receipts_db=runtime / "channels" / "receipts.db",
            media_root=runtime / "media",
            generated_root=generated_root.expanduser().resolve(),
        )

    def validate(self) -> None:
        for field in self.__dataclass_fields__:
            if field in ("root", "generated_root"):
                continue
            _inside(getattr(self, field), self.root)
        _inside(self.generated_root, self.root)


@dataclass(frozen=True)
class IdentityConfig:
    persona_id: str
    name: str
    partner: PartnerConfig
    routes: tuple[RouteConfig, ...]
    runtime: RuntimePaths
    profile_dir: pathlib.Path
    runtime_enabled: bool
    capabilities: dict[str, bool]
    dossier: dict[str, Any]
    transport: dict[str, str] = field(default_factory=dict)

    def route(self, channel: str, bot_account_id: str, peer_id: str,
              peer_type: str = "private") -> RouteConfig | None:
        candidate = (str(channel), str(bot_account_id), str(peer_id), str(peer_type))
        for route in self.routes:
            if (route.channel, route.bot_account_id, route.peer_id, route.peer_type) == candidate:
                return route
        return None

    def scoped_config(self, base: dict[str, Any]) -> dict[str, Any]:
        cfg = copy.deepcopy(base)
        cfg["contact"] = {
            "id": self.partner.id,
            "name": self.partner.name,
            "nickname": self.partner.display_name,
            "remark": self.partner.display_name,
            "address": self.partner.address,
            "search_key": self.partner.display_name,
        }
        cfg["user_gender"] = self.partner.gender
        cfg["digital_life_dir"] = str(self.runtime.memory_root)
        cfg["db_path"] = str(self.runtime.chat_db)
        cfg["persona"] = {
            "id": self.persona_id,
            "primary_contact_id": self.partner.id,
            "contact_aliases": {
                f"{r.channel}:{r.bot_account_id}:{r.peer_id}": self.partner.id
                for r in self.routes
            },
            "channels": {"qq": {
                **{k: v for k, v in self.transport.items() if k in ("ws_uri", "http_api")},
                "provider": "napcat",
                "bot_account_id": self.routes[0].bot_account_id if self.routes else "",
                "allow_user_ids": [r.peer_id for r in self.routes if r.channel == "qq_onebot"],
                "allow_note": "exact persona/partner/channel/bot/peer routes only",
                "send_surface": "onebot",
            }},
        }
        memory = cfg.setdefault("memory", {})
        memory.update({
            "qdrant_path": str(self.runtime.mem0_qdrant),
            "history_db": str(self.runtime.mem0_history),
            "collection": f"{self.persona_id}_{self.partner.id}",
            "contacts": [self.partner.id],
        })
        llm = cfg.setdefault("llm", {})
        llm.update({
            "session_state": str(self.runtime.llm_session),
            "workspace": str(self.runtime.llm_workspace),
        })
        imagegen = cfg.setdefault("imagegen", {})
        imagegen.update({
            "session_state": str(self.runtime.codex_session),
            "out_dir": str(self.runtime.generated_root),
            "enabled": bool(self.capabilities.get("image_generate", False)),
            "emergency_fallback": False,
        })
        appearance = cfg.setdefault("appearance", {})
        appearance["profile"] = self.persona_id
        appearance["profile_dir"] = str(self.profile_dir)
        appearance["assets_dir"] = str(self.profile_dir.parent)
        mac = cfg.setdefault("mac", {})
        mac["shot_path"] = str(self.runtime.media_root / "mac_shot.png")
        avatar = cfg.setdefault("avatar", {})
        avatar["album_dir"] = str(self.runtime.root / "photos" / "album")
        avatar["queue_path"] = str(self.runtime.root / "runtime" / "avatar" / "queue.json")
        mood = avatar.setdefault("mood", {})
        mood["roll_state_path"] = str(self.runtime.root / "runtime" / "avatar" / "album_gate.json")
        cfg["outbox"] = {**(cfg.get("outbox") or {}), "db": str(self.runtime.outbox_db)}
        cfg["qq_receipts"] = {**(cfg.get("qq_receipts") or {}), "db": str(self.runtime.receipts_db)}
        cfg["image_caption"] = {**(cfg.get("image_caption") or {}), "cache_dir": str(self.runtime.media_root / "caption")}
        cfg["voice_asr"] = {
            **(cfg.get("voice_asr") or {}), "cache_dir": str(self.runtime.media_root / "asr"),
            "prefer_qq_stt": False, "qq_ws_uri": "", "qq_ws_token": "",
        }
        cfg["identity_runtime"] = {
            "persona_id": self.persona_id,
            "persona_name": self.name,
            "partner_id": self.partner.id,
            "runtime_root": str(self.runtime.root),
            "runtime_enabled": self.runtime_enabled,
            "capabilities": dict(self.capabilities),
            "proactive_queue": str(self.runtime.proactive_queue),
            "schedule_overrides": str(self.runtime.schedule_overrides),
            "schedule_memory": str(self.runtime.schedule_memory),
            "life_schedule": str(self.runtime.root / "runtime" / "life_schedule.json"),
            "schedule_override_pending": str(self.runtime.root / "runtime" / "schedule_override_pending.json"),
            "media_root": str(self.runtime.media_root),
        }
        from shared_schedule import settings
        shared = settings(cfg)
        if shared:
            cfg["identity_runtime"]["life_schedule"] = str(shared["path"])
        return cfg


def _base_dossier(root: pathlib.Path) -> dict[str, Any]:
    return json.loads((root / "data" / "persona_dossier.json").read_text(encoding="utf-8"))


def load_identity(persona_id: str, base: dict[str, Any], *,
                  root: pathlib.Path = ROOT) -> IdentityConfig:
    if os.environ.get("ROLE_REGISTRY_DIR"):
        from role_runtime import selected_identity
        return selected_identity(base, role_id=persona_id)
    if not _ID_RE.fullmatch(persona_id or ""):
        raise ValueError(f"invalid persona_id: {persona_id!r}")
    if persona_id == "xiaotian":
        raise ValueError("xiaotian uses its existing production config")
    identities_root = pathlib.Path(os.environ.get("XIAOTIAN_IDENTITIES_DIR") or root / "identities")
    identity_root = _inside(identities_root / persona_id, identities_root)
    identity_path = identity_root / "identity.json"
    partner_path = identity_root / "partner.json"
    raw = json.loads(identity_path.read_text(encoding="utf-8"))
    partner_raw = json.loads(partner_path.read_text(encoding="utf-8"))
    if raw.get("persona_id") != persona_id or partner_raw.get("id") != raw.get("partner_id"):
        raise ValueError("identity/partner configuration mismatch")
    partner = PartnerConfig(
        id=partner_raw["id"], name=partner_raw["name"],
        display_name=partner_raw.get("display_name") or partner_raw["name"],
        address=partner_raw.get("address") or partner_raw["name"],
        gender=partner_raw.get("gender"), city=partner_raw.get("city"),
        occupation=partner_raw.get("occupation"),
    )
    routes = tuple(RouteConfig(persona_id=persona_id, partner_id=partner.id, **r)
                   for r in raw.get("routes") or [])
    if not routes or any(r.key[:2] != (persona_id, partner.id) for r in routes):
        raise ValueError("identity requires an exact route")
    profile_dir = pathlib.Path(raw["profile_dir"]).expanduser().resolve()
    if profile_dir.name != persona_id or profile_dir == identity_root:
        raise ValueError("profile_dir must be a dedicated persona directory")
    runtime = RuntimePaths.from_root(identity_root, identity_root / "photos" / "generated")
    runtime.validate()
    dossier = _base_dossier(root)
    dossier["identity"] = {
        **dossier.get("identity", {}), "name": raw["name"],
        "role": f"{partner.name}的女朋友（恋人）",
    }
    dossier["user_ref"] = {
        "name": partner.name, "display_name": partner.display_name,
        "address": partner.address, "gender": partner.gender,
        "city": partner.city, "occupation": partner.occupation,
    }
    dossier["relationship_story"] = raw.get("relationship_story")
    dossier["appearance"] = {
        **(dossier.get("appearance") or {}),
        **(raw.get("appearance") or {}), "profile_dir": str(profile_dir),
    }
    return IdentityConfig(
        persona_id=persona_id, name=raw["name"], partner=partner, routes=routes,
        runtime=runtime, profile_dir=profile_dir,
        runtime_enabled=bool(raw.get("runtime_enabled")),
        capabilities=dict(raw.get("capabilities") or {}), dossier=dossier,
        transport=dict(raw.get("transport") or {}),
    )


def scoped_load(base: dict[str, Any], persona_id: str | None = None, *,
                root: pathlib.Path = ROOT) -> dict[str, Any]:
    if os.environ.get("ROLE_REGISTRY_DIR"):
        from role_runtime import selected_config
        return selected_config(role_id=persona_id)
    selected = persona_id if persona_id is not None else os.environ.get("XIAOTIAN_IDENTITY", "xiaotian")
    if selected == "xiaotian":
        return copy.deepcopy(base)
    return load_identity(selected, base, root=root).scoped_config(base)
