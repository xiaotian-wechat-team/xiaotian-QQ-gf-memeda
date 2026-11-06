"""sync_to_memory / update_state must merge life_* without dropping emotion/proactive_beats."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import sys, json, tempfile, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import emotion
import life_calendar as lc
import memory


def test_update_state_merges_non_life_keys(tmp_path=None):
    root = pathlib.Path(tempfile.mkdtemp(prefix="xt-state-"))
    cid = "demo_contact"
    cdir = root / "contacts" / cid
    cdir.mkdir(parents=True)
    (cdir / ".lock").write_text("")
    init = {
        "emotion": emotion.default(),
        "proactive_beats": {"day": "2026-10-25", "done": {}, "last_slot_key": "", "last_activity": "", "shared_material_ids": [], "surf_target": None, "shares": []},
        "last_reply_at": "2026-10-25 09:00:00",
        "recent_nicknames": ["小熊"],
        "life_today": {"date": "2026-10-25", "kind": "weekend", "slots": []},
    }
    (cdir / "STATE.json").write_text(json.dumps(init, ensure_ascii=False, indent=2))
    old_dl, old_allowed = memory.DL, set(memory.ALLOWED)
    memory.DL = root
    memory.ALLOWED = {cid}
    try:
        memory.update_state(cid, life_now={"date": "2026-10-25", "start": "10:00", "end": "12:00",
                                           "activity": "测试", "bucket": "weekend_home", "energy": 0})
        st = memory.read_state(cid)
        assert "emotion" in st and st["emotion"]["intimacy"] == emotion.default()["intimacy"]
        assert "proactive_beats" in st and st["proactive_beats"]["day"] == "2026-10-25"
        assert st["recent_nicknames"] == ["小熊"]
        assert st["last_reply_at"] == "2026-10-25 09:00:00"
        assert st["life_now"]["activity"] == "测试"
        assert set(init) - {"life_today"} <= set(st)  # non-life kept
    finally:
        memory.DL, memory.ALLOWED = old_dl, old_allowed


def test_sync_to_memory_preserves_emotion_and_beats():
    root = pathlib.Path(tempfile.mkdtemp(prefix="xt-sync-"))
    cid = "demo_contact"
    cdir = root / "contacts" / cid
    cdir.mkdir(parents=True)
    (cdir / ".lock").write_text("")
    emo = emotion.default()
    emo["intimacy"] = 66.0
    beats = {"day": "2026-10-25", "done": {"morning": "10:20:00"}, "last_slot_key": "x",
             "last_activity": "赖床", "shared_material_ids": [7]}
    (cdir / "STATE.json").write_text(json.dumps({
        "emotion": emo,
        "proactive_beats": beats,
        "mood": 5,
        "energy": 4,
        "custom_keep": {"ok": True},
    }, ensure_ascii=False, indent=2))
    old_dl, old_allowed = memory.DL, set(memory.ALLOWED)
    memory.DL = root
    memory.ALLOWED = {cid}
    now = datetime.datetime(2026, 10, 25, 10, 30)
    try:
        payload = lc.sync_to_memory(cid, now=now, write_mem0=False)
        assert "life_now" in payload and "life_today" in payload
        st = memory.read_state(cid)
        assert st["emotion"]["intimacy"] == 66.0
        assert st["proactive_beats"]["done"]["morning"] == "10:20:00"
        assert st["proactive_beats"]["shared_material_ids"] == [7]
        assert st["custom_keep"] == {"ok": True}
        assert st["mood"] == 5
        assert st.get("life_today") is not None
        assert st.get("life_now") is not None
    finally:
        memory.DL, memory.ALLOWED = old_dl, old_allowed




def test_sync_seeds_missing_emotion_and_beats():
    """ensure path: missing emotion/proactive_beats get runtime defaults on sync."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="xt-seed-"))
    cid = "demo_contact"
    cdir = root / "contacts" / cid
    cdir.mkdir(parents=True)
    (cdir / ".lock").write_text("")
    (cdir / "STATE.json").write_text(json.dumps({"mood": 5}, ensure_ascii=False, indent=2))
    old_dl, old_allowed = memory.DL, set(memory.ALLOWED)
    memory.DL = root
    memory.ALLOWED = {cid}
    now = datetime.datetime(2026, 10, 25, 10, 30)
    try:
        assert "emotion" not in memory.read_state(cid)
        lc.sync_to_memory(cid, now=now, write_mem0=False)
        st = memory.read_state(cid)
        assert isinstance(st.get("emotion"), dict) and "pad" in st["emotion"]
        assert isinstance(st.get("proactive_beats"), dict)
        assert st["proactive_beats"].get("day") == "2026-10-25"
        assert st["mood"] == 5
    finally:
        memory.DL, memory.ALLOWED = old_dl, old_allowed

