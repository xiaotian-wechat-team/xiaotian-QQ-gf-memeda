"""场景真实参考图库：从 ~/xiaotian-assets/scene_refs 按场景向量取 top-k。
库由 tools/build_scene_refs.py 构建（SQLite + 图片文件）。

扩展：常用场景包（common_scenes）优先；同日 scene_ref probes 绑定，防穿帮。
探针落盘 data/scene_ref_day_probes.json。"""
from __future__ import annotations
import json, os, pathlib, re, sqlite3, struct, hashlib
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


def pick(scene, k=2, cfg=None, people_free_only=True, min_quality=0.0, *, date=None, rotate=False):
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
        sql = ("SELECT * FROM images WHERE scene_id=? AND quality>=? AND (NOT ? OR people_free=1) "
               "ORDER BY quality DESC, id ASC LIMIT ?")
        rows = list(con.execute(sql, (scene_id, min_quality, int(people_free_only), max(12, k * 3))))
    if len(rows) < k and qtext and not scene_id:
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
                if len(rows) >= max(12, k * 3): break
    con.close()
    out = []
    for r in rows:
        p = pathlib.Path(r["path"])
        if not p.is_file():
            p = assets_dir(cfg) / "images" / r["relpath"]
        if not p.is_file(): continue
        out.append((p, dict(r)))
    if out:
        import datetime
        from zoneinfo import ZoneInfo
        day = datetime.date.fromisoformat(str(date)) if date else datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date()
        role = (cfg.get("identity_runtime") or {}).get("persona_id", "xiaotian")
        seed = hashlib.sha256((role + ":" + str(scene_id or qtext)).encode()).hexdigest()
        offset = (day.toordinal() + int(seed[:8], 16)) % len(out)
        out = out[offset:] + out[:offset]
    if rotate:
        from scene_ref_rotation import select
        return select(out, k, cfg, "generic:" + str(scene_id or qtext))
    return out[:k]

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

def _probes_path(cfg=None):
    runtime = (cfg or {}).get("identity_runtime") or {}
    if runtime.get("runtime_root"):
        return pathlib.Path(runtime["runtime_root"]) / "runtime" / "scene_ref_day_probes.json"
    return DAY_PROBES_PATH


def _load_day_probes(cfg=None) -> dict:
    try:
        if _probes_path(cfg).is_file():
            return json.loads(_probes_path(cfg).read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"version": 1, "days": {}}


def _save_day_probes(doc: dict, cfg=None) -> None:
    path = _probes_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid
    tmp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


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
    doc = _load_day_probes(cfg)
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
        if not sk.startswith("common:") and len(existing) >= min(1, k):
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
                paths = cs.pick_common(pack_id, k=k, cfg=cfg, date=dkey)
                source = f"common:{pack_id}" if paths else f"unreviewed:{pack_id}"
        if not pack_id and len(paths) < k:
            q = None
            hints = list(slot.get("scene_hints") or [])
            q = hints[0] if hints else (slot.get("activity") or sk)
            extra = pick(q, k=k, cfg=cfg, people_free_only=True, date=dkey)
            for p, _meta in extra:
                if p not in paths:
                    paths.append(p)
                if len(paths) >= k:
                    break
            if source == "generic" and paths:
                source = "scene_refs"
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
        _save_day_probes(doc, cfg)
    return probes


