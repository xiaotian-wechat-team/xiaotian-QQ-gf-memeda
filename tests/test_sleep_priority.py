"""Sleep beats random sharing at selection, send and retry. No real messages/state."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
os.environ.setdefault("RELAY_ENV_FILE", "/nonexistent/xiaotian-test-relay.env")
import contextlib
import copy
import datetime as dt
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import config
import memory
import sleep_chat as sc
import proactive_scheduler as ps
import proactive_sender as sender
import proactive_limiter as limiter
from channels import ilink_weixin as iw, onebot_qq as qq, outbox_send as osnd

D = dt.datetime


def day(date):
    wake = "07:37" if date.day == 27 else "07:26"
    return {"date": date.isoformat(), "sleep_bounds": {"wake": wake}, "slots": [
        {"start": "00:00", "end": wake, "activity": "睡觉", "bucket": "sleep"},
        {"start": wake, "end": "22:47", "activity": "日常", "bucket": "campus_life"},
        {"start": "22:47", "end": "24:00", "activity": "回宿舍洗漱/准备睡", "bucket": "weekend_home"},
    ]}


class SleepPriority(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.state = {"life_today": day(dt.date(2026, 10, 27))}
        self.now = D(2026, 10, 27, 20)
        self.outbox_serial = 0
        self.full = copy.deepcopy(config.load())
        self.full["proactive"].update(enabled=True, transport="onebot", primary_channel="qq_onebot")
        # Explicit fixture aliases for contact demo_contact, without bypassing runtime scope checks.
        self.full['persona']['contact_aliases'].update({'qq_onebot:demo_contact': 'demo_contact', 'wechat_ilink:demo_contact': 'demo_contact'})
        self.full['persona']['channels']['qq']['allow_user_ids'] = ['demo_contact']
        self.stack.enter_context(patch('channels.persona_map.load', return_value=self.full))
        # Sleep cancellation tests must not start real image generation.
        self.full['proactive']['image_share_min_ratio'] = 0
        for target, kw in [
            ("life_calendar.get_day", {"side_effect": day}),
            ("schedule.is_sleeping", {"side_effect": lambda now: now.strftime("%H:%M") < day(now.date())["sleep_bounds"]["wake"]}),
            ("sleep_chat.cfg", {"return_value": copy.deepcopy(sc.DEFAULTS)}),
            ("memory.read_state", {"side_effect": lambda cid: copy.deepcopy(self.state)}),
            ("memory.ensure_defaults", {"return_value": None}),
            ("memory.update_state", {"side_effect": lambda cid, **kw: self.state.update(kw)}),
            ("memory.mutate_state", {"side_effect": self.mutate}),
            ("channels.outbox_send.persist_outbound_bubble", {"return_value": True}),
            ("schedule_fidelity.outbox_item_stale", {"return_value": (False, "")}),
            ("schedule_fidelity.mark_asked_his_day_on_flush_success", {"return_value": None}),
            ("time.sleep", {"return_value": None}),
            ("imagegen.generate", {"side_effect": RuntimeError("offline test: image generation forbidden")}),
            ("proactive_sender._now", {"side_effect": lambda: self.now}),
            ("proactive_sender._onebot_peer_and_cfg", {"return_value": ("demo_contact", {})}),
            ("proactive_sender._ilink_peer_and_ctx", {"return_value": ("demo_contact", "unused")}),
            ("channels.ilink_weixin._creds_path", {"return_value": pathlib.Path(__file__)}),
        ]:
            self.stack.enter_context(patch(target, **kw))
        self.stack.enter_context(patch("channels.outbox_store.DB_PATH", self.tmp / "outbox.db"))

    def mutate(self, cid, change):
        self.state.update(change(copy.deepcopy(self.state)))
        return copy.deepcopy(self.state)

    def goodnight(self, now=None):
        _, b, _ = sc.note_inbound("晚安", self.state, now or self.now)
        self.state[sc.STATE_KEY] = b

    def gate(self, beat="surf_share"):
        return lambda: sc.check_proactive_send(memory.read_state("demo_contact"), self.now, {"id": beat})

    def test_reported_2247_goodnight_cannot_wake_at_2302(self):
        self.goodnight(D(2026, 10, 27, 22, 47, 33))
        _, b = sc.note_outbound("嗯\n睡吧小熊", self.state, D(2026, 10, 27, 22, 47, 42))
        self.state[sc.STATE_KEY] = b
        for now in [D(2026, 10, 27, 22, 47, 43), D(2026, 10, 27, 23, 2), D(2026, 10, 28, 0, 1)]:
            self.assertIsNotNone(sc.should_suppress_proactive(self.state, now))
        mode, b = sc.refresh(self.state, D(2026, 10, 27, 23, 2))
        self.assertEqual(mode, "asleep")
        self.assertNotEqual(b.get("wake_reason"), "schedule_morning")
        self.assertEqual(b["proactive_sleep_until"], "2026-10-28 07:26:00")
        self.assertIsNotNone(sc.should_suppress_proactive(self.state, D(2026, 10, 28, 7, 25, 59)))
        self.assertIsNone(sc.should_suppress_proactive(self.state, D(2026, 10, 28, 7, 26)))

    def test_legacy_pending_and_asleep_migrate_without_evening_wake(self):
        for b in [{"mode": "pre_sleep", "pending_goodnight": {"from": "me", "at": "2026-10-27 22:47:42"}},
                  {"mode": "asleep", "asleep_since": "2026-10-27 22:57:42"}]:
            self.state[sc.STATE_KEY] = b
            mode, fresh = sc.refresh(self.state, D(2026, 10, 27, 23, 2))
            self.assertEqual(mode, "asleep")
            self.assertEqual(fresh["proactive_sleep_until"], "2026-10-28 07:26:00")

    def test_after_midnight_and_timezone_use_same_morning(self):
        self.goodnight(D(2026, 10, 28, 1, 15))
        _, b = sc.refresh(self.state, D(2026, 10, 27, 17, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(b["proactive_sleep_until"], "2026-10-28 07:26:00")
        self.assertIsNone(sc.should_suppress_proactive(self.state, D(2026, 10, 28, 8)))

    def test_next_wake_uses_weekend_schedule_not_fixed_eight(self):
        tomorrow = day(dt.date(2026, 10, 28))
        tomorrow["sleep_bounds"]["wake"] = "10:12"
        tomorrow["slots"][0]["end"] = "10:12"
        tomorrow["slots"][1]["start"] = "10:12"
        with patch("life_calendar.get_day", return_value=tomorrow):
            self.goodnight(D(2026, 10, 27, 22, 47))
            self.assertIsNotNone(sc.should_suppress_proactive(self.state, D(2026, 10, 28, 9)))
            self.assertIsNone(sc.should_suppress_proactive(self.state, D(2026, 10, 28, 10, 12)))

    def test_night_ping_can_get_reply_without_resuming_shares(self):
        self.goodnight(D(2026, 10, 27, 22, 47))
        mode, b, style = sc.note_inbound("还在吗", self.state, D(2026, 10, 27, 23, 2))
        self.assertEqual(style, "asleep")
        self.assertIn(mode, ("normal", "pre_sleep"))
        self.state[sc.STATE_KEY] = b
        self.assertIsNotNone(sc.should_suppress_proactive(self.state, D(2026, 10, 27, 23, 3)))

    def test_ack_does_not_cancel_goodnight_pause(self):
        _, b = sc.note_outbound("晚安小熊", self.state, D(2026, 10, 27, 22, 40))
        _, b, _ = sc.note_inbound("嗯收到", {**self.state, sc.STATE_KEY: b}, D(2026, 10, 27, 22, 41))
        self.state[sc.STATE_KEY] = b
        self.assertIsNotNone(sc.should_suppress_proactive(self.state, D(2026, 10, 27, 22, 42)))

    def test_prepare_slot_only_allows_closing_goodnight(self):
        now = D(2026, 10, 27, 23, 2)
        ctx = {"pending_from_him": False, "unanswered_proactive_turns": 0}
        beat = {"id": "surf_share", "must": True, "bypass_quiet": True,
                "slot": {"activity": "刷手机", "bucket": "surf"}}
        self.assertIn("睡前准备", ps.gates(now, self.state, {}, ctx, beat))
        self.assertIsNone(sc.should_suppress_proactive(self.state, now, {"id": "pre_sleep"}))
        self.assertIn("睡觉", ps.gates(D(2026, 10, 28, 1), self.state, {}, ctx, beat))

    def test_sleep_slot_wins_even_if_style_feature_disabled(self):
        with patch.object(sc, "cfg", return_value={"enabled": False}):
            self.assertIn("睡觉", sc.should_suppress_proactive(self.state, D(2026, 10, 28, 1)))

    def test_force_cannot_override_goodnight(self):
        self.goodnight()
        send = self.stack.enter_context(patch.object(sender, "send_via_onebot"))
        result = sender.run_once(self.now, force=True, st=self.state, cfg_full=self.full,
                                 rows=[], ui=False, qpath=self.tmp / "queue.json")
        self.assertEqual(result["action"], "SKIP")
        send.assert_not_called()

    def test_generation_crossing_bedtime_drops_before_transport(self):
        q = {"items": []}
        sender.enqueue(q, "看看这个\nWANT_IMAGE: 操场", "beat:daily_chat", self.now,
                       meta={"beat_meta": {"id": "daily_chat"}})
        path = self.tmp / "queue.json"
        sender.save_q(q, path)
        def generate(*a, **kw):
            self.now = D(2026, 10, 27, 22, 48)
            return {"paths": [str(self.tmp / "photo.png")], "texts": ["看看这个"]}
        with patch("chat_media.plan_chat_images", side_effect=generate), patch.object(sender, "send_via_onebot") as send:
            result = sender.run_once(cfg_full=self.full, rows=[], ui=False, qpath=path)
        self.assertEqual(result["action"], "DROP", result)
        self.assertIn("睡前准备", result["reason"])
        self.assertEqual(sender.load_q(path)["items"][0]["status"], "dropped")
        send.assert_not_called()

    def test_proactive_goodnight_receipt_sets_pause(self):
        limiter.record_success("demo_contact", {"id": "night-round", "text": "晚安小熊", "meta": {}}, self.now)
        self.assertIsNotNone(sc.should_suppress_proactive(self.state, self.now + dt.timedelta(seconds=1)))

    def _transport(self, channel, stop_after):
        sent = []
        def deliver(kind, value):
            sent.append((kind, value))
            if len(sent) == stop_after:
                self.goodnight()
            return {"ret": 0, "message_id": len(sent), "retcode": 0, "status":"ok", "data":{"message_id":len(sent)}}
        if channel == "onebot":
            self.stack.enter_context(patch.object(qq, "send_private_text_http", side_effect=lambda a, p, t, **k: deliver("text", t)))
            self.stack.enter_context(patch.object(qq, "send_private_image_http", side_effect=lambda a, p, t, **k: deliver("image", t)))
            send = sender.send_via_onebot
        else:
            import weixin_ilink.api as api
            class Bot:
                def __init__(self, **kw):
                    self.client = types.SimpleNamespace(opts=None)
                def send_text(self, p, text, **kw):
                    api.send_message(None, {"text": text})
            self.stack.enter_context(patch("weixin_ilink.WeixinBot", Bot))
            self.stack.enter_context(patch.object(api, "send_message", side_effect=lambda o, m: deliver("text", m["text"])))
            self.stack.enter_context(patch.object(iw.ILinkWeixinRunner, "_send_image_checked", side_effect=lambda *a, **kw: deliver("image", "photo")))
            send = sender.send_via_ilink
        self.stack.enter_context(patch("channels.share_send.split_caption_bubbles", return_value=["第一句", "第二句"]))
        result = send("第一句\n第二句", image_path="photo", link_url="https://example.com/item", before_send=self.gate())
        return result, sent

    def test_immediate_transports_stop_between_caption_link_image(self):
        for channel in ("onebot", "ilink"):
            for stop_after in (1, 2, 3):
                with self.subTest(channel=channel, stop_after=stop_after):
                    self.state.pop(sc.STATE_KEY, None)
                    result, sent = self._transport(channel, stop_after)
                    self.assertFalse(result[0], result)
                    self.assertTrue(result[2]["sleep_suppressed"])
                    self.assertEqual(len(sent), stop_after)
                    self.assertNotIn("image", [k for k, v in sent])

    def test_awake_transport_sends_full_share(self):
        result, sent = self._transport("onebot", 99)
        self.assertTrue(result[0], result)
        self.assertEqual([k for k, v in sent], ["text", "text", "text", "image"])

    def test_ilink_upload_crossing_goodnight_cannot_send_image(self):
        import weixin_ilink.api as api
        outer = self
        class Bot:
            def __init__(self, **kw):
                self.client = types.SimpleNamespace(opts=None)
            def send_image(self, *a, **kw):
                # Simulate a goodnight arriving while the SDK uploads the file.
                outer.goodnight()
                api.send_message(None, {"item_list": [{"type": 2}]})
        with patch("weixin_ilink.WeixinBot", Bot), patch.object(api, "send_message") as wire:
            result = sender.send_via_ilink("", image_path="photo", before_send=self.gate())
        self.assertFalse(result[0])
        self.assertTrue(result[2]["sleep_suppressed"])
        wire.assert_not_called()

    def test_cancelled_send_is_dropped_without_creating_outbox(self):
        q = {"items": []}
        sender.enqueue(q, "第一句\n第二句", "daily_chat", self.now)
        path = self.tmp / "queue.json"
        sender.save_q(q, path)
        with patch.object(sender, "send_via_onebot", return_value=(False, "已道晚安", {"sleep_suppressed": True})), \
             patch.object(qq, "_save_outbox") as save_outbox:
            result = sender.run_once(self.now, cfg_full=self.full, rows=[], ui=False, qpath=path)
        self.assertEqual(result["action"], "DROP", result)
        self.assertEqual(sender.load_q(path)["items"][0]["status"], "dropped")
        save_outbox.assert_not_called()

    def _outbox(self, channel, *, image=False, reply=False, sleep_after=None):
        path = self.tmp / "photo.png"
        path.write_bytes(b"offline fixture")
        self.outbox_serial += 1
        it = {"id": f"retry-{self.outbox_serial}", "peer": "demo_contact", "contact": "demo_contact", "status": "pending",
              "text": "第一句\n第二句", "created": self.now.isoformat(sep=" ")}
        if not reply:
            # Mirror/legacy retry has no proactive_round; it must still be guarded.
            it.update(reason="proactive ilink: network unavailable", beat_id="surf_share")
        if image:
            it.update(image_path=str(path), link_url="https://example.com/item")
        mod = qq if channel == "onebot" else iw
        mod._save_outbox({"items": [it]})
        sent = []
        def deliver(kind, value):
            sent.append((kind, value))
            if len(sent) == sleep_after:
                self.goodnight()
            return {"ret": 0, "message_id": len(sent)}
        with patch.object(osnd, "split_outbound_bubbles", side_effect=lambda s: s.splitlines()):
            if channel == "onebot":
                result = qq.flush_qq_outbox(lambda p, t: deliver("text", t),
                                           lambda p, ph, **k: deliver("image", ph),
                                           peer_id="demo_contact", now=self.now)
            else:
                bot = types.SimpleNamespace(client=types.SimpleNamespace(opts=None, _generate_client_id=lambda: "id"))
                with patch("weixin_ilink.api.send_message", side_effect=lambda o, m: deliver("text", m["item_list"][0]["text_item"]["text"])), \
                     patch.object(iw.ILinkWeixinRunner, "_send_text_checked", side_effect=lambda p, t, c: deliver("text", t)), \
                     patch.object(iw.ILinkWeixinRunner, "_send_image_checked", side_effect=lambda p, ph, c, **k: deliver("image", ph)):
                    result = iw.flush_ilink_outbox(bot, "demo_contact", "unused", now=self.now)
        saved = next(x for x in mod._load_outbox()["items"] if x["id"] == it["id"])
        return result, sent, saved

    def test_both_outboxes_drop_sleeping_proactive_not_user_reply(self):
        for channel in ("onebot", "ilink"):
            self.goodnight()
            result, sent, item = self._outbox(channel, image=True)
            self.assertEqual(item["status"], "dropped", result)
            self.assertEqual(sent, [])
            result, sent, item = self._outbox(channel, reply=True)
            self.assertEqual(item["status"], "sent", result)
            self.assertEqual(len(sent), 2)

    def test_both_outboxes_recheck_during_send(self):
        for channel in ("onebot", "ilink"):
            for image, count in [(False, 1), (True, 1), (True, 2), (True, 3)]:
                with self.subTest(channel=channel, image=image, count=count):
                    self.state.pop(sc.STATE_KEY, None)
                    result, sent, item = self._outbox(channel, image=image, sleep_after=count)
                    self.assertEqual(item["status"], "dropped", result)
                    self.assertEqual(len(sent), count)

    def test_state_read_failure_does_not_send(self):
        with patch.object(memory, "read_state", side_effect=ValueError("broken state")):
            with self.assertRaises(sc.ProactiveSleepBlocked):
                osnd.check_proactive_sleep({"reason": "proactive onebot: retry"}, self.now)


if __name__ == "__main__":
    unittest.main()
