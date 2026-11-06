"""用 USB iPhone（WDA）更换小甜自己的微信头像（iPhone 8 Plus / iOS 16）。v3：提速版。

流程（每步计时，元素定位 + 显式短轮询，失败重试；默认不存过程截图，--debug 才存）：
  0) 微信「我」页校验账号 demo_xiaotian_wxid，并直接在「我」页头部裁当前头像比对（不进个人资料）；
     已是目标图则退出（幂等，状态 already）；--force-upload 跳过比对强制重传。
  1) 传图：AP 隔离 → JPEG(512, q85) data URL 写剪贴板（不读回，读回要 2s；失败时 Safari 里自动带核对重写），
     Safari 长按地址栏「粘贴并前往」→ 长按图片「存储到“照片”」；data: 标签页留着下次复用。
     --keep-clipboard 才备份/恢复手机剪贴板（各 ~2s/~1s）。
  2) 微信（停在「我」页）：点头部进个人资料 → 头像 → 更多 → 从手机相册选择 → 最新一张（比对）→ 完成。
  3) 校验：个人资料页「头像」Image 元素裁剪比对；可选 --delete-photo。
  --auto：调用 tools/avatar_pick.py 自选头像（小红书只读 + 无真人脸 + 人设打分），14 天限 1 次（--force 例外），
  记录 data/avatar/history.json。只改头像；不发消息、不发朋友圈、不碰 Mac 微信。
用法：/opt/homebrew/bin/python3.13 tools/avatar_iphone.py (--image PATH --yes | --auto) [--force] [--force-upload]
      [--skip-transfer] [--delete-photo] [--debug] [--done PATH]
"""
import argparse, base64, datetime, io, json, pathlib, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMG = ROOT / "data" / "avatar" / "new_avatar.png"
STEPS = ROOT / "data" / "avatar" / "iphone_steps"
DONE = ROOT / "data" / "avatar" / "iphone_done.png"
HISTORY = ROOT / "data" / "avatar" / "history.json"
WDA = "http://localhost:8100"
WDA_BUNDLE = "com.example.WebDriverAgentRunner.xctrunner"  # placeholder; override via config.json wda_bundle


