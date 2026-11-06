"""离线：research ideas → activity_pool upsert + pool duration local fill。不启 daemon/agent。"""
import json, sys, pathlib, shutil
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import activity_pool_upsert as apu
import reasonableness as rz

TMP = ROOT / "data" / "devtest" / "pool_upsert_test.json"
TMP.parent.mkdir(parents=True, exist_ok=True)
# start from a tiny pool
base = {
    "version": 1,
    "tz": "Asia/Shanghai",
    "gate": {"scope": "duration_only"},
    "activities": [
        {
            "id": "ski_chongli",
            "name": "崇礼滑雪",
            "aliases": ["崇礼", "滑雪"],
            "category": "travel_winter",
            "bucket": "outdoor_outing",
            "reasonableness": {"typical_days": 4, "min_stay_days": 3},
            "rating": {"base": 0.8, "source": "seed"},
        },
        {
            "id": "city_citywalk",
            "name": "Citywalk",
            "aliases": ["citywalk", "东四"],
            "category": "city_fun",
            "bucket": "city_fun",
            "reasonableness": {"typical_days": 1, "min_stay_days": 1},
            "rating": {"base": 0.7, "source": "seed"},
        },
    ],
}
TMP.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n")

ideas = [
    {"platform": "douyin", "topic": "滑雪", "title": "寒假崇礼太舞滑雪攻略"},
    {"platform": "xiaohongshu", "topic": "citywalk", "title": "东四赏秋 Citywalk 探店"},
    {"platform": "xiaohongshu", "topic": "雪山", "title": "元旦适合去梅里雪山看日照金山吗"},
    {"platform": "xiaohongshu", "topic": "加拿大", "title": "Banff冬季自驾景点保姆级攻略"},
]
st = apu.upsert_ideas(ideas, pool_path=TMP, write=True, create_novel=True)
assert st["matched"] >= 2
assert st["created"] >= 1
doc = json.loads(TMP.read_text())
ids = {a["id"] for a in doc["activities"]}
assert "ski_chongli" in ids and "travel_meili" in ids
ski = next(a for a in doc["activities"] if a["id"] == "ski_chongli")
assert "research_sources" in ski or "崇礼" in (ski.get("reasonableness") or {}).get("research_note", "")

# mark one needing duration and local-fill via reasonableness (point POOL_FILE)
# copy TMP over a temp and patch
old_pool = rz.POOL_FILE
rz.POOL_FILE = TMP
try:
    doc = rz.load_pool()
    for a in doc["activities"]:
        if a["id"] == "travel_meili":
            a["reasonableness"]["needs_duration_review"] = True
            a["reasonableness"].pop("typical_days", None)
    rz.save_pool(doc)
    # force agent off
    import os
    os.environ.pop("REASONABLENESS_AGENT", None)
    r = rz.fill_pool_durations_with_agent(["travel_meili"])
    assert r.get("ok")
    assert "travel_meili" in (r.get("filled") or [])
    act = rz.activities_by_id()["travel_meili"]
    assert act["reasonableness"].get("typical_days")
    assert not act["reasonableness"].get("needs_duration_review")
finally:
    rz.POOL_FILE = old_pool

print("OK activity_pool_upsert + pool duration local", st["created"], st["matched"])