def get_day_probe(date: str, scene_key: str | None = None, slot: dict | None = None,
                  scene: str | None = None, cfg=None, k: int = 2) -> list[pathlib.Path]:
    """读取某日某 scene_key 的探针路径；无则尝试 ensure（需能加载 schedule）。"""
    try:
        import common_scenes as cs
        sk = scene_key or cs.scene_key_for_slot(slot, scene)
    except Exception:
        sk = scene_key or (f"act:{(scene or 'x')[:32]}")
    if sk and sk.startswith("common:"):
        return cs.pick_common(sk.split(":", 1)[1], k=k, cfg=cfg, date=date)
    doc = _load_day_probes(cfg)
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
                                pick_key=None, pack_scene=None, rotate=False):
    """常用包每次验证视觉审核并按天轮换；非 common 才走探针/泛化。

    明确场所优先于日程，不用 outfit id（campus-…）抢解析。
    common 缺少审核图时留空，不能回退到未经审核的泛化图。
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

    dkey = date or (slot or {}).get("_date") or (slot or {}).get("date")
    import common_scenes as cs
    pack_id = cs.resolve_pack_from_slot(slot, pack_text)
    if pack_id:
        # Revalidate content on every use; legacy probes cannot resurrect bad images.
        return [(p, {"source": f"common:{pack_id}", "pack_id": pack_id})
                for p in cs.pick_common(pack_id, k=k, cfg=cfg, date=dkey, rotate=rotate)]

    # Re-select deterministically for the day; old probes carry no quality proof.
    # 3) generic fallback (including when slot does not map to a common pack)
    rest = pick(generic_key, k=k, cfg=cfg, people_free_only=people_free_only, min_quality=min_quality, date=dkey, rotate=rotate)
    _add(rest)
    return out


# 日程地点 → 已入库环境图。校内 common pack 不走这里。
# 只用 location / scene_hints 等记录字段的字面地点标签，不用向量相似。
_PLACE_STOP = frozenset("""
北京 上海 海南 河北 新疆 学校 校园 学院 学生 附近 运动 休息 公共 室内 室外
环境 建筑 风景 通用 参考 照片 活动 计划 宿舍 寝室 食堂 教室 图书馆 晚上
上午 下午 早上 中午 周末 出发 返回 途中 区域 步道 平台 入口 座位 座椅 视角
乘客 接驳 车辆 方向 安全 边缘 初级 小镇 滑雪 餐厅 用餐 早餐 早饭 午饭 晚饭
夜市 沙滩 海岸 海湾 海边 雪山 雪场 雪道 冬季 积雪 全景 航拍 公园 森林
奥林匹克 南园 北园 酒店 民宿 木屋 餐桌 大厅 住宿 出差 实习 交通 背包 笔记
取景 踩点 车厢 高铁 列车 自己 随手 拍照 拍摄 开放 插曲
外景 夜景 内景 城市 舞蹈 机位 空镜 灯光 楼宇 亮灯 街区 展厅 园林 公共
""".split())
_SCRUB_WORDS = tuple(sorted((w for w in _PLACE_STOP if len(w) >= 2), key=len, reverse=True))
_PLACE_STRIP = tuple(sorted((
    "公共休息区", "休息边缘", "休息区", "乘客座位", "机舱座位", "客机机舱",
    "候机休息区", "候机厅", "室内餐桌", "雪具归还大厅", "雪具大厅", "初级练习区",
    "练习区", "步行街", "安全步道", "步道平台", "观景台", "南园", "北园", "沿途",
    "途中", "附近", "太子城", "角色计划", "非已预订", "待确认", "待核实",
), key=len, reverse=True))
_IN_VEHICLE = re.compile(r"乘客座位|机舱|接驳车辆|接驳|列车|高铁|航班|飞行|大巴|车厢")
_VEHICLE_GROUPS = (
    ("机舱", "客舱"),
    ("舷窗",),
    ("高铁", "列车"),
    ("座椅", "座位"),
    ("侧窗", "车窗"),
    ("小巴", "接驳"),
    ("候机",),
)


def _schedule_blob(slot):
    """Recorded venue text. Activity alone is not a place."""
    if not isinstance(slot, dict):
        return ""
    parts = []
    loc = str(slot.get("location") or "").strip()
    if loc and loc not in ("未知", "待定", "未确定", "不详"):
        parts.append(loc)
    for key in ("scene_hints",):
        for item in slot.get(key) or []:
            text = str(item).strip()
            if text:
                parts.append(text)
    for key in ("photo_subject", "destination_area"):
        text = str(slot.get(key) or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _visible_scene_name(name):
    """Drop negation parentheticals so「非禾木」cannot count as 禾木."""
    def repl(match):
        inner = match.group(1)
        if inner.startswith(("非", "不是", "不代表", "不能", "不证明")):
            return ""
        return inner
    return re.sub(r"[（(]([^）)]*)[）)]", repl, name or "")


def _scrub_place_text(text):
    """Drop generic venue words so「馆公共」「外景」cannot bridge two places."""
    text = re.sub(r"[（(][^）)]*[）)]", " ", text or "")
    for phrase in _PLACE_STRIP:
        text = text.replace(phrase, " ")
    for word in _SCRUB_WORDS:
        text = text.replace(word, " ")
    return text


def _place_needles(blob):
    text = _scrub_place_text(blob)
    found = []
    seen = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        upper = min(12, len(run))
        for length in range(upper, 1, -1):
            for index in range(len(run) - length + 1):
                gram = run[index:index + length]
                if gram in _PLACE_STOP or gram in seen or any(ch in gram for ch in "与的和或及"):
                    continue
                seen.add(gram)
                found.append(gram)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text):
        if token not in seen:
            seen.add(token)
            found.append(token)
    return found


_SLOT_INDOOR = ("室内", "住宿", "民宿", "餐桌", "早餐", "火锅", "茶桌", "陈列", "候机", "客舱", "机舱", "车厢", "包厢", "小店")
_SLOT_OUTDOOR = ("室外", "外景", "雪道", "沙滩", "海岸", "海滨", "步道", "观景", "村道", "街巷", "徒步", "滑雪", "雪山", "夜市", "公园")
_IMAGE_OUTDOOR = ("室外", "外景", "航拍", "远眺", "沙滩", "海岸", "海滨", "步道", "雪山", "雪道", "雪村", "建筑外观", "景区入口", "夜市", "缆车")
_PLACE_DENIALS = (
    ("禾木", ("非禾木", "不是禾木", "不代表禾木")),
    ("海棠湾", ("非海棠湾", "不是海棠湾", "不代表海棠湾")),
    ("阿勒泰", ("非阿勒泰", "不是阿勒泰")),
    ("太舞", ("不是太舞",)),
    ("喀纳斯", ("不是喀纳斯",)),
    ("白哈巴", ("不是喀纳斯湖/白哈巴",)),
    ("太子城", ("不是北京至太子城",)),
    ("天涯小镇", ("不能冒充天涯镇街巷",)),
    ("天涯镇", ("不能冒充天涯镇街巷",)),
)


def _slot_setting(slot):
    """Indoor/outdoor only from explicit words. Mixed or absent stays unknown."""
    if not isinstance(slot, dict):
        return None
    text = " ".join([
        str(slot.get("location") or ""), str(slot.get("activity") or ""),
        " ".join(str(h) for h in (slot.get("scene_hints") or [])),
        str(slot.get("photo_subject") or ""),
    ])
    if "室内" in text and "室外" not in text and "外景" not in text:
        return "indoor"
    if ("室外" in text or "外景" in text) and "室内" not in text:
        return "outdoor"
    indoor = any(word in text for word in _SLOT_INDOOR)
    outdoor = any(word in text for word in _SLOT_OUTDOOR)
    if indoor and not outdoor:
        return "indoor"
    if outdoor and not indoor:
        return "outdoor"
    return None


def _image_setting(scene_name, query):
    """Use scene_name/query only. A negated「室内」is outdoor evidence, not indoor."""
    text = f"{scene_name or ''} {query or ''}"
    negated_indoor = re.search(r"不是[^。；]{0,24}室内|不是[^。；]{0,24}内景|非室内", text) is not None
    if negated_indoor:
        indoor = False
    else:
        indoor = ("室内" in text or "候机厅" in text or "客舱" in text
                  or re.search(r"(?<![A-Za-z])indoor(?![A-Za-z])", text, re.I) is not None)
    outdoor = any(word in text for word in _IMAGE_OUTDOOR) or negated_indoor
    if indoor and outdoor:
        return None
    if indoor:
        return "indoor"
    if outdoor:
        return "outdoor"
    return None


def _image_denies_place(meta, blob):
    for place, phrases in _PLACE_DENIALS:
        if place in (blob or "") and any(phrase in meta for phrase in phrases):
            return True
    return False


def _setting_ok(item, blob, slot_setting):
    """Known indoor needs an indoor frame. Outdoor must not be filled with an interior.
    Explicit「非某地」cannot stand in for that place. Unknown image setting is not indoor proof."""
    meta = f"{item.get('scene_name') or ''} {item.get('query') or ''}"
    if _image_denies_place(meta, blob):
        return False
    image_setting = _image_setting(item.get("scene_name"), item.get("query"))
    if slot_setting == "indoor" and image_setting != "indoor":
        return False
    if slot_setting == "outdoor" and image_setting == "indoor":
        return False
    if slot_setting and image_setting and slot_setting != image_setting:
        return False
    return True


def _in_vehicle_slot(slot, blob):
    if (slot or {}).get("leg") in ("outbound", "return"):
        return True
    return bool(_IN_VEHICLE.search(blob or ""))


def _schedule_rows(cfg, people_free_only, min_quality):
    try:
        con = connect(cfg)
    except FileNotFoundError:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = list(con.execute(
            "SELECT id, scene_id, scene_name, query, quality, people_free, sha256, path, relpath "
            "FROM images WHERE quality>=? AND (NOT ? OR people_free=1)",
            (min_quality, 1 if people_free_only else 0),
        ))
    finally:
        con.close()
    out = []
    root = assets_dir(cfg)
    for row in rows:
        name = str(row["scene_name"] or "")
        path = pathlib.Path(row["path"] or "")
        if not path.is_file():
            path = root / "images" / str(row["relpath"] or "")
        if not path.is_file():
            continue
        scene_id = str(row["scene_id"] or "")
        out.append({
            "id": row["id"],
            "scene_id": scene_id,
            "scene_name": name,
            "query": str(row["query"] or ""),
            "visible": _visible_scene_name(name),
            "place_text": _scrub_place_text(_visible_scene_name(name)),
            "quality": float(row["quality"] or 0),
            "path": path,
            "generic": ("generic" in scene_id) or ("通用" in name),
            "travel": scene_id.startswith("travel-"),
        })
    return out


def _order_refs(items, k, cfg, date, key, rotate):
    if not items or k <= 0:
        return []
    ordered = sorted(items, key=lambda item: (
        0 if item["travel"] else 1, -item["quality"], item["id"] or 0, str(item["path"])))
    # same-day stable rotation; live generation then consumes scene_ref_rotation
    import datetime
    from zoneinfo import ZoneInfo
    day = (datetime.date.fromisoformat(str(date)) if date
           else datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date())
    role = ((cfg or {}).get("identity_runtime") or {}).get("persona_id", "xiaotian")
    seed = hashlib.sha256((role + ":" + str(key)).encode()).hexdigest()
    offset = (day.toordinal() + int(seed[:8], 16)) % len(ordered)
    ordered = ordered[offset:] + ordered[:offset]
    pairs = [(item["path"], {"source": "schedule-environment", "scene_id": item["scene_id"],
                              "needle": item.get("needle") or "", "scene_name": item["scene_name"]})
             for item in ordered]
    if rotate:
        from scene_ref_rotation import select
        return select(pairs, k, cfg or {}, "schedule:" + str(key))
    return pairs[:k]


def _shared_prefix_len(needle, name):
    if not needle or not name:
        return 0
    upper = min(12, len(needle), len(name))
    for length in range(upper, 5, -1):
        for index in range(len(needle) - length + 1):
            if needle[index:index + length] in name:
                return length
    return 0


def pick_scheduled_environment(slot, k=2, cfg=None, people_free_only=True, min_quality=0.0,
                               *, date=None, rotate=False):
    """Non-common schedule slot → ingested environment refs.

    Matches location / scene_hints / photo_subject text against scene_name.
    travel-* wins ties. Indoor slots keep only frames tagged indoor; outdoor slots
    drop interiors. A frame that says it is not that place is not a reference.
    In-vehicle slots only use generic cabin/seat refs that survive the same checks.
    No embedding and no outfit-id lookup: catalog similarity is not a venue.
    """
    k = max(0, int(k))
    if k == 0:
        return []
    blob = _schedule_blob(slot)
    if not blob:
        return []
    rows = _schedule_rows(cfg, people_free_only, min_quality)
    if not rows:
        return []
    slot_setting = _slot_setting(slot)
    if _in_vehicle_slot(slot, blob):
        scored = []
        for item in rows:
            if not item["generic"]:
                continue
            groups = [group for group in _VEHICLE_GROUPS
                      if any(word in blob for word in group)
                      and any(word in item["visible"] for word in group)]
            if groups:
                scored.append((len(groups), item))
        if not scored:
            return []
        best = max(score for score, _item in scored)
        chosen = []
        seen = set()
        for score, item in scored:
            if score != best or item["path"] in seen:
                continue
            seen.add(item["path"])
            item = dict(item, needle="vehicle")
            if _setting_ok(item, blob, slot_setting):
                chosen.append(item)
        return _order_refs(chosen, k, cfg, date, "vehicle", rotate)

    specific = [item for item in rows if not item["generic"]]
    hits = []
    for needle in _place_needles(blob):
        scene_ids = {item["scene_id"] for item in specific if needle in item["place_text"]}
        if scene_ids:
            hits.append((len(needle), len(scene_ids), needle))
    if not hits:
        return []
    # A longer outdoor name must not hide a shorter indoor frame that passes the setting check.
    qualified = []
    for length, df, needle in hits:
        images = [item for item in specific if needle in item["place_text"] and _setting_ok(item, blob, slot_setting)]
        if images:
            qualified.append((length, df, needle, images))
    if not qualified:
        return []
    best_len = max(item[0] for item in qualified)
    longest = [item for item in qualified if item[0] == best_len]
    raw_loc = str(slot.get("location") or "").strip()
    if raw_loc in ("未知", "待定", "未确定", "不详"):
        raw_loc = ""
    loc_text = _scrub_place_text(raw_loc)
    in_location = [item for item in longest if item[2] in loc_text]
    pool = in_location or longest
    rare = min(item[1] for item in pool)
    needles = [item[2] for item in pool if item[1] == rare]
    chosen = []
    seen = set()
    for _length, _df, needle, images in qualified:
        if needle not in needles:
            continue
        for item in images:
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            chosen.append(dict(item, needle=needle))
    # Same-resort travel photos whose names share a long prefix but not the full needle
    # (太舞雪景 vs 太舞滑雪小镇). Six characters keeps 海棠湾 from pulling 亚龙湾.
    if any(len(needle) >= 6 for needle in needles):
        for item in specific:
            if item["path"] in seen or not item["travel"] or not _setting_ok(item, blob, slot_setting):
                continue
            if any(_shared_prefix_len(needle, item["place_text"]) >= 6 for needle in needles):
                seen.add(item["path"])
                chosen.append(dict(item, needle=needles[0]))
    if not chosen:
        return []
    return _order_refs(chosen, k, cfg, date, needles[0], rotate)

