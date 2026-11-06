"""QQ 小号换头像：复用 avatar_pick 选图 + NapCat OneBot set_qq_avatar。

不走 iPhone WDA / avatar_iphone；不发消息/空间；不动 iLink。

策略：
  - 相册（--album）：无 14 天硬约束；走 mood 门控（心情/小甜心）+ 日概率 + 最短间隔小时
    （config.avatar.mood）。相册选图**允许人像**（不做标题/Vision 人脸硬筛）；xhs 仍禁真人。--force 跳过门控。
  - 小红书/本地池（--auto）：无 14 天硬约束（xhs_min_days 默认 0）；必须守 **source_poll**
    （静默时段为 poll_limits quiet ≈01:00–07:30，**不是** proactive quiet_hours/_weekend；
    另有日上限/间隔/captcha backoff）。配额满等无法检索时入队
    config.avatar.queue_path，次日 source_poll quiet 结束后第一号优先 dequeue 执行。
  - process-queue 与 album 共用 data/avatar/last_change.lock（默认 24h 内不连换）。
  - 静默时段勿加 --xhs-force；source_poll skipped 且未入队时可用本地候选（--no-search）。

用法：
  .venv/bin/python tools/avatar_qq.py --auto
  .venv/bin/python tools/avatar_qq.py --album --dry-run   # 相册随机，只落盘不调 API
  .venv/bin/python tools/avatar_qq.py --album             # 相册随机并真换（过 mood 门）
  .venv/bin/python tools/avatar_qq.py --image data/avatar/new_avatar.png --yes
  .venv/bin/python tools/avatar_qq.py --auto --force      # 忽略天数/心情门；仍守日上限除非 --no-search
  .venv/bin/python tools/avatar_qq.py --process-queue --dry-run
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import pathlib
import shutil
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

HISTORY = ROOT / "data" / "avatar" / "history.json"
IMG = ROOT / "data" / "avatar" / "new_avatar.png"
MIN_DAYS = 0  # 历史常量；实际以 config.avatar.xhs_min_days 为准（默认 0=无硬天数）
DEFAULT_WS = "ws://127.0.0.1:3001"

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None


def load_history(path=HISTORY):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return []


def append_history(entry, path=HISTORY):
    h = load_history(path)
    h.append(entry)
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(h, ensure_ascii=False, indent=1))
    return h


def rate_limited(now=None, path=HISTORY, days=MIN_DAYS, channel="qq", for_source="xhs"):
    """最近一次本 channel、且 source 匹配的真实更换距今 < days → 下次可换 ISO，否则 None。

    for_source="album"：只看 source==album。
    for_source!=album（默认 xhs）：只看 source!=album；缺省旧记录当 xhs（会拦）。
    days<=0 时直接放行（相册默认 album_min_days=0）。
    """
    if days is not None and days <= 0:
        return None
    now = now or datetime.datetime.now()
    last = []
    for e in load_history(path):
        if e.get("status") != "changed" or e.get("test"):
            continue
        if e.get("channel", channel) != channel:
            continue
        src = e.get("source") or "xhs"  # 缺省旧记录当 xhs
        if for_source == "album":
            if src != "album":
                continue
        else:
            if src == "album":
                continue
        last.append(e)
    if not last:
        return None
    t = datetime.datetime.fromisoformat(last[-1]["ts"])
    nxt = t + datetime.timedelta(days=days)
    return nxt.isoformat(timespec="minutes") if now < nxt else None


def avatar_limits():
    """从 config.json 读 album_min_days / xhs_min_days（xhs 默认 0）。"""
    try:
        import avatar_pick
        av = avatar_pick.load_avatar_cfg()
        return int(av.get("album_min_days", 0)), int(av.get("xhs_min_days", MIN_DAYS))
    except Exception:
        return 0, MIN_DAYS


def file_to_base64_uri(path: pathlib.Path) -> str:
    data = pathlib.Path(path).read_bytes()
    return "base64://" + base64.b64encode(data).decode("ascii")


def call_set_qq_avatar(ws_uri: str, file_ref: str, token: str = "", timeout: float = 30.0) -> dict:
    """正向 WS 调 set_qq_avatar；返回整包响应 dict。"""
    if websocket is None:
        raise RuntimeError("需要 websocket-client：.venv/bin/pip install websocket-client")
    uri = ws_uri
    if token and "access_token=" not in uri:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}access_token={token}"
    headers = [f"Authorization: Bearer {token}"] if token else []
    box: dict = {"resp": None, "err": None}
    echo = f"set_qq_avatar_{int(time.time())}"

    def on_open(ws):
        ws.send(
            json.dumps(
                {"action": "set_qq_avatar", "params": {"file": file_ref}, "echo": echo},
                ensure_ascii=False,
            )
        )

    def on_message(ws, message: str):
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            return
        if ev.get("echo") == echo:
            box["resp"] = ev
            ws.close()

    def on_error(_ws, e):
        box["err"] = str(e)

    app = websocket.WebSocketApp(
        uri, header=headers, on_open=on_open, on_message=on_message, on_error=on_error
    )
    t = threading.Thread(target=lambda: app.run_forever(ping_interval=None), daemon=True)
    t.start()
    t.join(timeout=timeout)
    try:
        app.close()
    except Exception:
        pass
    if box["resp"] is not None:
        return box["resp"]
    raise RuntimeError(f"set_qq_avatar 无响应 err={box['err']!r}")


def stage_image(src: pathlib.Path, dest: pathlib.Path = IMG) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = src.resolve()
    dest = dest.resolve()
    if src != dest:
        shutil.copy2(src, dest)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description="QQ 小号换头像（NapCat set_qq_avatar）")
    ap.add_argument("--image", default=str(IMG))
    ap.add_argument("--yes", action="store_true", help="手动指定图片时的执行开关")
    ap.add_argument("--auto", action="store_true", help="自选头像并更换（小红书/本地池）")
    ap.add_argument("--album", action="store_true", help="从自己相册随机抽一张并更换（mood 门控，无 14 天）")
    ap.add_argument("--force", action="store_true", help="忽略天数限流与相册 mood 门控")
    ap.add_argument("--test", action="store_true", help="history 标 test，不计入限流")
    ap.add_argument("--no-search", action="store_true", help="选图时不搜小红书")
    ap.add_argument("--xhs-force", action="store_true", help="仅手动试跑；静默时段勿用")
    ap.add_argument("--query", default=None)
    ap.add_argument("--ws", default=None, help="OneBot 正向 WS，默认 persona / 3001")
    ap.add_argument("--token", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只选图落盘写 history(dry)，不调 API")
    ap.add_argument("--process-queue", action="store_true", help="仅尝试执行队列中已到期的换头任务")
    ap.add_argument("--mood", type=float, default=None, help="相册门控：覆盖 mood 数值（测/dry）")
    ap.add_argument("--sweet-pulse", action="store_true", help="相册门控：视为小甜心波动")
    a = ap.parse_args(argv)

    if not (a.yes or a.auto or a.album or a.process_queue):
        print(json.dumps({"status": "need-flag", "hint": "--auto / --album / --process-queue 或 --image PATH --yes"}, ensure_ascii=False))
        return 2

    import avatar_pick as AP

    source = "album" if a.album else "xhs"
    album_min, xhs_min = avatar_limits()
    min_days = album_min if source == "album" else xhs_min
    now = datetime.datetime.now()
    queued_item = None

    # ---- 队列优先（小红书路径）----
    if (a.auto or a.process_queue) and not a.album:
        ready = AP.peek_ready(now=now)
        if ready:
            import source_poll
            ok, why = source_poll.check("xiaohongshu", now=now, force=a.xhs_force)
            if ok or a.no_search:
                queued_item = ready
                a.auto = True  # 走自选换头
                if ready.get("query") and not a.query:
                    a.query = ready.get("query")
            elif why in ("daily-cap", "global-daily-cap") or (why or "").startswith("daily"):
                # 仍满：推迟到明天第一号
                AP.dequeue(ready["id"])
                item = AP.enqueue_avatar(why, now=now, source="xhs", query=ready.get("query"), front=True)
                print(json.dumps({"status": "re-queued", "reason": why, "item": item}, ensure_ascii=False))
                if a.process_queue and not a.auto:
                    return 0
            else:
                # quiet / too-soon：留在队里
                print(json.dumps({"status": "queue-wait", "reason": why, "item": ready}, ensure_ascii=False))
                if a.process_queue and not (a.auto or a.album or a.yes):
                    return 0
        elif a.process_queue and not (a.auto or a.album or a.yes):
            print(json.dumps({"status": "queue-empty"}, ensure_ascii=False))
            return 0

    # ---- 相册 mood 门控 ----
    if a.album and not a.force and not a.test:
        snap = AP.load_mood_snapshot()
        if a.mood is not None:
            snap = dict(snap, mood=float(a.mood), source="cli")
        if a.sweet_pulse:
            snap["sweet_pulse"] = True
        ok, reason, detail = AP.album_allow(now=now, history=HISTORY, mood_snap=snap, force=False)
        if not ok:
            print(json.dumps({"status": "mood-skip", "reason": reason, "source": "album", "detail": detail}, ensure_ascii=False, indent=1))
            return 5

    # ---- 天数限流（xhs_min_days 默认 0；album_min_days 默认 0）----
    if not a.force and not a.test and not a.album:
        nxt = rate_limited(now, HISTORY, days=min_days, channel="qq", for_source=source)
        if nxt:
            print(json.dumps({"status": "rate-limited", "next": nxt, "channel": "qq", "source": source, "min_days": min_days}, ensure_ascii=False))
            return 3
    if a.album and not a.force and not a.test and album_min > 0:
        nxt = rate_limited(now, HISTORY, days=album_min, channel="qq", for_source="album")
        if nxt:
            print(json.dumps({"status": "rate-limited", "next": nxt, "channel": "qq", "source": "album", "min_days": album_min}, ensure_ascii=False))
            return 3

    # ---- QQ 换头最短间隔（process-queue / album / auto 共用 last_change.lock；dry-run 不拦）----
    if not a.force and not a.test and not a.dry_run and (a.auto or a.album or a.process_queue or a.yes):
        ok_iv, why_iv, det_iv = AP.change_interval_allow(now=now, force=False)
        if not ok_iv:
            print(json.dumps({"status": "change-interval", "reason": why_iv, "detail": det_iv}, ensure_ascii=False, indent=1))
            return 7

    # ---- auto：若检索会被日上限挡住 → 入队明天第一号（仍可用 --no-search 走本地）----
    if a.auto and not a.album and not a.no_search and not a.force:
        import source_poll
        ok, why = source_poll.check("xiaohongshu", now=now, force=a.xhs_force)
        if not ok and why in ("daily-cap", "global-daily-cap"):
            item = AP.enqueue_avatar(why, now=now, source="xhs", query=a.query, front=True)
            if queued_item:
                AP.dequeue(queued_item["id"])
            print(json.dumps({"status": "queued", "reason": why, "item": item,
                              "hint": "今日小红书配额满，已排到明天 source_poll quiet(≈07:30) 结束后第一号"}, ensure_ascii=False, indent=1))
            return 6
        if not ok and why == "quiet-hours" and not queued_item:
            # 静默：入队到今日 quiet 结束后；不强制真搜
            item = AP.enqueue_avatar(why, now=now, source="xhs", query=a.query, front=False)
            print(json.dumps({"status": "queued", "reason": why, "item": item,
                              "hint": "source_poll quiet(≈01:00–07:30)，已排队到 quiet 结束后；也可用 --no-search 走本地池"}, ensure_ascii=False, indent=1))
            return 6

    # resolve ws/token from persona if present（勿打印 token）
    ws_uri = a.ws or DEFAULT_WS
    token = a.token or ""
    try:
        from persona import load_persona  # type: ignore
        qq = (load_persona() or {}).get("channels", {}).get("qq", {}) or {}
        ws_uri = a.ws or qq.get("ws_uri") or ws_uri
        if a.token is None:
            token = qq.get("ws_token") or ""
    except Exception:
        pass

    pick = None
    image_path = pathlib.Path(a.image)
    if a.album or a.auto:
        pick = AP.pick(
            force=a.force,
            search=(not a.no_search) and (not a.album),
            xhs_force=a.xhs_force,
            query=a.query,
            source="album" if a.album else None,
        )
        if not pick or not pick.get("image"):
            # 搜索被 poll 挡住且无本地候选：入队
            st = (pick or {}).get("search_status") or ""
            if a.auto and not a.album and isinstance(st, str) and st.startswith("skipped:"):
                why = st.split(":", 1)[-1]
                if why in ("daily-cap", "global-daily-cap") or why == "quiet-hours":
                    item = AP.enqueue_avatar(why, now=now, source="xhs", query=a.query,
                                             front=why.startswith("daily"))
                    print(json.dumps({"status": "queued", "reason": why, "item": item, "pick": pick}, ensure_ascii=False))
                    return 6
            print(json.dumps({"status": "no-pick", "pick": pick}, ensure_ascii=False))
            return 4
        image_path = pathlib.Path(pick["image"])
        source = pick.get("source") or source

    if not image_path.is_file():
        print(json.dumps({"status": "missing-image", "image": str(image_path)}, ensure_ascii=False))
        return 4

    staged = stage_image(image_path, IMG)
    # Docker 看不到宿主机路径 → 一律 base64://
    file_ref = file_to_base64_uri(staged)

    if a.dry_run:
        entry = dict(
            ts=now.isoformat(timespec="seconds"),
            status="dry-run",
            channel="qq",
            source=source,
            image=str(staged),
            source_image=str(image_path),
            auto=a.auto or a.album,
            album=a.album,
            forced=a.force,
            test=a.test,
            queued_id=(queued_item or {}).get("id"),
        )
        if pick:
            entry.update({k: v for k, v in pick.items() if k != "image"})
            entry["source"] = pick.get("source") or source
            if pick.get("source_image"):
                entry["source_image"] = pick["source_image"]
        append_history(entry, HISTORY)
        if queued_item:
            AP.dequeue(queued_item["id"])
        print(json.dumps({"status": "dry-run", "source": entry["source"], "image": str(staged),
                          "source_image": entry.get("source_image"), "queued_id": entry.get("queued_id"),
                          "pick": pick}, ensure_ascii=False, indent=1))
        return 0

    try:
        resp = call_set_qq_avatar(ws_uri, file_ref, token=token)
    except Exception as e:
        entry = dict(
            ts=now.isoformat(timespec="seconds"),
            status="error",
            channel="qq",
            source=source,
            image=str(staged),
            source_image=str(image_path),
            error=str(e)[:300],
            auto=a.auto or a.album,
            album=a.album,
            forced=a.force,
            test=a.test,
        )
        if pick:
            entry.update({k: v for k, v in pick.items() if k != "image"})
            entry["source"] = pick.get("source") or source
            if pick.get("source_image"):
                entry["source_image"] = pick["source_image"]
        append_history(entry, HISTORY)
        print(json.dumps({"status": "error", "error": str(e)[:300], "image": str(staged)}, ensure_ascii=False))
        return 1

    retcode = resp.get("retcode")
    ok = resp.get("status") == "ok" or retcode == 0
    status = "changed" if ok else "failed"
    entry = dict(
        ts=now.isoformat(timespec="seconds"),
        status=status,
        channel="qq",
        source=source,
        image=str(staged),
        source_image=str(image_path),
        retcode=retcode,
        api_status=resp.get("status"),
        message=(resp.get("message") or resp.get("wording") or "")[:200],
        auto=a.auto or a.album,
        album=a.album,
        forced=a.force,
        test=a.test,
        queued_id=(queued_item or {}).get("id"),
    )
    if pick:
        entry.update({k: v for k, v in pick.items() if k != "image"})
        entry["source"] = pick.get("source") or source
        if pick.get("source_image"):
            entry["source_image"] = pick["source_image"]
    append_history(entry, HISTORY)
    if ok and queued_item:
        AP.dequeue(queued_item["id"])
    if ok and not a.test:
        try:
            AP.mark_avatar_changed(channel="qq", source=source, now=now, image=str(staged))
        except Exception:
            pass

    out = {
        "status": status,
        "retcode": retcode,
        "api_status": resp.get("status"),
        "message": entry["message"],
        "image": str(staged),
        "source_image": str(image_path),
        "uin": 10002,
        "source": source,
        "queued_id": entry.get("queued_id"),
        "pick": {
            k: pick.get(k)
            for k in ("title", "author", "origin", "source", "score", "reason", "source_url", "search_status", "px", "source_image")
            if pick and k in pick
        }
        if pick
        else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
