"""Offline regression: poisoned packs, stale probes, day rotation, both identities."""
import hashlib
import json
import os
import pathlib
import sys

os.environ['XIAOTIAN_TEST'] = '1'
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import common_scenes as cs
import scene_refs as sr
import identity


def reviewed_pack(root, pid='canteen', count=4):
    folder = root / pid
    (folder / 'images').mkdir(parents=True, exist_ok=True)
    items = {}
    for i in range(count):
        p = folder / 'images' / f'{i}.jpg'
        p.write_bytes(f'fixture {i}'.encode())
        items[p.name] = dict(approved=True, reviewer='test fixture', evidence='fixture, not production approval',
                            scene_match=True, campus_match=True, people_free=True,
                            sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    (folder / 'pack.json').write_text(json.dumps({'kept_files': list(items)}))
    (folder / 'visual_review.json').write_text(json.dumps({'version': 1, 'pack_id': pid, 'images': items}))
    return folder


def test_location_resolution():
    assert cs.resolve_pack('校外餐厅吃晚饭') is None
    assert cs.resolve_pack('窗口边吃午饭') is None
    assert cs.resolve_pack('舞蹈教室排练') == 'studio'
    assert cs.resolve_pack_from_slot({'activity': '食堂晚饭'}, '校外拉面店吃饭') == 'canteen'
    assert cs.resolve_pack_from_slot({'activity': '食堂晚饭'}, '宿舍看书') == 'canteen'


def test_approval_bound_to_bytes_and_allowlist(tmp_path):
    folder = reviewed_pack(tmp_path)
    cfg = {'imagegen': {'common_scenes_dir': str(tmp_path)}}
    assert len(cs.list_pack_images('canteen', cfg)) == 4
    (folder / 'images' / '0.jpg').write_bytes(b'replaced with ramen shop')
    assert len(cs.list_pack_images('canteen', cfg)) == 3
    (folder / 'pack.json').write_text('{"kept_files": []}')
    assert cs.list_pack_images('canteen', cfg) == []
    (folder / 'visual_review.json').unlink()
    assert cs.list_pack_images('canteen', cfg) == []


def test_daily_rotation(tmp_path):
    reviewed_pack(tmp_path)
    cfg = {'imagegen': {'common_scenes_dir': str(tmp_path)}}
    picks = [cs.pick_common('canteen', 2, cfg, date=f'2026-11-0{i}') for i in range(1, 5)]
    assert len({p[0] for p in picks}) == 4
    assert picks[0] == cs.pick_common('canteen', 2, cfg, date='2026-11-01')


def test_stale_probe_and_generic_cannot_bypass_review(tmp_path, monkeypatch):
    folder = reviewed_pack(tmp_path / 'common')
    bad = folder / 'images' / 'bad.jpg'
    bad.write_bytes(b'ramen shop')
    probes = tmp_path / 'probes.json'
    probes.write_text(json.dumps({'days': {'2026-11-01': {'probes': {
        'common:canteen': {'paths': [str(bad)]}}}}}))
    monkeypatch.setattr(sr, 'DAY_PROBES_PATH', probes)
    cfg = {'imagegen': {'common_scenes_dir': str(tmp_path / 'common')}}
    monkeypatch.setattr(sr, 'pick', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('unsafe fallback')))
    assert bad not in sr.get_day_probe('2026-11-01', 'common:canteen', cfg=cfg)
    (folder / 'visual_review.json').unlink()
    assert sr.pick_with_common_and_probes('食堂晚饭', cfg=cfg, date='2026-11-01') == []
    result = sr.ensure_day_probes({'date': '2026-11-01', 'slots': [{'activity': '食堂晚饭'}]}, cfg=cfg)
    assert result['common:canteen']['paths'] == []


def test_both_identities_use_review_and_isolate_probes(tmp_path, monkeypatch):
    identities = tmp_path / 'identities'
    role_dir = identities / 'demo_role'
    role_dir.mkdir(parents=True)
    (role_dir / 'identity.json').write_text(json.dumps({
        'persona_id': 'demo_role',
        'name': 'Demo companion',
        'partner_id': 'demo_contact',
        'profile_dir': str(tmp_path / 'profiles' / 'demo_role'),
        'routes': [{'channel': 'qq_onebot', 'bot_account_id': '100', 'peer_id': '200'}],
    }))
    (role_dir / 'partner.json').write_text(json.dumps({
        'id': 'demo_contact', 'name': 'Demo contact',
    }))
    monkeypatch.setenv('XIAOTIAN_IDENTITIES_DIR', str(identities))
    monkeypatch.delenv('ROLE_REGISTRY_DIR', raising=False)

    base = {'imagegen': {'common_scenes_dir': str(tmp_path / 'common')}}
    reviewed_pack(tmp_path / 'common')
    other = identity.scoped_load(base, 'demo_role')
    assert cs.common_dir(base) == cs.common_dir(other)
    assert sr._probes_path(base) != sr._probes_path(other)
    for cfg in [base, other]:
        assert len(sr.pick_with_common_and_probes('食堂午饭', cfg=cfg, date='2026-11-01')) == 2
    (tmp_path / 'common/canteen/visual_review.json').unlink()
    for cfg in [base, other]:
        assert sr.pick_with_common_and_probes('食堂午饭', cfg=cfg, date='2026-11-01') == []


