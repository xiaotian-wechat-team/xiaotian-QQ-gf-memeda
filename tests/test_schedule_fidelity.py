"""P0: schedule fidelity, outbox freshness, no user-internship default, curiosity gate."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import schedule_fidelity as sf


INTERN = {
    "bucket": "work_internship",
    "activity": "电影公司实习·资料整理/粗剪助理",
    "start": "13:13",
    "end": "17:37",
    "scene_hints": ["剪辑室", "实习"],
}


def test_ban_xiake_during_internship():
    assert sf.text_violates_slot("下课啦 今天排练排到腿软", INTERN)
    out = sf.enforce_outbound_text("下课啦 今天排练排到腿软", INTERN)
    assert "下课" not in out
    assert out  # fallback kept


def test_keep_internship_claim():
    t = "实习摸鱼两秒 屏幕好刺眼"
    assert not sf.text_violates_slot(t, INTERN)
    assert sf.enforce_outbound_text(t, INTERN) == t


def test_force_scene():
    bad = "下课自拍·教室"
    assert not sf.scene_matches_slot(bad, INTERN)
    forced = sf.force_scene_for_slot(INTERN, bad)
    assert "剪辑" in forced or "实习" in forced


def test_no_user_internship_ask():
    assert sf.user_internship_assumed("实习忙不忙")
    assert sf.user_internship_assumed("你实习还顺利吗")
    assert sf.user_internship_assumed("实习忙就说忙")
    assert not sf.user_internship_assumed("我实习摸鱼呢")
    out = sf.enforce_outbound_text("在吗\n实习忙不忙", INTERN)
    assert "实习忙不忙" not in out


def test_outbox_stale_mocked_and_mismatch():
    now = dt.datetime(2026, 10, 26, 15, 30, 0)
    mocked = {
        "id": "m1",
        "text": "下课啦",
        "reason": "proactive onebot: onebot send fail: mocked",
        "created": "2026-10-24 20:00:00",
        "status": "pending",
    }
    stale, why = sf.outbox_item_stale(mocked, now=now, slot=INTERN)
    assert stale and "mocked" in why

    mismatch = {
        "id": "m2",
        "text": "下课啦 回宿舍了",
        "reason": "proactive",
        "created": "2026-10-26 15:00:00",
        "status": "pending",
    }
    stale2, why2 = sf.outbox_item_stale(mismatch, now=now, slot=INTERN)
    assert stale2 and "slot_mismatch" in why2

    data = {"items": [mocked, mismatch, {
        "id": "ok",
        "text": "实习摸鱼",
        "reason": "proactive",
        "created": "2026-10-26 15:10:00",
        "status": "pending",
    }]}
    purged = sf.purge_outbox_items(data, now=now, slot=INTERN, clear_terminal_mocked=True)
    statuses = {it["id"]: it["status"] for it in purged["items"]}
    assert statuses["m1"] == "dropped"
    assert statuses["m2"] == "dropped"
    assert statuses["ok"] == "pending"


def test_curiosity_frequency_not_everyday():
    st = {}
    hits = 0
    for day in range(1, 31):
        now = dt.datetime(2026, 10, day if day <= 31 else 1, 16, 0, 0)
        # October has 31 days; use Nov for overflow-safe
        if day > 31:
            continue
        try:
            now = dt.datetime(2026, 10, day, 16, 0, 0)
        except ValueError:
            continue
        if sf.should_ask_his_day(now, st, {"beats": {"user_schedule_curiosity_p": 0.72}}):
            hits += 1
            # simulate asked → next day blocked by same-day / gap
            st = {"user_day_curiosity": sf.mark_asked({}, now)}
        else:
            # clear pending only for roll test across days without mark
            pass
    # With mark_asked each hit, subsequent days need gap; with p=0.72 expect many but not 31
    assert 5 <= hits <= 25, hits


def test_record_answer():
    now = dt.datetime(2026, 10, 26, 16, 0, 0)
    blob = sf.mark_asked({}, now, question="今天咋安排的")
    assert blob["pending_ask"] is True
    st = {"user_day_curiosity": blob}
    got = sf.maybe_capture_answer(st, "下午开会然后回家写东西", now=now, cid=None)
    assert got and got["pending_ask"] is False
    assert "开会" in got["summary"]


def test_inbound_gate_still_blocks_ganma():
    import photo_scene
    assert photo_scene.allow_inbound_image_gen("干嘛呢") is False
    assert photo_scene.user_asks_photo("干嘛呢") is False



def test_already_asked_mutual_exclusion():
    now = dt.datetime(2026, 10, 26, 16, 0, 0)
    assert not sf.already_asked_his_day_today({}, now)
    blob = sf.mark_asked_his_day({}, now, question="今天干嘛")
    st = {"user_day_curiosity": blob}
    assert sf.already_asked_his_day_today(st, now)
    assert not sf.should_ask_his_day(now, st, {"beats": {"user_schedule_curiosity_p": 1.0}})
    # next day may ask again (gap=1 default → blocked until gap clears; with gap 0 ok)
    nxt = now + dt.timedelta(days=1)
    assert not sf.already_asked_his_day_today(st, nxt)


def test_image_pendulum_stable_in_bucket():
    now = dt.datetime(2026, 10, 26, 10, 12, 0)
    a = sf.image_pendulum_allow(now, "schedule_brief", p=0.55, bucket_min=30)
    b = sf.image_pendulum_allow(now.replace(minute=20), "schedule_brief", p=0.55, bucket_min=30)
    assert a is b  # same 30-min bucket
    # extreme p
    assert sf.image_pendulum_allow(now, "meal", p=0.0) is False
    assert sf.image_pendulum_allow(now, "meal", p=1.0) is True


def test_outbox_interrogation_stale_drop():
    now = dt.datetime(2026, 10, 26, 16, 30, 0)
    slot = {"bucket": "city_fun", "activity": "逛街", "start": "15:00", "end": "18:00"}
    assert sf.is_interrogation_ask("你在干嘛呀")
    item = {
        "id": "s1",
        "text": "怎么不找我 在干嘛",
        "reason": "proactive onebot: HOLD",
        "beat_id": "silence_nudge",
        "created": "2026-10-26 14:00:00",  # >90 min → interrogation_expired
        "status": "pending",
        "slot_bucket": "weekend_home",
        "slot_start": "10:00",
    }
    stale, why = sf.outbox_item_stale(item, now=now, slot=slot)
    assert stale, (stale, why)
    assert "slot_changed" in why or "interrogation" in why or "silence" in why, why
    assert sf.rewrite_stale_interrogation(item["text"]) is None  # prefer drop

    # same slot, fresh → not stale by interrogation rules alone
    fresh = {
        "id": "s2",
        "text": "嗨呀想你了",
        "reason": "proactive",
        "created": "2026-10-26 16:20:00",
        "status": "pending",
        "slot_bucket": "city_fun",
        "slot_start": "15:00",
    }
    stale2, why2 = sf.outbox_item_stale(fresh, now=now, slot=slot)
    assert not stale2, why2



def test_outbox_ask_mark_only_on_flush_success(tmp_path, monkeypatch):
    """Enqueue/HOLD must not green; flush-success helper marks 「回执绿」."""
    import memory
    cid = memory.CFG["contact"]["id"]
    monkeypatch.setattr(memory, "DL", tmp_path)
    monkeypatch.setattr(memory, "ALLOWED", {cid})
    (tmp_path / "contacts" / cid).mkdir(parents=True)
    (tmp_path / "contacts" / cid / "STATE.json").write_text("{}", encoding="utf-8")
    now = dt.datetime(2026, 10, 26, 12, 0)

    # predicates
    assert sf.outbox_item_marks_ask_his_day({"beat_id": "ask_his_day"})
    assert sf.outbox_item_marks_ask_his_day({"beat_id": "schedule_brief", "ask_him": True})
    assert not sf.outbox_item_marks_ask_his_day({"beat_id": "schedule_brief", "ask_him": False})
    assert not sf.outbox_item_marks_ask_his_day({"beat_id": "meal"})
    # Plan B: activity_end only via stamped ask_him (id alone never marks)
    assert sf.outbox_item_marks_ask_his_day({"beat_id": "activity_end", "ask_him": True})
    assert not sf.outbox_item_marks_ask_his_day({"beat_id": "activity_end", "ask_him": False})
    assert not sf.outbox_item_marks_ask_his_day({"beat_id": "activity_end"})
    assert sf.beat_meta_marks_ask_his_day({"id": "activity_end", "ask_him": True})
    assert not sf.beat_meta_marks_ask_his_day({"id": "activity_end", "ask_him": False})
    assert not sf.beat_meta_marks_ask_his_day({"id": "activity_end"})
    assert sf.beat_meta_marks_ask_his_day({"id": "schedule_brief", "ask_him": True})
    assert sf.beat_meta_marks_ask_his_day({"id": "ask_his_day"})

    # pending outbox item alone → STATE still unmarked (enqueue does not call helper)
    pending = {
        "id": "h1", "contact": cid, "text": "你今天咋安排呀",
        "beat_id": "ask_his_day", "ask_him": True, "status": "pending",
    }
    st0 = memory.read_state(cid)
    assert not sf.already_asked_his_day_today(st0, now)

    # flush success helper → greens
    assert sf.mark_asked_his_day_on_flush_success(pending, now=now) is True
    st1 = memory.read_state(cid)
    assert sf.already_asked_his_day_today(st1, now)
    blob = st1.get("user_day_curiosity") or {}
    assert blob.get("ask_sent_ok") is True
    assert blob.get("last_asked") == "2026-10-26"

    # non-ask flush → no mark on a clean state rewrite
    (tmp_path / "contacts" / cid / "STATE.json").write_text("{}", encoding="utf-8")
    other = {"id": "h2", "contact": cid, "text": "晚饭好香", "beat_id": "meal", "status": "sent"}
    assert sf.mark_asked_his_day_on_flush_success(other, now=now) is False
    assert not sf.already_asked_his_day_today(memory.read_state(cid), now)




def test_activity_end_ask_him_flush_marks_and_without_does_not(monkeypatch, tmp_path):
    """activity_end+ask_him greens on flush; activity_end without ask_him never marks."""
    import memory
    cid = memory.CFG["contact"]["id"]
    monkeypatch.setattr(memory, "DL", tmp_path)
    monkeypatch.setattr(memory, "ALLOWED", {cid})
    (tmp_path / "contacts" / cid).mkdir(parents=True)
    (tmp_path / "contacts" / cid / "STATE.json").write_text("{}", encoding="utf-8")
    now = dt.datetime(2026, 10, 26, 18, 40)

    stamped = {
        "id": "ae1", "contact": cid, "text": "刚攀完有点酸，你在干嘛",
        "beat_id": "activity_end", "ask_him": True, "status": "pending",
    }
    assert sf.outbox_item_marks_ask_his_day(stamped)
    assert sf.mark_asked_his_day_on_flush_success(stamped, now=now) is True
    assert sf.already_asked_his_day_today(memory.read_state(cid), now)

    (tmp_path / "contacts" / cid / "STATE.json").write_text("{}", encoding="utf-8")
    no_ask = {
        "id": "ae2", "contact": cid, "text": "你在干嘛呀",
        "beat_id": "activity_end", "ask_him": False, "status": "pending",
    }
    assert not sf.outbox_item_marks_ask_his_day(no_ask)
    assert sf.mark_asked_his_day_on_flush_success(no_ask, now=now) is False
    assert not sf.already_asked_his_day_today(memory.read_state(cid), now)

    bare = {
        "id": "ae3", "contact": cid, "text": "你在干嘛呀",
        "beat_id": "activity_end", "status": "pending",
    }
    assert not sf.outbox_item_marks_ask_his_day(bare)
    assert not sf.beat_meta_marks_ask_his_day({"id": "activity_end"})

if __name__ == "__main__":
    test_ban_xiake_during_internship()
    test_keep_internship_claim()
    test_force_scene()
    test_no_user_internship_ask()
    test_outbox_stale_mocked_and_mismatch()
    test_curiosity_frequency_not_everyday()
    test_record_answer()
    test_inbound_gate_still_blocks_ganma()
    test_already_asked_mutual_exclusion()
    test_image_pendulum_stable_in_bucket()
    test_outbox_interrogation_stale_drop()
    # test_outbox_ask_mark_only_on_flush_success needs pytest tmp_path
    print("test_schedule_fidelity OK")
