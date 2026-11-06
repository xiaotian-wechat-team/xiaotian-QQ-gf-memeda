"""Offline regression: real review counterexamples, no chat/state/network I/O."""
import os
os.environ["XIAOTIAN_TEST"] = "1"
import copy
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import conversation_semantics as semantic
import life_calendar as lc
import reply_engagement as engagement
import schedule
import schedule_fidelity as sf
import sleep_chat as sleep
import chat_media
import photo_scene


INTERN = {"bucket": "work_internship", "activity": "电影公司实习", "scene_hints": ["剪辑室"]}
CAMPUS = {"bucket": "campus_life", "activity": "食堂午饭", "scene_hints": ["食堂"]}


def day(date):
    return {"date": date.isoformat(), "kind": "workday", "sleep_bounds": {
        "wake": "07:45", "prep_start": "23:10", "prep_end": "24:00"}, "slots": [
            {"start": "00:00", "end": "07:45", "bucket": "sleep", "activity": "睡觉"},
            {"start": "07:45", "end": "23:10", "bucket": "campus_life", "activity": "校园日常"},
            {"start": "23:10", "end": "24:00", "bucket": "weekend_home", "activity": "回宿舍洗漱/准备睡"}]}


class ConversationFidelity(unittest.TestCase):
    def test_sleep_mentions_are_not_signoffs_in_either_consumer(self):
        cases = ["今晚记得早点睡，昨晚熬夜太累了", "别说晚安，继续聊", "闭眼想象一下这个画面",
                 "你睡了吗？", "他说晚安就下线了", "昨天你说晚安", "你说‘晚安’是什么意思",
                 "早点睡对身体好", "晚安是什么意思", "我不睡了", "想听你说晚安", "明天先睡再说"]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(sleep.is_goodnight_text(text))
                self.assertFalse(engagement.is_closing(text))

    def test_actual_signoffs_are_shared_but_generic_bye_is_not_sleep(self):
        for text in ("晚安", "小熊晚安", "我先睡了啊", "嗯，睡吧小熊", "快睡吧", "先睡啦",
                     "我先睡了，明天继续聊", "晚安，下次接着聊"):
            self.assertTrue(sleep.is_goodnight_text(text), text)
            self.assertTrue(engagement.is_closing(text), text)
        self.assertTrue(engagement.is_closing("先聊到这，明天再聊"))
        self.assertFalse(sleep.is_goodnight_text("先聊到这，明天再聊"))

    def test_daytime_advice_does_not_set_overnight_pause(self):
        now = dt.datetime(2026, 10, 29, 12, 30)
        state = {"life_today": day(now.date())}
        with patch.object(sleep, "in_pre_sleep_window", return_value=False), \
                patch.object(sleep, "scheduled_sleep", return_value=False):
            for text in ("今晚记得早点睡，昨晚熬夜太累了", "别说晚安，继续聊", "闭眼想象一下这个画面"):
                mode, blob, style = sleep.note_inbound(text, state, now)
                self.assertEqual(mode, "normal")
                self.assertIsNone(blob.get("goodnight_at"))
                self.assertIsNone(sleep.should_suppress_proactive({**state, sleep.STATE_KEY: blob}, now))

    def test_night_reply_remains_enthusiastic_while_goodnight_blocks_proactive(self):
        cfg = {"reply": {"engagement": dict(engagement.DEFAULTS)}}
        now = dt.datetime(2026, 10, 29, 23, 40)
        state = {"life_today": day(now.date())}
        with patch.object(lc, "get_day", side_effect=day), patch.object(sleep, "scheduled_sleep", return_value=False):
            _, blob, _ = sleep.note_inbound("晚安", state, now)
            state[sleep.STATE_KEY] = blob
            for hour in (17, 23, 0, 6):
                at = now.replace(hour=hour)
                mode = engagement.select_response({"level": "normal"}, state, "demo_contact",
                                                   "别说晚安，继续聊", at, cfg)
                self.assertEqual(mode["response_mode"], "enthusiastic")
            self.assertIsNotNone(sleep.should_suppress_proactive(state, now))
            close = engagement.select_response({"level": "normal"}, state, "demo_contact", "晚安", now, cfg)
            self.assertEqual(close["response_mode"], "closing")

    def test_sleep_shortening_keeps_complete_contrasts_and_hard_limits(self):
        text = "我不是不想陪你说话，只是现在有点困了"
        for mode in ("pre_sleep", "asleep"):
            out = sleep.tighten_text(text, mode)
            self.assertEqual(out.replace("\n", ""), text)
            style = sleep.style_for(mode)
            self.assertLessEqual(len(out), style["max_chars_total"])
            self.assertLessEqual(len(out.splitlines()), style["max_bubbles"])
            self.assertTrue(all(len(line) <= style["max_bubble_chars"] for line in out.splitlines()))
        self.assertIn("才怪", sleep.tighten_text("我不想你。\n才怪，一直在等你消息。", "pre_sleep"))

    def test_overlong_explanation_is_not_cut_into_opposite_claim(self):
        for text in ("我不想理你了，因为你刚刚说的那句真的让我笑到根本说不出话",
                     "我不想你。\n才怪，你已经在我脑子里转了好久根本停不下来",
                     "如果你困了，等你把刚刚那个故事完整地讲完我就不再拉着你聊"):
            for mode in ("pre_sleep", "asleep"):
                self.assertEqual(sleep.tighten_text(text, mode), "我在呢")
        raw = "我不是不想陪你说话，只是你刚刚讲的故事我还想听下去\n你接着说，我在听呢"
        activity = {"response_mode": "enthusiastic", "reason": "evening"}
        self.assertEqual(sleep.tighten_text(raw, engagement.effective_sleep_style("asleep", activity)), raw)

    def test_other_people_future_past_negated_and_conditional_clauses_survive(self):
        cases = [({"bucket": "sleep", "activity": "睡觉"}, "你明天上课别迟到"),
                 ({"bucket": "sleep", "activity": "睡觉"}, "我明天实习结束给你打电话"),
                 (INTERN, "等我回宿舍陪你慢慢聊"),
                 ({"bucket": "school_class", "activity": "编导技法课"}, "你下班了吗"),
                 (INTERN, "昨天我下课以后去买了咖啡"),
                 (INTERN, "我不是在宿舍"),
                 (INTERN, "我还没下课"),
                 (INTERN, "如果我回宿舍就给你打电话"),
                 (INTERN, "室友刚下课"),
                 (INTERN, "小熊刚下课"),
                 (INTERN, "她说我刚下课"),
                 (INTERN, "她说‘我刚下课，回宿舍了’"),
                 (INTERN, "你说‘我刚下课’，是在逗我吗")]
        for slot, text in cases:
            with self.subTest(text=text):
                self.assertFalse(sf.text_violates_slot(text, slot))
                self.assertEqual(sf.enforce_outbound_text(text, slot), text)

    def test_only_incompatible_clause_is_repaired(self):
        text = "我刚下课，你实验没成功也别急，我想陪你慢慢说"
        result = sf.enforce_outbound_text(text, INTERN)
        self.assertNotIn("下课", result)
        self.assertIn("你实验没成功也别急，我想陪你慢慢说", result)
        self.assertIn("实习", result)
        self.assertEqual(sf.enforce_outbound_text("实习摸鱼两秒 屏幕好刺眼", INTERN), "实习摸鱼两秒 屏幕好刺眼")

    def test_university_ownership_precedes_generic_campus_hint(self):
        bad = "北京大学校园里 晴天 学生气OOTD全身随手拍"
        self.assertFalse(sf.scene_matches_slot(bad, CAMPUS))
        result = sf.force_scene_for_slot(CAMPUS, bad)
        self.assertIn("北京舞蹈学院", result)
        self.assertNotIn("北京大学", result)
        self.assertIn("OOTD全身", result)
        self.assertTrue(sf.scene_matches_slot(result, CAMPUS))
        self.assertFalse(sf.scene_matches_slot("厦门大学校园里的自拍", CAMPUS))

    def test_explicit_imagination_is_allowed_without_mutating_reality(self):
        before = copy.deepcopy(CAMPUS)
        scene = "北京大学校园里的想象照"
        self.assertTrue(sf.scene_matches_slot(scene, CAMPUS, imagined=True))
        self.assertEqual(sf.force_scene_for_slot(CAMPUS, scene, imagined=True), scene)
        self.assertEqual(CAMPUS, before)
        visit = {**CAMPUS, "location": "北京大学", "activity": "参观校园"}
        self.assertTrue(sf.scene_matches_slot("北京大学校园里随手拍", visit))

    def test_only_explicit_unnegated_unquoted_fiction_authorizes_bypass(self):
        for text in ("想象你在北京大学校园里，画一张", "假如你在月球上，给我看张照片",
                     "生成一张幻想中的海底图", "给我看虚构场景的照片"):
            self.assertTrue(semantic.is_imagined_request(text), text)
            self.assertTrue(photo_scene.user_asks_photo(text), text)
        for text in ("生成一张今天的照片", "画一张你今天的照片", "不要幻想，给我看今天真实照片",
                     "不是幻想，我要真实的照片", "他刚说‘假装在月球’，给我看今天照片",
                     "我昨晚幻想退休，发张你现在的照片", "什么是幻想图",
                     "假如我早点下班就好了，看看你今天照片", "你喜欢幻想吗，发张你今天照片"):
            self.assertFalse(semantic.is_imagined_request(text), text)

    def test_fiction_survives_early_scene_enrichment_and_missing_model_scene(self):
        request = "想象你在宿舍被窝里看雪，画一张"
        with patch.object(lc, "photo_context", side_effect=AssertionError("fiction must not read actual slot")):
            self.assertEqual(chat_media.enrich_scene("宿舍被窝里看雪", chat_tail=request,
                                                    imagined=True, force_schedule=True), "宿舍被窝里看雪")
            self.assertEqual(chat_media.enrich_scene(None, chat_tail=request, imagined=True), request)

    def test_fiction_plan_does_not_inject_real_props_or_claim_casual_photo(self):
        with patch.object(chat_media, "generate_paths", return_value=[pathlib.Path("/fixture/imagined.png")]) as generate:
            result = chat_media.plan_chat_images("想象月球", caption="月球上看地球", imagined=True,
                n_images=1, slot=INTERN, today={"date": "2026-10-29"},
                shot_type="portrait", shot_bits={"props": "公司剪辑机"})
        self.assertTrue(result["imagined"])
        self.assertIsNone(result["slot"])
        self.assertTrue(result["texts"][0].startswith("想象场景"))
        kwargs = generate.call_args.kwargs
        self.assertNotIn("shot_bits", kwargs)
        self.assertNotIn("slot", kwargs)
        self.assertNotIn("today", kwargs)
        self.assertEqual(kwargs["activity"], "")
        self.assertFalse(kwargs["persist_happy_slot"])

    def test_brain_integrates_early_fiction_and_real_school_guard(self):
        import brain, chat_search, llm, memory, weather
        from contextlib import ExitStack
        now = dt.datetime(2026, 10, 29, 20)
        state = {"life_today": {"date": now.date().isoformat(), "slots": [
            {**CAMPUS, "start": "00:00", "end": "24:00"}]}}
        with ExitStack() as stack:
            for obj, name, value in [
                (brain, "CFG", {**brain.CFG, "llm": {"backend": "deepseek"}, "life_calendar": {"sync_on_reply": False}}),
                (memory, "read_state", lambda *a: copy.deepcopy(state)),
                (memory, "persona", lambda: {"user": "", "soul": "", "memory": ""}),
                (memory, "contact_ctx", lambda *a: {"profile": "", "memory": ""}),
                (brain, "memory_block", lambda *a: ""), (brain, "state_block", lambda *a, **k: ""),
                (brain, "_nick", lambda *a: None), (weather, "needs", lambda *a: False),
                (llm, "pick_backend", lambda *a, **k: "deepseek"),
                (photo_scene, "route_shot_request", lambda *a: None),
                (sf, "resolve_slot", lambda *a, **k: CAMPUS),
                (lc, "photo_context", lambda *a, **k: {"bucket": "campus_life", "activity": "食堂午饭", "hints": ["食堂"]}),
            ]:
                stack.enter_context(patch.object(obj, name, value))
            stack.enter_context(patch.object(chat_search, "search", side_effect=AssertionError("no live search")))
            stack.enter_context(patch.object(llm, "complete", side_effect=AssertionError("no live model")))
            cases = [
                ("想象你在宿舍被窝里看雪，画一张", "画好了\nWANT_IMAGE:宿舍被窝里看雪", "宿舍被窝里", True),
                ("想象你在北京大学校园里，画一张", "好呀", "想象你在北京大学", True),
                ("生成一张今天的照片", "给你看\nWANT_IMAGE:北京大学校园里OOTD", "北京舞蹈学院", False),
                ("不要想象，给我看今天照片", "给你看\nWANT_IMAGE:北京大学校园里OOTD", "北京舞蹈学院", False),
            ]
            for request, raw, expected, imagined in cases:
                with self.subTest(request=request), patch.dict(brain.BACKENDS, {"deepseek": lambda *a: raw}):
                    brain.reply("demo_contact", [("them", request)], dry=True, now=now)
                    self.assertEqual(brain.LAST["imagined_scene"], imagined)
                    self.assertIn(expected, brain.LAST["want_image"])
                    if imagined:
                        self.assertIsNone(brain.LAST.get("shot_bits"))


