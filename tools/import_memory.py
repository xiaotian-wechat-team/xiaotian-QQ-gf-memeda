"""一次性导入：digital-life 的 MEMORY.md（全局 + contacts/<id>）条目 → mem0；chat.db 聊天 → cursor-agent 抽事实 → mem0。
[已过时] 条目跳过。可重复运行（mem.add 去重）。.venv/bin/python tools/import_memory.py [--dry-run]"""
import sys, re, pathlib, argparse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from config import load
import memory, mem

def md_items(path):
    out = []
    for line in pathlib.Path(path).read_text().splitlines() if pathlib.Path(path).exists() else []:
        m = re.match(r"\s*-\s+(.*)", line)
        if not m or "已过时" in line: continue
        t = m.group(1).strip(); d = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*)", t)
        out.append((d.group(2), d.group(1)) if d else (t, ""))
    return out

ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
cfg = load(); cid = cfg["contact"]["id"]; DL = memory.DL
items = [(f, ts, "MEMORY.md", 6) for f, ts in md_items(DL / "MEMORY.md")] + \
        [(f, ts, f"contacts/{cid}/MEMORY.md", 7) for f, ts in md_items(DL / "contacts" / cid / "MEMORY.md")]
c = memory.db()
rows = c.execute("SELECT sender,text FROM messages WHERE contact=? ORDER BY id", (cid,)).fetchall()
chat = [f"{'小甜' if s == 'me' else '他'}: {t}" for s, t in rows]
for i in range(0, len(chat), 150):
    for f in mem.extract_facts(chat[i:i + 150]): items.append((f["fact"], "", "chat.db", f.get("importance", 5)))
print(f"MEMORY 条目 + chat.db({len(rows)} 条) 抽取 → 共 {len(items)} 条")
for fact, ts, src, imp in items:
    r = None if a.dry_run else mem.add(cid, fact, imp, source=src, ts=ts)
    print(("DRY " if a.dry_run else ("ADD " if r else "DUP ")) + f"[{src}] {fact}")
