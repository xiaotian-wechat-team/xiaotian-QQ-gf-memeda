"""Native RPC client: no host paths, command strings or caller-selected identity."""
import base64
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
import uuid

from native_gateway import MAX_REQUEST, validate_payload


def invoke(operation, payload, cfg):
    from runtime_policy import require
    if os.environ.get("ROLE_REGISTRY_DIR"):
        from config import load
        current = load()
        binding = ("environment", "persona_id", "partner_id")
        if any(current["identity_runtime"].get(k) != cfg["identity_runtime"].get(k) for k in binding):
            raise PermissionError("native caller context changed")
        cfg = current
    require("image_generate" if operation == "appearance_generate" else operation, cfg)
    validate_payload(operation, payload)
    service = cfg.get("native_service") or {}
    parsed = urllib.parse.urlsplit(service.get("url", ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("explicit native service endpoint required")
    token_path = Path(service["token_file"])
    if token_path.is_symlink() or token_path.stat().st_mode & 0o077:
        raise PermissionError("native service token must be private")
    token = token_path.read_text().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("invalid native service token")
    request_id = uuid.uuid4().hex
    body = json.dumps({"operation": operation, "payload": payload, "request_id": request_id}).encode()
    if len(body) > MAX_REQUEST:
        raise ValueError("native request exceeds limit")
    request = urllib.request.Request(service["url"].rstrip("/") + "/invoke", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    # A native capability token must not be forwarded by an HTTP redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=min(float(service.get("timeout", 600)), 900)) as response:
        encoded = response.read(MAX_REQUEST + 1)
    if len(encoded) > MAX_REQUEST:
        raise ValueError("native response exceeds limit")
    result = json.loads(encoded)
    runtime = cfg["identity_runtime"]
    expected = {"environment": runtime["environment"], "role_id": runtime["persona_id"],
                "relation_id": runtime["partner_id"], "request_id": request_id}
    if any(result.get(k) != v for k, v in expected.items()):
        raise PermissionError("native response belongs to a different task or relationship")
    if payload.get("persona"):
        if result.get("persona_version") != payload["persona"]["version"] or result.get("assets_sha256") != payload["persona"]["assets_sha256"]:
            raise PermissionError("native response used a different persona or asset version")
    return result


def generate(scene, cfg, **ctx):
    from image_context import wire_context
    context = wire_context(ctx)
    payload = {"scene": scene, "context": context}
    if cfg.get("persona_version"):
        from personalization_runtime import image_preferences
        payload.update(persona={"version": cfg["persona_version"], "assets_sha256": cfg["personalization"]["assets_sha256"]}, preferences=image_preferences(cfg))
    result = invoke("image_generate", payload, cfg)
    encoded = result.get("images", [result.get("image")])
    if not isinstance(encoded, list) or len(encoded) != context.get("n_images", 1):
        raise ValueError("native image count mismatch")
    images = []
    for item in encoded:
        data = base64.b64decode(item, validate=True)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 16 * 1024 * 1024:
            raise ValueError("native image is not a bounded PNG")
        images.append(data)
    runtime = Path(cfg["identity_runtime"]["runtime_root"]).resolve()
    out = Path(cfg["imagegen"]["out_dir"]).resolve()
    if runtime not in out.parents:
        raise PermissionError("native output escapes relationship runtime")
    target = out / uuid.uuid4().hex
    target.mkdir(parents=True, mode=0o700)
    paths = []
    for i, data in enumerate(images):
        image = target / f"result-{i + 1}.png"
        image.write_bytes(data)
        paths.append(image)
    from personalization_runtime import report_loaded
    report_loaded("image", cfg)
    return paths


def media_text(operation, path, cfg):
    source = Path(path).resolve()
    root = Path(cfg["identity_runtime"]["runtime_root"]).resolve()
    if root not in source.parents or source.stat().st_size > 16 * 1024 * 1024:
        raise PermissionError("native input must belong to this relationship")
    payload = {"data": base64.b64encode(source.read_bytes()).decode()}
    text = invoke(operation, payload, cfg)["text"]
    if not isinstance(text, str) or len(text) > 32000:
        raise ValueError("invalid native text response")
    from personalization_runtime import report_loaded
    report_loaded("caption" if operation == "image_caption" else "voice", cfg)
    return text
