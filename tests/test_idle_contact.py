"""Offline deadline / nap / delivery regressions. No network or production state."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import copy
import datetime as dt
import json
import pathlib
import sys
from unittest.mock import Mock
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import config
import idle_contact as idle
import memory
import life_calendar as lc
import sleep_chat as sleep
import proactive_beats as beats
import proactive_scheduler as scheduler
import proactive_sender as sender
import proactive_limiter as limiter

D = dt.datetime
NOW = D(2026, 10, 29, 14)


def day(date):
    return {"date": date.isoformat(), "sleep_bounds": {"wake": "08:00"}, "slots": [
        {"start": "00:00", "end": "08:00", "bucket": "sleep", "activity": "睡觉"},
        {"start": "08:00", "end": "23:00", "bucket": "campus_life", "activity": "图书馆看书", "photo_ok": True},
        {"start": "23:00", "end": "24:00", "bucket": "sleep", "activity": "睡觉"}]}


@pytest.fixture
def env(monkeypatch, tmp_path):
    full = copy.deepcopy(config.load())
    full["proactive"]["idle_contact"] = {"enabled": True, "interval_sec": [5400, 9000]}
    full["proactive"].update(transport="onebot", primary_channel="qq_onebot", channel_policy="primary")
    monkeypatch.setattr(config, "load", lambda: full)
    monkeypatch.setattr(lc, "get_day", day)
    monkeypatch.setattr(memory, "DL", tmp_path)
    monkeypatch.setattr(memory, "CFG", {**memory.CFG, "db_path": str(tmp_path / "chat.db")})
    (tmp_path / "contacts/demo_contact").mkdir(parents=True)
    from channels import outbox_store
    monkeypatch.setattr(outbox_store, "DB_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(memory, "ensure_defaults", lambda *a, **kw: None)
    monkeypatch.setattr(sender, "_maybe_apply_unanswered_miss", lambda cid, st, *a: (st, {}))
    monkeypatch.setattr(scheduler, "busy_value", lambda *a: 80)
    monkeypatch.setattr(beats, "due_beats", lambda *a: [])
    monkeypatch.setattr(sender, "_onebot_peer_and_cfg", lambda: ("demo_contact", {}))
    monkeypatch.setattr("chat_media.plan_chat_images", Mock(side_effect=AssertionError("unexpected image generation")))
    monkeypatch.setattr(sender, "send_via_onebot", Mock(side_effect=AssertionError("unexpected network send")))
    monkeypatch.setattr(sender, "send_via_ilink", Mock(side_effect=AssertionError("unexpected network send")))
    st = {"life_today": day(NOW.date()), "last_inbound_at": "2026-10-29 09:00:00",
          "last_reply_at": "2026-10-29 09:01:00"}
    memory.update_state("demo_contact", **st)
    return full, st, tmp_path / "queue.json"


def test_stable_random_interval_and_actual_contact_reset(env):
    full, st, _ = env
    pc = full["proactive"]
    values = []
    for i in range(120):
        at = NOW - dt.timedelta(seconds=i)
        state = {"last_reply_at": idle.iso(at)}
        clock = idle.advance(NOW, state, [], pc)
        values.append(clock["target_seconds"])
        later = idle.advance(NOW + dt.timedelta(minutes=1), state, [], pc, json.loads(json.dumps(clock)))
        assert later["target_seconds"] == clock["target_seconds"]
        assert later["anchor_at"] == clock["anchor_at"]
    assert all(5400 <= n <= 9000 for n in values) and len(set(values)) > 100
    first = idle.advance(NOW, st, [], pc)
    assert first["due"]
    for who, text in [("them", "在呢"), ("me", "[图片]"), ("me", "刚吃完饭")]:
        reset = idle.advance(NOW, st, [(who, text, idle.iso(NOW))], pc, first)
        assert reset["awake_seconds"] == 0 and not reset["due"]
        assert reset["anchor_at"] == idle.iso(NOW)
    assert idle.advance(NOW, st, [("system", "draft", idle.iso(NOW))], pc, first) == first


def test_no_history_arms_now_instead_of_fabricating_old_contact(env):
    full, _, _ = env
    first = idle.advance(NOW, {}, [], full["proactive"])
    later = idle.advance(NOW + dt.timedelta(minutes=5), {}, [], full["proactive"], first)
    assert later["anchor_at"] == idle.iso(NOW) and later["awake_seconds"] == 300


def test_sleep_is_subtracted_across_midnight_and_nap(env):
    full, st, _ = env
    start = D(2026, 10, 28, 22, 30)
    end = D(2026, 10, 29, 9)
    assert idle.awake_seconds(start, end, st) == 5400
    nap = copy.deepcopy(st)
    nap["life_today"]["slots"].append({"start": "12:00", "end": "13:00", "bucket": "sleep"})
    nap["sleep_chat"] = {"goodnight_at": "2026-10-29 11:30:00"}
    # 11:30–13:30 conversation nap contains 12–13 schedule sleep: union, not sum.
    assert idle.awake_seconds(D(2026, 10, 29, 11), NOW, nap) == 3600
    st["last_reply_at"], st["last_inbound_at"] = idle.iso(start), idle.iso(start)
    before = idle.advance(D(2026, 10, 28, 23, 30), st, [], full["proactive"])
    after = idle.advance(end, st, [], full["proactive"], before)
    assert after["awake_seconds"] == 5400 and before["awake_seconds"] == 1800
    assert before["target_seconds"] == after["target_seconds"]


def test_real_morning_nap_cannot_pause_until_tomorrow(env):
    _, st, _ = env
    now = D(2026, 10, 29, 9, 49, 35)
    _, b, _ = sleep.note_inbound("我再睡会 / 昨天两点睡的", st, now)
    assert b["goodnight_at"] == idle.iso(now)
    _, after = sleep.note_outbound("接着睡吧\n我在课上偷偷回你", {**st, "sleep_chat": b}, now + dt.timedelta(seconds=10))
    assert after["goodnight_at"] == idle.iso(now)  # her advice does not restart his nap
    assert sleep.should_suppress_proactive({**st, "sleep_chat": after}, now + dt.timedelta(minutes=30))
    assert sleep.should_suppress_proactive({**st, "sleep_chat": after}, NOW) is None
    for advice in ("接着睡吧", "快睡吧", "你去睡吧，我在上课"):
        _, b = sleep.note_outbound(advice, st, now)
        assert not b.get("goodnight_at"), advice
    legacy = {"mode": "asleep", "goodnight_at": "2026-10-29 09:49:45",
              "asleep_since": "2026-10-29 09:59:45", "proactive_sleep_until": "2026-10-30 07:19:00"}
    mode, repaired = sleep.refresh({**st, "sleep_chat": legacy}, NOW)
    assert mode == "normal" and repaired["wake_reason"] == "daytime_sleep_expired"
    assert repaired["last_sleep"]["end"] == "2026-10-29 11:49:45"
    assert legacy["proactive_sleep_until"] == "2026-10-30 07:19:00"


@pytest.mark.parametrize("text", ["我不睡午觉", "昨天我再睡会", "你再睡会", "他说我再睡会", "‘我再睡会’是什么意思", "我睡个午觉吗？", "明天补觉", "再睡会，继续聊"])
def test_nap_mentions_do_not_close(text):
    from conversation_semantics import is_nap_signoff
    assert not is_nap_signoff(text)


def test_nap_ping_resumes_but_night_signoff_remains(env):
    _, st, _ = env
    now = D(2026, 10, 29, 10)
    _, b, _ = sleep.note_inbound("我睡个午觉", st, now)
    _, b, _ = sleep.note_inbound("醒了", {**st, "sleep_chat": b}, now + dt.timedelta(minutes=20))
    assert not b.get("goodnight_at")
    assert sleep.should_suppress_proactive({**st, "sleep_chat": b}, now + dt.timedelta(minutes=21)) is None
    _, b = sleep.note_outbound("晚安小熊", st, now.replace(hour=22))
    assert sleep.should_suppress_proactive({**st, "sleep_chat": b}, now.replace(hour=23))
    assert sleep.should_suppress_proactive({**st, "sleep_chat": b}, D(2026, 10, 30, 8, 1)) is None


def test_scheduled_nap_ends_at_its_slot_not_next_morning(env):
    _, st, _ = env
    st["life_today"]["slots"] = [
        {"start": "12:00", "end": "13:30", "bucket": "sleep", "activity": "睡觉"},
        {"start": "13:30", "end": "23:00", "bucket": "campus_life", "activity": "校园日常"}]
    at = D(2026, 10, 29, 12, 10)
    _, b, _ = sleep.note_inbound("我先睡了", st, at)
    assert sleep.conversation_sleep_interval({**st, "sleep_chat": b})[1] == at.replace(hour=13, minute=30)
    assert sleep.should_suppress_proactive({**st, "sleep_chat": b}, NOW) is None


def force_style(monkeypatch, style):
    draw = idle._draw
    monkeypatch.setattr(idle, "_draw", lambda key: style if key.startswith("idle-style:") else draw(key))


def test_deadline_overrides_lottery_budget_media_and_model_skip(env, monkeypatch):
    full, st, _ = env
    force_style(monkeypatch, 0)
    st.update(proactive_day="2026-10-29", proactive_count=10,
              proactive_unanswered_turns=100)
    pc = full["proactive"]
    clock = idle.advance(NOW, st, [], pc)
    assert limiter.limit_reason(NOW, st, pc, {"unanswered_proactive_turns": 0})
    r = scheduler.tick(NOW, pc, st, [], idle_clock=clock, draft_fn=lambda: None)
    assert r["action"] == "DRAFT" and r["beat"] == idle.BEAT and r["draft"]
    assert r["idle_fallback"]
    for override in ({"proactive_paused": True}, {"last_inbound_at": idle.iso(NOW)}):
        r = scheduler.tick(NOW, pc, {**st, **override}, [], idle_clock=clock, draft_fn=lambda: "在干嘛呢")
        assert r["action"] == "SKIP"
    asleep = scheduler.tick(NOW.replace(hour=23), pc, st, [], idle_clock=clock, draft_fn=lambda: "在干嘛呢")
    assert asleep["action"] == "SKIP"


def run(env, **kw):
    full, _, qpath = env
    return sender.run_once(NOW, cfg_full=full, qpath=qpath, ui=False, rows=[],
                           draft_fn=lambda: "小熊，在忙什么呀", **kw)


def confirm_sender(monkeypatch, sent):
    def send(text, **kw):
        kw["before_send"]()
        if text:
            sent.append(text)
            kw["on_confirmed"](text)
        if kw.get("image_path"):
            sent.append("[图片]")
            kw["on_confirmed"]("[图片]")
        return True, "mock:confirmed", None
    monkeypatch.setattr(sender, "send_via_onebot", send)


def test_sender_due_bypasses_next_consider_and_receipt_resets_clock(env, monkeypatch):
    full, st, qpath = env
    force_style(monkeypatch, 0)
    sender.save_q({"next_consider_at": "2026-10-30 20:00:00", "items": []}, qpath)
    sent = []
    confirm_sender(monkeypatch, sent)
    assert run(env)["action"] == "SENT" and len(sent) == 1
    result = memory.read_state("demo_contact")
    assert result["proactive_count"] == 0
    assert result["proactive_idle"]["count"] == 1
    assert result["last_idle_contact_at"] == idle.iso(NOW)
    assert run(env)["action"] == "WAIT" and len(sent) == 1
    clock = sender.load_q(qpath)["idle_contact"]
    assert clock["anchor_at"] == idle.iso(NOW) and not clock["due"]


def test_photo_only_one_and_no_opener(env, monkeypatch, tmp_path):
    force_style(monkeypatch, 2)
    path = tmp_path / "photo.png"
    path.write_bytes(b"mock")
    generator = Mock(return_value={"paths": [str(path)], "texts": ["must not send an opener"]})
    monkeypatch.setattr("chat_media.plan_chat_images", generator)
    sent = []
    confirm_sender(monkeypatch, sent)
    assert run(env)["action"] == "SENT"
    assert generator.call_args.kwargs["n_images"] == 1
    assert sent == ["[图片]"]
    assert memory.read_state("demo_contact")["last_reply_at"] == idle.iso(NOW)


def test_failed_photo_falls_back_to_text_without_claiming_sent_photo(env, monkeypatch):
    force_style(monkeypatch, 2)
    monkeypatch.setattr("chat_media.plan_chat_images", Mock(side_effect=RuntimeError("offline image failure")))
    sent = []
    confirm_sender(monkeypatch, sent)
    assert run(env)["action"] == "SENT"
    assert sent and all("WANT_IMAGE" not in x and "图片" not in x for x in sent)


def test_new_inbound_during_generation_cancels_photo(env, monkeypatch, tmp_path):
    force_style(monkeypatch, 2)
    path = tmp_path / "photo.png"
    path.write_bytes(b"mock")
    def generate(*a, **kw):
        memory.update_state("demo_contact", last_inbound_at=idle.iso(NOW))
        return {"paths": [str(path)]}
    monkeypatch.setattr("chat_media.plan_chat_images", generate)
    r = run(env)
    assert r["action"] == "DROP" and "新的实际联系" in r["reason"]
    sender.send_via_onebot.assert_not_called()


def test_unknown_delivery_never_resets_clock_or_redrafts_same_event(env, monkeypatch):
    force_style(monkeypatch, 0)
    transport = Mock(return_value=(False, "no ACK", {"delivery_status": "unknown"}))
    monkeypatch.setattr(sender, "send_via_onebot", transport)
    assert run(env)["action"] == "HOLD"
    assert memory.read_state("demo_contact")["last_reply_at"] == "2026-10-29 09:01:00"
    assert sender.load_q(env[2])["idle_contact"]["due"]
    run(env)
    assert transport.call_count == 1


def test_old_idle_retry_cannot_send_after_any_actual_contact(env):
    full, st, _ = env
    clock = idle.advance(NOW, st, [], full["proactive"])
    beat = idle.candidate(clock, st, NOW)
    item = {"contact": "demo_contact", "proactive_round": {
        "id": "retry1", "expires": "2026-10-29 15:00:00", "text": "在干嘛呀",
        "meta": {"beat_meta": beat}}}
    memory.update_state("demo_contact", last_reply_at=idle.iso(NOW))
    assert "新的实际联系" in limiter.retry_reason(item, NOW)


def test_retry_checks_new_inbound_between_confirmed_bubbles(env):
    full, st, _ = env
    from channels.outbox_send import check_proactive_sleep
    clock = idle.advance(NOW, st, [], full["proactive"])
    item = {"contact": "demo_contact", "sent_bubbles": ["小熊"], "proactive_round": {
        "id": "retry1", "meta": {"beat_meta": idle.candidate(clock, st, NOW)}}}
    memory.update_state("demo_contact", last_reply_at=idle.iso(NOW))
    check_proactive_sleep(item, NOW)  # Its own first confirmed bubble is allowed.
    memory.update_state("demo_contact", last_inbound_at=idle.iso(NOW))
    with pytest.raises(sleep.ProactiveSleepBlocked, match="新的实际联系"):
        check_proactive_sleep(item, NOW)
