"""小甜素材本地库：抓到的文字/图片/视频/音频统一入 SQLite（WAL）+ 媒体文件按 sha256 去重。

- DB:    ~/xiaotian-assets/materials/materials.db   (env XIAOTIAN_MATERIALS_DIR 可覆盖根目录)
- Media: <root>/media/<platform>/<yyyymm>/<sha256>.<ext>
- 有效期 30 天：expires_at = fetched_at + 30d；同一 source_url 再次入库 = 刷新并替换旧版本。
- 每次 add_item 后自动 purge_expired()（只删过期行 + 无引用文件，开销很小）。
本模块不联网、不抓取、不登录，只负责存储。
CLI: python -m src.material_store stats | purge | search <q> [--platform P] [--since ISO] | recent [n]
"""
import os, json, sqlite3, hashlib, shutil, time, pathlib, mimetypes, datetime as _dt

PLATFORMS = ("xiaohongshu", "douyin", "wechat_mp", "weibo", "bilibili", "bda_site")
TTL_DAYS = 30
TTL = TTL_DAYS * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, source_url TEXT NOT NULL UNIQUE,
  author TEXT, title TEXT, text TEXT, tags TEXT DEFAULT '[]',
  published_at REAL, fetched_at REAL NOT NULL, expires_at REAL NOT NULL,
  content_hash TEXT, lang TEXT, extra TEXT DEFAULT '{}', embedding BLOB);
CREATE INDEX IF NOT EXISTS ix_items_exp ON items(expires_at);
CREATE INDEX IF NOT EXISTS ix_items_plat ON items(platform, fetched_at);
CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('image','video','audio')), local_path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE, bytes INTEGER, width INTEGER, height INTEGER, duration REAL, source_url TEXT);
CREATE INDEX IF NOT EXISTS ix_media_item ON media(item_id);
CREATE INDEX IF NOT EXISTS ix_media_url ON media(source_url);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(title, text, tags, content='items', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid,title,text,tags) VALUES(new.id,new.title,new.text,new.tags); END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts,rowid,title,text,tags) VALUES('delete',old.id,old.title,old.text,old.tags); END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF title,text,tags ON items BEGIN
  INSERT INTO items_fts(items_fts,rowid,title,text,tags) VALUES('delete',old.id,old.title,old.text,old.tags);
  INSERT INTO items_fts(rowid,title,text,tags) VALUES(new.id,new.title,new.text,new.tags); END;
