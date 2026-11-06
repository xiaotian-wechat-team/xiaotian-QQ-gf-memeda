"""通道无关的入站/出站消息。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Inbound:
    channel: str          # wechat_ilink | qq_onebot | wechat_mac_ocr | …
    peer_id: str          # 通道侧对方 id（ilink user / qq uin / 微信备注键）
    text: str
    msg_id: str = ""
    ts: str = ""          # ISO 或通道原始时间
    msg_type: str = "text"
    raw: dict = field(default_factory=dict)

    @property
    def alias_key(self) -> str:
        return f"{self.channel}:{self.peer_id}"


@dataclass
class Outbound:
    channel: str
    peer_id: str
    text: str
    meta: dict = field(default_factory=dict)
