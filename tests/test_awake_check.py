import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import sleep_chat as sc

assert sc.is_awake_check_text("活动了？")
assert sc.is_awake_check_text("还活动了？")
assert sc.is_awake_check_text("活动")
assert sc.is_awake_check_text("hd =活动了？懂吗哈哈哈")
assert not sc.is_awake_check_text("明天社团活动报名")
print("ok awake_check")
