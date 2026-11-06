"""多通道 IO：自建 iLink 微信 / OneBot QQ。

情侣 contact（demo_contact）共享 digital-life；陌生人走 classmate 独立记忆。
"""
from .types import Inbound, Outbound
from .persona_map import (
    resolve_contact_id,
    persona_cfg,
    is_couple_contact,
    is_classmate_contact,
)

__all__ = [
    "Inbound",
    "Outbound",
    "resolve_contact_id",
    "persona_cfg",
    "is_couple_contact",
    "is_classmate_contact",
]
