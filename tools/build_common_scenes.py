#!/usr/bin/env python3
"""构建常用场景库（北舞校区）：宿舍/食堂/图书馆/体育场/教室/练功房。

统一来源族 + bge 场景向量近邻簇过滤（拒绝对穿帮的离群图）。无每包硬上限。

用法：
  .venv/bin/python tools/build_common_scenes.py --status
  .venv/bin/python tools/build_common_scenes.py --fetch [--packs dorm,library] [--max-candidates 40]
  .venv/bin/python tools/build_common_scenes.py --recluster   # 仅重跑簇过滤
  .venv/bin/python tools/build_common_scenes.py --dedupe-cross-pack

落盘：~/xiaotian-assets/scene_refs/common/<pack_id>/{images/,pack.json}
SOP：docs/common-scenes-sop.md
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from io import BytesIO

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common_scenes as cs

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# 簇门槛：相对质心 cosine；过低则放宽到次优但仍拒绝对离群
# 偏向更大保留集：无硬上限；候选够时尽量留几十张。向量门只剔真正离群。
MIN_COS_TO_CENTROID = float(os.environ.get("COMMON_SCENE_MIN_COS", "0.55"))
MAX_PHASH_HAMMING = int(os.environ.get("COMMON_SCENE_MAX_PHASH_HAM", "36"))
MIN_KEEP = int(os.environ.get("COMMON_SCENE_MIN_KEEP", "20"))
# 相对候选数的目标保留比例（在通过统一来源族之后）
KEEP_FRAC = float(os.environ.get("COMMON_SCENE_KEEP_FRAC", "0.75"))


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def download_one(url, referer=None):
    try:
        headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=14) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "")
        if len(data) < 5000 or len(data) > 15_000_000:
            return None, "size"
        if "html" in (ct or "").lower():
            return None, "html"
        return data, "ok"
    except Exception as e:
        return None, str(e)[:80]


def bing_search(query, first=1, count=35):
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/images/search?q={q}&form=HDRSC2&first={first}&qft=+filterui:photo-photo"
    raw, _ = http_get(url)
    text = raw.decode("utf-8", "ignore")
    urls = [html.unescape(u) for u in re.findall(r"&quot;murl&quot;:&quot;(.*?)&quot;", text)]
    out, seen = [], set()
    for u in urls:
        if not u.startswith("http"):
            continue
        low = u.lower()
        if any(x in low for x in (".svg", "logo", "sprite", "favicon", "1x1", "pixel.gif")):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:count]


def wiki_search(query, limit=12):
    api = (
        "https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch="
        + urllib.parse.quote(query)
        + f"&gsrnamespace=6&gsrlimit={limit}&prop=imageinfo"
        "&iiprop=url|size|mime&iiurlwidth=1280&format=json"
    )
    raw, _ = http_get(api)
    d = json.loads(raw.decode())
    out = []
    for p in (d.get("query") or {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        mime = ii.get("mime") or ""
        if not mime.startswith("image/") or "svg" in mime:
            continue
        u = ii.get("thumburl") or ii.get("url")
        if u:
            out.append(u.split("?")[0] if "upload.wikimedia.org" in u else u)
    return out


def load_embed_model():
    try:
        import outfit
        return outfit._load_model()
    except Exception as e:
        print("embed model fail:", e, flush=True)
        return None


def embed_texts(model, texts):
    import numpy as np
    if not model:
        return None
    v = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(v, dtype="float32")


def phash_hex(im):
    import imagehash
    return str(imagehash.phash(im))


def hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 999
    # imagehash hex
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return sum(c1 != c2 for c1, c2 in zip(a, b))


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def ensure_pack_dirs(pack_id: str):
    root = cs.pack_dir(pack_id)
    img = root / "images"
    rej = root / "rejected"
    img.mkdir(parents=True, exist_ok=True)
    rej.mkdir(parents=True, exist_ok=True)
    return root, img, rej


def source_family_of(url: str, preferred: str | None = None) -> str:
    if "wikimedia.org" in url or "wikipedia.org" in url:
        return "wikimedia"
    if "bing.com" in url or "bing.net" in url or "mm.bing" in url:
        return "bing"
    if "zhimg.com" in url or "zhihu" in url:
        return "zhihu"
    host = urllib.parse.urlparse(url).netloc.lower()
    if preferred and preferred in host:
        return preferred
    return host.split(":")[0] or "unknown"


def collect_candidates(pack_id: str, max_candidates: int = 40):
    meta = cs.PACKS[pack_id]
    # 统一来源族：所有 query 强制带校名
    campus_qs = []
    for q in meta["queries"]:
        if cs.CAMPUS_NAME not in q and cs.CAMPUS_NAME_EN not in q and "北舞" not in q and "BDA" not in q:
            q = f"{cs.CAMPUS_NAME} {q}"
        campus_qs.append(q)
    # 额外空景后缀（扩召回；仍强制校名）
    extra = [
        f"{cs.CAMPUS_NAME} {meta['zh']} 空景 无人",
        f"{cs.CAMPUS_NAME} {meta['zh']} 实拍 校园",
        f"{cs.CAMPUS_NAME_EN} {meta['zh']} empty no people",
        f"{cs.CAMPUS_NAME_EN} {meta['zh']} campus interior photo",
        f"北舞 {meta['zh']} 实景",
        f"北舞 {meta['zh']} 校园 空景",
    ]
    queries = list(dict.fromkeys(campus_qs + extra))
    # 先 wiki（公开可复用），再 bing 多页；同族优先
    scored_urls = []  # (family, url, query)
    # Bing 分页深度随目标候选放大（约每页 35）
    n_pages = max(2, min(8, (max_candidates // 25) + 2))
    page_starts = [1 + i * 35 for i in range(n_pages)]
    for q in queries:
        try:
            for u in wiki_search(q, limit=10):
                scored_urls.append(("wikimedia", u, q))
        except Exception as e:
            print(f"  [wiki] {pack_id} {e}", flush=True)
        time.sleep(random.uniform(0.3, 0.8))
        for first in page_starts:
            try:
                for u in bing_search(q, first=first, count=35):
                    scored_urls.append(("bing", u, q))
            except Exception as e:
                print(f"  [bing] {pack_id} first={first} {e}", flush=True)
            time.sleep(random.uniform(0.7, 1.4))
            if len(scored_urls) >= max_candidates * 4:
                break
        if len(scored_urls) >= max_candidates * 4:
            break
    # 去重 URL
    seen, uniq = set(), []
    for fam, u, q in scored_urls:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((fam, u, q))
    return uniq[: max_candidates * 3], queries


def save_candidate(img_dir: pathlib.Path, data: bytes, sha: str):
    from PIL import Image
    try:
        im = Image.open(BytesIO(data))
        im.load()
        im = im.convert("RGB")
        w, h = im.size
        if min(w, h) < 360:
            return None, "too-small", None, None
        if max(w, h) > 2400:
            im.thumbnail((1600, 1600), Image.LANCZOS)
            w, h = im.size
        ph = phash_hex(im)
    except Exception as e:
        return None, f"decode:{e}", None, None
    dest = img_dir / f"{sha[:16]}.jpg"
    if dest.exists():
        return dest, "exists", ph, (w, h)
    im.save(dest, "JPEG", quality=88, optimize=True)
    return dest, "ok", ph, (w, h)


def cluster_filter(records: list[dict], model, min_cos=MIN_COS_TO_CENTROID, canonical_query: str | None = None):
    """统一来源族 + bge 场景向量近邻；pHash 只剔极端离群。

    records: [{path, query, family, phash}]
    返回 kept, rejected, centroid(list|None), primary_family
    """
    import numpy as np
    from collections import Counter
    if not records:
        return [], [], None, None

    fam_counts = Counter(r["family"] for r in records)
    # 主来源族：张数最多；wikimedia 若≥MIN_KEEP 可优先（公开可复用）
    primary = fam_counts.most_common(1)[0][0]
    if fam_counts.get("wikimedia", 0) >= MIN_KEEP:
        primary = "wikimedia"
    same = [r for r in records if r["family"] == primary]
    if len(same) < MIN_KEEP and len(records) >= MIN_KEEP:
        # 单族不够时退回最大族（已是），不再 mixed，保证来源统一
        pass

    texts = [r.get("query") or cs.CAMPUS_NAME for r in same]
    embs = embed_texts(model, texts) if model else None
    if embs is None:
        kept, rejected = list(same), [dict(r, reject_reason=f"source_family!={primary}") for r in records if r["family"] != primary]
        return kept, rejected, None, primary

    # 质心：优先相对「规范查询」；否则同族 query 均值
    if canonical_query:
        can = embed_texts(model, [canonical_query])[0]
    else:
        can = embs.mean(axis=0)
        nrm = float(np.linalg.norm(can)) or 1.0
        can = can / nrm
    cos = embs @ can
    thr = min_cos
    # 若规范查询与候选整体偏斜，自适应到分位，但仍拒绝对尾部
    if float(np.quantile(cos, 0.75)) < min_cos:
        thr = max(0.60, float(np.quantile(cos, 0.40)))

    scored = [dict(r, cos=float(c)) for r, c in zip(same, cos)]
    scored.sort(key=lambda r: -r["cos"])

    # 目标保留数：无上限；至少 MIN_KEEP（候选允许时），或 KEEP_FRAC * |same|
    target = max(MIN_KEEP, int(round(len(scored) * KEEP_FRAC)))
    target = min(len(scored), target)  # 不能超过候选
    # cosine 门：先收 thr 以上；不足则按分数补齐到 target（只丢真正尾部）
    prelim = [r for r in scored if r["cos"] >= thr]
    if len(prelim) < target:
        prelim = scored[:target]
    # 仍希望丢掉明显离群：低于 max(thr-0.15, 分位 15%) 的不要硬塞
    if scored:
        floor = max(thr - 0.15, float(__import__("numpy").quantile([r["cos"] for r in scored], 0.15)))
        tight = [r for r in prelim if r["cos"] >= floor]
        if len(tight) >= min(MIN_KEEP, len(scored)):
            prelim = tight
        # 若 tight 太少但 scored 多，保证至少 target
        if len(prelim) < min(target, len(scored)):
            prelim = scored[:target]

    # pHash：只剔极端离群；若会跌破 target/MIN_KEEP 则完全跳过
    phs = [r.get("phash") for r in prelim if r.get("phash")]
    med_ph = None
    if len(phs) >= 5:
        best, best_s = phs[0], 1e9
        for cand in phs:
            s = sum(hamming(cand, o) for o in phs) / max(1, len(phs))
            if s < best_s:
                best, best_s = cand, s
        med_ph = best

    kept, rejected = [], []
    for r in prelim:
        if med_ph and r.get("phash") and hamming(r["phash"], med_ph) > MAX_PHASH_HAMMING:
            rejected.append(dict(r, reject_reason=f"phash_hamming>{MAX_PHASH_HAMMING}"))
        else:
            kept.append(r)
    if len(kept) < min(target, len(scored)):
        # 放宽：忽略 pHash，按 cosine 取满 target
        kept = scored[: max(target, min(MIN_KEEP, len(scored)))]
        rejected = [r for r in scored if r["path"] not in {x["path"] for x in kept}]

    # cosine 未入 prelim 的同族 → reject
    kept_paths = {r["path"] for r in kept}
    for r in scored:
        if r["path"] not in kept_paths and r not in rejected:
            rejected.append(dict(r, reject_reason=r.get("reject_reason") or f"cos<{thr:.3f}"))
    for r in records:
        if r["family"] != primary:
            rejected.append(dict(r, reject_reason=f"source_family!={primary}"))
    return kept, rejected, can.tolist(), primary


def write_pack_meta(pack_id, kept, rejected, centroid, family, queries, notes=None):
    root, img_dir, rej_dir = ensure_pack_dirs(pack_id)
    # move rejected files
    kept_names = []
    for r in kept:
        p = pathlib.Path(r["path"])
        kept_names.append(p.name)
    for r in rejected:
        p = pathlib.Path(r["path"])
        if p.is_file() and p.parent == img_dir:
            dest = rej_dir / p.name
            try:
                p.replace(dest)
                r["path"] = str(dest)
            except Exception:
                pass
    meta = {
        "pack_id": pack_id,
        "zh": cs.PACKS[pack_id]["zh"],
        "campus": cs.CAMPUS_NAME,
        "campus_en": cs.CAMPUS_NAME_EN,
        "source_family": family,
        "queries": queries,
        "min_cos": MIN_COS_TO_CENTROID,
        "max_phash_hamming": MAX_PHASH_HAMMING,
        "centroid_dim": len(centroid) if centroid else 0,
        "centroid": centroid,
        "kept_files": kept_names,
        "n_kept": len(kept_names),
        "n_rejected": len(rejected),
        "rejected": [
            {"file": pathlib.Path(r["path"]).name, "reason": r.get("reject_reason"), "cos": r.get("cos")}
            for r in rejected[:80]
        ],
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": notes or "Unified campus/source family; bge query-emb near-neighbor cluster + pHash gate.",
    }
    (root / "pack.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def fetch_pack(pack_id: str, model, max_candidates: int = 40):
    print(f"== fetch {pack_id} ({cs.PACKS[pack_id]['zh']}) ==", flush=True)
    root, img_dir, rej_dir = ensure_pack_dirs(pack_id)
    cand_urls, queries = collect_candidates(pack_id, max_candidates=max_candidates)
    print(f"  candidates urls: {len(cand_urls)}", flush=True)
    records = []
    seen_sha = set()
    for fam, url, q in cand_urls:
        if len(records) >= max_candidates:
            break
        data, why = download_one(url, referer="https://www.bing.com/" if fam == "bing" else "https://commons.wikimedia.org/")
        if not data:
            continue
        sha = sha256_bytes(data)
        if sha in seen_sha:
            continue
        dest, why2, ph, wh = save_candidate(img_dir, data, sha)
        if not dest or why2 not in ("ok", "exists"):
            continue
        seen_sha.add(sha)
        records.append({
            "path": str(dest),
            "query": q,
            "family": fam,
            "phash": ph,
            "url": url,
            "sha256": sha,
            "size": wh,
        })
        print(f"  + {fam} {dest.name} :: {q[:40]}", flush=True)
        time.sleep(random.uniform(0.25, 0.7))
    # 合并已有 images/（top-up 不丢旧图）
    have_names = {pathlib.Path(r["path"]).name for r in records}
    old_cand = {}
    old_path = root / "candidates.jsonl"
    if old_path.is_file():
        for line in old_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                old_cand[o.get("file")] = o
            except Exception:
                pass
    from PIL import Image as _Image
    for pth in list(img_dir.iterdir()) if img_dir.is_dir() else []:
        if pth.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        if pth.name in have_names:
            continue
        meta = old_cand.get(pth.name) or {}
        try:
            ph = meta.get("phash") or phash_hex(_Image.open(pth).convert("RGB"))
        except Exception:
            ph = meta.get("phash")
        records.append({
            "path": str(pth),
            "query": meta.get("query") or (queries[0] if queries else f"{cs.CAMPUS_NAME} {cs.PACKS[pack_id]['zh']}"),
            "family": meta.get("family") or "bing",
            "phash": ph,
            "url": meta.get("url"),
            "sha256": meta.get("sha256"),
        })
        have_names.add(pth.name)
    # 持久化候选元数据，供 --recluster 保留 family/query
    cand_path = root / "candidates.jsonl"
    with cand_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({
                "file": pathlib.Path(r["path"]).name,
                "query": r.get("query"),
                "family": r.get("family"),
                "url": r.get("url"),
                "sha256": r.get("sha256"),
                "phash": r.get("phash"),
            }, ensure_ascii=False) + "\n")
    canon = queries[0] if queries else f"{cs.CAMPUS_NAME} {cs.PACKS[pack_id]['zh']}"
    kept, rejected, centroid, family = cluster_filter(records, model, canonical_query=canon)
    meta = write_pack_meta(pack_id, kept, rejected, centroid, family, queries)
    print(f"  kept={meta['n_kept']} rejected={meta['n_rejected']} family={family}", flush=True)
    return meta


def recluster_pack(pack_id: str, model):
    root, img_dir, rej_dir = ensure_pack_dirs(pack_id)
    files = []
    for d in (img_dir, rej_dir):
        if d.is_dir():
            files.extend([p for p in d.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])
    old = cs.load_pack_meta(pack_id) or {}
    qs = old.get("queries") or list(cs.PACKS[pack_id]["queries"])
    canon = qs[0] if qs else f"{cs.CAMPUS_NAME} {cs.PACKS[pack_id]['zh']}"
    # candidates.jsonl 保留 per-file family/query
    by_name = {}
    cand_path = root / "candidates.jsonl"
    if cand_path.is_file():
        for line in cand_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                by_name[o.get("file")] = o
            except Exception:
                pass
    records = []
    from PIL import Image
    for p in files:
        if p.parent == rej_dir:
            dest = img_dir / p.name
            try:
                p.replace(dest)
                p = dest
            except Exception:
                pass
        try:
            im = Image.open(p).convert("RGB")
            ph = phash_hex(im)
        except Exception:
            continue
        meta = by_name.get(p.name) or {}
        fam = meta.get("family") or old.get("source_family") or "bing"
        if isinstance(fam, str) and fam.startswith("mixed:"):
            fam = fam.split(":", 1)[1].split("+")[0]
        records.append({
            "path": str(p),
            "query": meta.get("query") or canon,
            "family": fam,
            "phash": meta.get("phash") or ph,
            "url": meta.get("url"),
            "sha256": meta.get("sha256"),
        })
    kept, rejected, centroid, family = cluster_filter(records, model, canonical_query=canon)
    return write_pack_meta(pack_id, kept, rejected, centroid, family, qs)


def _file_sha256(path: pathlib.Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def cross_pack_dedupe(model=None, packs: list[str] | None = None):
    """跨 pack 排他：同一 sha256 / 同名文件只保留在匹配最好的一个 pack。

    规则（写入 SOP）：
    1) 按文件内容 sha256 建全局索引；同 hash 出现在多个 pack 时为冲突。
    2) 冲突时优先保留 cosine(query_emb, pack_canonical_query) 最高者；
       若无 embed，则保留 pack.json 里已有 cos，再否则保留 n_kept 更大的 pack，
       最后按 pack_id 字典序稳定打破平局。
    3) 落败侧：images/ → rejected/，并从 kept_files 剔除；candidates.jsonl 保留痕迹。
    """
    import numpy as np
    packs = packs or list(cs.PACKS.keys())
    # load per-pack kept records
    pack_kept: dict[str, list[dict]] = {}
    pack_meta: dict[str, dict] = {}
    canon_emb: dict[str, object] = {}
    for pid in packs:
        meta = cs.load_pack_meta(pid) or {}
        pack_meta[pid] = meta
        root, img_dir, rej_dir = ensure_pack_dirs(pid)
        kept_names = set(meta.get("kept_files") or [])
        by_name = {}
        cand_path = root / "candidates.jsonl"
        if cand_path.is_file():
            for line in cand_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                    by_name[o.get("file")] = o
                except Exception:
                    pass
        qs = meta.get("queries") or list(cs.PACKS[pid]["queries"])
        canon = qs[0] if qs else f"{cs.CAMPUS_NAME} {cs.PACKS[pid]['zh']}"
        if model is not None and pid not in canon_emb:
            try:
                canon_emb[pid] = embed_texts(model, [canon])[0]
            except Exception:
                canon_emb[pid] = None
        records = []
        if not img_dir.is_dir():
            pack_kept[pid] = []
            continue
        for pth in sorted(img_dir.iterdir()):
            if pth.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            if kept_names and pth.name not in kept_names:
                continue
            cmeta = by_name.get(pth.name) or {}
            sha = cmeta.get("sha256") or _file_sha256(pth)
            q = cmeta.get("query") or canon
            cos = None
            # prefer stored cos from rejected list? usually not for kept
            records.append({
                "path": str(pth),
                "file": pth.name,
                "sha256": sha,
                "query": q,
                "family": cmeta.get("family") or meta.get("source_family") or "bing",
                "phash": cmeta.get("phash"),
                "url": cmeta.get("url"),
                "pack_id": pid,
                "canon": canon,
                "cos": cos,
            })
        # score vs pack canonical
        if model is not None and records and canon_emb.get(pid) is not None:
            texts = [r["query"] for r in records]
            embs = embed_texts(model, texts)
            if embs is not None:
                can = canon_emb[pid]
                scores = embs @ can
                for r, s in zip(records, scores):
                    r["cos"] = float(s)
        pack_kept[pid] = records

    # index sha -> list of (pid, record)
    by_sha: dict[str, list[tuple[str, dict]]] = {}
    for pid, recs in pack_kept.items():
        for r in recs:
            sha = r.get("sha256")
            if not sha:
                continue
            by_sha.setdefault(sha, []).append((pid, r))

    conflicts = {sha: hits for sha, hits in by_sha.items() if len(hits) > 1}
    print(f"cross-pack: {len(conflicts)} shared hashes across packs", flush=True)
    moved = []  # (sha, winner, losers)
    losers_by_pack: dict[str, list[dict]] = {pid: [] for pid in packs}
    for sha, hits in conflicts.items():
        # highest cos → higher n_kept → lexicographically smallest pack_id
        scored = []
        for pid, r in hits:
            scored.append((
                r.get("cos") if r.get("cos") is not None else -1.0,
                int(pack_meta.get(pid, {}).get("n_kept") or 0),
                pid,
                r,
            ))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        win_pid = scored[0][2]
        loser_hits = [(pid, r) for pid, r in hits if pid != win_pid]
        moved.append((sha, win_pid, [p for p, _ in loser_hits]))
        for pid, r in loser_hits:
            losers_by_pack[pid].append(dict(r, reject_reason=f"cross_pack_dup_kept_in:{win_pid}"))

    # apply moves + rewrite pack meta
    for pid in packs:
        losers = losers_by_pack.get(pid) or []
        if not losers and not conflicts:
            continue
        loser_files = {r["file"] for r in losers}
        root, img_dir, rej_dir = ensure_pack_dirs(pid)
        # move loser files
        for r in losers:
            src = pathlib.Path(r["path"])
            if src.is_file() and src.parent == img_dir:
                dest = rej_dir / src.name
                try:
                    src.replace(dest)
                    r["path"] = str(dest)
                except Exception as e:
                    print(f"  move fail {pid}/{src.name}: {e}", flush=True)
        # rebuild kept from remaining images ∩ previous kept (minus losers)
        meta = pack_meta.get(pid) or {}
        prev_kept = list(meta.get("kept_files") or [])
        if prev_kept:
            new_kept_names = [n for n in prev_kept if n not in loser_files]
        else:
            new_kept_names = [r["file"] for r in pack_kept.get(pid, []) if r["file"] not in loser_files]
        # remaining records for centroid refresh
        remain = [r for r in pack_kept.get(pid, []) if r["file"] not in loser_files]
        # update paths after moves for remain (unchanged)
        family = meta.get("source_family") or "bing"
        qs = meta.get("queries") or list(cs.PACKS[pid]["queries"])
        centroid = meta.get("centroid")
        # refresh centroid if model + remain
        if model is not None and remain:
            texts = [r.get("query") or qs[0] for r in remain]
            embs = embed_texts(model, texts)
            if embs is not None:
                can = embs.mean(axis=0)
                nrm = float(np.linalg.norm(can)) or 1.0
                centroid = (can / nrm).tolist()
        # merge rejected list
        old_rej = list(meta.get("rejected") or [])
        new_rej_entries = [
            {"file": r["file"], "reason": r.get("reject_reason"), "cos": r.get("cos")}
            for r in losers
        ]
        # write pack.json directly (preserve notes + document dedupe)
        notes = meta.get("notes") or ""
        if "cross-pack exclusivity" not in (notes or ""):
            notes = (notes + " Cross-pack exclusivity: unique sha kept in best-matching pack.").strip()
        out = {
            "pack_id": pid,
            "zh": cs.PACKS[pid]["zh"],
            "campus": cs.CAMPUS_NAME,
            "campus_en": cs.CAMPUS_NAME_EN,
            "source_family": family,
            "queries": qs,
            "min_cos": meta.get("min_cos", MIN_COS_TO_CENTROID),
            "max_phash_hamming": meta.get("max_phash_hamming", MAX_PHASH_HAMMING),
            "centroid_dim": len(centroid) if centroid else 0,
            "centroid": centroid,
            "kept_files": new_kept_names,
            "n_kept": len(new_kept_names),
            "n_rejected": int(meta.get("n_rejected") or 0) + len(losers),
            "rejected": (new_rej_entries + old_rej)[:120],
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "notes": notes,
            "cross_pack_removed": len(losers),
        }
        (root / "pack.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {pid}: removed {len(losers)} cross-dup → kept={out['n_kept']}", flush=True)

    print(f"cross-pack done: resolved {len(conflicts)} hashes", flush=True)
    return {"conflicts": len(conflicts), "moved": moved}


def status():
    m = cs.manifest()
    print(json.dumps(m, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--recluster", action="store_true")
    ap.add_argument("--dedupe-cross-pack", action="store_true",
                    help="remove duplicate sha256 across packs (keep best pack match)")
    ap.add_argument("--packs", default="", help="comma pack ids; default all")
    ap.add_argument("--max-candidates", type=int, default=48)
    args = ap.parse_args(argv)
    if args.status or not (args.fetch or args.recluster or args.dedupe_cross_pack):
        status()
        if not (args.fetch or args.recluster or args.dedupe_cross_pack):
            return 0
    packs = [p.strip() for p in args.packs.split(",") if p.strip()] or list(cs.PACKS.keys())
    model = load_embed_model()
    print("embed:", "ok" if model else "UNAVAILABLE (family+phash only)", flush=True)
    if args.dedupe_cross_pack:
        cross_pack_dedupe(model, packs=packs)
        status()
        return 0
    for pid in packs:
        if pid not in cs.PACKS:
            print("unknown pack", pid)
            continue
        if args.fetch:
            fetch_pack(pid, model, max_candidates=args.max_candidates)
        elif args.recluster:
            meta = recluster_pack(pid, model)
            print(pid, "kept", meta["n_kept"], "family", meta["source_family"])
    status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
