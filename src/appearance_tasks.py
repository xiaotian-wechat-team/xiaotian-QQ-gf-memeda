"""Resumable original-photo SOP. SSOT: data/persona_dossier.json.

Task-owned candidates only: face -> first body front -> body orbit. Generation
is injectable for offline E2E; HTTP clients can never select a provider/command.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

from PIL import Image, ImageOps

from personalization_store import checked, digest, read_json, timestamp, write_json, Conflict
from personalization_schema import project

VIEWS = ("lprofile", "l34", "front", "r34", "rprofile")
STAGES = ("checking", "face", "body_front", "body")
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="appearance-sop")
_FUTURES = {}
_GUARD = threading.Lock()


def task_dir(store, task_id):
    if not isinstance(task_id, str) or len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
        raise ValueError("invalid appearance task")
    return checked(store.directory / "appearance" / task_id)


def appearance_fields(values):
    return {k: v for k, v in values.items() if k.startswith(("appearance.", "body.")) or k == "identity.gender"}


def fingerprint(values):
    return digest(appearance_fields(values))


def _photo_bytes(encoded):
    if not isinstance(encoded, str) or len(encoded) > 17 * 1024 * 1024:
        raise ValueError("每张原图不能超过12MB")
    raw = base64.b64decode(encoded, validate=True)
    if not 1 <= len(raw) <= 12 * 1024 * 1024:
        raise ValueError("原图大小无效")
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in {"JPEG", "PNG", "WEBP"} or getattr(im, "n_frames", 1) != 1:
            raise ValueError("请提供静态JPEG、PNG或WebP原图")
        if min(im.size) < 256 or im.width * im.height > 32_000_000:
            raise ValueError("请提供短边至少256像素且不超过3200万像素的照片")
        im.load()
        converted = ImageOps.exif_transpose(im).convert("RGB")
        out = io.BytesIO()
        converted.save(out, format="PNG")
    return raw, out.getvalue(), converted.size


def photo_bytes(encoded):
    try:
        return _photo_bytes(encoded)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("原图无法解码，请使用有效的JPEG、PNG或WebP照片") from exc


def create(store, photos, *, revision, staged_review=False, appearance_values=None):
    if not isinstance(photos, list) or not 2 <= len(photos) <= 16:
        raise ValueError("请提供2至16张原图，包含清晰脸部和完整全身参考")
    with store.lock():
        draft = store.draft()
        if draft["revision"] != revision:
            raise Conflict("草稿已变化，请保存后重新创建外貌任务")
        decoded = []
        for row in photos:
            if not isinstance(row, dict) or set(row) != {"name", "data"} or not isinstance(row["name"], str) or len(row["name"]) > 200:
                raise ValueError("invalid original photo")
            decoded.append((row["name"], *photo_bytes(row["data"])))
        if sum(len(row[1]) for row in decoded) > 64 * 1024 * 1024:
            raise ValueError("本次原图总大小不能超过64MB")
        task_id = uuid.uuid4().hex
        folder = task_dir(store, task_id)
        (folder / "originals").mkdir(parents=True, mode=0o700)
        (folder / "inputs").mkdir(mode=0o700)
        sources = []
        for index, (name, raw, normalized, size) in enumerate(decoded):
            key = str(index + 1)
            (folder / "originals" / key).write_bytes(raw)
            path = folder / "inputs" / f"{key}.png"
            path.write_bytes(normalized)
            sources.append(dict(name=name, file=str(path.relative_to(folder)), sha256=hashlib.sha256(normalized).hexdigest(), width=size[0], height=size[1]))
            sources[-1].update(original=f"originals/{key}", original_sha256=hashlib.sha256(raw).hexdigest())
        values = draft["values"] if appearance_values is None else appearance_values
        task = dict(id=task_id, role_id=store.persona, environment=store.environment, status="queued", stage=None,
                    relation_id=store.relation,
                    created_at=timestamp(), updated_at=timestamp(), revision=revision,
                    values=values, fingerprint=fingerprint(values), sources=sources,
                    completed={}, stage_assets={}, attempts={}, error=None, cancel_requested=False, owner_pid=None)
        if staged_review:
            task["stage_reviews"] = []
        write_json(folder / "task.json", task)
        return public(task)


def public(task):
    return {k: v for k, v in task.items() if k not in {"values"}}


def read(store, task_id):
    task = read_json(task_dir(store, task_id) / "task.json")
    if not task:
        raise ValueError("外貌任务不存在")
    if task["role_id"] != store.persona or task["environment"] != store.environment or task.get("relation_id", "partner") != store.relation:
        raise PermissionError("外貌任务归属不匹配")
    return task


def listing(store):
    return [public(recover(store, path.parent.name)) for path in sorted((store.directory / "appearance").glob("*/task.json"), reverse=True)]


@contextmanager
def task_claim(store, task_id):
    """One owner across processes, released by the OS even after a crash."""
    path = task_dir(store, task_id) / ".run.lock"
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def recover(store, task_id):
    folder = task_dir(store, task_id)
    with store.lock():
        task = read(store, task_id)
        # A queued worker must be able to claim its task while the UI polls this status.
        if task["status"] in {"running", "cancelling"}:
            with task_claim(store, task_id) as owned:
                if not owned:
                    return task
            task.update(status="cancelled" if task.get("cancel_requested") else "interrupted",
                        owner_pid=None, updated_at=timestamp(), error="上次生成进程已中断，可从已验证的阶段继续")
        elif task["status"] == "review" and not all(valid_stage(folder, task, stage) for stage in STAGES):
            task.update(status="interrupted", owner_pid=None, updated_at=timestamp(),
                        error="候选阶段素材缺失或已变化，可从已验证的阶段继续")
        elif task["status"] == "stage_review" and not all(valid_stage(folder, task, stage) for stage in task["completed"]):
            task.update(status="interrupted", updated_at=timestamp(), error="待确认素材已变化，请重新生成")
        else:
            return task
        write_json(folder / "task.json", task)
        return task


def artifact_path(folder, name):
    path = checked(folder / name)
    if not path.is_relative_to(folder):
        raise ValueError("任务素材路径无效")
    return path


def file_digest(folder, name):
    return hashlib.sha256(artifact_path(folder, name).read_bytes()).hexdigest()


def verify_inputs(folder, task):
    for source in task["sources"]:
        for name, expected in ((source["file"], source["sha256"]), (source.get("original"), source.get("original_sha256"))):
            if name and file_digest(folder, name) != expected:
                raise ValueError("原图与任务记录不一致，请使用原图新建任务")


def originals(store, task_id):
    task = read(store, task_id)
    folder = task_dir(store, task_id)
    verify_inputs(folder, task)
    return [{"name": source["name"], "data": base64.b64encode(artifact_path(folder, source["original"]).read_bytes()).decode()}
            for source in task["sources"]]


def stage_files(stage):
    if stage == "body_front":
        return ["body_front.png"]
    prefix = "canonical/body" if stage == "body" else "canonical"
    return [f"{prefix}/{name}.png" for name in ("sheet", *VIEWS)]


def valid_stage(folder, task, stage):
    if stage == "checking":
        return task["completed"].get(stage) == digest(task["sources"])
    assets = task.get("stage_assets", {}).get(stage) or {}
    if set(assets) != set(stage_files(stage)):
        return False
    try:
        return all(file_digest(folder, name) == sha for name, sha in assets.items())
    except OSError:
        return False


def probe(path):
    """Use local Vision to select references; quality scores are never approval."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    try:
        import frontal_extract as fe
        import Vision
        faces, _ = fe.faces(path)
        req = Vision.VNDetectHumanRectanglesRequest.alloc().init()
        Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(fe._cgimage(path), {}).performRequests_error_([req], None)
        people = list(req.results() or [])
        area = max((f["bbox"][2] * f["bbox"][3] for f in faces), default=0)
        full_body = any(p.boundingBox().size.height >= .65 for p in people) and 0 < area <= .12
        return dict(face_count=len(faces), face_area=area, full_body=full_body,
                    quality=max((float(f.get("quality") or 0) for f in faces), default=0))
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("原图质量检测尚不可用，请安装项目的本地Vision依赖后重试") from exc


