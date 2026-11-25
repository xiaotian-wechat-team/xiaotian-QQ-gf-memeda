"""Version-bound Codex adapter. Persona SSOT: data/persona_dossier.json.

The host provisions credentials and selects this command. No shared output
directory, fallback account, login, or transport is used by this adapter.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from native_gateway import validate_payload
from personalization_store import checked, digest


def asset_digest(root):
    root = checked(root)
    return digest({str(path.relative_to(root)): {"sha256": hashlib.sha256(checked(path).read_bytes()).hexdigest(),
                                                "size": path.stat().st_size}
                   for path in sorted(root.rglob("*")) if path.is_file()})


def codex_batch(command, prompt, refs, job, count):
    args = [command, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(job)]
    for path in refs:
        args.extend(["-i", str(path)])
    names = [f"result-{i + 1}.png" for i in range(count)]
    task = (prompt + f"\n用内置image_gen连续生成{count}张，同一人物与场景，构图自然变化。"
            + "禁止程序绘图、拼图或用旧图片替代。将本次实际生成文件分别复制为当前目录："
            + "、".join(names) + "；全部完成后停止。 $imagegen")
    result = subprocess.run(args, input=task, text=True, capture_output=True, cwd=job,
                            env=dict(os.environ), timeout=850)
    (job / "provider.jsonl").write_text(result.stdout)
    if result.returncode:
        raise RuntimeError("本人物生图提供方未成功完成请求")
    return names


def generate(request, command, *, runner=codex_batch):
    if not Path(command).is_absolute():
        raise ValueError("absolute Codex executable required")
    if request.get("operation") != "image_generate":
        raise ValueError("wrong native provider operation")
    payload = request["payload"]
    validate_payload("image_generate", payload)
    persona = payload.get("persona")
    if not persona or persona != {"version": request.get("persona_version"), "assets_sha256": request.get("assets_sha256")}:
        raise PermissionError("provider persona version mismatch")
    root, job = checked(request["assets_dir"]), checked(request["output_dir"])
    if asset_digest(root) != persona["assets_sha256"]:
        raise PermissionError("provider assets changed")
    import refpick
    import shot_modes
    from image_context import resolve
    cfg = {"appearance": {"profile_dir": str(root), "extra_sheets": False}}
    ctx = resolve(payload["scene"], payload.get("context"))
    count = ctx.get("n_images", 1)
    shots = ctx.get("shot_types") or [ctx.get("shot_type") or shot_modes.SHOT_THIRD] * count
    refs, brief = [], []
    for index, shot in enumerate(shots):
        if not refpick.canonical_refs(cfg):
            raise ValueError("请先应用本人物的标准脸部素材")
        if shot in {shot_modes.SHOT_THIRD, shot_modes.SHOT_FULL_BODY} and not refpick.body_orbit_refs(cfg):
            raise ValueError("请先应用本人物批准的全身五视图")
        selected, _ = refpick.pick_identity(payload["scene"], n_other=0, cfg=cfg, shot_type=shot)
        for path in selected:
            path = checked(path)
            if root not in path.parents:
                raise PermissionError("reference escapes current persona assets")
            if path not in refs:
                refs.append(path)
        brief.append(f"第{index + 1}张：" + (shot_modes.prompt_shot_block(shot, payload["scene"]) or "人物自然融入场景"))
    if len(refs) > 5:
        raise ValueError("reference count exceeds five")
    mapping = "\n".join(f"Image {i + 1}: {path.relative_to(root)}" for i, path in enumerate(refs))
    prompt = ("生成自然真实手机照片。当前人物版本：" + persona["version"] + "\n"
              + payload.get("preferences", "") + "\n场景与活动：" + payload["scene"]
              + "\n本次拍摄上下文：" + json.dumps(ctx, ensure_ascii=False, default=str)
              + "\n" + mapping + "\n" + "\n".join(brief)
              + "\nface只锁脸，body只锁身形比例；模卡服装、表情、背景均不能复刻。"
              "hands或legs仅在相应专用素材实际出现时锁对应局部；第一视角不要画出完整自拍脸。"
              "同批人物、地点和拍摄时刻一致。日常穿搭按场景与人物偏好换装。"
              "不得将生成完成时间当成拍摄时间；地点、室内外、天气和光照服从本次上下文，未知不猜。")
    names = runner(command, prompt, refs, job, count)
    if names != [f"result-{i + 1}.png" for i in range(count)]:
        raise ValueError("provider returned unexpected output names")
    from PIL import Image
    for name in names:
        path = checked(job / name)
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("provider did not return a bounded task image")
        with Image.open(path) as im:
            if im.format != "PNG":
                raise ValueError("provider output must be PNG")
            im.verify()
    # Recheck after generation before acknowledging actual use.
    if asset_digest(root) != persona["assets_sha256"]:
        raise PermissionError("provider assets changed during generation")
    return {"image_files": names, "persona_version": persona["version"], "assets_sha256": persona["assets_sha256"]}
