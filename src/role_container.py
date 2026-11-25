"""Compile least-privilege role workers into a Docker Compose document.

The engine image must contain only code/public resources. Configuration input
is projected per relationship before mounting; a worker cannot read sibling
relationship definitions. Native capabilities must use authenticated services.
"""
import copy
import re
from pathlib import Path

from role_registry import identifier


def worker_spec(role, relation_id, *, environment, image, state_dir, assets_dir, definition_file, ws_uri,
                grants_file=None, token_file=None, send=False):
    rid = identifier(role["role_id"])
    identifier(relation_id)
    if environment not in ("dev", "test", "prod"):
        raise ValueError("explicit environment required")
    if not re.fullmatch(r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}", image):
        raise ValueError("engine image must be pinned by sha256 digest")
    if not ws_uri.startswith(("ws://", "wss://")):
        raise ValueError("explicit OneBot endpoint required")
    inputs = [Path(p).expanduser().absolute() for p in (state_dir, assets_dir, definition_file)]
    if any(p.is_symlink() for source in inputs for p in [source, *source.parents]):
        raise ValueError("worker mounts cannot traverse symlinks")
    paths = [p.resolve() for p in inputs]
    if any(p == Path("/") or p == Path.home() for p in paths):
        raise ValueError("broad host mounts are forbidden")
    state, assets, definition = paths
    if send and (grants_file is None or token_file is None):
        raise ValueError("send requires separately mounted grants and transport token")
    private_mounts = []
    for source, target in ((grants_file, "/auth/grants.json"), (token_file, "/auth/onebot.token")):
        if source is None:
            continue
        source = Path(source).expanduser()
        if any(p.is_symlink() for p in [source, *source.parents]) or not source.is_file() or source.stat().st_mode & 0o022:
            raise ValueError("authorization inputs must be regular protected files")
        source = source.resolve()
        if any(source == p or p in source.parents for p in (state, assets)):
            raise ValueError("authorization inputs must be outside runtime and published assets")
        private_mounts.append({"type": "bind", "source": str(source), "target": target, "read_only": True})
    if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError("state, assets and definition mounts must be disjoint")
    projected = copy.deepcopy(role)
    projected["relationships"] = {relation_id: copy.deepcopy(role["relationships"][relation_id])}
    name = f"{environment}-{rid}-{relation_id}"
    service = {
        "image": image, "user": "10001:10001", "read_only": True,
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "pids_limit": 256, "mem_limit": "2g", "cpus": 2,
        "init": True, "restart": "no", "tmpfs": ["/tmp:rw,noexec,nosuid,size=256m,mode=1777"],
        "environment": {
            "ROLE_REGISTRY_DIR": "/registry", "ROLE_ID": rid, "ROLE_RELATION": relation_id,
            "ROLE_ENVIRONMENT": environment, "ROLE_DATA_ROOT": "/state", "ROLE_ASSETS_ROOT": "/assets",
            "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp/worker-home",
        },
        "volumes": [
            {"type": "bind", "source": str(state), "target": f"/state/{environment}/{rid}/relationships/{relation_id}"},
            {"type": "bind", "source": str(assets), "target": f"/assets/{rid}", "read_only": True},
            {"type": "bind", "source": str(definition), "target": f"/registry/{rid}/role.json", "read_only": True},
        ] + private_mounts,
        "command": ["python", "/engine/tools/onebot_qq_run.py", "--ws", ws_uri,
                    "--send" if send else "--dry-run", "--no-reconnect"],
        "networks": [name],
    }
    if grants_file is not None:
        service["environment"]["ROLE_GRANTS_FILE"] = "/auth/grants.json"
    if token_file is not None:
        service["command"] += ["--token-file", "/auth/onebot.token"]
    return projected, {"services": {name: service}, "networks": {name: {"internal": True}}}
