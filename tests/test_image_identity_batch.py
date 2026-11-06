"""Offline regressions for mixed-shot identity references; no real generation."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import config
import imagegen


@pytest.fixture
def batch_env(monkeypatch, tmp_path):
    cfg = copy.deepcopy(config.load())
    profile = tmp_path / "profile"
    for part in ("", "body", "hands", "legs"):
        folder = profile / "canonical" / part
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "sheet.png").write_bytes(b"reference fixture")
        if part:
            (folder / f"{part}.json").write_text(json.dumps({"status": "approved"}))
    cfg["appearance"]["profile_dir"] = str(profile)
    cfg["appearance"]["canonical_refs"] = ["sheet.png"]
    cfg["imagegen"].update(out_dir=str(tmp_path / "out"),
                           session_state=str(tmp_path / "session.json"), max_image_refs=5)
    monkeypatch.setattr(imagegen, "outfit_plan", lambda *a, **kw: {"text": "穿搭：米白开衫"})
    monkeypatch.setattr(imagegen, "pick_scene_refs", lambda *a, **kw: [tmp_path / f"scene{i}.jpg" for i in range(4)])
    monkeypatch.setattr(imagegen, "appearance", lambda *a: ("黑色长直发", []))
    monkeypatch.delenv("BODY_ORBIT_REFS", raising=False)
    monkeypatch.delenv("HANDS_REFS", raising=False)
    monkeypatch.delenv("LEGS_REFS", raising=False)
    return cfg, profile


@pytest.mark.parametrize("shots", [
    ["legs_focus", "full_body"],
    ["hand_closeup", "full_body"],
    ["pov_first_person", "full_body"],
    ["portrait", "full_body"],
    ["legs_focus", "portrait", "full_body"],
])
def test_later_full_body_retains_face_and_body_references(batch_env, shots):
    cfg, profile = batch_env
    cmd, images = imagegen.generate_local("餐馆门口看穿搭", dry=True, cfg=cfg,
                                         n_images=len(shots), shot_type=shots[0], shot_types=shots)
    refs = [Path(cmd[i + 1]) for i, arg in enumerate(cmd) if arg == "-i"]
    assert images == []
    assert refs[:2] == [profile / "canonical/sheet.png", profile / "canonical/body/sheet.png"]
    assert len(refs) <= 5
    if "hand_closeup" in shots:
        assert profile / "canonical/hands/sheet.png" in refs
    if "legs_focus" in shots:
        assert profile / "canonical/legs/sheet.png" in refs


def test_all_identity_types_fit_before_scenery(batch_env):
    cfg, profile = batch_env
    shots = ['hand_closeup', 'legs_focus', 'full_body', 'portrait']
    cmd, _ = imagegen.generate_local('餐馆门口', dry=True, cfg=cfg,
                                    n_images=4, shot_type=shots[0], shot_types=shots)
    refs = [Path(cmd[i+1]) for i, arg in enumerate(cmd) if arg == '-i']
    assert refs[:4] == [profile / 'canonical' / part / 'sheet.png'
                       for part in ('', 'body', 'hands', 'legs')]
    assert len(refs) == 5 and refs[-1].name == 'scene0.jpg'


@pytest.mark.parametrize('missing', ['sheet.png', 'body/sheet.png'])
def test_missing_required_reference_fails_before_generation(batch_env, missing):
    cfg, profile = batch_env
    (profile / 'canonical' / missing).unlink()
    with pytest.raises(RuntimeError, match='reference'):
        imagegen.generate_local('餐馆门口', dry=True, cfg=cfg, n_images=2,
                                shot_type='legs_focus', shot_types=['legs_focus', 'full_body'])


@pytest.mark.parametrize('retry', [False, True])
def test_real_pipeline_input_and_audit_keep_identity_on_retry(batch_env, monkeypatch, tmp_path, retry):
    import codex_session
    import hashlib
    cfg, profile = batch_env
    prompts, commands = [], []
    generated = tmp_path / 'model-result.png'
    generated.write_bytes(b'image fixture')
    monkeypatch.setattr(codex_session, 'current', lambda *a: None)
    monkeypatch.setattr(codex_session, 'save', lambda *a: None)
    monkeypatch.setattr(imagegen, 'rollout_sizes', lambda: {})
    monkeypatch.setattr(imagegen, '_from_rollouts', lambda *a: [])
    monkeypatch.setattr(imagegen, '_new_images', lambda *a:
                        [] if retry and len(commands) == 1 else [generated])
    def run(cmd, **kw):
        commands.append(cmd)
        prompts.append(kw['input'])
        return SimpleNamespace(returncode=0, stdout='policy blocked' if retry and len(commands) == 1 else '', stderr='')
    monkeypatch.setattr(imagegen.subprocess, 'run', run)
    monkeypatch.setattr(imagegen, 'blocked', lambda text: 'policy blocked' in text)
    _, imgs = imagegen.generate_local('餐馆门口', cfg=cfg, n_images=2,
        shot_type='legs_focus', shot_types=['legs_focus', 'full_body'], _meta={'chat': True})
    assert imgs and len(commands) == (2 if retry else 1)
    for prompt, cmd in zip(prompts, commands):
        assert '本次生成 2 张' in prompt
        assert '第 1 张：脸部五视图' in prompt and '第 2 张：身体五视图' in prompt
        assert '第 3 张：腿部五视图' in prompt
        assert '禁止把第 1 张当正脸身份模卡硬锁' not in prompt
        refs = [Path(cmd[i+1]) for i, arg in enumerate(cmd) if arg == '-i']
        assert refs[:2] == [profile/'canonical/sheet.png', profile/'canonical/body/sheet.png']
        assert len(refs) == 5
    output = imgs[0].parent
    saved_prompt = (output/'prompt.txt').read_text()
    meta = json.loads((output/'run.json').read_text())
    assert saved_prompt == prompts[-1]
    assert meta['prompt_sha256'] == hashlib.sha256(saved_prompt.encode()).hexdigest()
    assert meta['reference_sha256'][str(profile/'canonical/sheet.png')] == hashlib.sha256(b'reference fixture').hexdigest()
    assert meta['shot_types'] == ['legs_focus', 'full_body']