def _wda_bundle():
    """Signed WebDriverAgent bundle id; config.json wda_bundle wins over the placeholder."""
    try:
        src = str(ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from config import load as _load_cfg
        val = (_load_cfg() or {}).get("wda_bundle")
        if isinstance(val, str) and val.strip():
            return val.strip()
    except Exception:
        pass
    return WDA_BUNDLE

SAFARI, WECHAT, PHOTOS = "com.apple.mobilesafari", "com.tencent.xin", "com.apple.mobileslideshow"
ACCOUNT_ID = "demo_xiaotian_wxid"
B, C, T, I = "XCUIElementTypeButton", "XCUIElementTypeCell", "XCUIElementTypeTextField", "XCUIElementTypeImage"
MI = "XCUIElementTypeMenuItem"
SAME_THRESH = 28.0   # 32x32 RGB 平均绝对差，低于此视为同一张图
POLL = 0.12          # 轮询间隔（元素查询本身 ~0.2-0.4s）
MIN_DAYS = 14        # 自动换头像最小间隔
# 「我」页头像相对 "WeChat ID: …" 文本的位置（pt，2026-10-24 实测 ID=(112,123)，头像≈(28,84,64,64)）
ME_AVATAR = (-84, -39, 64, 64)


class AvatarError(RuntimeError): pass


def data_url(path, size=512, quality=85):
    """头像图 → 小 JPEG data URL（640 q90 ≈85K 字符 → 512 q85 ≈45K）。"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if max(im.size) > size: im = im.resize((size, size) if im.width == im.height else
                                           (size, round(im.height * size / im.width)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def img_diff(a, b, n=32):
    """两张 PIL 图（中心正方形裁剪后）缩到 n×n 的平均绝对差（0..255）。"""
    def norm(im):
        im = im.convert("RGB"); w, h = im.size; m = min(w, h)
        return im.crop(((w - m) // 2, (h - m) // 2, (w + m) // 2, (h + m) // 2)).resize((n, n))
    pa, pb = norm(a).tobytes(), norm(b).tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / (n * n * 3)


_SID = {}
def _post(path, body, base=WDA, opener=urllib.request.urlopen):
    """复用 sessionId（只在第一次或出错时取 /status）。"""
    for attempt in (0, 1):
        if attempt or base not in _SID: _SID[base] = json.load(opener(base + "/status"))["sessionId"]
        req = urllib.request.Request(f"{base}/session/{_SID[base]}{path}", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try: return json.load(opener(req))
        except Exception:
            if attempt: raise


def wait_until(fn, timeout=8.0, interval=POLL, sleep=time.sleep, clock=time.monotonic):
    end = clock() + timeout
    while True:
        v = fn()
        if v: return v
        if clock() >= end: return None
        sleep(interval)


def get_pasteboard(post=_post):
    return post("/wda/getPasteboard", {"contentType": "plaintext"}).get("value") or ""


def set_pasteboard(text, post=_post, sleep=time.sleep, raw_b64=False, verify=True, activate=True):
    """WDA 必须在前台才能写剪贴板（后台静默失败），写后读回核对，最多 3 次。activate=False 表示调用方已切到前台。"""
    content = text if raw_b64 else base64.b64encode(text.encode()).decode()
    for i in range(3):
        if activate or i: post("/wda/apps/activate", {"bundleId": _wda_bundle()}); sleep(0.6 + 0.5 * i)
        post("/wda/setPasteboard", {"content": content, "contentType": "plaintext"})
        if not verify or get_pasteboard(post): return
    raise AvatarError("setPasteboard 后剪贴板仍为空")


# ---------------- 历史 / 频率限制 ----------------
def load_history(path=HISTORY):
    try: return json.loads(pathlib.Path(path).read_text())
    except Exception: return []


def append_history(entry, path=HISTORY):
    h = load_history(path); h.append(entry); p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(h, ensure_ascii=False, indent=1)); return h


def rate_limited(now=None, path=HISTORY, days=MIN_DAYS):
    """最近一次真实更换（status=changed 且非 test）距今 < days 天 → 返回下次可换时间（ISO），否则 None。"""
    now = now or datetime.datetime.now()
    last = [e for e in load_history(path) if e.get("status") == "changed" and not e.get("test")]
    if not last: return None
    t = datetime.datetime.fromisoformat(last[-1]["ts"]); nxt = t + datetime.timedelta(days=days)
    return nxt.isoformat(timespec="minutes") if now < nxt else None


class Runner:
    def __init__(self, s, shot_dir=STEPS, sleep=time.sleep, post=_post, clock=time.monotonic, log=print, debug=False, keep_clipboard=False):
        self.s, self.dir, self.sleep, self.post, self.clock, self.log = s, pathlib.Path(shot_dir), sleep, post, clock, log
        self.debug, self.shots, self.timings, self._scale = debug, [], [], None
        self.keep_clipboard = keep_clipboard

    # ---------- 基础 ----------
    def step(self, name):
        r = self
        class _T:
            def __enter__(s_): s_.t = r.clock()
            def __exit__(s_, *exc):
                dt = r.clock() - s_.t; r.timings.append((name, round(dt, 1)))
                r.log(f"[{dt:5.1f}s] {name}{' (失败)' if exc[0] else ''}")
        return _T()

    def until(self, fn, timeout=8.0):
        return wait_until(fn, timeout, sleep=self.sleep, clock=self.clock)

    def shot(self, name, force=False):
        """取截图（比对用）；只有 debug/force 才落盘。"""
        im = self.s.screenshot()
        if self.debug or force:
            self.dir.mkdir(parents=True, exist_ok=True); p = self.dir / f"{name}.png"; im.save(str(p)); self.shots.append(str(p))
        return im

    def scale(self, im):
        if self._scale is None:
            w = self.s.window_size()[0]; self._scale = im.size[0] / w if w else 1.0
        return self._scale

    def crop(self, im, b):
        k = self.scale(im)
        return im.crop((int(b.x * k), int(b.y * k), int((b.x + b.width) * k), int((b.y + b.height) * k)))

    def el(self, label, typ=None):
        return self.s(type=typ, label=label) if typ else self.s(label=label)

    def wait(self, label, typ=None, timeout=8.0):
        e = self.el(label, typ)
        return e if self.until(lambda: e.exists, timeout) else None

    def tap(self, label, typ=B, until=None, timeout=8.0, retries=2):
        """点元素；until=(label, typ) 或 callable，等到出现为止，不出现就重点。"""
        for i in range(retries + 1):
            e = self.wait(label, typ, timeout if i == 0 or until else 2)
            if not e:
                if i: return  # 之前点过，元素已消失 → 视为已推进
                raise AvatarError(f"找不到 {typ}「{label}」")
            e.tap()
            if until is None: return
            ok = until if callable(until) else (lambda u=until: self.el(*u).exists)
            if self.until(ok, timeout): return
        raise AvatarError(f"点「{label}」后没等到下一状态")

    def click_center(self, b, dy=0):
        self.s.click(int(b.x + b.width / 2), int(b.y + b.height / 2 + dy))

    # ---------- 微信导航 / 校验 ----------
    def me_tab(self):
        """切微信 → 退回根页 → 「我」Tab → 校验账号；返回 ID 文本 bounds。"""
        s = self.s; idl = self.el(f"WeChat ID: {ACCOUNT_ID}")
        s.app_activate(WECHAT)
        for _ in range(6):
            if idl.exists: return idl.bounds
            back = self.el("返回", B)
            if back.exists: back.tap(); self.sleep(0.3); continue
            if self.el("我", B).exists:
                self.tap("我", until=lambda: idl.exists or s(labelContains="WeChat ID").exists, timeout=5); break
            self.sleep(0.3)
        if not idl.exists: raise AvatarError("当前登录的不是小甜的号（或不在「我」页），停止")
        return idl.bounds

    def me_thumb(self, idb, name="05_me_tab"):
        """「我」页头部头像：按 ID 文本相对位置裁剪，无需进个人资料。"""
        im = self.shot(name); dx, dy, w, h = ME_AVATAR
        R = type("R", (), dict(x=idb.x + dx, y=idb.y + dy, width=w, height=h))
        return self.crop(im, R)

    def open_profile(self, idb=None):
        if self.el("头像", C).exists: return
        idb = idb or self.me_tab()
        for _ in range(3):
            self.click_center(idb, dy=-15)   # ID 上方就是昵称区
            if self.until(lambda: self.el("头像", C).exists, 5): return
        raise AvatarError("没进入个人资料页")

    def current_thumb(self, name):
        im = self.shot(name)
        e = self.s(type=I, label="头像")     # 个人资料页「头像」行缩略图有 label，单次查询
        if e.exists: b = e.bounds
        else:
            cell = self.el("头像", C).bounds; h = cell.height * 0.6
            b = type("R", (), dict(x=cell.x + cell.width - h - 36, y=cell.y + (cell.height - h) / 2, width=h, height=h))
        return self.crop(im, b)

    # ---------- 传图 ----------
    def transfer(self, image):
        s = self.s
        with self.step("写入 data URL 到剪贴板"):
            url = data_url(image)
            self.post("/wda/apps/activate", {"bundleId": _wda_bundle()}); self.sleep(0.6)
            if self.keep_clipboard:   # getPasteboard 每次 ~2s，默认不备份
                try: self.saved_clip = get_pasteboard(self.post)
                except Exception: self.saved_clip = None
            # 不读回核对（读回 ~2s）；若没写进去，Safari 里不会出现「粘贴并前往」，再带核对重写一次
            set_pasteboard(url, post=self.post, sleep=self.sleep, activate=False, verify=False)
        with self.step("Safari 粘贴打开"):
            s.app_activate(SAFARI)
            tf, allow = s(type=T), s(label="无线局域网与蜂窝网络")
            self.until(lambda: tf.exists or allow.exists, 8)
            if allow.exists: allow.tap(); self.sleep(0.5)
            go, paste = s(label="粘贴并前往"), s(type=MI, label="粘贴")
            if not self.until(lambda: tf.exists, 5): raise AvatarError("Safari 地址栏不见了")
            b = tf.bounds
            pasted = False
            for i in range(2):
                if b.y <= 500: break
                # 未编辑的底部地址栏：长按 →「粘贴并前往」一步到位（省掉 点击/粘贴/回车）
                s.tap_hold(int(b.x + b.width / 2), int(b.y + b.height / 2), 1.0)
                pasted = bool(self.until(lambda: go.exists, 2.5))
                if pasted: go.tap(); break
                if i == 0:      # 剪贴板可能没写进去：收起菜单 → 带核对重写 → 回 Safari 再试
                    s.click(400, 30); set_pasteboard(url, post=self.post, sleep=self.sleep)
                    s.app_activate(SAFARI); self.until(lambda: tf.exists, 5); b = tf.bounds
            if not pasted:  # 兜底：旧流程 进入编辑 → 点文本 → 粘贴 → 回车
                for _ in range(3):
                    if not self.until(lambda: tf.exists, 5): raise AvatarError("Safari 地址栏不见了")
                    b = tf.bounds
                    if b.y > 500:
                        tf.tap(); self.until(lambda: tf.exists and tf.bounds.y < 500, 3); b = tf.bounds
                    self.click_center(b)
                    if self.until(lambda: paste.exists, 2.5): paste.tap(); break
                else:
                    raise AvatarError("地址栏菜单里没有「粘贴」")
                s.send_keys("\n")
            img = s(type=I)
            # 等地址栏回到底部（非编辑）且图片出现；长按点固定在图片区域（512 图按屏宽显示，y≈20..435）
            loaded = lambda: tf.exists and tf.bounds.y > 500 and img.exists
            if not self.until(loaded, 10): raise AvatarError("Safari 没显示图片")
            self.sleep(0.4)
            if self.debug: self.shot("02_safari_dataurl")
        with self.step("存储到照片"):
            save = self.el("存储到“照片”", B)
            for _ in range(3):
                s.tap_hold(207, 200, 1.2)
                if self.until(lambda: save.exists, 3): break
                if self.debug: self.shot("03_hold_fail")
            else:
                raise AvatarError("长按后没有「存储到“照片”」")
            save.tap(); self.until(lambda: not save.exists, 4); self.sleep(0.6)   # 给照片库写入留一点时间
        # 不再关闭标签页：下次「粘贴并前往」复用同一个 data: 标签页，不会累积（省 ~4s）

    def restore_clipboard(self):
        clip = getattr(self, "saved_clip", None)
        if clip is None: return
        try: set_pasteboard(clip, post=self.post, sleep=self.sleep, raw_b64=True, verify=False)
        except Exception as e: self.log("恢复剪贴板失败（忽略）:", e)
        self.saved_clip = None

    # ---------- 换头像 ----------
    def change(self, target):
        s = self.s
        is_photo = lambda: s(type=I, labelContains="照片").exists
        with self.step("头像大图 → 更多 → 相册"):
            self.tap("头像", C, until=("更多", B))
            self.tap("更多", until=("从手机相册选择", B))
            self.tap("从手机相册选择", until=is_photo)
        with self.step("选最新照片（比对）"):
            im = self.shot("09_picker")
            # 选择器按时间正序，文档顺序最后一个即最新；只取它的 label/bounds（逐个取 10 张要 ~2s）
            imgs = s(type=I, labelContains="照片").find_elements()
            if not imgs: raise AvatarError("相册选择器里没有照片")
            last = imgs[-1]
            if not (last.label or "").startswith("照片"): raise AvatarError(f"选择器最后一个元素不是照片：{last.label}")
            newest = last.bounds
            d = img_diff(self.crop(im, newest), target)
            self.log(f"  相册最新图与目标差异 {d:.1f}")
            if d > SAME_THRESH * 1.5: raise AvatarError(f"相册最新一张不是目标图（差异 {d:.1f}），停止")
            for _ in range(3):
                self.click_center(newest, dy=5)
                if self.wait("完成", B, 5): break
            else: raise AvatarError("没进入裁剪页")
        with self.step("完成并上传"):
            self.tap("完成", until=lambda: not self.el("完成", B).exists and self.el("更多", B).exists, timeout=20, retries=0)
            self.tap("返回", until=("头像", C))

    def delete_photo(self, target):
        s = self.s
        with self.step("删除相册临时图"):
            s.app_activate(PHOTOS)
            self.tap("图库", until=lambda: s(type=I, labelContains="照片").exists, timeout=8)
            self.sleep(0.3)
            # 照片 App 的文档顺序不可靠（实测最后一个元素不是最新），按屏幕位置取右下角那张
            imgs = [e.bounds for e in s(type=I, labelContains="照片").find_elements()]
            imgs = [b for b in imgs if 60 < b.y < 690]
            if not imgs: raise AvatarError("照片 App 网格为空")
            nb = max(imgs, key=lambda b: (b.y, b.x))
            im = self.shot("14_photos_grid")
            if img_diff(self.crop(im, nb), target) > SAME_THRESH * 1.5: raise AvatarError("照片最新一张不是临时图，不删")
            self.click_center(nb); self.tap("删除", until=("删除照片", B)); self.tap("删除照片", B)


def run(s, image, skip_transfer=False, delete=False, done=DONE, shot_dir=STEPS, force_upload=False, debug=False, keep_clipboard=False, **kw):
    from PIL import Image
    target = Image.open(image)
    r = Runner(s, shot_dir, debug=debug, keep_clipboard=keep_clipboard, **kw); t0 = r.clock(); status = "changed"
    try:
        with r.step("「我」页校验账号 + 比对当前头像"):
            idb = r.me_tab()
            d0 = img_diff(r.me_thumb(idb), target); r.log(f"  当前头像与目标差异 {d0:.1f}")
        if d0 < SAME_THRESH and not force_upload:
            status = "already"; im = r.shot("12_profile_after", force=True)
            done_img = r.me_thumb(idb)
        else:
            if not skip_transfer:
                r.transfer(image)
            with r.step("回微信进个人资料"):
                r.s.app_activate(WECHAT); r.open_profile(idb)
            r.change(target)
            with r.step("校验新头像"):
                done_img = r.current_thumb("12_profile_after")
                d1 = img_diff(done_img, target); r.log(f"  新头像与目标差异 {d1:.1f}")
                if d1 >= SAME_THRESH: raise AvatarError(f"个人资料页头像与目标不一致（差异 {d1:.1f}）")
            if delete and not skip_transfer:
                try: r.delete_photo(target)
                except Exception as e: r.log("删除临时图失败（忽略）:", e)
        done = pathlib.Path(done); done.parent.mkdir(parents=True, exist_ok=True); done_img.save(str(done))
    finally:
        if getattr(r, "saved_clip", None) is not None:
            with r.step("恢复剪贴板"): r.restore_clipboard()
            try: r.s.app_activate(WECHAT)
            except Exception: pass
    total = r.clock() - t0; r.log(f"总耗时 {total:.1f}s  状态 {status}")
    return dict(status=status, done=str(done), total=round(total, 1), timings=r.timings, shots=r.shots)


def main(argv=None, connect=None, picker=None, now=None, history=HISTORY):
    ap = argparse.ArgumentParser(); ap.add_argument("--image", default=str(IMG))
    ap.add_argument("--yes", action="store_true", help="手动指定图片时的执行开关（防误跑）")
    ap.add_argument("--auto", action="store_true", help="小甜自选头像并更换（无需用户确认）")
    ap.add_argument("--force", action="store_true", help="忽略 14 天限制（--auto 时也传给采集 --force，上限仍生效）")
    ap.add_argument("--force-upload", action="store_true", help="即使已是目标图也重新上传（测速用）")
    ap.add_argument("--test", action="store_true", help="测速/测试运行，history 里标 test，不计入 14 天限制")
    ap.add_argument("--skip-transfer", action="store_true"); ap.add_argument("--delete-photo", action="store_true")
    ap.add_argument("--debug", action="store_true"); ap.add_argument("--keep-clipboard", action="store_true", help="备份并恢复手机剪贴板（+~2s）"); ap.add_argument("--done", default=str(DONE))
    a = ap.parse_args(argv)
    if not (a.yes or a.auto): print("需要 --auto（自选）或 --image PATH --yes"); return 2
    now = now or datetime.datetime.now()
    if not a.force and not a.test:
        nxt = rate_limited(now, history)
        if nxt: print(json.dumps({"status": "rate-limited", "next": nxt}, ensure_ascii=False)); return 3
    pick = None
    if a.auto:
        if picker is None:
            sys.path.insert(0, str(ROOT / "tools")); import avatar_pick; picker = avatar_pick.pick
        pick = picker(force=a.force)
        if not pick or not pick.get("image"):
            print(json.dumps({"status": "no-pick", "pick": pick}, ensure_ascii=False)); return 4
        a.image = pick["image"]
    if connect is None:
        sys.path.insert(0, str(pathlib.Path("~/iphone").expanduser())); import wda_fast; connect = wda_fast.connect
    res = run(connect(), a.image, a.skip_transfer, a.delete_photo, a.done, force_upload=a.force_upload, debug=a.debug, keep_clipboard=a.keep_clipboard)
    entry = dict(ts=now.isoformat(timespec="seconds"), status=res["status"], image=str(a.image), total_s=res["total"],
                 auto=a.auto, forced=a.force, test=a.test)
    if pick: entry.update({k: v for k, v in pick.items() if k != "image"})
    if res["status"] == "changed" or pick: append_history(entry, history)
    print(json.dumps({k: v for k, v in res.items() if k != "shots"}, ensure_ascii=False)); return 0


if __name__ == "__main__":
    sys.exit(main())
