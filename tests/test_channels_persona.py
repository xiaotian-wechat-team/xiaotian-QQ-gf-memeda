"""离线：persona 映射 + OneBot / iLink 归一化。不连网、不写微信、不启 daemon。"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from channels.persona_map import (
    resolve_contact_id, persona_cfg, is_couple_contact, is_classmate_contact,
    classmate_address, CLASSMATE_PREFIX,
)
from channels.onebot_qq import normalize_event, _seg_text
from channels.ilink_weixin import normalize_raw_msg, CHANNEL as ILINK_CH, self_test as ilink_self_test
from channels.types import Inbound
from channels.ilink_smoke import check as ilink_check

cfg = {
    "contact": {"id": "demo_contact"},
    "persona": {
        "id": "xiaotian",
        "primary_contact_id": "demo_contact",
        "contact_aliases": {
            "wechat:demo_contact": "demo_contact",
            "qq_onebot:10001": "demo_contact",
            "wechat_ilink:demo@im.wechat": "demo_contact",
        },
    },
}
assert resolve_contact_id("wechat", "demo_contact", cfg) == "demo_contact"
assert resolve_contact_id("qq_onebot", "10001", cfg) == "demo_contact"
assert resolve_contact_id("wechat_ilink", "demo@im.wechat", cfg) == "demo_contact"
# P0: 未登记 peer → classmate，不再回落到情侣 primary
cid_stranger = resolve_contact_id("qq_onebot", "1", cfg)
assert is_classmate_contact(cid_stranger), cid_stranger
assert cid_stranger.startswith(CLASSMATE_PREFIX)
assert not is_couple_contact(cid_stranger, cfg)
# 危险通配 star→primary 必须忽略
cfg2 = json.loads(json.dumps(cfg))
cfg2["persona"]["contact_aliases"]["qq_onebot:*"] = "demo_contact"
assert is_classmate_contact(resolve_contact_id("qq_onebot", "777", cfg2))
# star→非 primary 仍可用
cfg3 = json.loads(json.dumps(cfg))
cfg3["persona"]["contact_aliases"]["qq_onebot:*"] = "classmate_pool"
assert resolve_contact_id("qq_onebot", "777", cfg3) == "classmate_pool"

assert classmate_address("20002", {"sender": {"nickname": "小明"}}) == "小明同学"

ev = {
    "post_type": "message", "message_type": "private", "user_id": 10001,
    "message": [{"type": "text", "data": {"text": "你好呀"}}],
    "message_id": 42, "time": 1700000000,
}
m = normalize_event(ev)
assert isinstance(m, Inbound)
assert m.channel == "qq_onebot" and m.peer_id == "10001" and m.text == "你好呀"
assert normalize_event({"post_type": "meta_event"}) is None
assert normalize_event({"post_type": "message", "message_type": "group", "user_id": 1, "message": "x"}) is None
assert _seg_text("纯文本") == "纯文本"

raw = {
    "message_type": 1,
    "from_user_id": "demo@im.wechat",
    "message_id": 7,
    "create_time_ms": 1700000000000,
    "item_list": [{"type": 1, "text_item": {"text": "ilink你好"}}],
}
im = normalize_raw_msg(raw)
assert im and im.channel == ILINK_CH and im.peer_id == "demo@im.wechat" and im.text == "ilink你好"
assert normalize_raw_msg({"message_type": 2, "from_user_id": "demo@im.wechat", "item_list": []}) is None

a = resolve_contact_id("wechat_ilink", "demo@im.wechat", cfg)
b = resolve_contact_id("qq_onebot", "10001", cfg)
assert a == b == "demo_contact"

assert ilink_self_test() == 0

r = ilink_check()
assert r["primary"] == "self_built_python"
assert r["openclaw_required"] is False
assert r["sdk_ok"] is True

p = persona_cfg()
assert p["primary_contact_id"] and p["id"] == "xiaotian"
wil = (p.get("channels") or {}).get("wechat_ilink") or {}
assert wil.get("provider") == "weixin-ilink"
assert "ilink_creds" in str(wil.get("credentials_file") or "")
# live: wildcard removed
assert "wechat_ilink:*" not in (p.get("contact_aliases") or {})
print("OK")
