"""proactive send_via_onebot：配文气泡 →（可选）整链 → 无配文图片；链接不经 bubble_split。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import proactive_sender as S
from channels.share_send import share_send_plan, peel_caption_and_link

calls = []

def fake_text(api, peer, text, token=""):
    calls.append(("text", text))
    return {"retcode": 0, "status": "ok"}

def fake_image(api, peer, path, *, caption=None, token=""):
    calls.append(("image", path, caption))
    return {"retcode": 0, "status": "ok"}

import channels.onebot_qq as ob
ob.send_private_text_http = fake_text
ob.send_private_image_http = fake_image
S._onebot_peer_and_cfg = lambda: ("10001", {"http_api": "http://127.0.0.1:3000", "ws_token": ""})

# 1) caption + image, no link
calls.clear()
ok, why, *_ = S.send_via_onebot("今天课有点满\n晚上去练功房", image_path="/tmp/fake.png")
assert ok, (ok, why)
assert calls[0][0] == "text" and "课" in calls[0][1], calls
assert calls[-1][0] == "image" and calls[-1][2] in (None, ""), calls
assert all(c[0] != "image" or i == len(calls) - 1 for i, c in enumerate(calls)), calls
print("caption_then_image OK", calls)

# 2) share lane: caption → intact URL → image (URL never fragmented)
calls.clear()
url = "https://www.douyin.com/video/7455542024306052389"
cap = "学长\n刷到个滑雪的\n崇礼雪如意\n寒假人得挤爆吧"
# if URL wrongly glued into caption, peel must still isolate it
glued = cap + "\n" + url
peeled_cap, peeled_link = peel_caption_and_link(glued, source_url=url)
assert url not in peeled_cap and peeled_link == url, (peeled_cap, peeled_link)
plan = share_send_plan(cap, source_url=url, image_path="/tmp/ski.png")
assert plan["link_url"] == url
assert url not in "\n".join(plan["bubbles"])
assert all(not b.startswith("http") for b in plan["bubbles"]), plan

ok, why, *_ = S.send_via_onebot(cap, image_path="/tmp/ski.png", link_url=url)
assert ok, (ok, why)
kinds = [c[0] for c in calls]
assert kinds[-1] == "image", calls
assert ("text", url) in calls, calls
# URL is exactly one text call, intact
url_calls = [c for c in calls if c[0] == "text" and "douyin" in c[1]]
assert url_calls == [("text", url)], url_calls
# order: all caption texts, then url, then image
url_i = next(i for i, c in enumerate(calls) if c == ("text", url))
img_i = next(i for i, c in enumerate(calls) if c[0] == "image")
assert url_i < img_i
assert all(c[0] == "text" for c in calls[:url_i])
print("share_lane caption→link→image OK", calls)

# 3) unit: plan never feeds URL into bubble_split path
from bubble_split import split_bubbles
bubs = split_bubbles(plan["caption"])
assert url not in bubs and not any("douyin" in b for b in bubs)
print("ALL OK")