class ImaginedLighting(unittest.TestCase):
    def test_realism_uses_depicted_time_and_removes_conflicting_modifiers(self):
        import imagegen, outfit, realism
        at = dt.datetime(2026, 10, 29, 2)
        with patch.object(outfit, "plan", side_effect=lambda *a, **k: {"text": "日常穿搭", "vector": {}}), \
                patch.dict(os.environ, {"REALISM": "1", "REALISM_SEED": "12"}):
            for scene, expected in (("想象白天在海边晒太阳", False), ("想象深夜海边看星空", True)):
                plan = imagegen.outfit_plan(scene, now=at, imagined=True)
                self.assertEqual(plan["realism"]["night"], expected)
                self.assertEqual(imagegen._scene_night(scene, plan, at, imagined=True), expected)
                self.assertTrue(all(realism.BY_ID[i].get("time", "any") in
                    ("any", "night" if expected else "day") for i in plan["realism"]["ids"]))
            real = imagegen.outfit_plan("今天在海边", now=at)
            self.assertTrue(real["realism"]["night"])
            self.assertTrue(imagegen._scene_night("晴天海边", real, at))

    def test_local_and_macbook_pending_do_not_force_fictional_day_to_night(self):
        import imagegen, codex_session
        from contextlib import ExitStack
        at = dt.datetime(2026, 10, 29, 2)
        scene = "想象白天在海边晒太阳"
        # Keep an intentionally stale night flag: explicit fiction must win.
        plan = {"text": "日常穿搭", "realism": {"night": True}, "imagined": True}
        class PromptCaptured(Exception):
            pass
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            cfg = {"imagegen": {"out_dir": directory, "ssh_host": ""}}
            for obj, name, value in ((imagegen, "pick_refs", lambda *a, **k: ([], [])),
                    (imagegen, "pick_scene_refs", lambda *a, **k: []),
                    (imagegen, "appearance", lambda *a: ("", [])),
                    (imagegen, "rollout_sizes", lambda: {}),
                    (imagegen, "build_cmd", lambda *a, **k: ["never-run"]),
                    (codex_session, "current", lambda *a: None)):
                stack.enter_context(patch.object(obj, name, value))
            stack.enter_context(patch.object(imagegen.subprocess, "run", side_effect=AssertionError("no process")))
            with patch.object(imagegen, "build_prompt", side_effect=PromptCaptured) as prompt:
                with self.assertRaises(PromptCaptured):
                    imagegen.generate_local(scene, cfg=cfg, _plan=plan, imagined=True, now=at)
                self.assertFalse(prompt.call_args.kwargs["night"])
                with self.assertRaises(PromptCaptured):
                    imagegen.generate_local(scene, cfg=cfg, _plan={**plan, "imagined": False}, now=at)
                self.assertTrue(prompt.call_args.kwargs["night"])
            with patch.object(imagegen, "_is_night_hour", return_value=True), \
                    patch.object(imagegen, "outfit_plan", return_value=copy.deepcopy(plan)), \
                    patch.object(imagegen, "_relay_note", return_value=""):
                with self.assertRaisesRegex(RuntimeError, "ssh_host"):
                    imagegen.generate_macbook(scene, cfg=cfg, imagined=True, now=at)
                job = json.loads(next((pathlib.Path(directory) / "pending").glob("*.json")).read_text())
                self.assertNotIn("光照硬约束", job["prompt"])
                self.assertIn(scene, job["prompt"])


