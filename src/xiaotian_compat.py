"""Read-only compatibility view over the existing xiaotian config."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from persona_manifest import PersonaManifest


def manifest_from_legacy_config(config: dict[str, Any], *, dossier_path: str = "data/persona_dossier.json") -> PersonaManifest:
    """Build a schema-v2 view without mutating or persisting the legacy config."""
    contact = copy.deepcopy(config.get("contact") or {})
    persona = copy.deepcopy(config.get("persona") or {})
    if persona.get("id", "xiaotian") != "xiaotian":
        raise ValueError("legacy compatibility adapter only handles persona xiaotian")
    contact_id = str(contact.get("id") or persona.get("primary_contact_id") or "")
    if not contact_id:
        raise ValueError("legacy config has no primary contact")
    raw = {
        "schema_version": 2,
        "persona_id": "xiaotian",
        "persona_name": "小甜",
        "relationship_kind": "romantic_partner",
        "language_policy": {"default": "zh-CN", "supported": ["zh-CN", "en", "fr", "yue"]},
        "style_policy": {"source": "legacy brain and chat skill", "mode": "compatibility"},
        "memory_namespace": f"xiaotian_{contact_id}",
        "providers": {"chat": "legacy_brain", "memory": "legacy_memory", "emotion": "legacy_emotion",
                      "schedule": "legacy_schedule", "image": "legacy_imagegen", "voice": None,
                      "campus": "legacy_campus", "proactive": "legacy_proactive", "reflection": "legacy_reflect"},
        "capabilities": {"chat": True, "memory": True, "emotion": True, "schedule": True,
                          "campus": True, "image": True, "voice": False, "proactive": True, "reflection": True},
        "facts": {"dossier_path": dossier_path, "contact_id": contact_id,
                  "contact_name": contact.get("name"), "contact_gender": config.get("user_gender")},
        "routes": [{"channel": "ilink", "allow_user_ids": [contact_id]},
                   {"channel": "qq_onebot", "allow_user_ids": [contact_id]}]
    }
    return PersonaManifest.from_dict(raw)


def load_legacy_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
