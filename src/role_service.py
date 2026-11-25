"""Reconcile one role's selected release, with health-gated service rollback.

Only this role's Compose project is operated. Runtime bind directories are
preserved on success, failure and rollback; no volume or image pruning occurs.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from role_container import worker_spec
from role_registry import Registry, identifier
from role_release import plan as release_plan


def deployment_plan(release_root, role_id, environment, state_root, credentials_root, bindings):
    identifier(role_id)
    root = Path(release_root).expanduser().resolve()
    role_root = root / "roles" / role_id
    active = role_root / "active"
    if not active.is_symlink():
        raise ValueError("select an installed role release first")
    target = active.resolve(strict=True)
    if target.parent != role_root / "releases":
        raise ValueError("selected release escapes role")
    if not (target / "assets").is_dir():
        raise ValueError("role release must include an assets directory (a README is sufficient when empty)")
    manifest = json.loads((target / "manifest.json").read_text())
    sources = {name: target / name for name in manifest["files"]}
    if release_plan(role_id, manifest["engine_sha256"], sources) != manifest:
        raise ValueError("selected release content changed")
    definitions = [json.loads(p.read_text()) for p in (root / "roles").glob("*/active/definition/role.json")]
    role = Registry(definitions, environment).role(role_id)  # Global exact-route conflict check.
    if set(bindings) != set(role["relationships"]):
        raise ValueError("provide explicit deployment bindings for every selected relationship")
    state_root = Path(state_root).expanduser().resolve()
    credentials_root = Path(credentials_root).expanduser().resolve()
    metadata_root = root / "deployments" / environment / role_id
    compose, projected, expected_bots = {"services": {}, "networks": {}}, {}, {}
    credential_hashes = {}
    for relation_id in role["relationships"]:
        identifier(relation_id)
        options = bindings[relation_id]
        authority = credentials_root / environment / role_id / relation_id
        grant, token = authority / "grants.json", authority / "onebot.token"
        if not grant.is_file() or not token.is_file():
            raise ValueError("explicit per-relationship grants and token required")
        for p in (grant, token):
            credential_hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        definition, part = worker_spec(role, relation_id, environment=environment,
            image="sha256:" + manifest["engine_sha256"],
            state_dir=state_root / environment / role_id / "relationships" / relation_id,
            assets_dir=target / "assets", definition_file=metadata_root / "PLACEHOLDER" / relation_id / "role.json",
            ws_uri=options["ws_uri"], grants_file=grant, token_file=token, send=options.get("send") is True)
        if role.get("personalization"):
            from personalization_runtime import digest_assets
            p = role["personalization"]
            p = json.loads(json.dumps(p["relationships"].get(relation_id, {}) if "relationships" in p else p))
            if not p:
                p = None
            if p is not None:
                p.update(release_version=manifest["release_id"], assets_sha256=digest_assets(manifest))
                definition["personalization"] = p
        name = next(iter(part["services"]))
        service = part["services"][name]
        service["restart"] = "unless-stopped"
        if options.get("network"):
            expected_network = f"role-{environment}-{role_id}-{relation_id}"
            if options["network"] != expected_network:
                raise ValueError("transport network must belong to this exact relationship")
            part["networks"][name] = {"external": True, "name": expected_network}
        compose["services"].update(part["services"])
        compose["networks"].update(part["networks"])
        projected[relation_id] = definition
        bots = [b["bot_account_id"] for b in definition["relationships"][relation_id]["bindings"]
                if b["environment"] == environment and b["channel"] == "qq_onebot"]
        if len(set(bots)) != 1:
            raise ValueError("one QQ account per relationship worker is required")
        expected_bots[name] = bots[0]
    digest = hashlib.sha256(json.dumps({"compose": compose, "definitions": projected,
                                       "credentials": credential_hashes}, sort_keys=True).encode()).hexdigest()
    directory = metadata_root / digest
    for service in compose["services"].values():
        service["labels"] = {"role.deployment": digest}
        for mount in service["volumes"]:
            mount["source"] = mount["source"].replace(str(metadata_root / "PLACEHOLDER"), str(directory))
    return {"role_id": role_id, "environment": environment, "release_id": manifest["release_id"],
            "deployment_id": digest, "project": f"role-{environment}-{role_id}", "directory": str(directory),
            "active_link": str(active), "compose": compose, "definitions": projected, "expected_bots": expected_bots}


def command(args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=90).stdout


def immutable_json(path, value):
    """Never follow links or replace a previously recorded deployment."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("deployment metadata cannot traverse symlinks")
    content = json.dumps(value, indent=2, sort_keys=True)
    if path.exists():
        if path.read_text() != content:
            raise ValueError("immutable deployment metadata changed")
        return
    with path.open("x") as stream:
        stream.write(content)


