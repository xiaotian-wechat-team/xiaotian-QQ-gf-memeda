"""Research ideas (XHS/Douyin) → upsert into data/activity_pool.json.

Does not start daemon. Duration fields may be provisional; fill via
reasonableness.fill_pool_durations_with_agent (Cursor duration-only).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import unicodedata
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
POOL = ROOT / "data" / "activity_pool.json"
RESEARCH = ROOT / "data" / "collect_runs" / "life_enrich_research.json"

# title/topic keywords → existing activity id
KEYWORD_TO_ID: list[tuple[list[str], str]] = [
    (["崇礼", "太舞", "富龙", "万龙", "云顶", "翠云山", "滑雪"], "ski_chongli"),
    (["禾木", "喀纳斯", "阿勒泰", "白哈巴"], "travel_hemu"),
    (["三亚", "海棠湾", "亚龙湾", "蜈支洲", "天涯", "镇海"], "travel_sanya"),
    (["东京", "涩谷", "日本冬季"], "travel_tokyo_winter"),
    (["首尔", "明洞", "弘大"], "travel_seoul_winter"),
    (["清迈"], "travel_chiangmai"),
    (["巴厘"], "travel_bali"),
    (["北海道", "札幌"], "travel_hokkaido_ski"),
    (["新加坡"], "travel_singapore"),
    (["大阪", "京都"], "travel_osaka_winter"),
    (["香港", "澳门"], "travel_hk_macau"),
    (["台北", "台湾"], "travel_taipei"),
    (["剧本杀", "古堡逃脱", "逃脱", "密室"], "city_script_kill"),
    (["Livehouse", "livehouse", "演出"], "city_livehouse"),
    (["citywalk", "Citywalk", "东四", "文创", "南锣", "鼓楼"], "city_citywalk"),
    (["图书馆", "复习", "自习"], "study_library"),
    (["排练", "基训"], "school_rehearsal"),
]

# novel destination seeds (create if unseen)
NOVEL_SEEDS: list[tuple[list[str], dict]] = [
    (["梅里", "日照金山"], {
        "id": "travel_meili",
        "name": "梅里雪山",
        "aliases": ["梅里", "日照金山"],
        "category": "travel_winter",
        "bucket": "outdoor_outing",
        "location": {"region": "云南", "city": "德钦", "hub": "西南"},
        "reasonableness": {
            "typical_days": 4, "typical_days_min": 3, "typical_days_max": 6,
            "min_stay_days": 3, "season": ["winter"],
            "needs_duration_review": True,
        },
        "rating": {"base": 0.75, "source": "research_upsert"},
        "scene_match": ["雪山", "梅里", "日照金山"],
    }),
    (["Banff", "班夫"], {
        "id": "travel_banff",
        "name": "班夫冬日",
        "aliases": ["Banff", "班夫"],
        "category": "travel_abroad",
        "bucket": "travel",
        "location": {"region": "加拿大", "city": "Banff", "hub": "abroad"},
        "reasonableness": {
            "typical_days": 6, "typical_days_min": 4, "typical_days_max": 9,
            "min_stay_days": 4, "season": ["winter"],
            "needs_duration_review": True,
        },
        "rating": {"base": 0.7, "source": "research_upsert"},
        "scene_match": ["Banff", "雪山", "自驾"],
    }),
    (["杂货", "文创铺"], {
        "id": "city_creative_shops",
        "name": "北京文创杂货铺打卡",
        "aliases": ["文创", "杂货铺"],
        "category": "city_fun",
        "bucket": "city_fun",
        "location": {"region": "北京", "city": "北京", "hub": "北京"},
        "reasonableness": {
            "typical_days": 1, "typical_days_min": 1, "typical_days_max": 1,
            "min_stay_days": 1, "typical_hours": 4, "min_hours": 2,
            "needs_duration_review": True,
        },
        "rating": {"base": 0.72, "source": "research_upsert"},
        "scene_match": ["文创", "探店"],
    }),
]

CATEGORY_DEFAULT_DAYS = {
    "travel_winter": 5,
    "travel_abroad": 6,
    "city_fun": 1,
    "study": 1,
    "school": 1,
    "transit": 1,
    "outdoor_outing": 1,
}


def load_pool(path: pathlib.Path | None = None) -> dict:
    p = path or POOL
    if not p.exists():
        return {"version": 1, "tz": "Asia/Shanghai", "gate": {}, "activities": []}
    return json.loads(p.read_text())


def save_pool(doc: dict, path: pathlib.Path | None = None) -> pathlib.Path:
    p = path or POOL
    doc["updated"] = dt.date.today().isoformat()
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return p


def load_research_ideas(path: pathlib.Path | None = None) -> list[dict]:
    p = path or RESEARCH
    if not p.exists():
        return []
    doc = json.loads(p.read_text())
    return list(doc.get("ideas") or [])


def _slug_from_title(title: str) -> str:
    # keep CJK + alnum; hash tail for uniqueness
    t = re.sub(r"\s+", "", title or "")
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)[:12] or "idea"
    h = hashlib.md5(title.encode()).hexdigest()[:6]
    return f"research_{t}_{h}"


def match_existing_id(text: str, by_id: dict[str, dict]) -> str | None:
    blob = text or ""
    best: tuple[int, str] | None = None
    for keys, aid in KEYWORD_TO_ID:
        if aid not in by_id:
            continue
        for k in keys:
            if k and k in blob:
                score = len(k)
                if not best or score > best[0]:
                    best = (score, aid)
    if best:
        return best[1]
    # also match pool aliases
    for aid, act in by_id.items():
        names = [act.get("name") or ""] + list(act.get("aliases") or [])
        for n in names:
            if n and len(n) >= 2 and n in blob:
                return aid
    return None


def match_novel_seed(text: str) -> dict | None:
    for keys, seed in NOVEL_SEEDS:
        if any(k in (text or "") for k in keys):
            return json.loads(json.dumps(seed))  # deep copy
    return None


def _append_research(act: dict, idea: dict) -> None:
    title = (idea.get("title") or "").strip()
    if not title:
        return
    rz = act.setdefault("reasonableness", {})
    notes = rz.get("research_note") or ""
    line = f"[{idea.get('platform')}] {title}"
    if line not in notes:
        rz["research_note"] = (notes + " | " + line).strip(" |")[:800]
    src = act.setdefault("research_sources", [])
    entry = {"platform": idea.get("platform"), "topic": idea.get("topic"), "title": title}
    if entry not in src:
        src.append(entry)
        if len(src) > 12:
            del src[:-12]
    # light alias: pick 2–6 char CJK token
    for m in re.findall(r"[\u4e00-\u9fff]{2,6}", title):
        if m in ("一定", "还有", "不要", "攻略", "旅行", "寒假", "冬天", "大学生"):
            continue
        aliases = act.setdefault("aliases", [])
        if m not in aliases and m != act.get("name") and len(aliases) < 16:
            aliases.append(m)
            break


def upsert_ideas(
    ideas: list[dict] | None = None,
    *,
    pool_path: pathlib.Path | None = None,
    write: bool = True,
    create_novel: bool = True,
) -> dict:
    """Merge research ideas into activity_pool. Returns stats."""
    ideas = ideas if ideas is not None else load_research_ideas()
    doc = load_pool(pool_path)
    acts = doc.setdefault("activities", [])
    by = {a["id"]: a for a in acts}
    matched = 0
    created = 0
    skipped = 0
    touched: list[str] = []

    for idea in ideas:
        title = (idea.get("title") or "").strip()
        topic = (idea.get("topic") or "").strip()
        blob = f"{title} {topic}"
        if not title:
            skipped += 1
            continue
        aid = match_existing_id(blob, by)
        if aid:
            _append_research(by[aid], idea)
            matched += 1
            if aid not in touched:
                touched.append(aid)
            continue
        if create_novel:
            seed = match_novel_seed(blob)
            if seed:
                sid = seed["id"]
                if sid in by:
                    _append_research(by[sid], idea)
                    matched += 1
                else:
                    seed.setdefault("reasonableness", {})["research_note"] = (
                        f"upsert from research: {title[:80]}"
                    )
                    seed["research_sources"] = [{
                        "platform": idea.get("platform"),
                        "topic": topic,
                        "title": title,
                    }]
                    acts.append(seed)
                    by[sid] = seed
                    created += 1
                    touched.append(sid)
                continue
            # generic city/outdoor idea → lightweight activity
            sid = _slug_from_title(title)
            if sid in by:
                _append_research(by[sid], idea)
                matched += 1
                continue
            # only create for entertainment/travel-ish titles (avoid spam)
            if not re.search(
                r"旅行|滑雪|雪山|探店|周末|citywalk|剧本|演出|海边|民宿|攻略|逛",
                blob,
                re.I,
            ):
                skipped += 1
                continue
            cat = "city_fun"
            bucket = "city_fun"
            if re.search(r"滑雪|雪山|海边|旅行|民宿", blob):
                cat, bucket = "travel_winter", "travel"
            typ = CATEGORY_DEFAULT_DAYS.get(cat, 1)
            new = {
                "id": sid,
                "name": title[:24],
                "aliases": [],
                "category": cat,
                "bucket": bucket,
                "location": {"region": "未知", "city": "", "hub": ""},
                "reasonableness": {
                    "typical_days": typ,
                    "typical_days_min": max(1, typ - 1),
                    "typical_days_max": typ + 2,
                    "min_stay_days": max(1, typ - 1 if typ > 1 else 1),
                    "needs_duration_review": True,
                    "research_note": f"auto upsert: {title[:100]}",
                },
                "rating": {"base": 0.65, "source": "research_upsert"},
                "scene_match": re.findall(r"[\u4e00-\u9fff]{2,4}", title)[:4],
                "research_sources": [{
                    "platform": idea.get("platform"),
                    "topic": topic,
                    "title": title,
                }],
            }
            acts.append(new)
            by[sid] = new
            created += 1
            touched.append(sid)
        else:
            skipped += 1

    doc["activities"] = list(by.values())
    # stable-ish order: seeds first by original order then new
    stats = {
        "ideas": len(ideas),
        "matched": matched,
        "created": created,
        "skipped": skipped,
        "touched_ids": touched,
        "n_activities": len(doc["activities"]),
        "path": str(pool_path or POOL),
    }
    doc.setdefault("enrichment", {})["last_upsert"] = {
        **{k: stats[k] for k in ("matched", "created", "skipped", "ideas")},
        "at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if write:
        save_pool(doc, pool_path)
    stats["doc"] = doc
    return stats


def activities_needing_duration(doc: dict | None = None) -> list[str]:
    doc = doc or load_pool()
    out = []
    for a in doc.get("activities") or []:
        rz = a.get("reasonableness") or {}
        if rz.get("needs_duration_review") or not rz.get("typical_days"):
            out.append(a["id"])
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--research", default=str(RESEARCH))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-novel", action="store_true")
    args = ap.parse_args()
    ideas = load_research_ideas(pathlib.Path(args.research))
    st = upsert_ideas(ideas, write=not args.dry_run, create_novel=not args.no_novel)
    st.pop("doc", None)
    print(json.dumps(st, ensure_ascii=False, indent=2))