def test_imagegen_slot_id_cannot_override_common_review(tmp_path, monkeypatch):
    import imagegen
    monkeypatch.delenv('XIAOTIAN_TEST')  # selector only: never invoke a provider
    folder = reviewed_pack(tmp_path / 'common')
    cfg = {'imagegen': {'common_scenes_dir': str(tmp_path / 'common')}}
    monkeypatch.setattr(sr, 'pick', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('slot bypass')))
    slot = {'activity': '食堂晚饭', 'scene_id': 'campus-010'}
    selected = imagegen.pick_scene_refs('食堂晚饭', {}, cfg, k=2, slot=slot, date='2026-11-01')
    assert len(selected) == 2
    assert all(cs.approved_image(p, 'canteen', cfg) for p in selected)
    (folder / 'visual_review.json').unlink()
    assert imagegen.pick_scene_refs('食堂晚饭', {}, cfg, k=2, slot=slot, date='2026-11-01') == []


def test_external_scene_does_not_reuse_canteen_slot(tmp_path, monkeypatch):
    import imagegen
    monkeypatch.delenv('XIAOTIAN_TEST')
    seen = []
    image = tmp_path / 'external.jpg'
    image.write_bytes(b'fixture')
    def pick(key, **kwargs):
        seen.append(key)
        return [(image, {})]
    monkeypatch.setattr(sr, 'pick', pick)
    monkeypatch.setattr(sr, 'get_day_probe', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('stale slot probe')))
    result = imagegen.pick_scene_refs('校外拉面店吃晚饭', {'top': [{'id': 'campus-010'}]},
        {'imagegen': {'common_scenes_dir': str(tmp_path)}}, k=1,
        slot={'activity': '食堂晚饭', 'scene_id': 'campus-010'}, date='2026-11-01')
    assert result == []
    assert seen == []


def test_live_reference_rotation_survives_restart_and_is_role_local(tmp_path, monkeypatch):
    import imagegen
    import scene_ref_rotation
    monkeypatch.delenv('XIAOTIAN_TEST')  # selectors only
    folder = reviewed_pack(tmp_path / 'common', count=12)
    base = {'imagegen': {'common_scenes_dir': str(tmp_path / 'common'),
                         'session_state': str(tmp_path / 'xiaotian/session.json')}}
    other = {**base, 'identity_runtime': {'persona_id': 'xiaosu',
                                         'runtime_root': str(tmp_path / 'xiaosu')}}
    def choose(cfg, rotate=True):
        return imagegen.pick_scene_refs('食堂午饭', {}, cfg, k=4,
            slot={'activity': '食堂午饭'}, date='2026-11-02', rotate=rotate)
    preview = choose(base, False)
    first = choose(base)
    assert first == preview
    assert choose(base, False) == preview  # previews don't consume rotation
    second = choose(base)
    import importlib
    importlib.reload(scene_ref_rotation)
    third = choose(base)
    assert len(set(first + second + third)) == 12
    assert choose(base) == first
    # Xiaosu starts at its own daily order; Xiaotian did not consume it.
    assert choose(other) == choose(other, False)
    # Revoke approval after selection: stored history cannot resurrect refs.
    (folder / 'visual_review.json').unlink()
    assert choose(base) == choose(other) == []


def test_generic_live_reference_rotation(tmp_path, monkeypatch):
    import sqlite3
    folder = tmp_path / 'generic'
    folder.mkdir()
    con = sqlite3.connect(folder / sr.DB_NAME)
    con.execute('CREATE TABLE images (id INTEGER, scene_id TEXT, quality REAL, people_free INTEGER, '
                'sha256 TEXT, path TEXT, relpath TEXT, embedding BLOB)')
    for i in range(8):
        p = folder / f'{i}.jpg'
        p.write_bytes(str(i).encode())
        con.execute('INSERT INTO images VALUES (?,?,?,?,?,?,?,?)',
                    (i, 'campus-001', 1, 1, str(i), str(p), p.name, None))
    con.commit(); con.close()
    cfg = {'imagegen': {'scene_refs_dir': str(folder), 'session_state': str(tmp_path / 'session.json')}}
    monkeypatch.setattr(sr, '_embed_text', lambda *a: (_ for _ in ()).throw(AssertionError('no model')))
    first = sr.pick('campus-001', 4, cfg, date='2026-11-02', rotate=True)
    second = sr.pick('campus-001', 4, cfg, date='2026-11-02', rotate=True)
    assert not {p for p, _ in first} & {p for p, _ in second}


def test_concurrent_reference_reservations_do_not_choose_same_first_image(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from scene_ref_rotation import select
    candidates = []
    for i in range(8):
        p = tmp_path / f'{i}.png'; p.write_bytes(str(i).encode()); candidates.append(p)
    cfg = {'imagegen': {'session_state': str(tmp_path / 'session.json')}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        selected = list(pool.map(lambda _: select(candidates, 2, cfg, 'common:canteen'), range(4)))
    assert len({p for pair in selected for p in pair}) == 8


def test_all_shots_keep_new_capture_instruction():
    import imagegen
    for shot in ['hand_closeup', 'pov_first_person', 'full_body', 'portrait', 'legs_focus']:
        roles = ['face', 'body'] if shot == 'full_body' else [{'hand_closeup':'hands', 'legs_focus':'legs'}.get(shot, 'face')]
        prompt = imagegen.build_prompt('食堂午饭', 'fixture', n_canonical=len(roles),
                                      reference_roles=roles, n_scene_refs=3, shot_type=shot, n_images=2)
        assert '忽略会话先前的画面，重新拍摄' in prompt, shot
        assert '多张均在同一地点内取景' in prompt, shot
        assert '会话旧图不能提供身份' in prompt, shot