def wait_ready(prefix, expected_bots, deployment_id, execute, timeout):
    deadline = time.monotonic() + timeout
    while True:
        ids = execute(prefix + ["ps", "-q"]).split()
        containers = json.loads(execute(["docker", "inspect", *ids])) if ids else []
        running = {c["Config"]["Labels"]["com.docker.compose.service"] for c in containers
                   if c["State"].get("Running") and not c["State"].get("Restarting")
                   and c["Config"]["Labels"].get("role.deployment") == deployment_id}
        ready = running == set(expected_bots)
        if ready:
            for name, bot in expected_bots.items():
                log = execute(prefix + ["logs", "--no-color", name])
                if f"login_info uin={bot} " not in log:
                    ready = False
                    break
        if ready:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("selected role workers did not reach transport readiness")
        time.sleep(0.5)


def apply(deployment, *, execute=command, timeout=60):
    directory = Path(deployment["directory"])
    metadata = directory.parent
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("deployment metadata cannot traverse symlinks")
    metadata.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(metadata / ".service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if Path(deployment["active_link"]).resolve().name != deployment["release_id"]:
            raise ValueError("selected release changed; regenerate deployment plan")
        running_file = metadata / "running.json"
        if running_file.is_symlink():
            raise ValueError("running record cannot be a symlink")
        previous = json.loads(running_file.read_text()) if running_file.exists() else None
        if previous and (previous.get("project") != deployment["project"]
                         or metadata not in Path(previous["compose_file"]).resolve().parents
                         or "expected_bots" not in previous):
            raise ValueError("previous running record belongs to another deployment scope")
        directory.mkdir(exist_ok=True)
        for relation, definition in deployment["definitions"].items():
            folder = directory / identifier(relation)
            folder.mkdir(exist_ok=True)
            immutable_json(folder / "role.json", definition)
        compose_file = directory / "compose.json"
        immutable_json(compose_file, deployment["compose"])
        prefix = ["docker", "compose", "-p", deployment["project"], "-f", str(compose_file)]
        try:
            execute(prefix + ["up", "-d", "--no-build", "--remove-orphans"])
            wait_ready(prefix, deployment["expected_bots"], deployment["deployment_id"], execute, timeout)
            if Path(deployment["active_link"]).resolve().name != deployment["release_id"]:
                raise ValueError("selected release changed during service switch")
            record = {"release_id": deployment["release_id"], "deployment_id": deployment["deployment_id"],
                      "compose_file": str(compose_file), "project": deployment["project"],
                      "expected_bots": deployment["expected_bots"]}
            fd, path = tempfile.mkstemp(prefix=".running-", dir=metadata)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(record, stream)
                os.replace(path, running_file)
            finally:
                if os.path.exists(path):
                    os.unlink(path)
            return record
        except BaseException:
            if previous:
                rollback = ["docker", "compose", "-p", deployment["project"], "-f", previous["compose_file"]]
                execute(rollback + ["up", "-d", "--no-build", "--remove-orphans"])
                wait_ready(rollback, previous["expected_bots"], previous["deployment_id"], execute, timeout)
            else:
                execute(prefix + ["down", "--timeout", "10"])
            raise
