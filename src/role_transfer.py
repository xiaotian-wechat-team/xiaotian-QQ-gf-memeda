"""Streaming role release protocol; never extracts archive paths directly."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile

from role_release import install, plan

MAX_FILES = 20000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


def write_bundle(manifest, sources, stream):
    if plan(manifest["role_id"], manifest["engine_sha256"], sources) != manifest:
        raise ValueError("release changed before transfer")
    data = json.dumps(manifest, ensure_ascii=False).encode()
    with tarfile.open(fileobj=stream, mode="w|") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        for name, entry in manifest["files"].items():
            info = tarfile.TarInfo("payload/" + name)
            info.size = entry["size"]
            with Path(sources[name]).open("rb") as source:
                archive.addfile(info, source)


def receive_bundle(stream, destination):
    """Verify complete payload before installing. No active/runtime writes."""
    with tempfile.TemporaryDirectory(prefix="role-transfer-") as temporary:
        root = Path(temporary)
        sources = {}
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            first = archive.next()
            if first is None or first.name != "manifest.json" or not first.isfile() or first.size > 8 * 1024 * 1024:
                raise ValueError("first archive entry must be a bounded manifest")
            manifest = json.load(archive.extractfile(first))
            entries = manifest.get("files")
            if not isinstance(entries, dict) or not 1 <= len(entries) <= MAX_FILES:
                raise ValueError("invalid release file count")
            total = 0
            # Never use extract()/extractall(): even the temporary paths are
            # generated locally and do not depend on supplied archive names.
            while (member := archive.next()) is not None:
                name = member.name.removeprefix("payload/")
                if (not member.name.startswith("payload/") or name not in entries or name in sources
                        or not member.isfile() or member.size > MAX_FILE_BYTES or member.size < 0):
                    raise ValueError("unexpected, duplicate or unsafe archive member")
                entry = entries[name]
                if member.size != entry["size"]:
                    raise ValueError("payload size mismatch")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("release exceeds transfer size limit")
                target = root / str(len(sources))
                digest = hashlib.sha256()
                source = archive.extractfile(member)
                with target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise ValueError("payload digest mismatch")
                sources[name] = target
            if set(sources) != set(entries):
                raise ValueError("incomplete release transfer")
        target = install(manifest, sources, destination)
        return manifest, target
