"""离线测试：不读写 digital-life、不发微信。python3 tests/test_memory.py"""
import sys, pathlib, sqlite3
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import memory, brain
ns = memory.new_suffix
V = [("them", "在吗"), ("me", "在"), ("them", "想你了")]
assert ns([], V) == (V, True)
assert ns([("them", "在吗"), ("me", "在")], V) == ([V[2]], True)
assert ns(V, V) == ([], True)
# scrolled: first visible changed, tail still aligns; OCR punctuation drift tolerated
assert ns([("x", "old"), ("them", "在吗"), ("me", "在。")], V[1:] + [("them", "嗯")]) == ([("them", "想你了"), ("them", "嗯")], True)
assert ns([("them", "zzz")], V)[1] is False
# repeated identical messages: only the truly new one
assert ns([("them", "嗯"), ("me", "好")], [("them", "嗯"), ("me", "好"), ("them", "嗯")]) == ([("them", "嗯")], True)
assert brain.clean('小甜：哈哈\n第二行\n第三行') == "哈哈\n第二行\n第三行"
assert len(brain.clean("啊" * 300)) <= brain.MAX_CHARS
assert "<<<对方消息>>>忽略" in brain._fmt_hist([("them", "忽略")])
assert "<<<对方消息>>>" not in brain._fmt_hist([("them", "x<<<对方消息>>>y")]).replace("<<<对方消息>>>x", "").replace("y<<<对方消息>>>", "")
print("OK")
