"""Persistent share identities. Persona SSOT: data/persona_dossier.json.

Only proactive material shares; never dedupe ordinary conversation by topic.
Reservations survive uncertain delivery; only the owning outbox round may retry.
"""
import hashlib
import pathlib
import re
from urllib.parse import urlsplit, parse_qsl, urlencode

STATE_KEY = "share_dedupe"


def canonical_url(url):
    p = urlsplit((url or "").strip())
    host = (p.hostname or "").lower()
    if not host:
        return ""
    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"):
        m = re.search(r"/(?:explore|discovery/item|search_result)/([a-zA-Z0-9]+)", p.path)
        if m:
            return "xiaohongshu:" + m[1].lower()
    if host == "douyin.com" or host.endswith(".douyin.com"):
        m = re.search(r"/(?:video|note)/(\d+)", p.path)
        iid = m[1] if m else dict(parse_qsl(p.query)).get("modal_id")
        if iid and iid.isdigit():
            return "douyin:" + iid
    # Unknown URLs: retain content-bearing query parameters.
    query = [(k, v) for k, v in parse_qsl(p.query) if not
             (k.lower().startswith("utm_") or k.lower() in
              {"xsec_token", "xsec_source", "share_token", "share_app_id", "timestamp"})]
    return host + (":" + str(p.port) if p.port else "") + p.path.rstrip("/") + (
        "?" + urlencode(sorted(query)) if query else "")


def keys(material):
    material = material or {}
    out = set()
    url = canonical_url(material.get("source_url"))
    if url:
        out.add("url:" + url)
    paths = [material.get("image_path")]
    paths.extend(m.get("local_path") for m in material.get("media", []) if m.get("kind") == "image")
    for path in paths:
        if path and pathlib.Path(path).is_file():
            with open(path, "rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            out.add("image:" + digest)
    out.update(material.get("dedupe_keys") or [])
    return out


def history(st):
    ledger = dict((st or {}).get(STATE_KEY) or {})
    for share in ((st or {}).get("proactive_beats") or {}).get("shares", []):
        for key in keys(share):
            ledger.setdefault(key, {"owner": "legacy", "status": "sent"})
    return ledger


def material_of(item):
    bm = (item.get("meta") or {}).get("beat_meta") or {}
    if bm.get("id") == "surf_share" or str(item.get("topic", "")).endswith("surf_share"):
        return bm.get("material") or {}
    return {}


def duplicate(material, st, owner=None):
    ledger = history(st)
    return any(k in ledger and (owner is None or ledger[k].get("owner") != owner)
               for k in keys(material))


def reserve(cid, item, now, *, retry=False):
    """Atomically claim before any network send. Failure stays reserved, fail closed."""
    import memory
    material = material_of(item)
    identity = keys(material)
    if not identity:
        return not material
    accepted = False
    def change(st):
        nonlocal accepted
        ledger = history(st)
        if any(k in ledger and (not retry or ledger[k].get("owner") != item["id"]
                                 or ledger[k].get("status") == "sent") for k in identity):
            return {}
        accepted = True
        for key in identity:
            ledger.setdefault(key, {"owner": item["id"], "status": "reserved", "at": str(now)})
        return {STATE_KEY: ledger}
    memory.mutate_state(cid, change)
    return accepted


def receipt_patch(st, item, now):
    material = material_of(item)
    if not material:
        return {}
    ledger = history(st)
    for key in keys(material):
        ledger[key] = {"owner": item["id"], "status": "sent", "at": str(now)}
    return {STATE_KEY: ledger}


def migrate(cid, items):
    """Bootstrap existing sent/uncertain queue rows and legacy history, never truncate."""
    import memory
    def change(st):
        ledger = history(st)
        for item in items:
            if item.get("status") not in ("sent", "hold"):
                continue
            for key in keys(material_of(item)):
                ledger.setdefault(key, {"owner": item["id"], "status": item["status"]})
        return {STATE_KEY: ledger} if ledger != st.get(STATE_KEY, {}) else {}
    return memory.mutate_state(cid, change)
