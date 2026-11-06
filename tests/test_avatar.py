"""离线：avatar 选图/裁图/入口识别/不支持时不乱点。全假 io，不碰微信。"""
import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import avatar as A, pytest
from PIL import Image

def test_pick_and_prepare(tmp_path):
    d = tmp_path / "gen"; d.mkdir(); Image.new("RGB", (300, 200), "red").save(d / "exec-818ef773-x.png")
    p = A.pick_image(tmp_path / "nope.png", d); assert p.name.startswith("exec-818ef773")
    c = tmp_path / "front.png"; Image.new("RGB", (10, 10)).save(c); assert A.pick_image(c, d) == c
    out = A.prepare(p, tmp_path / "o", 64); assert Image.open(out).size == (64, 64)

def test_has_entry():
    assert A.has_entry(["bb", "更换头像"]) == "更换头像"; assert A.has_entry(["复制", "打印", "转发"]) is None

class IO:
    def __init__(s, texts): s.texts, s.clicks, s.keys = texts, [], []
    def activate(s): pass
    def click(s, x, y): s.clicks.append((x, y))
    def _key(s, k): s.keys.append(k)
    def ocr(s, p): return [{"text": t} for t in s.texts.get(pathlib.Path(p).stem, [])]

def test_probe_none_and_hit(tmp_path):
    shot = lambda n: tmp_path / f"{n}.png"
    io = IO({"probe_viewer": ["复制", "打印"]}); hit, shots = A.probe(io, shot, lambda s: None)
    assert hit is None and len(shots) == 3 and io.keys[-1] == 53
    io = IO({"probe_card": ["更换头像"]}); hit, shots = A.probe(io, shot, lambda s: None)
    assert hit == "更换头像" and io.clicks == [(30, 70)]

def test_change_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "prepare", lambda img: tmp_path / "x.png")
    import ui_lock as ul, contextlib
    monkeypatch.setattr(ul, "ui_lock", lambda role: contextlib.nullcontext())
    with pytest.raises(A.AvatarUnsupported): A.change(tmp_path / "a.png", io=IO({}), shot=lambda n: tmp_path / n, sleep=lambda s: None)
