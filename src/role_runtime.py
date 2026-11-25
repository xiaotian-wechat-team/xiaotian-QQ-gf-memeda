"""Bind a registered role and relationship to one fixed worker context."""
import copy
import json
import os
from pathlib import Path

from role_registry import Registry, identifier


def deployment_grants(role_id, relation_id, environment):
    path = os.environ.get("ROLE_GRANTS_FILE")
    if not path:
        return {"runtime_enabled": False, "capabilities": {"dry_run": True}}
    source = Path(path).expanduser()
    if source.is_symlink() or source.stat().st_mode & 0o022:
        raise PermissionError("deployment grants must not be symlinked or group/world writable")
    grants = json.loads(source.read_text())
    expected = {"role_id": role_id, "relation_id": relation_id, "environment": environment}
    if any(grants.get(k) != v for k, v in expected.items()):
        raise PermissionError("deployment grants belong to a different context")
    capabilities = grants.get("capabilities") or {}
    known = {"dry_run", "inbound", "manual_send", "auto_reply", "image_generate", "image_send", "image_caption", "voice_transcribe", "proactive", "avatar_update", "reflection"}
    if any(k not in known or type(v) is not bool for k, v in capabilities.items()):
        raise ValueError("invalid deployment capabilities")
    return grants


def selected_config(*, role_id=None):
    """Do not merge a new role with the legacy production configuration."""
    identity = selected_identity({}, role_id=role_id)
    role = Registry.read(os.environ["ROLE_REGISTRY_DIR"], os.environ["ROLE_ENVIRONMENT"]).role(identity.persona_id)
    settings = role.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("role requires explicit settings; legacy defaults are forbidden")
    cfg = identity.scoped_config(settings)
    cfg["identity_runtime"].update(environment=os.environ["ROLE_ENVIRONMENT"], schema_version=2)
    cfg["identity_runtime"]["routes"] = [
        {"channel": r.channel, "bot_account_id": r.bot_account_id,
         "peer_id": r.peer_id, "peer_type": r.peer_type}
        for r in identity.routes
    ]
    cfg["role_workflows"] = copy.deepcopy(role["workflows"])
    cfg["role_dossier"] = copy.deepcopy(identity.dossier)
    cfg["role_style"] = str(role.get("style") or "遵循当前角色档案和关系类型；未记录的事实不编造。")
    cfg["relationship_kind"] = role["relationships"][identity.partner.id]["kind"]
    p = role.get("personalization") or {}
    p = copy.deepcopy(p["relationships"].get(identity.partner.id, {}) if "relationships" in p else p)
    if p.get("release_version"):
        from personalization_schema import project, role_values
        from personalization_runtime import style_prompt
        from personalization_store import _merge
        p["values"] = role_values(role, identity.partner.id)
        _merge(cfg, p.get("settings") or project(p["values"])["settings"])
        p.update(version=p["release_version"], role_id=identity.persona_id, relation_id=identity.partner.id, environment=os.environ["ROLE_ENVIRONMENT"])
        cfg["personalization"] = p
        cfg["persona_version"] = p["version"]
        cfg["role_style"] = style_prompt(project(p["values"]))
        session = Path(cfg["llm"]["session_state"])
        cfg["llm"]["session_state"] = str(session.with_name(session.stem + "." + p["version"] + session.suffix))
        cfg["llm"]["workspace"] = str(Path(cfg["llm"]["workspace"]) / p["version"])
    grants = deployment_grants(identity.persona_id, identity.partner.id, os.environ["ROLE_ENVIRONMENT"])
    cfg["native_service"] = copy.deepcopy(grants.get("native_service") or {})
    from runtime_policy import workflow_enabled
    cfg["imagegen"]["enabled"] = cfg["imagegen"]["enabled"] and workflow_enabled("image", cfg)
    for key, workflow in (("image_caption", "caption"), ("voice_asr", "voice"), ("proactive", "proactive")):
        cfg.setdefault(key, {})["enabled"] = cfg.get(key, {}).get("enabled", True) is True and workflow_enabled(workflow, cfg)
    return cfg


def selected_identity(base, *, role_id=None):
    from identity import IdentityConfig, PartnerConfig, RouteConfig, RuntimePaths
    required = ("ROLE_REGISTRY_DIR", "ROLE_ENVIRONMENT", "ROLE_ID", "ROLE_RELATION", "ROLE_DATA_ROOT", "ROLE_ASSETS_ROOT")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("missing explicit role context: " + ", ".join(missing))
    rid = identifier(os.environ["ROLE_ID"])
    if role_id is not None and role_id != rid:
        raise ValueError("worker role cannot change")
    relation_id = identifier(os.environ["ROLE_RELATION"])
    environment = os.environ["ROLE_ENVIRONMENT"]
    role = Registry.read(os.environ["ROLE_REGISTRY_DIR"], environment).role(rid)
    relation = role["relationships"][relation_id]
    person = relation.get("person") or {}
    partner = PartnerConfig(id=relation_id, name=person.get("name") or relation_id,
                            display_name=person.get("display_name") or person.get("name") or relation_id,
                            address=person.get("address") or person.get("name") or relation_id,
                            gender=person.get("gender"), city=person.get("city"), occupation=person.get("occupation"))
    root = Path(os.environ["ROLE_DATA_ROOT"]).expanduser().resolve() / environment / rid / "relationships" / relation_id
    assets = Path(os.environ["ROLE_ASSETS_ROOT"]).expanduser().resolve() / rid
    runtime = RuntimePaths.from_root(root, root / "photos" / "generated")
    runtime.validate()
    routes = tuple(RouteConfig(rid, relation_id, b["channel"], b["bot_account_id"], b["peer_id"], b["peer_type"])
                   for b in relation.get("bindings", []) if b.get("environment") == environment)
    if not routes:
        raise ValueError("selected relationship has no binding in this environment")
    dossier = copy.deepcopy(role["dossier"])
    dossier["user_ref"] = {"name": partner.name, "address": partner.address, "gender": partner.gender,
                           "display_name": partner.display_name, "city": partner.city, "occupation": partner.occupation}
    dossier["relationship_story"] = relation.get("story")
    dossier["identity"]["relationship_to_user"] = relation.get("description")
    grants = deployment_grants(rid, relation_id, environment)
    return IdentityConfig(rid, dossier["identity"]["name"], partner, routes, runtime, assets,
                          grants.get("runtime_enabled") is True, dict(grants.get("capabilities") or {}), dossier)