def test_heal_proactive_from_queue_restores_wiped_done():
    """Whole-file life-only wipe → ensure_defaults/heal restores done+last_proactive from queue."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="xt-heal-"))
    cid = "demo_contact"
    cdir = root / "contacts" / cid
    cdir.mkdir(parents=True)
    (cdir / ".lock").write_text("")
    # life-only wipe fingerprint
    (cdir / "STATE.json").write_text(json.dumps({
        "life_today": {"date": "2026-10-25", "kind": "weekend", "slots": []},
        "life_now": {"start": "19:30", "end": "21:00", "activity": "798"},
        "life_plan_meta": {"synced_at": "2026-10-24T20:00:00", "source": "draft"},
    }, ensure_ascii=False, indent=2))
    qdir = pathlib.Path(tempfile.mkdtemp(prefix="xt-q-"))
    qpath = qdir / "proactive_queue.json"
    qpath.write_text(json.dumps({
        "items": [
            {"status": "sent", "topic": "beat:pre_sleep", "sent_at": "2026-10-25 22:47:05",
             "meta": {"beat_done_key": "pre_sleep", "beat_meta": {"id": "pre_sleep"}}},
            {"status": "sent", "topic": "beat:meal", "sent_at": "2026-10-25 18:32:05",
             "meta": {"beat_done_key": "meal@18:06"}},
        ]
    }, ensure_ascii=False))
    old_dl, old_allowed, old_cfg = memory.DL, set(memory.ALLOWED), dict(memory.CFG)
    memory.DL = root
    memory.ALLOWED = {cid}
    memory.CFG = {**memory.CFG, "db_path": str(qdir / "chat.db")}
    now = datetime.datetime(2026, 10, 25, 23, 10)
    try:
        kv = memory.heal_proactive_from_queue(cid, now=now, queue_path=qpath)
        assert kv.get("last_proactive_at") == "2026-10-25 22:47:05"
        st = memory.read_state(cid)
        assert st["proactive_beats"]["done"]["pre_sleep"] == "22:47:05"
        assert st["proactive_beats"]["done"]["meal@18:06"] == "18:32:05"
        assert "life_today" in st  # life keys kept
        # second heal is no-op
        assert memory.heal_proactive_from_queue(cid, now=now, queue_path=qpath) == {}
    finally:
        memory.DL, memory.ALLOWED, memory.CFG = old_dl, old_allowed, old_cfg


def test_update_state_verify_roundtrip():
    root = pathlib.Path(tempfile.mkdtemp(prefix="xt-ver-"))
    cid = "demo_contact"
    cdir = root / "contacts" / cid
    cdir.mkdir(parents=True)
    (cdir / ".lock").write_text("")
    (cdir / "STATE.json").write_text("{}")
    old_dl, old_allowed = memory.DL, set(memory.ALLOWED)
    memory.DL = root
    memory.ALLOWED = {cid}
    try:
        memory.update_state(cid, last_proactive_at="2026-10-25 22:47:05",
                            proactive_beats={"day": "2026-10-25", "done": {"pre_sleep": "22:47:05"}})
        st = memory.read_state(cid)
        assert st["last_proactive_at"] == "2026-10-25 22:47:05"
        assert st["proactive_beats"]["done"]["pre_sleep"] == "22:47:05"
    finally:
        memory.DL, memory.ALLOWED = old_dl, old_allowed


if __name__ == "__main__":
    test_update_state_merges_non_life_keys()
    print("ok test_update_state_merges_non_life_keys")
    test_sync_to_memory_preserves_emotion_and_beats()
    print("ok test_sync_to_memory_preserves_emotion_and_beats")
    test_sync_seeds_missing_emotion_and_beats()
    print("ok test_sync_seeds_missing_emotion_and_beats")
    test_heal_proactive_from_queue_restores_wiped_done()
    print("ok test_heal_proactive_from_queue_restores_wiped_done")
    test_update_state_verify_roundtrip()
    print("ok test_update_state_verify_roundtrip")
    print("OK")
