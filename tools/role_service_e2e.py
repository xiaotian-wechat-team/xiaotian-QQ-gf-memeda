#!/usr/bin/env python3
"""Actual Docker service switching/rollback against isolated fake OneBot only."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from role_release import plan, install, activate
from role_service import deployment_plan, apply, command
from role_isolation_e2e import prepare


def run(workspace, image):
    compose, all_instances = prepare(workspace, image)
    instances = [i for i in all_instances if i['relation'] == 'alice' and i['role'] != 'mentor']
    workspace = workspace.resolve()
    # Dedicated fixed project names are guarded before any creation or cleanup.
    projects = [f"role-test-{i['role']}" for i in instances] + ['role-switch-fakes']
    for project in projects:
        if command(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}']).strip():
            raise RuntimeError('acceptance project already exists: ' + project)
    fake_compose = {'services': {}, 'networks': {}}
    for i in instances:
        fake_compose['services'][i['fake']] = compose['services'][i['fake']]
        fake_compose['networks'][i['worker']] = {'internal': True, 'name': f"role-test-{i['role']}-alice"}
        if command(['docker', 'network', 'ls', '-q', '--filter', 'name=^' + fake_compose['networks'][i['worker']]['name'] + '$']).strip():
            raise RuntimeError('acceptance network already exists')
    fake_file = workspace / 'fake-compose.json'
    fake_file.write_text(json.dumps(fake_compose))
    fake_prefix = ['docker', 'compose', '-p', 'role-switch-fakes', '-f', str(fake_file)]
    releases, state, authority = [workspace / p for p in ('releases', 'state', 'authority')]
    deployments = {}
    evidence = {'passed': False, 'image': image, 'checks': []}

    def release(i, version, expected):
        source = workspace / f"{i['role']}-{version}.json"
        role = json.loads((Path(i['base']) / 'role.json').read_text())
        role['dossier']['identity']['name'] = i['role'] + version
        source.write_text(json.dumps(role))
        asset = Path(i['base']) / 'assets/sentinel'
        files = {'definition/role.json': source, 'assets/sentinel': asset}
        manifest = plan(i['role'], image.removeprefix('sha256:'), files)
        install(manifest, files, releases)
        activate(releases, i['role'], manifest['release_id'], expected_release=expected)
        return manifest['release_id']

    def deployment(i, bad=False):
        return deployment_plan(releases, i['role'], 'test', state, authority,
            {'alice': {'ws_uri': 'ws://unavailable:3001' if bad else f"ws://{i['fake']}:3001",
                       'network': f"role-test-{i['role']}-alice"}})

    def ids(d):
        return command(['docker', 'compose', '-p', d['project'], '-f', str(Path(d['directory']) / 'compose.json'), 'ps', '-q']).split()

    try:
        command(fake_prefix + ['up', '-d'])
        for i in instances:
            deadline = time.monotonic() + 30
            while True:
                try:
                    command(fake_prefix + ['exec', '-T', i['fake'], 'python', '-c',
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3002/health',timeout=2).read()"])
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
        for i in instances:
            dest = authority / 'test' / i['role'] / 'alice'
            dest.mkdir(parents=True)
            for src, name in [('grants.json', 'grants.json'), ('token', 'onebot.token')]:
                (dest / name).write_bytes((Path(i['base']) / 'auth' / src).read_bytes())
                (dest / name).chmod(0o400)
            runtime = state / 'test' / i['role'] / 'relationships/alice'
            runtime.mkdir(parents=True)
            (runtime / 'memory-sentinel').write_text(i['role'] + '-preserved')
            command(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0',
                '--mount', f'type=bind,source={runtime},target=/state',
                '--mount', f'type=bind,source={dest},target=/auth', image, 'python', '-c',
                "import os; [os.chown(p,10001,10001) for p in ['/state','/state/memory-sentinel','/auth/grants.json','/auth/onebot.token']]"])
            release(i, 'v1', None)
            d = deployment(i)
            deployments[i['role']] = d
            apply(d)
        selected, other = instances
        before_other = ids(deployments[other['role']])
        old = deployments[selected['role']]
        old_ids = ids(old)
        v2 = release(selected, 'v2', old['release_id'])
        current = deployment(selected)
        apply(current)
        deployments[selected['role']] = current
        assert ids(current) != old_ids
        record = Path(current['directory']).parent / 'running.json'
        before_record = record.read_bytes()
        release(selected, 'v3', v2)
        broken = deployment(selected, bad=True)
        try:
            apply(broken, timeout=5)
        except TimeoutError:
            pass
        else:
            raise AssertionError('unreachable fake transport unexpectedly ready')
        assert record.read_bytes() == before_record
        running = json.loads(command(['docker', 'inspect', *ids(current)]))
        assert all(c['State']['Running'] and c['Config']['Labels']['role.deployment'] == current['deployment_id'] for c in running)
        assert ids(deployments[other['role']]) == before_other
        for i in instances:
            assert (state / 'test' / i['role'] / 'relationships/alice/memory-sentinel').read_text() == i['role'] + '-preserved'
        evidence.update(passed=True, checks=['v1 startup', 'v2 switch', 'v3 transport failure',
            'v2 rollback readiness', 'other role container IDs unchanged', 'both memory sentinels unchanged'],
            rollback_containers=running)
    except BaseException as exc:
        evidence['error'] = repr(exc)
        raise
    finally:
        (workspace / 'SERVICE_REPORT.json').write_text(json.dumps(evidence, indent=2))
        for role, d in deployments.items():
            prefix = ['docker', 'compose', '-p', d['project'], '-f', str(Path(d['directory']) / 'compose.json')]
            (workspace / f'{role}-service.log').write_text(command(prefix + ['logs', '--no-color']))
            command(prefix + ['down', '--timeout', '10'])
        command(fake_prefix + ['down', '--timeout', '10'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    run(args.workspace, args.image)
