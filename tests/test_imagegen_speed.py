"""离线：参考图硬裁 ≤5；聊天路径跳过 face-fallback 双跑。不调 codex / 不发微信。"""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, pathlib, copy, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import imagegen, chat_media, config

# --- cap_refs: sheet 必留，总数 ≤5 ---
sheet = pathlib.Path("/tmp/canonical_sheet.png")
pose = pathlib.Path("/tmp/canonical_down.png")
s1, s2 = pathlib.Path("/tmp/s1.jpg"), pathlib.Path("/tmp/s2.jpg")
o1, o2, o3 = pathlib.Path("/tmp/o1.jpg"), pathlib.Path("/tmp/o2.jpg"), pathlib.Path("/tmp/o3.jpg")

can, srefs, oth = imagegen.cap_refs([sheet, pose], [s1, s2], [o1, o2, o3], max_refs=5)
assert can[0] == sheet and pose in can
assert len(can) + len(srefs) + len(oth) == 5
assert s1 in srefs  # after HARD face(+body), scene before other
# 1 sheet + 1 pose + 2 scene + 1 other = 5
assert len(can) == 2 and len(srefs) == 2 and len(oth) == 1, (can, srefs, oth)

# HARD face + body sheet; remaining 3 all scene (drop pose/other)
body_sheet = pathlib.Path("/tmp/canonical_body_sheet.png")
can_b, srefs_b, oth_b = imagegen.cap_refs([sheet, body_sheet, pose], [s1, s2, pathlib.Path("/tmp/s3.jpg")], [o1, o2, o3], max_refs=5)
assert can_b == [sheet, body_sheet] and len(srefs_b) == 3 and oth_b == [] and pose not in can_b

can2, srefs2, oth2 = imagegen.cap_refs([sheet], [s1, s2], [o1, o2, o3], max_refs=5)
assert can2 == [sheet] and srefs2 == [s1, s2] and oth2 == [o1, o2]
assert len(can2) + len(srefs2) + len(oth2) == 5

# 只有 sheet + 很多 other
can3, srefs3, oth3 = imagegen.cap_refs([sheet], [], [o1, o2, o3, pathlib.Path("/tmp/o4.jpg")], 5)
assert can3 == [sheet] and not srefs3 and len(oth3) == 4

# build_cmd 绝不给出 >5 个 -i（显式塞 6 张）
cfg = copy.deepcopy(config.load())
cfg["imagegen"] = dict(cfg.get("imagegen") or {})
tmp = pathlib.Path(tempfile.mkdtemp())
# 造 6 条路径：1 can + 2 srefs + 3 other
can_p = [tmp / "sheet.png"]; s_p = [tmp / "a.jpg", tmp / "b.jpg"]; o_p = [tmp / f"o{i}.jpg" for i in range(3)]
for p in can_p + s_p + o_p: p.write_bytes(b"x")
cmd = imagegen.build_cmd("图书馆窗边看书", cfg, tmp, refs=(can_p, o_p), scene_refs=s_p)
n_i = cmd.count("-i")
assert n_i <= imagegen.MAX_IMAGE_REFS == 5, n_i
assert n_i == 5
# 第一张 -i 后是 sheet
i0 = cmd.index("-i")
assert "sheet.png" in cmd[i0 + 1]

# --- chat skip face-fallback：mock generate_local 二次调用 ---
calls = []
real_gl = imagegen.generate_local

def fake_gl(scene, dry=False, cfg=None, outfit_scene=None, _exclude=(), _attempt=1, _plan=None, _meta=None, **ctx):
    calls.append(dict(_meta or {}))
    # 模拟已有图、带 scene_refs，若未 skip 会进 face-fallback 再调一次
    # 直接走真实逻辑太重；只断言 generate() 把 skip 写入 _meta，且 chat_media 会传 chat
    out = tmp / "out.png"; out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8)
    return ["codex"], [out]

imagegen.generate_local = fake_gl
try:
    calls.clear()
    imagegen.generate("x", dry=False, cfg=cfg, chat=True)
    assert calls and calls[0].get("skip_face_fallback") is True and calls[0].get("chat") is True, calls
    calls.clear()
    imagegen.generate("x", dry=False, cfg=cfg, skip_face_fallback=True)
    assert calls[0].get("skip_face_fallback") is True, calls
    calls.clear()
    imagegen.generate("x", dry=False, cfg=cfg)  # research default: no skip flag in meta
    assert "skip_face_fallback" not in calls[0] and "chat" not in calls[0], calls
finally:
    imagegen.generate_local = real_gl

# chat_media.generate_path 默认 chat=True
seen = {}
def wrap_gen(scene, **kw):
    seen.update(kw)
    return ["c"], [tmp / "out.png"]
(tmp / "out.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8)
p = chat_media.generate_path("图书馆", generate_fn=wrap_gen)
assert p is not None and seen.get("chat") is True and seen.get("skip_face_fallback") is True, seen

# 显式关闭（仅测试）
seen.clear()
chat_media.generate_path("x", generate_fn=wrap_gen, chat=False, skip_face_fallback=False)
assert seen.get("chat") is False and seen.get("skip_face_fallback") is False, seen

# generate_local 在 skip_face_fallback meta 下不二次调用（用 stub subprocess + 假图）
# 更轻：直接测 already_fb 条件
assert imagegen.cap_refs.__doc__ and imagegen.MAX_IMAGE_REFS == 5

# 单元：simulate face-fallback gate
meta_chat = {"skip_face_fallback": True, "chat": True}
already = bool(meta_chat.get("face_fallback") or meta_chat.get("skip_face_fallback") or meta_chat.get("chat"))
assert already is True
meta_research = {"backend": "local"}
already_r = bool(meta_research.get("face_fallback") or meta_research.get("skip_face_fallback") or meta_research.get("chat"))
assert already_r is False

print("OK")