class MidnightCarryover(unittest.TestCase):
    def setUp(self):
        self.date = dt.date(2026, 12, 4)
        self.today, self.previous = day(self.date), day(self.date - dt.timedelta(days=1))
        self.previous["sleep_bounds"]["sleep_min"] = 26 * 60 + 27

    def test_carryover_is_continuous_and_daytime_is_untouched(self):
        before = copy.deepcopy(self.today)
        result = lc.repair_midnight_carryover(self.today, self.previous)
        self.assertEqual(self.today, before)
        self.assertEqual(result["slots"][0]["start"], "00:00")
        self.assertEqual(result["slots"][2]["start"], "02:27")
        self.assertEqual(result["slots"][2]["bucket"], "sleep")
        self.assertEqual(result["slots"][3:], before["slots"][1:])
        for left, right in zip(result["slots"], result["slots"][1:]):
            self.assertEqual(left["end"], right["start"])
        self.assertEqual(lc.repair_midnight_carryover(result, self.previous), result)

    def test_frozen_overrides_trips_and_execution_are_preserved(self):
        self.assertEqual(lc.repair_midnight_carryover(self.today, self.previous, frozen=True), self.today)
        for target, key, value in (("day", "source", "user_override"),
                                   ("day", "kind", "travel"), ("slot", "trip_id", "trip1"),
                                   ("slot", "locked", True), ("day", "locked", True),
                                   ("slot", "execution_status", "confirmed")):
            changed = copy.deepcopy(self.today)
            (changed if target == "day" else changed["slots"][0])[key] = value
            self.assertEqual(lc.repair_midnight_carryover(changed, self.previous), changed)

    def test_legacy_seed_read_projection_and_cached_state_agree(self):
        previous = copy.deepcopy(self.previous)
        previous["sleep_bounds"].pop("sleep_min")
        doc = {"days": [previous, self.today]}
        at = dt.datetime.combine(self.date, dt.time(0, 15))
        with patch.object(lc, "_load_draft", return_value=doc), \
                patch.object(lc, "today_local", return_value=self.date), \
                patch.object(lc, "_bind_day_scene_probes"), \
                patch.object(schedule, "day_plan", return_value={"sleep": 1587}):
            self.assertNotEqual(lc.slot_at(at)["bucket"], "sleep")
            st = {"life_today": copy.deepcopy(self.today), "life_now": {
                **self.today["slots"][0], "date": self.date.isoformat()}}
            self.assertNotEqual(lc.resolve_life_now(st, at)["bucket"], "sleep")
            self.assertEqual(lc.slot_at(at.replace(hour=3))["bucket"], "sleep")
            self.assertEqual(doc["days"][1], self.today)
            doc["frozen"] = {"active": True, "from": self.date.isoformat(), "to": self.date.isoformat()}
            snapshot = copy.deepcopy(doc)
            projected = lc.get_day(self.date)
            self.assertNotEqual(lc.slot_at(at)["bucket"], "sleep")
            self.assertEqual(projected["slots"][3:], self.today["slots"][1:])
            self.assertTrue(projected["sleep_rollover"]["frozen_compatibility"])
            self.assertEqual(projected["sleep_rollover"]["reason"], "legacy_midnight_sleep_clipping")
            self.assertEqual(doc, snapshot)
            # Explicit slot locks are stronger than the release compatibility exception.
            doc["days"][1]["slots"][0]["locked"] = True
            self.assertEqual(lc.slot_at(at)["bucket"], "sleep")

    def test_historical_draft_is_not_reinterpreted(self):
        doc = {"days": [self.previous, self.today]}
        with patch.object(lc, "today_local", return_value=self.date + dt.timedelta(days=1)):
            self.assertEqual(lc._project_sleep_carryover(self.today, doc, self.date), self.today)


if __name__ == "__main__":
    unittest.main()
