"""底层门闩：闲聊不得生图/hold_on；明确要图才允许。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import photo_scene

assert photo_scene.allow_inbound_image_gen("干嘛呢") is False
assert photo_scene.allow_inbound_image_gen("在干嘛") is False
assert photo_scene.allow_inbound_image_gen("嗨") is False
assert photo_scene.allow_inbound_image_gen("发张图") is True
assert photo_scene.allow_inbound_image_gen("来张自拍") is True
assert photo_scene.allow_inbound_image_gen("看看手") is True
assert photo_scene.allow_inbound_image_gen("看看腿") is True
assert photo_scene.user_asks_photo("干嘛呢") is False
print("test_inbound_image_gate OK")
