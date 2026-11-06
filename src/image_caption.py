"""Inbound image → short Chinese caption via Magpie (Cursor quota), before brain.

Production path uses Magpie http://127.0.0.1:3425/v1 only (default auto/gpt-6-luna).
Do NOT call api.kimi.com here. Fail soft → bare [图片]. Cache by file sha256.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import pathlib
import re
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from config import ROOT, load

DEFAULT_BASE = "http://127.0.0.1:3425/v1"
DEFAULT_MODEL = "auto/gpt-6-luna"
DEFAULT_PROMPT = (
    "用一两句中文描述这张图的主要内容（场景、人物/物体、氛围）。不要列表，不要编号。"
)
_TAG_RE = re.compile(r"\[图片(?:×\d+)?\]")


def _cfg() -> dict:
    c = load()
    ic = dict(c.get("image_caption") or {})
    llm = c.get("llm") or {}
    if "magpie_base" not in ic:
        ic["magpie_base"] = llm.get("magpie_base") or DEFAULT_BASE
    ic.setdefault("enabled", True)
    ic.setdefault("model", DEFAULT_MODEL)
    ic.setdefault("effort", "low")
    ic.setdefault("timeout", 45)
    ic.setdefault("max_edge", 1280)
    ic.setdefault("jpeg_quality", 85)
    ic.setdefault("prompt", DEFAULT_PROMPT)
    ic.setdefault("max_caption_chars", 120)
    ic.setdefault("cache_dir", "data/caption_cache")
    return ic


def _cache_dir(cfg: dict | None = None) -> pathlib.Path:
    cfg = cfg or _cfg()
    d = pathlib.Path(cfg.get("cache_dir") or "data/caption_cache")
    if not d.is_absolute():
        d = ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_hash(path: str | pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(sha: str, cfg: dict | None = None) -> pathlib.Path:
    return _cache_dir(cfg) / f"{sha}.json"


def cache_get(sha: str, cfg: dict | None = None) -> Optional[str]:
    p = _cache_path(sha, cfg)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cap = (data.get("caption") or "").strip()
    return cap or None


def cache_put(sha: str, caption: str, *, meta: dict | None = None, cfg: dict | None = None) -> None:
    cfg = cfg or _cfg()
    payload = {
        "caption": (caption or "").strip(),
        "model": cfg.get("model") or DEFAULT_MODEL,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        **(meta or {}),
    }
    _cache_path(sha, cfg).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _magpie_key() -> str | None:
    try:
        from llm_adapters import grok as magpie
        return magpie.api_key()
    except Exception:
        return None


def prep_jpeg_b64(path: str | pathlib.Path, *, max_edge: int = 1280, quality: int = 85) -> tuple[str, tuple[int, int], int]:
    """Resize + JPEG encode; return (b64, size, nbytes)."""
    from PIL import Image

    p = pathlib.Path(path)
    im = Image.open(p)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")
    w, h = im.size
    edge = max(w, h)
    if edge > max_edge:
        scale = max_edge / float(edge)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=int(quality), optimize=True)
    raw = buf.getvalue()
    return base64.b64encode(raw).decode("ascii"), im.size, len(raw)


def caption_image(path: str | pathlib.Path, *, cfg: dict | None = None, use_cache: bool = True) -> Optional[str]:
    """Caption one local image via Magpie. Soft-fail → None."""
    cfg = cfg or _cfg()
    if not cfg.get("enabled", True):
        return None
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return None
    try:
        sha = file_hash(p)
    except OSError:
        return None
    if use_cache:
        hit = cache_get(sha, cfg)
        if hit:
            return hit
    key = _magpie_key()
    if not key:
        return None
    try:
        b64, size, nbytes = prep_jpeg_b64(
            p, max_edge=int(cfg.get("max_edge") or 1280), quality=int(cfg.get("jpeg_quality") or 85)
        )
    except Exception:
        return None
    base = (cfg.get("magpie_base") or DEFAULT_BASE).rstrip("/")
    model = cfg.get("model") or DEFAULT_MODEL
    # Refuse accidental Kimi coding API in production path
    if "api.kimi.com" in base:
        return None
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": cfg.get("prompt") or DEFAULT_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "temperature": 0.2,
        "stream": False,
    }
    effort = cfg.get("effort")
    if effort:
        body["effort"] = effort
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    timeout = float(cfg.get("timeout") or 45)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    except Exception:
        return None
    try:
        text = (payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("「」\"'“”")
    max_c = int(cfg.get("max_caption_chars") or 120)
    if len(text) > max_c:
        text = text[: max_c - 1].rstrip() + "…"
    if not text:
        return None
    if use_cache:
        cache_put(
            sha,
            text,
            meta={
                "path": str(p),
                "prep_size": list(size),
                "prep_bytes": nbytes,
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "returned_model": payload.get("model"),
            },
            cfg=cfg,
        )
    return text


def format_tag_with_caption(tag: str, caption: str) -> str:
    """[图片] / [图片×2] + caption → [图片：…] / [图片×2：…]."""
    inner = tag.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    cap = (caption or "").strip()
    if not cap:
        return f"[{inner}]"
    return f"[{inner}：{cap}]"


def enrich_text(text: str, image_paths: list[str] | None, *, cfg: dict | None = None) -> tuple[str, dict]:
    """Replace first bare [图片]/×N] with bracket caption. Soft-fail keeps original.

    Returns (new_text, info) where info has ok/caption/cached/error/path/hash.
    """
    cfg = cfg or _cfg()
    info: dict[str, Any] = {"ok": False, "caption": None, "path": None, "hash": None, "cached": False}
    text = text or ""
    if not cfg.get("enabled", True):
        info["error"] = "disabled"
        return text, info
    if re.search(r"\[图片(?:×\d+)?：", text):
        info["error"] = "already_enriched"
        info["ok"] = True
        return text, info
    m = _TAG_RE.search(text)
    if not m:
        info["error"] = "no_tag"
        return text, info
    paths = [p for p in (image_paths or []) if p and pathlib.Path(p).expanduser().is_file()]
    if not paths:
        info["error"] = "no_local_image"
        return text, info
    path = paths[0]
    info["path"] = path
    try:
        sha = file_hash(path)
        info["hash"] = sha
        if cache_get(sha, cfg):
            info["cached"] = True
    except OSError as e:
        info["error"] = f"hash:{type(e).__name__}"
        return text, info
    cap = caption_image(path, cfg=cfg, use_cache=True)
    if not cap:
        info["error"] = "caption_failed"
        return text, info
    info["ok"] = True
    info["caption"] = cap
    enriched = format_tag_with_caption(m.group(0), cap)
    new_text = text[: m.start()] + enriched + text[m.end() :]
    return new_text, info


def enrich_inbound(msg, *, cfg: dict | None = None):
    """Mutate Inbound.text in place when image paths exist under raw._xiaotian_media."""
    cfg = cfg or _cfg()
    media = ((getattr(msg, "raw", None) or {}).get("_xiaotian_media") or {})
    paths = list(media.get("image_paths") or [])
    # also accept top-level meta for tests
    if not paths and isinstance(getattr(msg, "raw", None), dict):
        paths = list((msg.raw.get("image_paths") or []))
    new_text, info = enrich_text(getattr(msg, "text", "") or "", paths, cfg=cfg)
    if info.get("ok") and new_text != (msg.text or ""):
        msg.text = new_text
    # stash for callers / logs
    if isinstance(msg.raw, dict):
        msg.raw = {**msg.raw, "_xiaotian_caption": info}
    return msg, info
