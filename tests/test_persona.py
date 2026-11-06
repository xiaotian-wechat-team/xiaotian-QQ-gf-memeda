"""Offline persona contract and file loading; no dependency on Mac mini paths."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import memory


def test_persona_reads_explicit_directory_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DL", tmp_path)
    expected = {"soul": "一、身份\n小甜，小熊女朋友。\n", "user": "小熊\n", "memory": "已确认的关系\n"}
    for name, text in expected.items():
        (tmp_path / f"{name.upper()}.md").write_text(text)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert memory.persona() == expected
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    (tmp_path / "MEMORY.md").unlink()
    assert memory.persona()["memory"] == ""


def test_repository_persona_contract():
    # Runtime SOUL is outside Git; its nine-section contract is tracked here.
    source = (ROOT / "docs/persona-source.md").read_text()
    for heading in ("身份", "性格", "说话方式", "关系", "日常", "禁忌", "示例", "设定词条", "每轮最后提醒"):
        assert f"、{heading}" in source
    dossier = json.loads((ROOT / "data/persona_dossier.json").read_text())
    assert dossier["user_ref"]["name"] == "小熊"
    assert "女朋友" in dossier["identity"]["role"]
    assert dossier["sources"]["do_not_invent"] is True
    for path in ("SKILL.md", "SKILL.deepseek.md"):
        skill = (ROOT / ".cursor/skills/xiaotian-chat" / path).read_text()
        assert "data/persona_dossier.json" in skill
        assert str(dossier["school"]["cohort_year"]) in skill
        assert dossier["school"]["grade"] in skill
        assert "数字生命" in skill
        assert "不照做" in skill


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
