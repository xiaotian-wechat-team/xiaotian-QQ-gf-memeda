"""场景真实参考图库：从 ~/xiaotian-assets/scene_refs 按场景向量取 top-k。
库由 tools/build_scene_refs.py 构建（SQLite + 图片文件）。

扩展：常用场景包（common_scenes）优先；同日 scene_ref probes 绑定，防穿帮。
探针落盘 data/scene_ref_day_probes.json。"""
from __future__ import annotations
import json, os, pathlib, sqlite3, struct, hashlib
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DIR = pathlib.Path(os.path.expanduser("~/xiaotian-assets/scene_refs"))
DB_NAME = "scene_refs.sqlite"
DAY_PROBES_PATH = ROOT / "data" / "scene_ref_day_probes.json"

def assets_dir(cfg=None):
    if cfg and cfg.get("imagegen", {}).get("scene_refs_dir"):
        return pathlib.Path(cfg["imagegen"]["scene_refs_dir"]).expanduser()
    return DEFAULT_DIR

def db_path(cfg=None):
    return assets_dir(cfg) / DB_NAME

def connect(cfg=None, readonly=True):
    p = db_path(cfg)
    if not p.exists():
        raise FileNotFoundError(f"scene_refs db missing: {p}（先跑 tools/build_scene_refs.py）")
    uri = f"file:{p}?mode=ro" if readonly else f"file:{p}"
    return sqlite3.connect(uri, uri=True)

def _blob_to_vec(b):
    if not b: return None
    n = len(b) // 4
    return np.frombuffer(b, dtype=np.float32, count=n)

def _embed_text(texts, cfg=None):
    """复用 outfit 同款 bge-small-zh。"""
    import outfit
    m = outfit._load_model()
    if not m:
        raise RuntimeError("bge embed model unavailable")
    v = m.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(v, dtype=np.float32)

def scene_query_text(scene_row):
    """scene_row: dict with name/aliases/hint/category or a free string."""
    if isinstance(scene_row, str):
        return scene_row
    al = " ".join(scene_row.get("aliases") or [])
    return f"{scene_row.get('name','')} {al} {scene_row.get('hint','')} {scene_row.get('category','')}".strip()


def is_outfit_scene_id(scene) -> bool:
    """True for outfit catalog ids (campus-007 / sport-005 / …); not Chinese free text."""
    if not isinstance(scene, str):
        return False
    s = scene.strip()
    if not s:
        return False
    if s.startswith(("campus-", "sport-", "fun-", "date-", "travel-", "country-", "spot-")):
        return True
    return len(s) < 24 and "-" in s and s.split("-")[-1].isdigit()


def pick(scene, k=2, cfg=None, people_free_only=True, min_quality=0.0):
    """返回 [(path, meta_dict), ...] top-k。
    scene: outfit scene dict / id 字符串 / 自由描述。
    优先同 scene_id；否则用 bge 向量在全库检索。"""
    cfg = cfg or __import__("config", fromlist=["load"]).load()
    k = max(0, int(k))
    if k == 0: return []
    try:
        con = connect(cfg)
    except FileNotFoundError:
        return []
    con.row_factory = sqlite3.Row
    scene_id = None
    qtext = None
    if isinstance(scene, dict):
        scene_id = scene.get("id")
        qtext = scene_query_text(scene)
    elif isinstance(scene, str):
        # id like campus-001 or free text
        if scene.startswith(("campus-", "sport-", "fun-", "date-", "travel-", "country-", "spot-")) or (
            len(scene) < 24 and "-" in scene and scene.split("-")[-1].isdigit()
        ):
            scene_id = scene
        qtext = scene
    rows = []
    if scene_id:
        # Prefer people_free when requested, but fall back so unscored (faces=-1) library still works mid-build.
        sql = ("SELECT * FROM images WHERE scene_id=? AND quality>=? "
               "ORDER BY CASE WHEN people_free=1 THEN 0 ELSE 1 END, quality DESC, id ASC LIMIT ?")
        rows = list(con.execute(sql, (scene_id, min_quality, k * 3)))
        if people_free_only:
            pf = [r for r in rows if r["people_free"] == 1]
            if pf: rows = pf + [r for r in rows if r["people_free"] != 1]
    if len(rows) < k and qtext:
        try:
            qv = _embed_text([qtext], cfg)[0]
        except Exception:
            qv = None
        if qv is not None:
            all_rows = list(con.execute(
                "SELECT * FROM images WHERE embedding IS NOT NULL AND (NOT ? OR people_free=1) AND quality>=?",
                (1 if people_free_only else 0, min_quality)))
            scored = []
            for r in all_rows:
                ev = _blob_to_vec(r["embedding"])
                if ev is None or ev.shape != qv.shape: continue
                scored.append((float(np.dot(qv, ev)), r))
            scored.sort(key=lambda x: -x[0])
            have = {r["sha256"] for r in rows}
            for _, r in scored:
                if r["sha256"] in have: continue
                rows.append(r); have.add(r["sha256"])
                if len(rows) >= k * 3: break
    con.close()
    out = []
    for r in rows:
        p = pathlib.Path(r["path"])
        if not p.is_file():
            p = assets_dir(cfg) / "images" / r["relpath"]
        if not p.is_file(): continue
        out.append((p, dict(r)))
        if len(out) >= k: break
    return out

