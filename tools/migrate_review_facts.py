#!/usr/bin/env python3
"""Narrow approved 2026-10-29 correction; persona SSOT: data/persona_dossier.json.

Default is read-only. Never changes chat.db, STATE, schedules or vector memory.
Only the global SOUL cohort and confirmed demo_contact derived-memory correction apply.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- review-facts-20261029 -->"
NOTE = ("\n\n" + MARKER + "\n## 事实校正（2026-10-29）\n"
        "对方在 2026-10-28 12:45 明确说“我在北京大学读博呢”（demo_contact 聊天记录 ID 421）。"
        "10月26日反思中的“问他吃饭实习”记录的是小甜自己的问句，不是对方确认了实习；"
        "不能据此推断他的工作或实习经历。实习单位和编创助理属于小甜。\n")


def plan(cfg):
    dl = Path(cfg["digital_life_dir"]).expanduser()
    edits = []
    soul = dl / "SOUL.md"
    if soul.exists():
        before = soul.read_text()
        lines = before.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if "北京舞蹈学院" in line and "大三" in line:
                if "2023级" in line or "2023 级" in line:
                    lines[i] = line.replace("2023级", "2024级").replace("2023 级", "2024 级")
                elif "2024" not in line:
                    lines[i] = line.replace("大三", "2024级大三", 1)
        after = "".join(lines)
        if after != before:
            edits.append((soul, before, after, "cohort_2024_keep_junior"))
    # Only add the factual correction if its original first-person evidence exists.
    db_path = Path(cfg["db_path"]).expanduser()
    if db_path.exists():
        con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT text FROM messages WHERE contact='demo_contact' AND sender='them' AND id=421").fetchone()
        finally:
            con.close()
        if row and "我在北京大学读博" in row[0]:
            profile = dl / "contacts/demo_contact/PROFILE.md"
            if profile.exists():
                before = profile.read_text()
                if MARKER not in before:
                    lines = before.splitlines(keepends=True)
                    position = 1 if lines and lines[0].startswith("#") else 0
                    lines.insert(position, MARKER + "\n- 学业：在北京大学读博（本人 2026-10-28 明确说，聊天 ID 421）；不据此推断实习经历。\n")
                    edits.append((profile, before, "".join(lines), "confirmed_identity_in_chat_profile"))
            for path in (dl / "contacts/demo_contact/MEMORY.md", dl / "contacts/demo_contact/diary/2026-10-26.md"):
                # Do not create absent historical files or touch another contact.
                if path.exists():
                    before = path.read_text()
                    if MARKER not in before:
                        edits.append((path, before, before.rstrip() + NOTE, "attributed_user_fact_and_assumption_correction"))
    return edits


def apply(edits):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    reports = []
    for path, before, after, reason in edits:
        if path.read_text() != before:
            raise RuntimeError(f"File changed since planning: {path}")
        backup = path.with_name(path.name + ".review-backup-" + stamp)
        shutil.copy2(path, backup)
        temp = path.with_name(path.name + ".review-tmp-" + stamp)
        try:
            temp.write_text(after)
            os.chmod(temp, path.stat().st_mode & 0o777)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
        reports.append({"path": str(path), "backup": str(backup), "reason": reason})
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    edits = plan(json.loads((ROOT / "config.json").read_text()))
    result = apply(edits) if args.apply else [{"path": str(e[0]), "reason": e[3]} for e in edits]
    print(json.dumps({"applied": args.apply, "changes": result}, ensure_ascii=False, indent=2))
