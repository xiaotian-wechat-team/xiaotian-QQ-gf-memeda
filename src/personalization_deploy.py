"""Optional deployment adapter; definitions SSOT: data/persona_dossier.json.

Only an existing host-owned service binding opts a role into reconciliation.
HTTP drafts cannot create bindings, grants, endpoints or credentials.
"""
from pathlib import Path


def binding(store):
    from personalization_store import read_json
    value = read_json(store.destination / "service-bindings" / (store.persona + ".json"))
    if value is not None and (value.get("role_id") != store.persona or value.get("environment") != store.environment):
        raise PermissionError("服务绑定与人物环境不匹配")
    return value


def reconcile(store, *, execute=None):
    settings = binding(store)
    if settings is None:
        return {"status": "pending", "reason": "未连接服务协调器；工作流将在下一轮读取所选版本"}
    import role_service
    deployment = role_service.deployment_plan(store.destination, store.persona, store.environment,
        settings["state_root"], settings["credentials_root"], settings["bindings"])
    from personalization_store import write_json
    write_json(store.directory / "deployments" / (deployment["release_id"] + ".json"), deployment)
    result = role_service.apply(deployment, **({"execute": execute} if execute is not None else {}))
    return {"status": "waiting_consumers", "deployment_id": result["deployment_id"]}


def report_directory(store):
    value = binding(store)
    if value is None:
        return None
    return (Path(value["state_root"]) / store.environment / store.persona /
            "relationships" / store.relation / "persona-loaded")


def recover_service(store, previous, failed_version):
    if binding(store) is None:
        return
    if previous:
        return reconcile(store)
    # A first deployment has no earlier release to reconcile. Stop only the
    # exact deployment just started, retaining its private runtime data.
    from personalization_store import read_json
    import role_service
    running = store.destination / "deployments" / store.environment / store.persona / "running.json"
    record = read_json(running)
    if record is None:
        return
    if record.get("release_id") != failed_version or record.get("project") != f"role-{store.environment}-{store.persona}":
        raise ValueError("恢复目标与失败的人物部署不匹配")
    compose = Path(record["compose_file"])
    if running.parent not in compose.resolve().parents:
        raise ValueError("恢复部署路径不在受管目录内")
    role_service.command(["docker", "compose", "-p", record["project"], "-f", str(compose), "down", "--timeout", "10"])
    running.unlink()
