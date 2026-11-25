"""MacBook subscription runner. Persona SSOT: data/persona_dossier.json.

Uses the same reference selection and photo recipe as the local route, with the
existing ChatGPT Codex login. Calendar snapshots come from the sending host;
that host rechecks the recorded slot before dispatch and delivery.
"""
import argparse
import copy
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import codex_session
import imagegen
import imagegen_audit
from config import load
from photo_recipe import RECIPE


def request_context(scene, request):
    if request.get("scene") != scene:
        raise ValueError("MacBook scene differs from the recorded request")
    snap = request.get("photo_schedule")
    if not isinstance(snap, dict):
        raise ValueError("MacBook request requires a recorded photo schedule")
    from photo_schedule import local_time, location
    depicted = local_time(snap["depiction_time"])
    slot = snap["slot"]
    if (depicted.date().isoformat() != snap["date"] or not location(slot)
            or not slot.get("activity") or slot.get("photo_ok") is False
            or not slot["start"] <= depicted.strftime("%H:%M") < slot["end"]):
        raise ValueError("invalid recorded MacBook photo context")
    return {**{k: request[k] for k in ("shot_type", "shot_types", "shot_bits", "n_images") if k in request},
            "photo_schedule": snap, "now": depicted, "date": snap["date"], "slot": slot,
            "activity": f"{location(slot)} {slot['activity']}"}


def generate(scene, output=None, outfit=None, realism=None, *, request=None, cfg=None):
    if os.environ.get("XIAOTIAN_TEST"):
        raise RuntimeError("live MacBook generation is forbidden in test mode")
    cfg = copy.deepcopy(cfg if cfg is not None else load())
    from imagegen_scope import forbid_xiaosu_fallback
    forbid_xiaosu_fallback(cfg)
    if cfg.get("persona_version") or (cfg.get("identity_runtime") or {}).get("schema_version") == 2:
        raise PermissionError("this identity is not provisioned for MacBook emergency generation")
    home = Path.home()
    profile = Path(os.environ.get("ASSETS") or home / "xiaotian-assets/appearance" / cfg["appearance"]["profile"])
    cfg["appearance"]["profile_dir"] = str(profile.expanduser())
    ig = cfg.setdefault("imagegen", {})
    codex = os.environ.get("CODEX") or "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    if not Path(codex).is_file():
        codex = shutil.which(codex) or shutil.which("codex") or codex
    ig.update(cmd=codex, prompt_recipe=RECIPE, reasoning_effort="high",
              session_state=str(Path(os.environ.get("SESSION_DIR") or home / ".xiaotian") / "macbook-image-session.json"))
    if request is None:
        from photo_schedule import bind
        ctx = bind(scene)
        request = imagegen.macbook_photo_request(scene, {}, ctx, cfg)
        request["photo_schedule"] = ctx["photo_schedule"]
    ctx = request_context(scene, request)
    if request.get("scene_refs_mode"):
        ig["scene_refs_mode"] = request["scene_refs_mode"]
    if outfit:
        import photo_lighting
        plan = dict(text=outfit, realism_text=realism or "", emotion_text=os.environ.get("EMOTION_TEXT", ""),
                    slot=ctx["slot"], date=ctx["date"], photo_schedule=ctx["photo_schedule"],
                    depiction_time=ctx["now"].isoformat(),
                    lighting=request.get("lighting") or photo_lighting.resolve(ctx["now"], ctx["slot"], cfg))
    else:
        plan = imagegen.outfit_plan(scene, cfg=cfg, **ctx)
        if realism is not None:
            plan["realism_text"] = realism
        if request.get("lighting"):
            plan["lighting"] = request["lighting"]
    prepared = imagegen.prepare_photo(scene, cfg, plan, ctx)
    out = Path(output).expanduser() if output else home / "xiaotian-assets/generated" / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = out.resolve()
    child_env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        child_env.pop(key, None)
    child_env["IMAGEGEN_REASONING_EFFORT"] = "high"
    login = subprocess.run([codex, "login", "status"], capture_output=True, text=True, timeout=30, env=child_env)
    if login.returncode or "Logged in using ChatGPT" not in login.stdout + login.stderr:
        raise RuntimeError("MacBook requires the existing ChatGPT login; no API-key or login fallback")
    session_file = codex_session.state_path(cfg)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    with session_file.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        out.mkdir(parents=True, exist_ok=True)
        (out / "prompt.txt").write_text(prepared["prompt"], encoding="utf-8")
        (out / "outfit.json").write_text(json.dumps(prepared["plan"], ensure_ascii=False, indent=1))
        meta = dict(imagegen.photo_input_meta(prepared, cfg), backend="macbook", reasoning_effort="high",
                    codex_home=str(imagegen.codex_home()))
        sid = codex_session.current(cfg)
        started, offsets = time.time(), imagegen.rollout_sizes()
        def run(session):
            cmd = imagegen.build_cmd(scene, cfg, out, session_id=session,
                refs=prepared["refs"], scene_refs=prepared["scene_refs"],
                shot_type=prepared["shot_type"], required_refs=prepared["required_refs"], reasoning_effort="high")
            evidence = imagegen_audit.invocation(out, cmd)
            meta.update(session_id=session, session="resume" if session else "new", status="running",
                        actual_cli_references=evidence["references"], invocation_log="invocations.jsonl")
            imagegen.write_meta(out, meta)
            return subprocess.run(cmd, input=prepared["prompt"], capture_output=True, text=True,
                                  timeout=float(ig.get("timeout", 900)), cwd=out, env=child_env)
        try:
            result = run(sid)
            if sid and result.returncode and codex_session.resume_broken(result.stderr):
                codex_session.forget(cfg)
                sid = None
                result = run(None)
        except subprocess.TimeoutExpired:
            codex_session.forget(cfg)
            imagegen.write_meta(out, dict(meta, status="timeout"))
            raise
        (out / "codex.log").write_text(result.stdout + "\n--- stderr ---\n" + result.stderr)
        observation = imagegen_audit.tool_references(result.stdout)
        (out / "tool_references.jsonl").write_text(json.dumps(observation, ensure_ascii=False) + "\n")
        new_sid = codex_session.thread_id(result.stdout) or sid
        if new_sid and not result.returncode:
            codex_session.save(cfg, new_sid)
        imgs = [Path(shutil.copy2(p, out / p.name)) for p in imagegen._new_images(started)] or imagegen._from_rollouts(started, out, offsets)
        imgs += list((out / "generated_images").glob("*.png"))
        imgs = list(dict.fromkeys(imgs))[:prepared["n_images"]]
        meta.update(session_id=new_sid, tool_reference_evidence=observation, status="ok" if imgs else "failed", images=[p.name for p in imgs])
        imagegen.write_meta(out, meta)
        if not imgs:
            raise RuntimeError(f"MacBook produced no images; see {out / 'codex.log'}")
        return imgs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scene")
    ap.add_argument("output", nargs="?")
    ap.add_argument("outfit", nargs="?", default=os.environ.get("OUTFIT_TEXT"))
    ap.add_argument("realism", nargs="?", default=os.environ.get("REALISM_TEXT"))
    args = ap.parse_args()
    request = json.loads(os.environ["PHOTO_REQUEST_JSON"]) if os.environ.get("PHOTO_REQUEST_JSON") else None
    for path in generate(args.scene, args.output, args.outfit, args.realism, request=request):
        print(path)


if __name__ == "__main__":
    main()
