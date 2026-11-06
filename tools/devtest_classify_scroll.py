"""只读真机：在当前聊天面板内滚轮上翻若干屏并截图（不点击、不发送），持 ui_lock。"""
import sys, time, shutil, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import Quartz, mac_wechat_io as m, ui_lock as ul
def scroll(lines):
    _, x, y, w, h = m.window()
    pt = (x + (m.SIDEBAR_W + w) / 2, y + 250)
    mv = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, 0); Quartz.CGEventPost(Quartz.kCGHIDEventTap, mv)
    for _ in range(abs(lines)):
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, 1 if lines > 0 else -1)
        Quartz.CGEventSetLocation(ev, pt); Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev); time.sleep(0.02)
    time.sleep(0.6)
if __name__ == "__main__":
    n, step = int(sys.argv[1]), int(sys.argv[2])
    out = pathlib.Path(m.SHOT).parent / "devtest"
    with ul.ui_lock("reply"):
        m.activate_if_needed()
        assert m.in_chat_with(), "not in target chat"
        for i in range(n):
            m.capture(); shutil.copy(m.SHOT, out / f"scroll_{i}.png")
            scroll(step)
