"""Authenticated, relationship-bound native operations with a macOS sandbox.

The caller supplies data, never paths or commands. Deployment-owned accounts
select the provider and its filesystem scope. No endpoint lists or fetches jobs.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

from role_registry import identifier

MAX_REQUEST = 24 * 1024 * 1024
OPERATIONS = {"image_generate", "appearance_generate", "image_caption", "voice_transcribe"}


def validate_payload(operation, payload):
    if operation not in OPERATIONS or not isinstance(payload, dict):
        raise ValueError("unsupported native operation")
    if operation == "appearance_generate":
        if set(payload) != {"stage", "prompt", "refs"} or payload.get("stage") not in {"face", "body_front", "body"}:
            raise ValueError("invalid appearance stage")
        if not isinstance(payload.get("prompt"), str) or not 1 <= len(payload["prompt"]) <= 8000:
            raise ValueError("invalid appearance prompt")
        refs = payload.get("refs")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 5:
            raise ValueError("appearance requires one to five references")
        for encoded in refs:
            if not isinstance(encoded, str) or len(encoded) > 16 * 1024 * 1024:
                raise ValueError("appearance reference too large")
            data = base64.b64decode(encoded, validate=True)
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("appearance references must be PNG bytes")
    elif operation == "image_generate":
        if set(payload) - {"scene", "context", "persona", "preferences"} or not isinstance(payload.get("scene"), str) or not 1 <= len(payload["scene"]) <= 8000:
            raise ValueError("image generation requires a bounded scene")
        if "persona" in payload:
            persona = payload["persona"]
            if not isinstance(persona, dict) or set(persona) != {"version", "assets_sha256"} or any(not isinstance(v, str) or not re.fullmatch("[a-f0-9]{64}", v) for v in persona.values()):
                raise ValueError("invalid persona/asset version binding")
        if "preferences" in payload and (not isinstance(payload["preferences"], str) or len(payload["preferences"]) > 32000):
            raise ValueError("invalid image preferences")
        from image_context import validate_context
        validate_context(payload.get("context", {}))
    else:
        if set(payload) != {"data"} or not isinstance(payload["data"], str):
            raise ValueError("native media input must contain bytes, not a path")
        data = base64.b64decode(payload["data"], validate=True)
        if not 1 <= len(data) <= 16 * 1024 * 1024:
            raise ValueError("invalid native media size")


def sandbox_profile(account, job):
    """Deny filesystem data access except system runtime and this relationship."""
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    readable = ["/System", "/usr", "/bin", "/sbin", "/Library/Apple", "/private/etc",
                "/private/var/db/dyld", "/dev", str(Path(sys.base_prefix).resolve()), str(Path(sys.prefix).resolve()),
                account["engine_root"], account["assets_root"], account["runtime_root"]]
    rules = ["(version 1)", "(deny default)",
             "(allow process-exec process-fork signal sysctl-read mach-lookup)",
             "(allow file-read-metadata)"]
    for destination in account.get("network_outbound", []):
        if not isinstance(destination, str) or not re.fullmatch(r"[a-zA-Z0-9.*:\[\]-]+", destination):
            raise ValueError("invalid native egress destination")
        rules.append(f"(allow network-outbound (remote tcp {quote(destination)}))")
    rules.append('(allow file-read-data (literal "/"))')  # macOS dyld startup, not descendant contents.
    rules += [f"(allow file-read* (subpath {quote(p)}))" for p in readable]
    rules += [f"(allow file-write* (subpath {quote(account['runtime_root'])}))",
              '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules)


def run_provider(account, operation, payload, job):
    if sys.platform != "darwin":
        raise RuntimeError("native subprocess provider requires macOS sandbox-exec")
    command = account["providers"][operation]
    if not isinstance(command, list) or not command or any(not isinstance(p, str) for p in command):
        raise ValueError("provider must be a fixed argument array")
    if not Path(command[0]).is_absolute():
        raise ValueError("provider executable must be absolute")
    profile = job / "sandbox.sb"
    profile.write_text(sandbox_profile(account, job))
    home = Path(account["runtime_root"]) / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home),
           "CODEX_HOME": str(home / ".codex"), "TMPDIR": str(job),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    request = {"operation": operation, "payload": payload, "output_dir": str(job),
               "assets_dir": account["assets_root"], "persona_version": account.get("persona_version"),
               "assets_sha256": account.get("assets_sha256")}
    result = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(profile), *command],
                            input=json.dumps(request), text=True, capture_output=True, cwd=job,
                            env=env, timeout=min(int(account.get("timeout", 600)), 900), check=True)
    if len(result.stdout) > MAX_REQUEST:
        raise ValueError("native provider response too large")
    return json.loads(result.stdout)


class Gateway:
    def __init__(self, config_file, provider=run_provider):
        self.config_file = Path(config_file)
        self.provider = provider
        self._locks = {}
        self._lock = threading.Lock()

    def accounts(self):
        path = self.config_file
        if path.is_symlink() or path.stat().st_mode & 0o022:
            raise PermissionError("native service config must be deployment-owned and protected")
        accounts = json.loads(path.read_text())["accounts"]
        contexts, tokens, roots = set(), set(), []
        for account in accounts:
            context = (account["environment"], identifier(account["role_id"]), identifier(account["relation_id"]))
            token = account["token_sha256"]
            if context[0] not in ("dev", "test", "prod") or context in contexts or token in tokens or not re.fullmatch("[a-f0-9]{64}", token):
                raise ValueError("invalid or duplicate native service identity")
            contexts.add(context)
            tokens.add(token)
            for key in ("runtime_root", "assets_root", "engine_root"):
                raw = Path(account[key]).expanduser().absolute()
                if any(p.is_symlink() for p in (raw, *raw.parents)):
                    raise ValueError("native roots cannot traverse symlinks")
                p = raw.resolve()
                if p in (Path("/"), Path.home()):
                    raise ValueError("native service cannot expose broad host roots")
                account[key] = str(p)
            runtime = Path(account["runtime_root"])
            public_roots = [Path(p).resolve() for p in ("/System", "/usr", "/bin", "/sbin", "/Library/Apple",
                                                        "/private/etc", "/private/var/db/dyld", "/dev",
                                                        sys.prefix, sys.base_prefix)]
            if any(runtime == p or p in runtime.parents for p in public_roots):
                raise ValueError("native private runtime cannot live in shared system/library roots")
            if any(runtime == p or runtime in p.parents or p in runtime.parents for p in roots):
                raise ValueError("native relationship runtime roots must not overlap")
            roots.append(runtime)
            if set(account["providers"]) - OPERATIONS:
                raise ValueError("unknown native provider operation")
        # A role's read roots must not accidentally expose another runtime.
        for account in accounts:
            own = Path(account["runtime_root"])
            for key in ("assets_root", "engine_root"):
                read_root = Path(account[key])
                if any(p == read_root or read_root in p.parents or p in read_root.parents for p in roots):
                    raise ValueError("native read root overlaps another relationship")
        return accounts

    def invoke(self, token, body):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        account = next((a for a in self.accounts() if hmac.compare_digest(a["token_sha256"], token_hash)), None)
        if account is None or account.get("enabled") is not True:
            raise PermissionError("native access denied")
        if not isinstance(body, dict) or set(body) != {"operation", "payload", "request_id"}:
            raise ValueError("unexpected request fields")
        operation, payload = body["operation"], body["payload"]
        if operation not in account["providers"]:
            raise PermissionError("native operation denied")
        if not isinstance(body["request_id"], str) or not re.fullmatch("[a-f0-9]{32}", body["request_id"]):
            raise ValueError("invalid request ID")
        validate_payload(operation, payload)
        persona = payload.get("persona") if operation == "image_generate" else None
        if persona:
            if persona != {"version": account.get("persona_version"), "assets_sha256": account.get("assets_sha256")}:
                raise PermissionError("native service has not loaded the requested persona assets")
            from personalization_store import digest
            root = Path(account["assets_root"])
            sources = {}
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise PermissionError("native persona assets cannot contain symlinks")
                if path.is_file():
                    sources[str(path.relative_to(root))] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
            if digest(sources) != persona["assets_sha256"]:
                raise PermissionError("native persona asset content changed")
        context = tuple(account[k] for k in ("environment", "role_id", "relation_id"))
        with self._lock:
            lock = self._locks.setdefault(context, threading.Lock())
        with lock:
            job_id = uuid.uuid4().hex
            job = Path(account["runtime_root"]) / "jobs" / job_id
            if any(p.is_symlink() for p in (job, *job.parents)):
                raise PermissionError("native job directory cannot traverse symlinks")
            job.mkdir(parents=True, mode=0o700)
            result = self.provider(account, operation, payload, job)
            if persona and (result.get("persona_version") != persona["version"] or result.get("assets_sha256") != persona["assets_sha256"]):
                raise ValueError("native provider did not acknowledge its actual persona version")
            if operation in {"image_generate", "appearance_generate"}:
                files = result.get("image_files", [result.get("image_file")])
                expected = payload.get("context", {}).get("n_images", 1)
                if not isinstance(files, list) or len(files) != expected or len(set(files)) != len(files):
                    raise ValueError("native provider image count mismatch")
                encoded = []
                for filename in files:
                    if not isinstance(filename, str):
                        raise ValueError("invalid native image filename")
                    image = job / filename
                    if image.is_symlink() or job not in image.resolve().parents:
                        raise PermissionError("native provider result escapes task")
                    if not image.is_file() or image.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError("invalid native image size")
                    data = image.read_bytes()
                    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        raise ValueError("native image must be PNG")
                    encoded.append(base64.b64encode(data).decode())
                if sum(map(len, encoded)) > MAX_REQUEST - 4096:
                    raise ValueError("native image batch exceeds response limit")
                output = {"images": encoded}
                if len(encoded) == 1:
                    output = {"image": encoded[0]}  # backwards compatible single image
            else:
                if not isinstance(result.get("text"), str) or len(result["text"]) > 32000:
                    raise ValueError("invalid native text result")
                output = {"text": result["text"]}
            if persona:
                output.update(persona_version=persona["version"], assets_sha256=persona["assets_sha256"])
            return {"request_id": body["request_id"], "job_id": job_id,
                    **dict(zip(("environment", "role_id", "relation_id"), context)), **output}


def server(address, gateway):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *_args):
            pass  # Never log Authorization or media/prompt bodies.

        def do_POST(self):
            try:
                if self.path != "/invoke":
                    raise ValueError("unknown native endpoint")
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= MAX_REQUEST:
                    raise ValueError("invalid request length")
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    raise PermissionError("native access denied")
                output = gateway.invoke(auth[7:], json.loads(self.rfile.read(size)))
                status = 200
            except PermissionError:
                status, output = 403, {"error": "native access denied"}
            except (ValueError, KeyError, TypeError):
                status, output = 400, {"error": "invalid native request"}
            except Exception:
                status, output = 502, {"error": "native provider failed"}
            data = json.dumps(output).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return ThreadingHTTPServer(address, Handler)
