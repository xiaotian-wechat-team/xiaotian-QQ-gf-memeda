"""Offline persona/feedback regressions; no LLM, network or live state access."""
import copy
import datetime as dt
import json
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import emotion
import lang_pack
import nickname
import persona_context


class PersonaReliability(unittest.TestCase):
    def test_identity_is_always_present_and_subjects_are_separate(self):
        text = persona_context.prompt_block("你好", now=dt.date(2026, 10, 29))
        for fact in ("北京舞蹈学院", "2024级/大三", "小熊", "话剧社", "英语", "法语", "粤语", "不得移给对方"):
            self.assertIn(fact, text)
        self.assertNotIn("173", text)
        self.assertNotIn("canonical", text)
        self.assertLessEqual(len(text), 650)
        self.assertTrue(text.endswith("\n"))

    def test_topics_use_dossier_even_without_memory_retrieval(self):
        for question, expected in (
            ("你爸妈做什么", "父母做小生意"),
            ("你室友叫什么", "林晓晚"),
            ("你不爱吃什么", "苦瓜"),
            ("你喜欢喝啥", "冰美式"),
            ("我们的恋爱纪念日", "2025-02-14"),
            ("你在哪里读书", "万寿寺路1号"),
        ):
            self.assertIn(expected, persona_context.prompt_block(question))

    def test_unknown_and_age_do_not_freeze_or_invent(self):
        dossier = json.loads(persona_context.DOSSIER.read_text())
        dossier["family"] = {"parents": None}
        text = persona_context.prompt_block("你爸妈做什么", now=dt.date(2027, 5, 15), dossier=dossier)
        self.assertIn('"年龄":21', text)
        self.assertIn('"parents":null', text)
        self.assertIn("未记载即未知", text)
        self.assertNotIn("父母做小生意", text)

    def test_feedback_gets_care_even_after_daily_warm_cap(self):
        now = dt.datetime(2026, 10, 29, 12)
        for text in ("哦\n你好凶", "你怎么这么凶", "你刚才好凶", "你太敷衍我了", "感觉被敷衍了"):
            with self.subTest(text=text):
                state = emotion.default()
                state.update(daily_sweet_date="2026-10-29", daily_sweet_count=99)
                self.assertEqual(nickname.emotion(text)[0], "sad")
                self.assertEqual(emotion.sweet_mode(state, text, now=now)["mode"], "care")
                self.assertIn("先具体接住", nickname.feedback_prompt_tip(text))
                _, vector, _ = nickname.pick(text, {"mood": 9}, random.Random(1), now=now)
                self.assertLessEqual(vector["tease"], .25)

    def test_reported_or_negated_feedback_does_not_create_complaint(self):
        for text in ("你不凶", "你觉得我好凶", "不是说你好凶", "朋友说你好凶", '他发来“你好凶”', "导师被敷衍了"):
            self.assertFalse(nickname.relationship_feedback(text), text)
            self.assertEqual(nickname.feedback_prompt_tip(text), "", text)

    def test_topic_words_and_other_peoples_emotions_are_neutral(self):
        now = dt.datetime(2026, 10, 29, 12)
        for text in ("导师生气了", "我导师今天很生气", "我在做病毒实验", "我要去吃菜", "我去切菜", "买了猪肉"):
            with self.subTest(text=text):
                state = emotion.default()
                state["updated"] = now.isoformat()
                actual, label = emotion.appraise(copy.deepcopy(state), text, now=now)
                self.assertEqual(label, "neutral")
                self.assertEqual(actual["intimacy"], state["intimacy"])

    def test_personal_distress_still_gets_care_without_relationship_penalty(self):
        now = dt.datetime(2026, 10, 29, 12)
        state = emotion.default()
        state["updated"] = now.isoformat()
        result, label = emotion.appraise(copy.deepcopy(state), "我被导师气死了", now=now)
        self.assertEqual(label, "angry")
        self.assertEqual(result["intimacy"], state["intimacy"])
        self.assertEqual(nickname.emotion("我生病了")[0], "sad")
        self.assertEqual(nickname.emotion("你真菜")[0], "tease")
        result, _ = emotion.appraise(copy.deepcopy(state), "你把我气死了", now=now)
        self.assertLess(result["intimacy"], state["intimacy"])

    def test_mandarin_words_do_not_switch_to_cantonese(self):
        for text in ("好啦，我去实验室了", "吃了点心", "吓死我了", "我想吃点心"):
            self.assertIsNone(lang_pack.detect(text), text)
        self.assertEqual(lang_pack.detect("你食咗飯未呀"), "yue")
        self.assertEqual(lang_pack.detect("我唔知呀"), "yue")
        self.assertEqual(lang_pack.detect("bonjour, comment ça va?"), "fr")
        self.assertEqual(lang_pack.detect("how are you tonight?"), "en")


if __name__ == "__main__":
    unittest.main()
