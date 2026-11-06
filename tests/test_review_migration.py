"""Approved fact migration: evidence required, original history preserved."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location("review_migration", Path(__file__).resolve().parents[1] / "tools/migrate_review_facts.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def setup(root, source=True):
    contact = root / "contacts/demo_contact"
    (contact / "diary").mkdir(parents=True)
    (root / "SOUL.md").write_text("北京舞蹈学院2023级大三\n其他设定\n")
    (contact / "PROFILE.md").write_text("# demo_contact\n已有身份信息\n")
    (contact / "MEMORY.md").write_text("已有记忆\n")
    (contact / "diary/2026-10-26.md").write_text("问他吃饭实习\n")
    (contact / "STATE.json").write_text('{"proactive_count":7}')
    db = root / "chat.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE messages(id,contact,sender,text)")
        c.execute("INSERT INTO messages VALUES(421,'demo_contact',?,?)", ("them" if source else "me", "我在北京大学读博呢"))
    return {"digital_life_dir": str(root), "db_path": str(db)}


def test_migration_preserves_history_state_and_is_idempotent(tmp_path):
    cfg = setup(tmp_path)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    edits = migration.plan(cfg)
    assert len(edits) == 4
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    reports = migration.apply(edits)
    assert migration.plan(cfg) == []
    assert (tmp_path / "chat.db").read_bytes() == before["chat.db"]
    assert (tmp_path / "contacts/demo_contact/STATE.json").read_bytes() == before["contacts/demo_contact/STATE.json"]
    assert "2024级大三" in (tmp_path / "SOUL.md").read_text()
    assert "北京大学读博" in (tmp_path / "contacts/demo_contact/PROFILE.md").read_text()[:400]
    assert "问他吃饭实习" in (tmp_path / "contacts/demo_contact/diary/2026-10-26.md").read_text()
    for report in reports:
        original = str(Path(report["path"]).relative_to(tmp_path))
        assert Path(report["backup"]).read_bytes() == before[original]


def test_own_utterance_does_not_become_user_fact(tmp_path):
    cfg = setup(tmp_path, source=False)
    assert [e[3] for e in migration.plan(cfg)] == ["cohort_2024_keep_junior"]


def test_concurrent_edit_is_not_overwritten(tmp_path):
    cfg = setup(tmp_path)
    edits = migration.plan(cfg)
    edits[0][0].write_text("用户刚修改的人设")
    with pytest.raises(RuntimeError, match="changed"):
        migration.apply(edits)
    assert edits[0][0].read_text() == "用户刚修改的人设"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