"""


def root():
    return pathlib.Path(os.environ.get("XIAOTIAN_MATERIALS_DIR") or "~/xiaotian-assets/materials").expanduser()


def _ts(v):
    if v is None or isinstance(v, (int, float)):
        return v
    return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _kind(path):
    m = mimetypes.guess_type(str(path))[0] or ""
    return "video" if m.startswith("video") else "audio" if m.startswith("audio") else "image"


class Store:
    def __init__(self, base=None):
        self.root = pathlib.Path(base).expanduser() if base else root()
        (self.root / "media").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "materials.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ---------- write ----------
    def add_item(self, platform, source_url, *, author=None, title=None, text=None, tags=(),
                 published_at=None, fetched_at=None, lang=None, extra=None, media=(),
                 move=False, embed_fn=None, now=None):
        """media: [path | {"path", "kind"?, "source_url"?, "width"?, "height"?, "duration"?}].
        同 URL 已存在 → 刷新：更新字段、重置 30 天有效期、旧媒体替换为新媒体（旧文件无引用即删除）。
        返回 item id。"""
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform {platform}")
        now = now if now is not None else time.time()
        fetched = _ts(fetched_at) or now
        tags = list(tags or [])
        chash = hashlib.sha256(json.dumps([title, text, tags], ensure_ascii=False).encode()).hexdigest()
        emb = None
        if embed_fn:
            try:
                import array
                emb = array.array("f", embed_fn(f"{title or ''}\n{text or ''}")).tobytes()
            except Exception:
                emb = None
        row = (platform, author, title, text, json.dumps(tags, ensure_ascii=False), _ts(published_at),
               fetched, fetched + TTL, chash, lang, json.dumps(extra or {}, ensure_ascii=False), emb)
        old_files = []
        with self.db:
            cur = self.db.execute("SELECT id FROM items WHERE source_url=?", (source_url,)).fetchone()
            if cur:
                iid = cur["id"]
                self.db.execute("""UPDATE items SET platform=?,author=?,title=?,text=?,tags=?,published_at=?,
                    fetched_at=?,expires_at=?,content_hash=?,lang=?,extra=?,embedding=? WHERE id=?""", row + (iid,))
                old_files = [r["local_path"] for r in self.db.execute("SELECT local_path FROM media WHERE item_id=?", (iid,))]
                self.db.execute("DELETE FROM media WHERE item_id=?", (iid,))
            else:
                iid = self.db.execute("""INSERT INTO items(platform,author,title,text,tags,published_at,fetched_at,
                    expires_at,content_hash,lang,extra,embedding,source_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                      row + (source_url,)).lastrowid
            for m in media or ():
                self._add_media(iid, m if isinstance(m, dict) else {"path": m}, platform, fetched, move)
        self._unlink_unreferenced(old_files)
        self.purge_expired(now=now)
        return iid

    def _add_media(self, iid, m, platform, fetched, move):
        src = pathlib.Path(m["path"]).expanduser()
        murl = m.get("source_url")
        sha = _sha(src)
        ex = self.db.execute("SELECT id,item_id FROM media WHERE sha256=?", (sha,)).fetchone()
        if ex:  # sha 去重：文件已存在；若属于其他条目则只记引用，不重复存
            if ex["item_id"] != iid:
                r = self.db.execute("SELECT extra FROM items WHERE id=?", (iid,)).fetchone()
                e = json.loads(r["extra"] or "{}"); e.setdefault("shared_media", []).append(sha)
                self.db.execute("UPDATE items SET extra=? WHERE id=?", (json.dumps(e, ensure_ascii=False), iid))
            if move and src.exists() and str(src) != self._path_of(sha):
                src.unlink()
            return
        ext = src.suffix.lower() or ".bin"
        ym = _dt.datetime.fromtimestamp(fetched).strftime("%Y%m")
        dst = self.root / "media" / platform / ym / f"{sha}{ext}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            (shutil.move if move else shutil.copy2)(str(src), str(dst))
        elif move:
            src.unlink()
        self.db.execute("""INSERT INTO media(item_id,kind,local_path,sha256,bytes,width,height,duration,source_url)
            VALUES(?,?,?,?,?,?,?,?,?)""", (iid, m.get("kind") or _kind(src), str(dst), sha, dst.stat().st_size,
                                          m.get("width"), m.get("height"), m.get("duration"), murl))

    def _path_of(self, sha):
        r = self.db.execute("SELECT local_path FROM media WHERE sha256=?", (sha,)).fetchone()
        return r["local_path"] if r else None

    def _unlink_unreferenced(self, paths):
        n = 0
        for p in set(paths):
            if not self.db.execute("SELECT 1 FROM media WHERE local_path=?", (p,)).fetchone():
                try:
                    os.unlink(p); n += 1
                except FileNotFoundError:
                    pass
        return n

    def purge_expired(self, now=None):
        """删除 expires_at < now 的条目（媒体行级联删除）及不再被引用的媒体文件。"""
        now = now if now is not None else time.time()
        with self.db:
            paths = [r[0] for r in self.db.execute(
                "SELECT m.local_path FROM media m JOIN items i ON i.id=m.item_id WHERE i.expires_at<?", (now,))]
            n = self.db.execute("DELETE FROM items WHERE expires_at<?", (now,)).rowcount
        return {"items": n, "files": self._unlink_unreferenced(paths)}

    # ---------- read ----------
    def _items(self, rows):
        out = []
        for r in rows:
            d = dict(r); d.pop("embedding", None)
            d["tags"] = json.loads(d["tags"] or "[]"); d["extra"] = json.loads(d["extra"] or "{}")
            d["media"] = [dict(m) for m in self.db.execute("SELECT * FROM media WHERE item_id=?", (d["id"],))]
            out.append(d)
        return out

    def search(self, query, platform=None, since=None, limit=20):
        q, args = "", []
        if platform:
            q += " AND i.platform=?"; args.append(platform)
        if since is not None:
            q += " AND i.fetched_at>=?"; args.append(_ts(since))
        if len(query) >= 3:  # trigram 需要 ≥3 字符
            fq = '"' + query.replace('"', '""') + '"'
            sql = f"SELECT i.* FROM items_fts f JOIN items i ON i.id=f.rowid WHERE items_fts MATCH ?{q} ORDER BY rank LIMIT ?"
            return self._items(self.db.execute(sql, [fq] + args + [limit]))
        like = f"%{query}%"
        sql = f"SELECT i.* FROM items i WHERE (i.title LIKE ? OR i.text LIKE ? OR i.tags LIKE ?){q} ORDER BY i.fetched_at DESC LIMIT ?"
        return self._items(self.db.execute(sql, [like] * 3 + args + [limit]))

    def recent(self, n=10, platform=None):
        if platform:
            rows = self.db.execute("SELECT * FROM items WHERE platform=? ORDER BY fetched_at DESC LIMIT ?", (platform, n))
        else:
            rows = self.db.execute("SELECT * FROM items ORDER BY fetched_at DESC LIMIT ?", (n,))
        return self._items(rows)

    def get(self, item_id):
        """按 id 取一条（含 media / extra）；不存在返回 None。"""
        if item_id is None:
            return None
        try:
            iid = int(item_id)
        except (TypeError, ValueError):
            return None
        row = self.db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        return self._items([row])[0] if row else None

    def get_by_url(self, source_url):
        if not source_url:
            return None
        row = self.db.execute("SELECT * FROM items WHERE source_url=?", (source_url,)).fetchone()
        return self._items([row])[0] if row else None

    def stats(self):
        q = lambda s: self.db.execute(s).fetchall()
        return {"root": str(self.root),
                "items": q("SELECT COUNT(*) FROM items")[0][0],
                "by_platform": {r[0]: r[1] for r in q("SELECT platform,COUNT(*) FROM items GROUP BY platform")},
                "media": {r[0]: {"n": r[1], "bytes": r[2]} for r in q("SELECT kind,COUNT(*),SUM(bytes) FROM media GROUP BY kind")},
                "oldest_expires_at": q("SELECT MIN(expires_at) FROM items")[0][0]}


_default = None
def _s():
    global _default
    if _default is None:
        _default = Store()
    return _default

def add_item(*a, **k): return _s().add_item(*a, **k)
def search(*a, **k): return _s().search(*a, **k)
def recent(*a, **k): return _s().recent(*a, **k)
def get(*a, **k): return _s().get(*a, **k)
def get_by_url(*a, **k): return _s().get_by_url(*a, **k)
def purge_expired(*a, **k): return _s().purge_expired(*a, **k)
def stats(): return _s().stats()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m src.material_store")
    p.add_argument("cmd", choices=["stats", "purge", "search", "recent"])
    p.add_argument("q", nargs="?", default="")
    p.add_argument("--platform"); p.add_argument("--since")
    a = p.parse_args(argv)
    if a.cmd == "stats": r = stats()
    elif a.cmd == "purge": r = purge_expired()
    elif a.cmd == "recent": r = recent(int(a.q or 10), a.platform)
    else: r = search(a.q, a.platform, a.since)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
