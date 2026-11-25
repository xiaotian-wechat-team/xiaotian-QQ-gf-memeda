"""Block ungrounded life_order completion claims (已下单 / 取餐码 / 司机)."""
from __future__ import annotations

import re

# First-person / assertive completion claims without real order_result.
_ORDERED = re.compile(
    r"(?:我\s*)?(?:(?:已经|刚|刚刚|刚才)\s*)?(?:帮你|给你|替你)?(?:下好单|下单成功|下单了|订好了|点好了|买好了)"
    r"|(?:帮你)(?:下好单|下单成功|下单了)"
    r"|(?:下单成功|下好单了)"
)
# Soft confirm 「那我给你下单了哦」 is persona wording, not a completion claim.
_SOFT_OK = ("你求求我嘛～", "那我给你下单了哦")
_PICKUP = re.compile(r"(?:取餐码|取件码|餐码)(?:是|为)?\s*[A-Za-z0-9]+")
_DRIVER = re.compile(
    r"(?:司机)(?:已经)?(?:接单|出发|到了|在路上|来了)|(?:车)(?:已经)?(?:到了|在路上)"
)
_SAFE = "这单还没真下成"


def order_result_ok(order_result: dict | None) -> bool:
    """True only when a real successful placement result is present."""
    if not isinstance(order_result, dict):
        return False
    if order_result.get("status") not in ("ok", "success"):
        return False
    # dry_run results must not authorize completion talk
    if order_result.get("dry_run"):
        return False
    return True


def scrub_order_claims(text: str, order_result: dict | None = None) -> str:
    """Rewrite ungrounded 已下单/取餐码/司机 claims when there is no real success."""
    if order_result_ok(order_result):
        return text or ""
    parts = re.split(r"([\n。！？!?；;，,])", text or "")
    for i in range(0, len(parts), 2):
        part = parts[i]
        if not part or not part.strip():
            continue
        if part.strip() in _SOFT_OK or any(s in part for s in _SOFT_OK):
            continue
        # Skip questions and clear negation / future intent.
        if re.search(r"(?:吗|嘛|？|\?)$", part.strip()):
            continue
        if re.search(r"(?:没|未|别|不要|还没|不会|准备|想|要不要)", part):
            continue
        if _ORDERED.search(part) or _PICKUP.search(part) or _DRIVER.search(part):
            parts[i] = _SAFE
    return "".join(parts)