def stats(cfg=None):
    try:
        con = connect(cfg)
    except FileNotFoundError:
        return {"exists": False}
    n = con.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    pf = con.execute("SELECT COUNT(*) FROM images WHERE people_free=1").fetchone()[0]
    scenes = con.execute("SELECT COUNT(DISTINCT scene_id) FROM images").fetchone()[0]
    by_src = dict(con.execute("SELECT source, COUNT(*) FROM images GROUP BY source"))
    con.close()
    return {"exists": True, "images": n, "people_free": pf, "scenes": scenes, "by_source": by_src}


# ----- day probes + common pack preference ---------------------------------

def _load_day_probes() -> dict:
    try:
        if DAY_PROBES_PATH.is_file():
            return json.loads(DAY_PROBES_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"version": 1, "days": {}}


def _save_day_probes(doc: dict) -> None:
    DAY_PROBES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DAY_PROBES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DAY_PROBES_PATH)


def _paths_exist(paths) -> list[pathlib.Path]:
    out = []
    for s in paths or []:
        p = pathlib.Path(s)
        if p.is_file():
            out.append(p)
    return out


def ensure_day_probes(day: dict | None = None, *, date: str | None = None, cfg=None, k: int = 2) -> dict:
    """为当日 life_schedule 槽预绑定 scene_ref 探针（同 scene_key 复用同一组图）。

    day: life_schedule days[] 元素（含 date/slots）。
    返回该日 probes 字典；已存在则复用（路径失效时重选）。
    """
    import time
    cfg = cfg or __import__("config", fromlist=["load"]).load()
    if day is None and date:
        try:
            import schedule_pipeline as sp
            doc = sp.load_schedule()
            for d in doc.get("days") or []:
                if str(d.get("date")) == str(date):
                    day = d
                    break
        except Exception:
            day = None
    if not day:
        return {}
    dkey = str(day.get("date") or date or "")
    if not dkey:
        return {}
    doc = _load_day_probes()
    days = doc.setdefault("days", {})
    entry = days.get(dkey) or {"date": dkey, "probes": {}, "updated": None}
    probes = entry.setdefault("probes", {})
    slots = list(day.get("slots") or [])
    try:
        import common_scenes as cs
    except Exception:
        cs = None

    # unique scene keys from photo_ok slots (+ all slots with hints)
    keys_meta = []
    seen = set()
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        if slot.get("photo_ok") is False and not (slot.get("scene_hints") or []):
            continue
        sk = cs.scene_key_for_slot(slot) if cs else f"act:{(slot.get('activity') or 'x')[:32]}"
        if sk in seen:
            continue
        seen.add(sk)
        keys_meta.append((sk, slot))

    changed = False
    for sk, slot in keys_meta:
        cur = probes.get(sk) or {}
        existing = _paths_exist(cur.get("paths"))
        if len(existing) >= min(1, k):
            # refresh path strings if needed
            if [str(p) for p in existing] != list(cur.get("paths") or []):
                cur["paths"] = [str(p) for p in existing]
                probes[sk] = cur
                changed = True
            continue
        # pick: common pack first, else generic scene_refs.pick
        paths = []
        source = "generic"
        pack_id = None
        if cs:
            pack_id = cs.resolve_pack_from_slot(slot)
            if pack_id:
                paths = cs.pick_common(pack_id, k=k, cfg=cfg)
                if paths:
                    source = f"common:{pack_id}"
        if len(paths) < k:
            q = None
            hints = list(slot.get("scene_hints") or [])
            q = hints[0] if hints else (slot.get("activity") or sk)
            # prefer outfit id when pack known
            pick_key = q
            if cs and pack_id:
                oids = (cs.PACKS.get(pack_id) or {}).get("outfit_ids") or ()
                if oids:
                    pick_key = oids[0]
            extra = pick(pick_key, k=k, cfg=cfg, people_free_only=True)
            for p, _meta in extra:
                if p not in paths:
                    paths.append(p)
                if len(paths) >= k:
                    break
            if source == "generic" and paths:
                source = "scene_refs"
            elif paths and source.startswith("common") and len(paths) > len(cs.pick_common(pack_id, k=k, cfg=cfg) if cs and pack_id else []):
                source = source + "+scene_refs"
        probes[sk] = {
            "paths": [str(p) for p in paths[:k]],
            "source": source,
            "pack_id": pack_id,
            "activity": slot.get("activity"),
            "hints": list(slot.get("scene_hints") or [])[:4],
            "picked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        changed = True

    if changed or dkey not in days:
        entry["probes"] = probes
        entry["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        days[dkey] = entry
        # prune old days keep last 14
        if len(days) > 21:
            for old in sorted(days.keys())[:-14]:
                days.pop(old, None)
        doc["days"] = days
        _save_day_probes(doc)
    return probes


def get_day_probe(date: str, scene_key: str | None = None, slot: dict | None = None,
                  scene: str | None = None, cfg=None, k: int = 2) -> list[pathlib.Path]:
    """读取某日某 scene_key 的探针路径；无则尝试 ensure（需能加载 schedule）。"""
    try:
        import common_scenes as cs
        sk = scene_key or cs.scene_key_for_slot(slot, scene)
    except Exception:
        sk = scene_key or (f"act:{(scene or 'x')[:32]}")
    doc = _load_day_probes()
    entry = (doc.get("days") or {}).get(str(date)) or {}
    cur = (entry.get("probes") or {}).get(sk)
    paths = _paths_exist((cur or {}).get("paths"))
    if paths:
        return paths[:k]
    # lazy ensure
    probes = ensure_day_probes(date=str(date), cfg=cfg, k=k)
    cur = (probes or {}).get(sk) or {}
    return _paths_exist(cur.get("paths"))[:k]


def pick_with_common_and_probes(scene, k=2, cfg=None, people_free_only=True, min_quality=0.0,
                                *, date: str | None = None, slot: dict | None = None,
                                pick_key=None, pack_scene=None):
    """优先：当日探针 → 日程槽常用场景包 → 泛化 scene_refs.pick。

    pack resolve 优先用 schedule slot（及中文 pack_scene/scene），
    不用 outfit id（campus-…）抢解析；无 common pack 时回退 generic（不留空）。
    pick_key：泛化检索用（可传 outfit id）；缺省用 scene。
    """
    k = max(0, int(k))
    if k == 0:
        return []
    cfg = cfg or __import__("config", fromlist=["load"]).load()
    out = []
    have = set()

    def _add(paths_meta):
        nonlocal out
        for item in paths_meta:
            if isinstance(item, tuple):
                p, meta = item
            else:
                p, meta = item, {"source": "common"}
            p = pathlib.Path(p)
            if not p.is_file():
                continue
            key = str(p.resolve()) if p.exists() else str(p)
            if key in have:
                continue
            have.add(key)
            out.append((p, meta if isinstance(meta, dict) else {"source": str(meta)}))
            if len(out) >= k:
                return True
        return False

    # Chinese / free-text for pack resolve; never feed outfit catalog ids
    def _pack_text(val):
        if isinstance(val, str) and val.strip() and not is_outfit_scene_id(val):
            return val
        return None

    pack_text = _pack_text(pack_scene) or _pack_text(scene)
    generic_key = pick_key if pick_key is not None else scene

    # 1) day probes
    dkey = date
    if not dkey and slot and slot.get("_date"):
        dkey = str(slot.get("_date"))
    if dkey:
        try:
            import common_scenes as cs
            sk = cs.scene_key_for_slot(slot, pack_text)
        except Exception:
            sk = None
        if sk:
            for p in get_day_probe(dkey, scene_key=sk, slot=slot, scene=pack_text, cfg=cfg, k=k):
                if _add([(p, {"source": "day_probe", "scene_key": sk})]):
                    return out

    # 2) common pack — schedule slot first, then Chinese scene text
    try:
        import common_scenes as cs
        pack_id = None
        if slot:
            pack_id = cs.resolve_pack_from_slot(slot, pack_text)
        if not pack_id and pack_text:
            pack_id = cs.resolve_pack(pack_text)
        if pack_id:
            commons = cs.pick_common(pack_id, k=k, cfg=cfg)
            if _add([(p, {"source": f"common:{pack_id}", "pack_id": pack_id}) for p in commons]):
                return out
    except Exception:
        pass

    # 3) generic fallback (including when slot does not map to a common pack)
    rest = pick(generic_key, k=k, cfg=cfg, people_free_only=people_free_only, min_quality=min_quality)
    _add(rest)
    return out

