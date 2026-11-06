"""lang_pack detect + dossier hometown/languages."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import lang_pack

ROOT = Path(__file__).resolve().parents[1]


def test_detect_en_fr_yue():
    assert lang_pack.detect("I miss you baby") == "en"
    assert lang_pack.detect("bonjour mon amour, ça va?") == "fr"
    assert lang_pack.detect("好啦得唔得啊") == "yue"
    assert lang_pack.detect("今天排练好累") is None


def test_dossier_xiamen_and_langs():
    d = json.loads((ROOT / "data/persona_dossier.json").read_text(encoding="utf-8"))
    assert d["school"]["hometown"] == "福建厦门"
    assert "无锡" not in json.dumps(d, ensure_ascii=False)
    assert set(d["languages"]["speaks"]) >= {"普通话", "英语", "法语", "粤语"}
    assert d["identity"]["birthday"] == "2006-05-15"
    assert d["identity"]["mbti"] == "ENFP"


def test_prompt_hint_only_non_default():
    assert lang_pack.prompt_hint("今天排练好累") == ""
    assert lang_pack.prompt_hint("") == ""
    h = lang_pack.prompt_hint("I miss you baby")
    assert h and "偏英语" in h
    assert "默认普通话" not in h  # baseline no longer every turn

