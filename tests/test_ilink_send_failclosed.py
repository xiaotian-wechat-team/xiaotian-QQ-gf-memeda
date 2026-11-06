"""send_via_ilink / outbox: prepare failed must not look like SENT."""
import sys, pathlib, json, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import weixin_ilink.api as api
import proactive_sender as S
from channels import ilink_weixin as iw

# stub peer/ctx
S._ilink_peer_and_ctx = lambda: ("demo@im.wechat", "fake-token")

class _Opts: pass
class _Client:
    opts = _Opts()
    def _generate_client_id(self): return "cid"
class _Bot:
    def __init__(self, *a, **k):
        self.client = _Client()
        self._ctx_cache = {}
    def send_text(self, to, text, context_token=None):
        api.send_message(self.client.opts, {"to_user_id": to, "context_token": context_token, "item_list":[{"type":1,"text_item":{"text":text}}]})
    def send_image(self, *a, **k):
        raise RuntimeError("not used")

# patch WeixinBot import path used inside send_via_ilink
import weixin_ilink
weixin_ilink.WeixinBot = _Bot

_orig = api.send_message
api.send_message = lambda opts, msg, *a, **k: {"ret": -2, "errmsg": "prepare failed"}
try:
    # also stub creds
    iw._creds_path = lambda: pathlib.Path(__file__)  # exists
    ok, why, *_ = S.send_via_ilink("晚安测试")
    assert ok is False, (ok, why)
    assert "prepare failed" in why.lower(), why
    print("failclosed_ok", why)
finally:
    api.send_message = _orig

# success path
api.send_message = lambda opts, msg, *a, **k: {"ret": 0, "message_id": 4242}
try:
    ok, why, *_ = S.send_via_ilink("晚安测试")
    assert ok is True and "mid=4242" in why, (ok, why)
    print("success_ok", why)
finally:
    api.send_message = _orig

print("OK")
