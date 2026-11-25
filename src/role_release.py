"""Content-addressed role releases; installing never activates or edits state."""
import hashlib
import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from role_registry import identifier


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != name:
        raise ValueError("invalid release path")
    if path.parts[0] not in {"definition", "assets", "workflows"}:
        raise ValueError("release can contain only definition/assets/workflows")
    if any(part.lower() in {"runtime", "secrets", ".env", "memory", "sessions"} for part in path.parts):
        raise ValueError("runtime/private state cannot enter a release")
    if path.name.lower().endswith((".env", ".db", ".sqlite", ".sqlite3", ".log", ".token", ".pem", ".key", ".p12")) or path.name.lower() in {
        "state.json", "memory.md", "user.md", "profile.md", "auth.json", "grants.json", "token", "credentials.json"}:
        raise ValueError("runtime/private file cannot enter a release")
    return name


def _reject_authority(value):
    """Known credential and runtime authority fields belong to the deploy host."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"api_key", "ws_token", "qq_ws_token", "password", "access_token",
                                    "token", "refresh_token", "private_key", "client_secret", "api_secret",
                                    "authorization", "runtime_enabled", "capabilities"} and child:
                raise ValueError("credentials/deployment grants must not be shipped in role releases")
            _reject_authority(child)
    elif isinstance(value, list):
        for child in value:
            _reject_authority(child)


def plan(role_id, engine_digest, files):
    identifier(role_id)
    if not isinstance(engine_digest, str) or len(engine_digest) != 64 or any(c not in "0123456789abcdef" for c in engine_digest):
        raise ValueError("engine sha256 required")
    entries = {}
    for name, source in sorted(files.items()):
        _name(name)
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError("only regular files can enter a release")
        entries[name] = {"sha256": _digest(source.read_bytes()), "size": source.stat().st_size}
    if "definition/role.json" not in entries:
        raise ValueError("role definition required")
    role = json.loads(Path(files["definition/role.json"]).read_text())
    if role.get("role_id") != role_id:
        raise ValueError("release role mismatch")
    _reject_authority(role)
    manifest = {"schema_version": 1, "role_id": role_id, "engine_sha256": engine_digest, "files": entries}
    manifest["release_id"] = _digest(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    return manifest


def install(manifest, sources, destination):
    """Install a verified immutable version. Runtime and active pointer untouched."""
    current = plan(manifest["role_id"], manifest["engine_sha256"], sources)
    if current != manifest:
        raise ValueError("release inputs changed since plan")
    root = Path(destination).expanduser().resolve()
    target = root / "roles" / identifier(manifest["role_id"]) / "releases" / manifest["release_id"]
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("release destination cannot traverse symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if json.loads((target / "manifest.json").read_text()) != manifest:
            raise ValueError("existing release manifest conflict")
        for name, entry in manifest["files"].items():
            if _digest((target / name).read_bytes()) != entry["sha256"]:
                raise ValueError("existing release content conflict")
        return target
    staging = Path(tempfile.mkdtemp(prefix=".stage-", dir=target.parent))
    try:
        for name, entry in manifest["files"].items():
            data = Path(sources[name]).read_bytes()
            if _digest(data) != entry["sha256"]:
                raise ValueError("source changed while copying")
            dest = staging / _name(name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(0o444)
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def activate(destination, role_id, release_id, *, expected_release):
    """Atomically select an installed version, with compare-and-swap protection.

    This changes the deployment selection only. A running service must be
    explicitly reconciled with that selection by its deployment supervisor.
    """
    identifier(role_id)
    if not isinstance(release_id, str) or len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
        raise ValueError("invalid release ID")
    root = Path(destination).expanduser().resolve()
    role_dir = root / "roles" / role_id
    target = role_dir / "releases" / release_id
    for path in (root / "roles", role_dir, role_dir / "releases", target):
        if path.is_symlink():
            raise ValueError("deployment path cannot traverse symlinks")
    manifest_path = target / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("manifest cannot be a symlink")
    manifest = json.loads(manifest_path.read_text())
    signed = {k: v for k, v in manifest.items() if k != "release_id"}
    actual_id = _digest(json.dumps(signed, sort_keys=True, separators=(",", ":")).encode())
    if actual_id != release_id or manifest.get("release_id") != release_id or manifest.get("role_id") != role_id:
        raise ValueError("release manifest binding mismatch")
    for name, entry in manifest["files"].items():
        path = target / _name(name)
        if any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents):
            raise ValueError("release content cannot traverse symlinks")
        data = path.read_bytes()
        if len(data) != entry["size"] or _digest(data) != entry["sha256"]:
            raise ValueError("release content changed")
    fd = os.open(role_dir / ".publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active = role_dir / "active"
        current = None
        if active.is_symlink():
            link = os.readlink(active)
            candidate = PurePosixPath(link)
            if len(candidate.parts) != 2 or candidate.parts[0] != "releases":
                raise ValueError("active selection points outside role releases")
            current = candidate.parts[1]
        elif active.exists():
            raise ValueError("active selection must be a managed symlink")
        if current != expected_release:
            raise ValueError("active release changed since deployment plan")
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=".active-", dir=role_dir)
        os.close(temporary_fd)
        temporary = Path(temporary_name)
        try:
            temporary.unlink()
            temporary.symlink_to(f"releases/{release_id}")
            os.replace(temporary, active)
        finally:
            if temporary.is_symlink() or temporary.exists():
                temporary.unlink()
    return active
