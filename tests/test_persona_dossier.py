"""xiaotian-chat identity must stay ⊆ persona_dossier school/name (no invented drift)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOSSIER = ROOT / "data" / "persona_dossier.json"
CHAT_SKILL = ROOT / ".cursor" / "skills" / "xiaotian-chat" / "SKILL.md"


def test_dossier_exists_and_schema():
    d = json.loads(DOSSIER.read_text(encoding="utf-8"))
    assert d.get("sources", {}).get("do_not_invent") is True
    assert d["identity"]["name"] == "小甜"
    assert d["identity"]["birthday"] == "2006-05-15"
    assert d["school"]["institution"] == "北京舞蹈学院"
    assert d["school"]["institution_short"] == "北舞"
    assert d["school"]["hometown"] == "福建厦门"
    assert "海淀" in (d["school"].get("campus") or "")
    assert d["body"]["height_cm"] == 173
    assert d["body"]["weight_kg"] == 50
    assert "sheet.png" in d["appearance"]["canonical_refs"]
    assert "identity" in d["usage"]["chat_must_read"]
    assert "school" in d["usage"]["chat_must_read"]
    assert "无锡" not in json.dumps(d, ensure_ascii=False)


def test_chat_skill_identity_subset_of_dossier():
    d = json.loads(DOSSIER.read_text(encoding="utf-8"))
    skill = CHAT_SKILL.read_text(encoding="utf-8")
    m = re.search(r"## 身份\n([\s\S]*?)\n## ", skill)
    assert m, "xiaotian-chat missing ## 身份"
    ident = m.group(1)
    assert d["identity"]["name"] in ident
    assert d["school"]["institution"] in ident
    assert d["school"]["college"] in ident
    assert "编导" in ident and "编导" in d["school"]["major"]
    assert d["school"]["grade"] in ident
    assert d["school"]["hometown"] in ident or "厦门" in ident
    assert d["orgs"]["student_union"]["title"] in ident
    for club in d["orgs"]["clubs"]:
        assert club["name"] in ident
    assert "5.15" in ident or "05-15" in d["identity"]["birthday"]
