"""Offline daily sessions/resume argv; temp state and mocked subprocess only."""
import datetime
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import llm
import llm_session


@pytest.fixture
def session(tmp_path, monkeypatch):
    cfg = {"session_state": str(tmp_path / "sess.json"), "workspace": str(tmp_path / "ws"),
           "cmd": "cursor-agent", "reuse_session": True, "model": "", "timeout": 30}
    monkeypatch.setattr(llm, "load", lambda: {"llm": cfg})
    monkeypatch.setattr(llm, "is_preview", lambda: False)
    return cfg


def test_day_rotation_and_resume_argv(session, monkeypatch):
    cfg = session
    d1 = datetime.datetime(2026, 10, 24, 9)
    d2 = d1 + datetime.timedelta(days=1)
    assert llm_session.current(cfg, d1) is None
    llm_session.save(cfg, "C1", d1)
    assert llm_session.current(cfg, d1) == "C1" and llm_session.current(cfg, d2) is None
    llm_session.save(cfg, "C1", d1)
    assert llm_session.load_state(cfg)["count"] == 2
    llm_session.forget(cfg, d1)
    assert llm_session.current(cfg, d1) is None
    assert llm_session.resume_broken("Error: thread/resume failed: no rollout found")
    calls = []
    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="NEW-CHAT-ID" if cmd[1] == "create-chat" else "在呢", stderr="")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm_session.ensure(cfg, "cursor-agent", d1) == "NEW-CHAT-ID"
    llm_session.save(cfg, "SID1", d1)
    # Freeze the actual date helper used by ensure/save, not an unused FakeDT class.
    monkeypatch.setattr(llm_session, "today", lambda now=None: d1.date().isoformat())
    calls.clear()
    assert llm.complete("hi", reuse_session=True) == "在呢"
    resume = next(c for c in calls if "--resume" in c)
    assert resume[resume.index("--resume") + 1] == "SID1"
    assert resume[resume.index("--workspace") + 1] == cfg["workspace"]
    assert "ask" in resume and "--sandbox" in resume
    calls.clear()
    assert llm.complete("{}", reuse_session=False) == "在呢"
    assert all("--resume" not in c for c in calls)


def test_failed_resume_reopens_without_reusing_broken_id(session, monkeypatch):
    cfg = session
    llm_session.save(cfg, "BROKEN")
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "BROKEN" in cmd:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="session not found")
        return types.SimpleNamespace(returncode=0, stdout="NEW" if cmd[1] == "create-chat" else "好了", stderr="")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.complete("hello", reuse_session=True) == "好了"
    assert llm_session.current(cfg) == "NEW"
    assert len([c for c in calls if "--resume" in c]) == 2
    assert any(h.get("session_id") == "BROKEN" and h.get("broken") for h in llm_session.load_state(cfg)["history"])


def test_preview_never_changes_real_daily_session(session, monkeypatch):
    cfg = session
    llm_session.save(cfg, "LIVE")
    before = pathlib.Path(cfg["session_state"]).read_bytes()
    calls = []
    monkeypatch.setattr(llm, "is_preview", lambda: True)
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="草稿", stderr="")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.complete("preview", reuse_session=True) == "草稿"
    assert all("--resume" not in c for c in calls)
    assert pathlib.Path(cfg["session_state"]).read_bytes() == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