def prompt(stage, values):
    projections = project(values)
    facts = {"identity": {"gender": values.get("identity.gender")},
             "body": projections["dossier"].get("body"), "appearance": projections["dossier"].get("appearance")}
    common = ("使用内置image_gen生成真实摄影标准素材，不用程序绘画。参考人物必须保持同一身份；未知身高体重不编造。\n"
              + json.dumps(facts, ensure_ascii=False) + "\n")
    order = "从画面左到右五格等宽：左侧90度、左45度、正面、右45度、右侧90度。无文字、编号或边框。"
    if stage == "face":
        return common + "原图属于同一个人。生成标准头肩五视图，保持原图脸型五官和发型，同一件素色圆领上衣、浅灰背景与均匀柔光，中性表情。" + order
    if stage == "body_front":
        return common + "图1是本次选定的脸部五视图，仅此锁脸；其余是本人的原始全身照片，只参考身形比例。首次建立正面全身基准：站姿自然、正对镜头、头顶和双脚完整，浅灰合身圆领T恤、黑色运动短裤、赤脚，浅灰背景、均匀柔光。不能拿他人SOP示例猜本人身形。"
    if stage == "body":
        return common + "Use case: identity-preserve. Image 1 is this task's body front CORE for body proportions, outfit and lighting ONLY. Images 2–5 are the selected face sheet/front/left profile/right profile: FACE HARD LOCK. Generate exactly FIVE full-length figures from head to bare feet; same gray fitted tee and black athletic shorts, no cropped feet. " + order + " 不修改脸部标准，不参考旧身体侧图。"
    raise ValueError("unknown canonical stage")


