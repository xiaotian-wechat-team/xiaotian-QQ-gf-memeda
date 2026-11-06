"""Share / surf_share send lane — decoupled from chat bubble_split.

Order (product): caption bubbles → source_url (one intact message) → image.

Caption text alone may pass through bubble_split. The link never enters
split_bubbles / pack_bubbles; it is sent as its own step.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def peel_caption_and_link(
    caption: str | None,
    *,
    source_url: str | None = None,
) -> Tuple[str, str]:
    """Return (caption_without_urls, link_to_send).

    Prefer explicit source_url; otherwise keep a URL peeled from caption as the
    link step. Caption that goes to bubble_split must not contain the link.
    """
    from bubble_split import extract_urls

    cap = (caption or "").strip()
    preferred = (source_url or "").strip()
    rest, found = extract_urls(cap)
    link = preferred or (found[0] if found else "")
    # Drop any URL lines/fragments from caption; link lane owns them.
    return (rest or "").strip(), link


def split_caption_bubbles(caption: str | None, **split_kw) -> List[str]:
    """Chat-style bubbles for share caption only (no link)."""
    text = (caption or "").strip()
    if not text:
        return []
    try:
        from bubble_split import split_bubbles
        return list(split_bubbles(text, **split_kw) or [text])
    except Exception:
        return [text]


def share_send_plan(
    caption: str | None,
    *,
    source_url: str | None = None,
    image_path: str | None = None,
    **split_kw,
) -> dict:
    """Dry-friendly plan: caption bubbles, link atom, image path."""
    cap, link = peel_caption_and_link(caption, source_url=source_url)
    bubbles = split_caption_bubbles(cap, **split_kw)
    return {
        "caption": cap,
        "bubbles": bubbles,
        "link_url": link or "",
        "image_path": str(image_path) if image_path else "",
        "order": ["caption", "link", "image"],
    }
