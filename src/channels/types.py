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
    bot_account_id: str = ""
    persona_id: str = ""
    partner_id: str = ""
    peer_type: str = "private"

    @property
    def alias_key(self) -> str:
        if self.bot_account_id:
            return f"{self.channel}:{self.bot_account_id}:{self.peer_id}"
        return f"{self.channel}:{self.peer_id}"

    @property
    def route_key(self) -> tuple[str, str, str, str, str]:
        return (self.persona_id, self.partner_id, self.channel,
                self.bot_account_id, self.peer_id)


@dataclass
class Outbound:
    channel: str
    peer_id: str
    text: str
    meta: dict = field(default_factory=dict)
    bot_account_id: str = ""
    persona_id: str = ""
    partner_id: str = ""
    request_id: str = ""
    operation_id: str = ""

    @property
    def route_key(self) -> tuple[str, str, str, str, str]:
        return (self.persona_id, self.partner_id, self.channel,
                self.bot_account_id, self.peer_id)