def local_provider(stage, text, refs, output, cfg):
    """Fixed provider: output must be placed in this exact invocation's folder."""
    if os.environ.get("XIAOTIAN_TEST"):
        raise RuntimeError("测试禁止真实生图，请注入假生成器")
    if (cfg.get("identity_runtime") or {}).get("schema_version") == 2:
        from native_client import invoke
        payload = dict(stage=stage, prompt=text, refs=[base64.b64encode(p.read_bytes()).decode() for p in refs])
        result = invoke("appearance_generate", payload, cfg)
        raw = base64.b64decode(result["image"], validate=True)
        output.write_bytes(raw)
        return
    from imagegen_scope import locked, environment
    command = (cfg.get("imagegen") or {}).get("cmd") or "codex"
    with locked(cfg):
        codex_provider(command, text, refs, output, environment(cfg))


def codex_provider(command, text, refs, output, env):
    args = [command, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", str(output.parent)]
    for ref in refs:
        args.extend(["-i", str(checked(ref))])
    args.extend(["--", "-"])
    result = subprocess.run(args, input=text + "\n生成后把实际产出复制为当前目录result.png；仅复制真实生成图，禁止用代码绘制替代。只生成一张后停止。 $imagegen",
                            text=True, capture_output=True, env=env, cwd=output.parent, timeout=900)
    (output.parent / "provider.jsonl").write_text(result.stdout)
    if result.returncode or not output.is_file():
        raise RuntimeError("此阶段未返回任务内图片；请检查本人物生图服务的登录与可用状态后重试")


def split_sheet(source, destination):
    checked(destination).mkdir(parents=True, exist_ok=True)
    with Image.open(checked(source)) as im:
        im.load()
        if im.width < 5 * 128 or im.height < 128 or im.width <= im.height:
            raise ValueError("五视图总览应为横向五格，请重试此阶段")
        for index, view in enumerate(VIEWS):
            im.crop((im.width * index // 5, 0, im.width * (index + 1) // 5, im.height)).save(destination / (view + ".png"))
    shutil.copyfile(source, destination / "sheet.png")


def run(store, task_id, *, provider=local_provider, inspect=probe):
    # Do not use PID files as a lock: PIDs can be recycled and multiple console
    # processes may receive the same retry request.
    read(store, task_id)
    with task_claim(store, task_id) as owned:
        if not owned:
            return public(read(store, task_id))
        return _run_claimed(store, task_id, provider=provider, inspect=inspect)


def _run_claimed(store, task_id, *, provider, inspect):
    folder = task_dir(store, task_id)
    with store.lock():
        task = read(store, task_id)
        if task["status"] == "approved":
            return public(task)
        if task["status"] == "review" and all(valid_stage(folder, task, stage) for stage in STAGES):
            return public(task)
        if task["status"] == "stage_review" and all(valid_stage(folder, task, stage) for stage in task["completed"]):
            return public(task)
        task.update(status="running", owner_pid=os.getpid(), cancel_requested=False, error=None)
        write_json(folder / "task.json", task)
    from config import load, snapshot
    try:
        cfg = load()
        verify_inputs(folder, task)
        # A retry may reuse only verified outputs from this task. Corruption of
        # a face invalidates every later stage, while a body-only failure keeps
        # the already verified face and front.
        with store.lock():
            for index, stage in enumerate(STAGES):
                if stage in task["completed"] and not valid_stage(folder, task, stage):
                    for invalid in STAGES[index:]:
                        task["completed"].pop(invalid, None)
                        task.setdefault("stage_assets", {}).pop(invalid, None)
                        if invalid in task.get("stage_reviews", []):
                            task["stage_reviews"].remove(invalid)
                    write_json(folder / "task.json", task)
                    break
        with snapshot(cfg):
            for stage in STAGES:
                with store.lock():
                    latest = read(store, task_id)
                    if latest.get("cancel_requested"):
                        latest.update(status="cancelled", updated_at=timestamp())
                        write_json(folder / "task.json", latest)
                        return public(latest)
                    task = latest
                    if stage in task["completed"]:
                        if "stage_reviews" in task and stage in {"face", "body_front"} and stage not in task["stage_reviews"]:
                            task.update(status="stage_review", stage=stage, owner_pid=None, updated_at=timestamp())
                            write_json(folder / "task.json", task)
                            return public(task)
                        continue
                    task.update(stage=stage, updated_at=timestamp())
                    task["attempts"][stage] = task["attempts"].get(stage, 0) + 1
                    write_json(folder / "task.json", task)
                if stage == "checking":
                    for source in task["sources"]:
                        path = checked(folder / source["file"])
                        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                            raise ValueError("原图与任务记录不一致")
                        source["analysis"] = inspect(path)
                    singles = [s for s in task["sources"] if s["analysis"].get("face_count") == 1]
                    if not singles:
                        raise ValueError("缺少清晰的单人脸部原图，请补充原图后新建任务")
                    bodies = [s for s in singles if s["analysis"].get("full_body")]
                    if not bodies:
                        raise ValueError("缺少可辨认身形比例的完整全身原图，请补充后新建任务")
                    task["face_refs"] = [s["file"] for s in sorted(singles, key=lambda s: (s["analysis"].get("quality", 0), s["analysis"].get("face_area", 0)), reverse=True)[:4]]
                    task["body_refs"] = [s["file"] for s in bodies[:4]]
                    result_digest = digest(task["sources"])
                else:
                    face = folder / "canonical"
                    if stage == "face":
                        refs = [folder / p for p in task["face_refs"]]
                    elif stage == "body_front":
                        refs = [face / "sheet.png", *[folder / p for p in task["body_refs"]]][:5]
                    else:
                        refs = [folder / "body_front.png", *[face / (n + ".png") for n in ("sheet", "front", "lprofile", "rprofile")]]
                    attempt = folder / "attempts" / f"{stage}-{task['attempts'][stage]}"
                    checked(attempt).mkdir(parents=True, exist_ok=False)
                    output = attempt / "result.png"
                    text = prompt(stage, task["values"])
                    write_json(attempt / "request.json", dict(stage=stage, prompt=text, refs=[str(p.relative_to(folder)) for p in refs]))
                    provider(stage, text, refs, output, cfg)
                    checked(output)
                    with Image.open(output) as im:
                        im.verify()
                    if stage == "face":
                        split_sheet(output, face)
                    elif stage == "body_front":
                        shutil.copyfile(output, folder / "body_front.png")
                    else:
                        split_sheet(output, face / "body")
                    result_digest = hashlib.sha256(output.read_bytes()).hexdigest()
                with store.lock():
                    current = read(store, task_id)
                    task["cancel_requested"] = current.get("cancel_requested", False)
                    task["completed"][stage] = result_digest
                    if stage != "checking":
                        task.setdefault("stage_assets", {})[stage] = {name:file_digest(folder, name) for name in stage_files(stage)}
                    if "stage_reviews" in task and stage in {"face", "body_front"} and not task["cancel_requested"]:
                        task.update(status="stage_review", owner_pid=None, updated_at=timestamp())
                        write_json(folder / "task.json", task)
                        return public(task)
                    write_json(folder / "task.json", task)
            with store.lock():
                task = read(store, task_id)
                task.update(status="cancelled" if task.get("cancel_requested") else "review", owner_pid=None, updated_at=timestamp())
                write_json(folder / "task.json", task)
            return public(task)
    except Exception as exc:
        with store.lock():
            task = read(store, task_id)
            task.update(status="cancelled" if task.get("cancel_requested") else "failed", owner_pid=None, error=str(exc)[:1000], updated_at=timestamp())
            write_json(folder / "task.json", task)
        return public(task)


def start(store, task_id, *, provider=local_provider, inspect=probe):
    key = (str(store.root), store.environment, store.persona, store.relation, task_id)
    with _GUARD:
        reviews, previous = _FUTURES.get(key, (None, None))
        task = read(store, task_id)
        current_reviews = tuple(task.get("stage_reviews", ()))
        if previous is not None and not previous.done() and current_reviews == reviews:
            return public(task)
        if task["status"] in {"approved", "stage_review", "review"}:
            return public(task)
        _FUTURES[key] = (current_reviews, EXECUTOR.submit(run, store, task_id, provider=provider, inspect=inspect))
    return public(task)


def cancel(store, task_id):
    with store.lock():
        task = read(store, task_id)
        if task["status"] == "approved":
            raise ValueError("已批准候选不能取消")
        task.update(cancel_requested=True, status="cancelled" if task["status"] != "running" else "cancelling")
        write_json(task_dir(store, task_id) / "task.json", task)
        return public(task)


def approve_stage(store, task_id, stage):
    with store.lock():
        task = read(store, task_id)
        folder = task_dir(store, task_id)
        if task["status"] != "stage_review" or task["stage"] != stage or "stage_reviews" not in task:
            raise Conflict("外貌阶段已变化，请查看最新候选")
        if stage not in {"face", "body_front"} or not all(valid_stage(folder, task, step) for step in task["completed"]):
            raise ValueError("候选素材已变化，请重试后确认")
        task["stage_reviews"].append(stage)
        task.update(status="queued", updated_at=timestamp())
        write_json(folder / "task.json", task)
        return public(task)


def approve(store, task_id):
    with store.lock():
        task = read(store, task_id)
        if task["status"] == "approved":
            return public(approved_candidate(store, task_id))
        if task["status"] not in {"review", "approved"} or task.get("cancel_requested"):
            raise ValueError("请先完成生成并检查两张总览与十张单视图")
        folder = task_dir(store, task_id)
        verify_inputs(folder, task)
        if not all(valid_stage(folder, task, stage) for stage in STAGES):
            raise ValueError("候选素材已变化，请重新生成并检查")
        files = [f"canonical/{n}.png" for n in ("sheet", *VIEWS)] + [f"canonical/body/{n}.png" for n in ("sheet", *VIEWS)]
        task["assets"] = {name: hashlib.sha256(checked(folder / name).read_bytes()).hexdigest() for name in files}
        write_json(folder / "canonical" / "canonical.json", dict(status="approved", sheet="sheet.png", views={v:v + ".png" for v in VIEWS}, candidate=task_id))
        write_json(folder / "canonical" / "body" / "body.json", dict(status="approved", sheet="sheet.png", views=list(VIEWS), candidate=task_id, fingerprint=task["fingerprint"]))
        task["metadata_assets"] = {name:file_digest(folder, name) for name in ("canonical/canonical.json", "canonical/body/body.json")}
        task.update(status="approved", approved_at=timestamp())
        write_json(folder / "task.json", task)
        return public(task)


def approved_candidate(store, task_id, values=None):
    task = read(store, task_id)
    if task["status"] != "approved":
        raise ValueError("外貌候选尚未确认")
    if values is not None and task["fingerprint"] != fingerprint(values):
        raise ValueError("外貌描述或身形已变化，请重新生成或清除旧候选选择")
    folder = task_dir(store, task_id)
    verify_inputs(folder, task)
    for name, sha in {**task["assets"], **task.get("metadata_assets", {})}.items():
        if hashlib.sha256(checked(folder / name).read_bytes()).hexdigest() != sha:
            raise ValueError("候选素材已变化，需要重新检查")
    return task


def candidate_release_files(store, task_id):
    task = approved_candidate(store, task_id)
    folder = task_dir(store, task_id)
    files = {"assets/" + name:folder / name for name in task["assets"]}
    for name in ("canonical/canonical.json", "canonical/body/body.json"):
        files["assets/" + name] = folder / name
    return files


def asset(store, task_id, name):
    if name == "body_front.png":
        task = read(store, task_id)
        folder = task_dir(store, task_id)
        if not valid_stage(folder, task, "body_front"):
            raise ValueError("正面全身基准尚未生成")
        return artifact_path(folder, name)
    allowed = {f"{kind}/{n}.png" for kind in ("face", "body") for n in ("sheet", *VIEWS)}
    if name not in allowed:
        raise ValueError("invalid preview image")
    read(store, task_id)
    kind, filename = name.split("/")
    path = task_dir(store, task_id) / "canonical"
    if kind == "body":
        path /= "body"
    path = checked(path / filename)
    if not path.is_file():
        raise ValueError("此阶段图片尚未生成")
    return path
