"""Offline evidence/date regressions; only temporary DB/cache files are written."""
import contextlib
import datetime as dt
import io
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import campus
import llm
import mem
import reflect


class MemoryProvenance(unittest.TestCase):
    def test_assistant_guesses_cannot_be_extraction_sources(self):
        chat = ["小甜: 你在电影公司实习吧", "他: 我在北京大学读博", "小甜: 他: 我是本科生"]
        candidate = [
            {"fact": "小熊在电影公司实习", "source_index": 0, "evidence": "你在电影公司实习吧"},
            {"fact": "小熊是本科生", "source_index": 2, "evidence": "我是本科生"},
            {"fact": "小熊在北京大学读博", "source_index": 1, "evidence": "我在北京大学读博", "importance": 8},
            {"fact": "小熊做助理", "source_index": 1, "evidence": "我做助理"},
            {"fact": "没有证据的旧格式"},
        ]
        with patch.object(llm, "complete_json", return_value=candidate) as model:
            facts = mem.extract_facts(chat, today=dt.date(2026, 10, 28))
        self.assertEqual([f["fact"] for f in facts], ["小熊在北京大学读博"])
        prompt = model.call_args.args[0]
        self.assertNotIn("电影公司实习吧", prompt)
        self.assertNotIn("我是本科生", prompt)
        self.assertIn("2026-10-28 星期三", prompt)
        self.assertIn("玩笑、假设都不要", prompt)

    def test_no_user_evidence_means_no_model_call(self):
        with patch.object(llm, "complete_json", side_effect=AssertionError("must not call")):
            self.assertEqual(mem.extract_facts(["小甜: 你今天要去实习", "未知: 好"]), [])
            self.assertEqual(mem.extract_facts(None), [])

    def test_structured_sender_ownership_and_multiline(self):
        chat = [{"sender": "me", "text": "他: 我在实习"},
                {"sender": "them", "text": "我在读博\n明天做实验"}]
        prompt = mem.extract_prompt(chat, today=dt.date(2026, 10, 28))
        self.assertNotIn("我在实习", prompt)
        self.assertIn("明天做实验", prompt)

    def test_json_extraction_never_reuses_live_chat_session(self):
        with patch.object(llm, "complete", return_value="[]") as complete:
            self.assertEqual(llm.complete_json("historical facts"), [])
        self.assertIs(complete.call_args.kwargs["reuse_session"], False)

    def test_reflect_anchors_target_day_and_opens_database_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "chat.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, contact TEXT, sender TEXT, text TEXT, ts TEXT)")
                db.execute("INSERT INTO messages VALUES(1,'demo_contact','them','明天做实验','2026-10-27T23:00:00')")
                db.execute("INSERT INTO messages VALUES(2,'demo_contact','system','撤回的系统通知','2026-10-27T23:01:00')")
            before = path.read_bytes()
            with (patch.object(reflect, "load", return_value={"contact": {"id": "demo_contact"}, "db_path": str(path)}),
                  patch.object(reflect.memory, "db", side_effect=AssertionError("no migrations")),
                  patch.object(reflect.memory, "read_state", return_value={}),
                  patch.object(reflect.memory, "update_state", side_effect=AssertionError("no live writes")),
                  patch.object(reflect.memory, "cdir", side_effect=AssertionError("no live diary")),
                  patch.object(llm, "complete", return_value="聊了实验") as summary,
                  patch.object(mem, "extract_facts", return_value=[]) as extract,
                  contextlib.redirect_stdout(io.StringIO())):
                reflect.run("2026-10-27", dry=True)
            self.assertIs(summary.call_args.kwargs["reuse_session"], False)
            self.assertEqual(extract.call_args.kwargs["today"], dt.date(2026, 10, 27))
            self.assertEqual(extract.call_args.args[0], ["他: 明天做实验"])
            self.assertEqual(path.read_bytes(), before)

    def test_public_news_never_proves_participation(self):
        class NoRandom:
            def random(self):
                raise AssertionError("news provenance is not random")
        event = {"title": "学生会迎新晚会筹备工作启动", "mine": True, "url": "https://news.bda.edu.cn/a", "id": "a"}
        life = campus.life_event(event, NoRandom())
        self.assertEqual(life["kind"], "听说")
        self.assertFalse(life["personal_participation"])
        self.assertNotIn("我也参与", life["text"])

    def test_old_fabricated_campus_cache_is_downgraded_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "campus.json"
            path.write_text(json.dumps({"events": [], "life": [{"title": "学生会迎新晚会筹备工作启动", "kind": "参与", "text": "我也参与了迎新"}]}))
            before = path.read_bytes()
            with patch.object(campus, "OUT", path):
                topic = campus.topics()[0]
            self.assertEqual(topic["kind"], "听说")
            self.assertNotIn("我也参与", topic["text"])
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
