#!/usr/bin/env python3
"""Container isolation acceptance using only the existing fake OneBot server.

Creates a fresh, disposable workspace. Never mounts production data or opens
host ports. Run against a locally built, content-addressed acceptance image.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from role_container import worker_spec


def prepare(workspace, image):
    workspace = workspace.expanduser().resolve()
    if workspace.exists():
        raise FileExistsError("acceptance workspace must be new")
    workspace.mkdir(parents=True, mode=0o700)
    compose = {"services": {}, "networks": {}}
    instances = []
    for i, (rid, relation) in enumerate((r, c) for r in ("xiaotian", "xiaosu", "mentor")
                                       for c in ("alice", "bob")):
        name = f"test-{rid}-{relation}"
        base = workspace / name
        state, assets, auth = [base / p for p in ("state", "assets", "auth")]
        for p in (state, assets, auth):
            p.mkdir(parents=True)
        (state / "sentinel").write_text(name)
        (assets / "sentinel").write_text(rid)
        grants = auth / "grants.json"
        grants.write_text(json.dumps({"role_id": rid, "relation_id": relation, "environment": "test",
                                     "runtime_enabled": True,
                                     "capabilities": {"inbound": True, "auto_reply": True}}))
        token = auth / "token"
        token.write_text("fake-only-token")
        grants.chmod(0o400)
        token.chmod(0o400)
        peer, bot = str(9000000100 + i), str(9000000200 + i)
        fake = f"fake-{rid}-{relation}"
        role = {"schema_version": 2, "role_id": rid, "dossier": {"identity": {"name": rid}},
                "workflows": {"reply": {"enabled": True}},
                "settings": {"reply": {"human_typing": False, "think_max_awake_sec": 0.01},
                             "llm": {"short_backend": "deepseek", "short_model": "fake-model",
                                     "deepseek_base": f"http://{fake}:3002/v1"},
                             "weather": {"enabled": False}, "memory": {"enabled": False}},
                "relationships": {relation: {"kind": "friend", "person": {"name": relation},
                    "bindings": [{"environment": "test", "channel": "qq_onebot", "bot_account_id": bot,
                                  "peer_id": peer, "peer_type": "private"}]}}}
        projected, spec = worker_spec(role, relation, environment="test", image=image,
            state_dir=state, assets_dir=assets, definition_file=base / "role.json",
            ws_uri=f"ws://{fake}:3001", grants_file=grants, token_file=token, send=True)
        (base / "role.json").write_text(json.dumps(projected))
        worker = spec["services"][name]
        worker["environment"]["DEEPSEEK_API_KEY"] = "fake-only-key"
        worker["mem_limit"] = "256m"
        worker["cpus"] = 0.5
        compose["services"].update(spec["services"])
        compose["networks"].update(spec["networks"])
        compose["services"][fake] = {
            "image": image, "user": "10001:10001", "read_only": True,
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
            "network_mode": None, "networks": [name], "restart": "no",
            "mem_limit": "128m", "pids_limit": 64,
            "command": ["python", "/engine/tools/onebot_mock_server.py", "--host", "0.0.0.0",
                        "--bot-id", bot, "--peer-id", peer, "--llm-reply", f"收到{rid}_{relation}"]}
        del compose["services"][fake]["network_mode"]
        instances.append({"worker": name, "fake": fake, "role": rid, "relation": relation,
                          "peer": peer, "bot": bot, "base": str(base)})
    (workspace / "compose.json").write_text(json.dumps(compose, indent=2))
    (workspace / "instances.json").write_text(json.dumps(instances, indent=2))
    return compose, instances


def run(workspace, image, instances):
    project = "role-e2e-" + str(os.getpid())
    prefix = ["docker", "compose", "-p", project, "-f", str(workspace / "compose.json")]
    evidence = {"image": image, "project": project, "checks": [], "passed": False}

    def command(args, timeout=60):
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"command failed: {args}\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def execute(service, script, *args):
        return command(prefix + ["exec", "-T", service, "python", "-c", script, *args])

    def control(fake, path, payload=None):
        return json.loads(execute(fake,
            "import urllib.request,json,sys; "
            "data=None if sys.argv[2]=='null' else sys.argv[2].encode(); "
            "r=urllib.request.Request('http://127.0.0.1:3002'+sys.argv[1],data=data,"
            "headers={'Content-Type':'application/json'}); "
            "print(urllib.request.urlopen(r,timeout=3).read().decode())", path, json.dumps(payload)))

    def wait(predicate, timeout=40):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except RuntimeError as exc:
                last = exc
            time.sleep(0.25)
        raise TimeoutError(f"container condition timed out: {last}")

    try:
        # Only newly created fake fixtures are writable by this provisioning
        # container. No production or arbitrary user-supplied directory mounts.
        for instance in instances:
            base = Path(instance["base"])
            command(["docker", "run", "--rm", "--network", "none", "--user", "0:0",
                     "--mount", f"type=bind,source={base / 'state'},target=/state",
                     "--mount", f"type=bind,source={base / 'auth'},target=/auth", image,
                     "python", "-c", "import os; "
                     "[os.chown(p,10001,10001) for p in ['/state','/state/sentinel','/auth/grants.json','/auth/token']]; "
                     "os.chmod('/state',0o700)"])
        command(prefix + ["up", "-d", *[i["fake"] for i in instances]])
        for i in instances:
            wait(lambda: control(i["fake"], "/health")["ok"])
        command(prefix + ["up", "-d", *[i["worker"] for i in instances]])
        container_ids = command(prefix + ["ps", "-q"]).split()
        assert len(container_ids) == len(instances) * 2
        inspected = json.loads(command(["docker", "inspect", *container_ids]))
        (workspace / "inspect-containers.json").write_text(json.dumps(inspected, indent=2))
        networks = set()
        for container in inspected:
            host = container["HostConfig"]
            assert container["Config"]["User"] == "10001:10001"
            assert host["ReadonlyRootfs"] and not host["Privileged"]
            assert "ALL" in host["CapDrop"] and "no-new-privileges:true" in host["SecurityOpt"]
            assert not host.get("PortBindings")
            for mount in container["Mounts"]:
                assert mount["Type"] == "bind"
                assert workspace in Path(mount["Source"]).parents
            networks.update(container["NetworkSettings"]["Networks"])
        inspected_networks = json.loads(command(["docker", "network", "inspect", *sorted(networks)]))
        assert len(inspected_networks) == len(instances)
        assert all(n["Internal"] and len(n["Containers"]) == 2 for n in inspected_networks)
        (workspace / "inspect-networks.json").write_text(json.dumps(inspected_networks, indent=2))
        for i in instances:
            wait(lambda: control(i["fake"], "/health")["clients"] == 1)
            probe = execute(i["worker"], '''
import json, os, pathlib, socket, sys
rid, relation, forbidden_host, other_service = sys.argv[1:]
own = pathlib.Path('/state/test') / rid / 'relationships' / relation
assert os.getuid() == 10001
assert (own / 'sentinel').read_text() == f'test-{rid}-{relation}'
(own / 'write-probe').write_text('own-write-ok')
assert list(pathlib.Path('/state/test').iterdir()) == [pathlib.Path('/state/test') / rid]
assert list(own.parent.iterdir()) == [own]
assert list(pathlib.Path('/registry').iterdir()) == [pathlib.Path('/registry') / rid]
for p in [forbidden_host, '/var/run/docker.sock', '/engine/config.json', '/engine/data/persona_dossier.json']:
    assert not pathlib.Path(p).exists(), p
for p in ['/auth/grants.json', f'/registry/{rid}/role.json', f'/assets/{rid}/sentinel', '/engine/escape']:
    try:
        with open(p, 'a') as stream:
            stream.write('unexpected-write')
    except OSError:
        pass
    else:
        raise AssertionError('write escaped: ' + p)
try:
    socket.getaddrinfo(other_service, 3001)
except socket.gaierror:
    pass
else:
    raise AssertionError('sibling network visible')
print(json.dumps({'uid': os.getuid(), 'own_write': True, 'foreign_read_denied': True,
                  'readonly_mounts': True, 'sibling_network_denied': True}))
''', i["role"], i["relation"], str(workspace / "instances.json"),
                next(j["fake"] for j in instances if j["worker"] != i["worker"]))
            evidence["checks"].append({"worker": i["worker"], "isolation": json.loads(probe)})
            control(i["fake"], "/inject", {"text": f"private_{i['worker']}", "message_id": 42})
        for i in instances:
            wait(lambda: '"status": "confirmed"' in command(prefix + ["logs", "--no-color", i["worker"]]))
            events = control(i["fake"], "/events")["events"]
            outbound = [e["payload"] for e in events if e["direction"] == "outbound"]
            assert len(outbound) == 1, events
            assert str(outbound[0]["params"]["user_id"]) == i["peer"]
            prompts = [e["payload"] for e in events if e["direction"] == "model_request"]
            assert len(prompts) == 1, events
            prompt_text = json.dumps(prompts[0], ensure_ascii=False)
            assert f"private_{i['worker']}" in prompt_text
            assert all(f"private_{j['worker']}" not in prompt_text for j in instances if j != i)
            rows = json.loads(execute(i["worker"],
                "import sqlite3,pathlib,json; p=next(pathlib.Path('/state').rglob('chat.db')); "
                "print(json.dumps(sqlite3.connect(p).execute(\"SELECT contact,text FROM messages WHERE sender='them'\").fetchall()))"))
            assert rows == [[i["relation"], f"private_{i['worker']}"]], rows
            (workspace / f"{i['worker']}-events.json").write_text(json.dumps(events, indent=2))
        evidence["passed"] = True
    except BaseException as exc:
        evidence["error"] = repr(exc)
        raise
    finally:
        try:
            (workspace / "containers.log").write_text(command(prefix + ["logs", "--no-color"]))
            (workspace / "containers.json").write_text(command(prefix + ["ps", "--all", "--format", "json"]))
        finally:
            (workspace / "REPORT.json").write_text(json.dumps(evidence, indent=2))
            command(prefix + ["down", "--timeout", "10"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--image", required=True, help="sha256 image ID from docker image inspect")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    _, instances = prepare(workspace, args.image)
    if not args.prepare_only:
        run(workspace, args.image, instances)
    print(workspace)


if __name__ == "__main__":
    main()
