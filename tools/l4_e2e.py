#!/usr/bin/env python3
"""Run the real worker only against an isolated fake OneBot control plane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from config import load

REAL_BOT = "2143846790"
REAL_PEER = "1007712160"
PRODUCTION_BOT = "10001"


def loopback(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("ws", "http") or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"only loopback WS/HTTP control is allowed: {url}")
    return url


def preflight(workspace: pathlib.Path, ws_uri: str, control: str, *, images=False) -> dict:
    loopback(ws_uri)
    loopback(control)
    workspace = workspace.expanduser().resolve()
    if workspace in (ROOT, pathlib.Path.home()) or ROOT in workspace.parents:
        raise ValueError("workspace must be outside the repository and home root")
    if os.environ.get("XIAOTIAN_IDENTITY") != "xiaosu":
        raise ValueError("XIAOTIAN_IDENTITY=xiaosu is required")
    for key in ("XIAOTIAN_CONFIG_FILE", "XIAOTIAN_IDENTITIES_DIR"):
        value = os.environ.get(key)
        if not value or workspace not in pathlib.Path(value).expanduser().resolve().parents:
            raise ValueError(f"{key} must be inside the isolated workspace")
    cfg = load()
    runtime = cfg.get("identity_runtime") or {}
    caps = runtime.get("capabilities") or {}
    if not runtime.get("runtime_enabled") or not caps.get("inbound") or not caps.get("auto_reply"):
        raise ValueError("isolated runtime_enabled/inbound/auto_reply must be true for L4")
    for key in ("image_send", "image_generate", "proactive"):
        if caps.get(key) and not (images and key.startswith("image_")):
            raise ValueError(f"{key} must remain false")
    if images and os.environ.get("ROLE_REGISTRY_DIR"):
        raise ValueError("legacy deployment acceptance cannot substitute a registered-role entry")
    if images and not all(caps.get(k) for k in ("image_send", "image_generate")):
        raise ValueError("isolated image acceptance requires both image capabilities")
    route = " ".join((cfg.get("persona") or {}).get("contact_aliases") or {})
    if REAL_BOT in route or REAL_PEER in route or PRODUCTION_BOT in route:
        raise ValueError("real QQ/bot identifiers are forbidden in L4")
    if images and route != "qq_onebot:9000000002:9000000001":
        raise ValueError("image acceptance requires the exact fake bot/peer pair")
    runtime_root = pathlib.Path(runtime["runtime_root"]).resolve()
    if workspace not in runtime_root.parents:
        raise ValueError("identity runtime escapes workspace")
    if images:
        for key in ("life_schedule", "schedule_overrides", "schedule_memory", "schedule_override_pending"):
            if runtime_root not in pathlib.Path(runtime[key]).resolve().parents:
                raise ValueError(f"{key} escapes isolated runtime")
    path_keys = ("db_path", "digital_life_dir")
    if images and runtime_root not in pathlib.Path(cfg["imagegen"]["out_dir"]).resolve().parents:
        raise ValueError("image output escapes isolated runtime")
    for key in path_keys:
        if runtime_root not in pathlib.Path(cfg[key]).expanduser().resolve().parents:
            raise ValueError(f"{key} escapes identity runtime")
    for section, key in (("memory", "qdrant_path"), ("memory", "history_db"),
                         ("llm", "session_state"), ("imagegen", "session_state"),
                         ("outbox", "db"), ("qq_receipts", "db"), ("mac", "shot_path"),
                         ("avatar", "queue_path")):
        if runtime_root not in pathlib.Path(cfg[section][key]).expanduser().resolve().parents:
            raise ValueError(f"{section}.{key} escapes identity runtime")
    roll_path = ((cfg.get("avatar") or {}).get("mood") or {}).get("roll_state_path")
    if not roll_path or runtime_root not in pathlib.Path(roll_path).expanduser().resolve().parents:
        raise ValueError("avatar.mood.roll_state_path escapes identity runtime")
    for section, key in (("appearance", "assets_dir"), ("avatar", "album_dir")):
        if workspace not in pathlib.Path(cfg[section][key]).expanduser().resolve().parents:
            raise ValueError(f"{section}.{key} escapes isolated workspace")
    voice = cfg.get("voice_asr") or {}
    if voice.get("prefer_qq_stt") or voice.get("qq_ws_uri") or voice.get("qq_ws_token"):
        raise ValueError("L4 voice_asr must not use QQ STT or any NapCat endpoint")
    return cfg


def init_workspace(workspace: pathlib.Path, deployment: pathlib.Path | None = None, *, images=False) -> None:
    workspace = workspace.expanduser().resolve()
    if workspace in (ROOT, pathlib.Path.home()) or ROOT in workspace.parents:
        raise ValueError("workspace must be outside the repository and home root")
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"workspace is not empty: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    source = (deployment or ROOT).expanduser().resolve()
    if images and deployment is None:
        raise ValueError("image acceptance requires --deployment; template is not the target")
    files = [source / "config.json", source / "identities/xiaosu/identity.json",
             source / "identities/xiaosu/partner.json"]
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    files += [p for p in (source / "run_xiaosu_onebot.sh", source / "tools/run_xiaosu_onebot.sh") if p.is_file()]
    manifest = {"deployment": str(source), "files": {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                "acceptance_engine": str(ROOT), "images": images,
                "note": "Deployment-derived isolated config with fake routes; not production launchd acceptance."}
    (workspace / "deployment-source.json").write_text(json.dumps(manifest, indent=2))
    base = json.loads((source / "config.json").read_text())
    shared = base.get("shared_life_schedule")
    if shared:
        # Keep the deployment's recorded slots, but never bind a fake worker
        # to a production calendar or another relationship's memory ledger.
        from shared_schedule import settings
        calendar_source = settings(base)["path"]
        calendar_bytes = calendar_source.read_bytes()
        calendar = json.loads(calendar_bytes)
        calendar.pop("memory_ledger", None)
        for day in calendar.get("days", []):
            day.pop("memory", None)
        calendar_target = workspace / "identities/xiaosu/runtime/life_schedule.json"
        calendar_target.parent.mkdir(parents=True, exist_ok=True)
        calendar_target.write_text(json.dumps(calendar, ensure_ascii=False, indent=2))
        base["shared_life_schedule"] = {"path": str(calendar_target), "owner": "xiaosu"}
        manifest["calendar_sha256"] = hashlib.sha256(calendar_bytes).hexdigest()
        (workspace / "deployment-source.json").write_text(json.dumps(manifest, indent=2))
    (workspace / "config.json").write_text(json.dumps(base, ensure_ascii=False, indent=2))
    target = workspace / "identities" / "xiaosu"
    target.mkdir(parents=True, exist_ok=True)
    raw = json.loads((source / "identities" / "xiaosu" / "identity.json").read_text())
    raw["profile_dir"] = str(workspace / "profile" / "xiaosu")
    raw["runtime_enabled"] = True
    raw["capabilities"].update({"manual_send": False, "auto_reply": True, "inbound": True,
                                "image_send": images, "image_generate": images, "proactive": False})
    raw["routes"] = [{"channel": "qq_onebot", "bot_account_id": "9000000002",
                      "peer_id": "9000000001", "peer_type": "private"}]
    (target / "identity.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    (target / "partner.json").write_text((source / "identities" / "xiaosu" / "partner.json").read_text())


def request(control: str, path: str, payload=None) -> dict:
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = urllib.request.Request(control + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def outbound_count(control: str) -> int:
    return sum(row["direction"] == "outbound" for row in request(control, "/events")["events"])


def turn_count(cfg: dict) -> int:
    path = pathlib.Path(cfg["db_path"])
    if not path.is_file():
        return 0
    with sqlite3.connect(path) as db:
        return db.execute("SELECT COUNT(*) FROM messages WHERE sender='them'").fetchone()[0]


def ack_count(control: str) -> int:
    return sum(row["direction"] == "ack" for row in request(control, "/events")["events"])


def cancel_count(workspace: pathlib.Path) -> int:
    try:
        return (workspace / "worker.log").read_text().count("cancel ")
    except OSError:
        return 0


def turn_settled(control: str, cfg: dict, target: int, before_outbound: int) -> bool:
    outbound = outbound_count(control)
    return turn_count(cfg) >= target and outbound > before_outbound and ack_count(control) >= outbound


def completed_turn(workspace, offset, expected):
    path = workspace / "worker.log"
    if not path.exists():
        return False
    with path.open("rb") as stream:
        stream.seek(offset)
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    for line in lines:
        if not line.startswith("[onebot] turn_complete "):
            continue
        try:
            event = json.loads(line.removeprefix("[onebot] turn_complete "))
        except json.JSONDecodeError:
            continue  # A concurrent write may not have finished its line yet.
        if any(str(event.get(key)) != str(value) for key, value in expected.items()):
            continue
        if event.get("blocked") or event.get("cancelled") or event.get("error"):
            raise AssertionError(f"worker did not complete delivery: {event}")
        if event.get("status") == "confirmed" and event.get("sent", 0) > 0:
            return True
    return False


def wait_for(control: str, predicate, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except (urllib.error.URLError, ConnectionError):
            value = None
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError("L4 condition timed out")


class Worker:
    def __init__(self, ws_uri: str, workspace: pathlib.Path, python: str, stub: str | None):
        self.ws_uri, self.workspace, self.python, self.stub = ws_uri, workspace, python, stub
        self.process = None

    def start(self):
        token = self.workspace / "fake.token"
        token.write_text("fake-only-token\n")
        token.chmod(0o600)
        command = [self.python, str(ROOT / "tools" / "onebot_qq_run.py"),
                   "--ws", self.ws_uri, "--token-file", str(token), "--send", "--no-reconnect"]
        if self.stub is not None:
            command += ["--stub-reply", self.stub]
        log = (self.workspace / "worker.log").open("a")
        self.process = subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(),
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def run(args):
    ws_uri, control = loopback(args.ws), loopback(args.control)
    workspace = args.workspace.expanduser().resolve()
    cfg = preflight(workspace, ws_uri, control, images=args.images)
    scenario = json.loads(args.scenario.read_text())
    wait_for(control, lambda: request(control, "/health"), 10)
    if args.images and args.stub_reply is not None:
        raise ValueError("image E2E must run brain; --stub-reply is not allowed")
    worker = Worker(ws_uri, workspace, args.python, args.stub_reply)
    results = []
    before_outbound = 0
    before_cancel = 0
    log_offset = 0
    expected_turn = {}
    try:
        worker.start()
        wait_for(control, lambda: request(control, "/health")["clients"] > 0, 20)
        for step in scenario["steps"]:
            if "inject" in step:
                before = outbound_count(control)
                before_outbound = before
                before_cancel = cancel_count(workspace)
                log_path = workspace / "worker.log"
                log_offset = log_path.stat().st_size if log_path.exists() else 0
                injected = request(control, "/inject", step["inject"])["event"]
                expected_turn = {"msg_id": injected["message_id"],
                                 "bot": injected["self_id"], "peer": injected["user_id"]}
                if step.get("expect_blocked"):
                    time.sleep(1)
                    after = outbound_count(control)
                    if after != before:
                        raise AssertionError(f"blocked route produced outbound: {step}")
                    results.append({"step": step, "outbound": after})
            elif "wait_turn" in step:
                target = int(step["wait_turn"])
                def settled():
                    if cancel_count(workspace) > before_cancel:
                        raise AssertionError(f"turn cancelled before send: {step}")
                    return (completed_turn(workspace, log_offset, expected_turn)
                            and turn_settled(control, cfg, target, before_outbound))
                wait_for(control, settled, args.timeout)
                results.append({"step": step, "turns": turn_count(cfg),
                                "outbound": outbound_count(control)})
            elif "assert_images" in step:
                events = request(control, "/events")["events"]
                sent = [e["payload"] for e in events if e["direction"] == "outbound"
                        and any(s.get("type") == "image" for s in e["payload"].get("params", {}).get("message", [])
                                if isinstance(s, dict))]
                acks = {e["payload"].get("echo"): e["payload"] for e in events if e["direction"] == "ack"}
                if len(sent) != int(step["assert_images"]):
                    raise AssertionError("image transport count mismatch")
                if not all(acks.get(e.get("echo"), {}).get("retcode") == 0 for e in sent):
                    raise AssertionError("images lack matching successful transport acknowledgement")
                if not any(e["direction"] == "model_request" for e in events):
                    raise AssertionError("no model request observed at fake LLM endpoint")
                (workspace / "image-transport-evidence.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))
                results.append({"step": step, "confirmed_images": len(sent)})
            elif "wait_outbound" in step:
                target = int(step["wait_outbound"])
                wait_for(control, lambda: outbound_count(control) >= target, args.timeout)
                results.append({"step": step, "outbound": outbound_count(control)})
            elif step.get("wait_duplicate"):
                before = outbound_count(control)
                time.sleep(2)
                after = outbound_count(control)
                if after != before:
                    raise AssertionError("duplicate inbound produced another outbound")
                results.append({"step": step, "outbound": after})
            elif step.get("restart_worker"):
                worker.stop()
                worker.start()
                wait_for(control, lambda: request(control, "/health")["clients"] > 0, 20)
            elif step.get("assert_history"):
                db = sqlite3.connect(cfg["db_path"])
                text = "\n".join(row[0] for row in db.execute("SELECT text FROM messages"))
                db.close()
                if step["assert_history"] not in text:
                    raise AssertionError(f"missing history marker: {step['assert_history']}")
                results.append({"step": step, "ok": True})
            else:
                raise ValueError(f"unsupported scenario step: {step}")
        report = {"scenario": scenario["name"], "ok": True, "results": results,
                  "outbound": outbound_count(control)}
    finally:
        worker.stop()
    report_path = workspace / "l4-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="L4 fake-OneBot real-worker E2E")
    ap.add_argument("--workspace", type=pathlib.Path, required=True)
    ap.add_argument("--scenario", type=pathlib.Path)
    ap.add_argument("--init-workspace", action="store_true")
    ap.add_argument("--deployment", type=pathlib.Path, help="actual deployment checkout (read only)")
    ap.add_argument("--images", action="store_true", help="isolated image acceptance; never production")
    ap.add_argument("--ws", default="ws://127.0.0.1:3911")
    ap.add_argument("--control", default="http://127.0.0.1:3912")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--stub-reply", default=None)
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args(argv)
    if args.init_workspace:
        init_workspace(args.workspace, args.deployment, images=args.images)
        print(f"export XIAOTIAN_IDENTITY=xiaosu\n"
              f"export XIAOTIAN_CONFIG_FILE={args.workspace.expanduser().resolve() / 'config.json'}\n"
              f"export XIAOTIAN_IDENTITIES_DIR={args.workspace.expanduser().resolve() / 'identities'}")
        return 0
    if args.check_only:
        cfg = preflight(args.workspace, loopback(args.ws), loopback(args.control), images=args.images)
        print(json.dumps({"ok": True, "runtime": cfg["identity_runtime"]}, ensure_ascii=False))
        return 0
    if not args.scenario:
        ap.error("--scenario is required unless --init-workspace is used")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
