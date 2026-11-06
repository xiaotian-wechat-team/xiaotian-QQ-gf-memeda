"""QQ OneBot / NapCat full-pipeline health check (read-only, non-spam).

Checks process/channel, text send plan, voice ASR readiness, imagegen/Magpie,
schedule/proactive, plus cheap disk/log extras. Does NOT send chat messages,
does NOT restart services, does NOT touch iLink/WeChat beyond a SKIP note.

Exit codes:
  0 — all critical checks OK
  1 — any critical FAIL
  2 — script error

Usage:
  .venv/bin/python scripts/qq_pipeline_health.py
  scripts/qq_pipeline_health.sh
  .venv/bin/python -m qq_pipeline_health   # from src on PYTHONPATH
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    from config import ROOT, load
except Exception:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from config import ROOT, load  # type: ignore
    except Exception:
        def load():  # type: ignore
            return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


LOG_DIR = Path.home() / "Library" / "Logs" / "xiaotian"
LAUNCHD_LABEL = "com.xiaotian.onebot"
WS_PROBE_TIMEOUT = 6.0
HTTP_TIMEOUT = 3.0
LOG_SPIKE_WINDOW_MIN = 30
LOG_SPIKE_THRESHOLD = 8
DISK_MIN_FREE_MB = 512
HOLD_HTTP_WINDOW_MIN = 60

# Critical check ids — any FAIL → exit 1
CRITICAL = frozenset(
    {
        "process.launchd",
        "process.napcat_http",
        "process.login",
        "process.onebot_ws",
        "text.send_plan",
        "text.whitelist",
        "schedule.proactive_transport",
        "schedule.queue_readable",
    }
)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


def _check(
    cid: str,
    status: str,
    reason: str,
    *,
    detail: Any = None,
    critical: Optional[bool] = None,
) -> dict:
    if critical is None:
        critical = cid in CRITICAL
    row = {
        "id": cid,
        "status": status,  # OK | FAIL | SKIP | WARN
        "reason": reason,
        "critical": bool(critical),
    }
    if detail is not None:
        row["detail"] = detail
    return row


def _run(argv: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(url: str, timeout: float = HTTP_TIMEOUT) -> tuple[bool, Any, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(body), f"HTTP {getattr(r, 'status', 200)}"
            except json.JSONDecodeError:
                return True, body[:200], f"HTTP {getattr(r, 'status', 200)} non-json"
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def _http_ping(url: str, timeout: float = HTTP_TIMEOUT) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = getattr(r, "status", 200)
            return 200 <= int(code) < 500, f"HTTP {code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── 1) Process / channel ─────────────────────────────────────────────


def check_process(cfg: dict) -> list[dict]:
    out: list[dict] = []
    uid = os.getuid()
    domain = f"gui/{uid}"
    # launchctl print
    code, stdout, stderr = _run(["launchctl", "print", f"{domain}/{LAUNCHD_LABEL}"])
    loaded = code == 0
    pid = None
    last_exit = None
    runs = None
    if loaded:
        for line in stdout.splitlines():
            s = line.strip()
            if s.startswith("pid = "):
                try:
                    pid = int(s.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif s.startswith("last exit code = "):
                last_exit = s.split("=", 1)[1].strip()
            elif s.startswith("runs = "):
                try:
                    runs = int(s.split("=", 1)[1].strip())
                except ValueError:
                    pass
    # orphan / manual process
    code2, out2, _ = _run(["pgrep", "-f", "onebot_qq_run.py --send"], timeout=5)
    orphan_pids = [p for p in (out2 or "").split() if p.isdigit()]

    recent_ok = False
    age_note = ""
    log_path = LOG_DIR / "onebot.log"
    if log_path.is_file():
        try:
            mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
            age_sec = (_now() - mtime).total_seconds()
            age_note = f"onebot.log mtime {_iso(mtime)} ({int(age_sec)}s ago)"
            recent_ok = age_sec < 3600  # touched within 1h
        except OSError as e:
            age_note = f"log stat fail: {e}"

    if loaded and pid and pid > 0:
        out.append(
            _check(
                "process.launchd",
                "OK",
                f"{LAUNCHD_LABEL} loaded pid={pid} runs={runs} last_exit={last_exit}",
                detail={"pid": pid, "runs": runs, "last_exit": last_exit, "log": age_note},
            )
        )
    elif loaded and not pid:
        out.append(
            _check(
                "process.launchd",
                "FAIL",
                f"{LAUNCHD_LABEL} loaded but not running "
                f"(last_exit={last_exit}; orphans={orphan_pids or '-'})",
                detail={"orphan_pids": orphan_pids, "log": age_note},
            )
        )
    elif orphan_pids:
        out.append(
            _check(
                "process.launchd",
                "FAIL",
                f"{LAUNCHD_LABEL} not loaded; only manual onebot_qq_run --send "
                f"pids={orphan_pids}",
                detail={"orphan_pids": orphan_pids, "log": age_note},
            )
        )
    else:
        out.append(
            _check(
                "process.launchd",
                "FAIL",
                f"{LAUNCHD_LABEL} not loaded and no onebot_qq_run process",
                detail={"stderr": (stderr or "")[:160], "log": age_note},
            )
        )

    if recent_ok:
        out.append(
            _check(
                "process.recent",
                "OK",
                age_note or "recent log activity",
                critical=False,
            )
        )
    elif log_path.is_file():
        out.append(
            _check(
                "process.recent",
                "WARN",
                age_note or "log stale",
                critical=False,
            )
        )
    else:
        out.append(
            _check(
                "process.recent",
                "SKIP",
                "no onebot.log yet",
                critical=False,
            )
        )

    # NapCat HTTP
    onebot_cfg = cfg.get("onebot") or {}
    persona_qq = ((cfg.get("persona") or {}).get("channels") or {}).get("qq") or {}
    http_api = (
        onebot_cfg.get("http_api")
        or persona_qq.get("http_api")
        or "http://127.0.0.1:3000"
    ).rstrip("/")
    ws_url = (
        onebot_cfg.get("ws_url")
        or persona_qq.get("ws_uri")
        or "ws://127.0.0.1:3001"
    )

    # port reachability
    host, port = "127.0.0.1", 3000
    try:
        from urllib.parse import urlparse

        u = urlparse(http_api)
        host = u.hostname or host
        port = u.port or 3000
    except Exception:
        pass

    if not _tcp_open(host, port):
        out.append(
            _check(
                "process.napcat_http",
                "FAIL",
                f"NapCat HTTP not reachable {host}:{port}",
            )
        )
        out.append(
            _check(
                "process.login",
                "FAIL",
                "skipped: HTTP down",
            )
        )
    else:
        ok, data, meta = _http_json(f"{http_api}/get_login_info")
        if not ok:
            out.append(
                _check(
                    "process.napcat_http",
                    "FAIL",
                    f"get_login_info request failed: {meta}",
                )
            )
            out.append(
                _check("process.login", "FAIL", f"no login payload: {meta}")
            )
        else:
            out.append(
                _check(
                    "process.napcat_http",
                    "OK",
                    f"reachable {http_api} ({meta})",
                )
            )
            # login sanity
            login_fail = False
            reason = ""
            detail: dict[str, Any] = {}
            if isinstance(data, dict):
                ret = data.get("retcode", data.get("status"))
                status = str(data.get("status") or "")
                payload = data.get("data") if isinstance(data.get("data"), dict) else data
                uin = (payload or {}).get("user_id") if isinstance(payload, dict) else None
                nick = (payload or {}).get("nickname") if isinstance(payload, dict) else None
                detail = {"retcode": ret, "status": status, "uin": uin, "nickname": nick}
                msg = str(data.get("message") or data.get("wording") or "")
                # ErrCode / logged-out heuristics
                blob = json.dumps(data, ensure_ascii=False)
                logged_out_markers = (
                    "ErrCode",
                    "登录",
                    "掉线",
                    "未登录",
                    "logged out",
                    "not login",
                    "账号状态",
                )
                if ret not in (0, "0", None) and status not in ("ok", "success", ""):
                    login_fail = True
                    reason = f"retcode={ret} status={status!r} msg={msg[:80]!r}"
                elif not uin:
                    login_fail = True
                    reason = f"missing user_id in login data; status={status!r}"
                elif any(m.lower() in blob.lower() for m in ("errcode", "logged out", "not login")):
                    # only fail if also no uin or bad ret — soft if uin present
                    if ret not in (0, "0", None) and ret != 0:
                        login_fail = True
                        reason = f"login error markers in response: {msg[:80]!r}"
                    else:
                        reason = f"uin={uin} nick={nick!r} (markers ignored; ret ok)"
                else:
                    reason = f"uin={uin} nick={nick!r} retcode={ret}"
            else:
                login_fail = True
                reason = f"unexpected login payload type={type(data).__name__}"

            out.append(
                _check(
                    "process.login",
                    "FAIL" if login_fail else "OK",
                    reason,
                    detail=detail,
                )
            )

    # OneBot WS
    ws_host, ws_port = "127.0.0.1", 3001
    try:
        from urllib.parse import urlparse

        wu = urlparse(ws_url)
        ws_host = wu.hostname or ws_host
        ws_port = wu.port or 3001
    except Exception:
        pass

    if not _tcp_open(ws_host, ws_port):
        out.append(
            _check(
                "process.onebot_ws",
                "FAIL",
                f"OneBot WS port closed {ws_host}:{ws_port} ({ws_url})",
            )
        )
    else:
        # cheap WS get_login_info if websocket-client present
        ws_ok, ws_reason, ws_detail = _probe_ws_login(ws_url, persona_qq.get("ws_token") or "")
        out.append(
            _check(
                "process.onebot_ws",
                "OK" if ws_ok else "FAIL",
                ws_reason,
                detail=ws_detail,
            )
        )

    return out


def _probe_ws_login(ws_uri: str, token: str = "") -> tuple[bool, str, Any]:
    try:
        import websocket  # type: ignore
    except ImportError:
        # port open is already known; import missing → degrade to TCP-only OK soft?
        # WS is critical for inbound — without client lib, report OK on TCP with note
        return True, f"TCP open; websocket-client missing (skip get_login_info over WS)", {
            "mode": "tcp_only"
        }

    import threading

    uri = ws_uri
    if token and "access_token=" not in uri:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}access_token={token}"
    headers = [f"Authorization: Bearer {token}"] if token else []
    box: dict[str, Any] = {"ok": False, "err": None, "login": None}

    def on_open(ws):
        try:
            ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "health"}))
        except Exception as e:
            box["err"] = str(e)

    def on_message(ws, message: str):
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            return
        if ev.get("echo") == "health":
            box["login"] = ev.get("data") or ev
            box["ok"] = True
            try:
                ws.close()
            except Exception:
                pass

    def on_error(_ws, e):
        box["err"] = str(e)

    try:
        app = websocket.WebSocketApp(
            uri, header=headers, on_open=on_open, on_message=on_message, on_error=on_error
        )
        t = threading.Thread(
            target=lambda: app.run_forever(ping_interval=None), daemon=True
        )
        t.start()
        t.join(timeout=WS_PROBE_TIMEOUT)
        try:
            app.close()
        except Exception:
            pass
    except Exception as e:
        return False, f"WS probe exception: {e}", None

    if box["ok"]:
        login = box["login"] or {}
        uin = login.get("user_id")
        if not uin and isinstance(login, dict):
            # nested
            data = login.get("data") if isinstance(login.get("data"), dict) else None
            uin = (data or {}).get("user_id")
        return True, f"WS probe OK uin={uin}", {"uin": uin, "login": login}
    return False, f"WS probe FAIL err={box['err']!r}", {"err": box["err"]}


# ── 2) Text path ─────────────────────────────────────────────────────


def check_text(cfg: dict) -> list[dict]:
    out: list[dict] = []
    pc = cfg.get("proactive") or {}
    transport = (pc.get("transport") or "").lower()
    primary = (pc.get("primary_channel") or "").lower()
    policy = (pc.get("channel_policy") or "").lower()

    # config surface
    cfg_ok = primary in ("qq_onebot", "qq", "onebot") or transport in (
        "onebot",
        "qq",
    )
    out.append(
        _check(
            "text.config",
            "OK" if cfg_ok else "FAIL",
            f"primary_channel={primary!r} transport={transport!r} channel_policy={policy!r}",
            detail={"primary_channel": primary, "transport": transport, "channel_policy": policy},
            critical=True if not cfg_ok else False,
        )
    )
    # promote to critical id used in CRITICAL set via send_plan

    # resolve send plan (no send)
    plan = None
    plan_err = None
    try:
        from proactive_sender import _resolve_send_plan

        plan, pol = _resolve_send_plan(pc)
    except Exception as e:
        plan_err = f"{type(e).__name__}: {e}"

    if plan_err:
        out.append(
            _check(
                "text.send_plan",
                "FAIL",
                f"_resolve_send_plan error: {plan_err}",
            )
        )
    elif plan and "onebot" in plan:
        out.append(
            _check(
                "text.send_plan",
                "OK",
                f"plan={plan} policy={pol}",
                detail={"plan": plan, "policy": pol},
            )
        )
    else:
        out.append(
            _check(
                "text.send_plan",
                "FAIL",
                f"plan={plan} does not include onebot (policy={pol})",
                detail={"plan": plan, "policy": pol},
            )
        )

    # whitelist peer present (do not message)
    try:
        from channels.onebot_qq import _qq_allow_ids
        from channels.persona_map import persona_cfg

        allow = sorted(_qq_allow_ids(cfg))
        aliases = (persona_cfg(cfg).get("contact_aliases") or {})
        couple_alias = aliases.get("qq_onebot:10001") or next(
            (v for k, v in aliases.items() if k.startswith("qq_onebot:") and v == "demo_contact"),
            None,
        )
    except Exception as e:
        allow = []
        couple_alias = None
        out.append(
            _check(
                "text.whitelist",
                "FAIL",
                f"cannot load allowlist: {e}",
            )
        )
        return out

    if allow:
        out.append(
            _check(
                "text.whitelist",
                "OK",
                f"allow_user_ids={allow} alias_demo_contact={couple_alias!r} (not messaging)",
                detail={"allow_user_ids": allow, "alias": couple_alias},
            )
        )
    else:
        out.append(
            _check(
                "text.whitelist",
                "FAIL",
                "persona.channels.qq.allow_user_ids empty — would block all couple peers",
            )
        )

    out.append(
        _check(
            "text.ilink_note",
            "SKIP",
            "iLink/WeChat path not checked (QQ-only health; iLink remains backup)",
            critical=False,
        )
    )
    return out


# ── 3) Voice path ────────────────────────────────────────────────────


def check_voice(cfg: dict) -> list[dict]:
    out: list[dict] = []
    # config defaults live in voice_asr._cfg()
    try:
        import voice_asr

        vc = voice_asr._cfg()
        action = vc.get("qq_stt_action") or "fetch_ptt_text"
        backend = vc.get("backend") or "mlx_whisper"
        out.append(
            _check(
                "voice.config",
                "OK",
                f"qq_stt_action={action!r} fallback_backend={backend!r}",
                detail={"voice_asr": {k: vc.get(k) for k in ("qq_stt_action", "backend", "model", "enabled")}},
                critical=False,
            )
        )
        # import path / function present
        if hasattr(voice_asr, "qq_stt_fetch") and hasattr(voice_asr, "enrich_text"):
            out.append(
                _check(
                    "voice.fetch_ptt_path",
                    "OK",
                    "voice_asr.qq_stt_fetch + enrich_text importable (probe via import/config only)",
                    critical=False,
                )
            )
        else:
            out.append(
                _check(
                    "voice.fetch_ptt_path",
                    "FAIL",
                    "voice_asr missing qq_stt_fetch/enrich_text",
                    critical=False,
                )
            )
        # fallback deps — do not load mlx_whisper weights
        mlx_spec = None
        try:
            import importlib.util

            mlx_spec = importlib.util.find_spec("mlx_whisper")
        except Exception:
            pass
        if mlx_spec:
            out.append(
                _check(
                    "voice.fallback_dep",
                    "OK",
                    "mlx_whisper importable (not loaded)",
                    critical=False,
                )
            )
        else:
            out.append(
                _check(
                    "voice.fallback_dep",
                    "WARN",
                    "mlx_whisper not installed; QQ native fetch_ptt_text is primary",
                    critical=False,
                )
            )
        try:
            import websocket  # noqa: F401

            out.append(
                _check(
                    "voice.ws_dep",
                    "OK",
                    "websocket-client present for NapCat STT WS call",
                    critical=False,
                )
            )
        except ImportError:
            out.append(
                _check(
                    "voice.ws_dep",
                    "WARN",
                    "websocket-client missing — qq_stt_fetch cannot run",
                    critical=False,
                )
            )
    except Exception as e:
        out.append(
            _check(
                "voice.config",
                "FAIL",
                f"voice_asr import/config failed: {e}",
                critical=False,
            )
        )
    return out


# ── 4) Image path ────────────────────────────────────────────────────


def check_image(cfg: dict) -> list[dict]:
    out: list[dict] = []
    ig = cfg.get("imagegen") or {}
    url = ig.get("magpie_url") or "http://127.0.0.1:3425/v1/models"
    hold = bool(ig.get("hold_on_before_generate"))

    ok, meta = _http_ping(url, timeout=2.0)
    out.append(
        _check(
            "image.magpie",
            "OK" if ok else "FAIL",
            f"{'up' if ok else 'down'} {url} ({meta}) — ping only, no generate",
            critical=False,
        )
    )
    out.append(
        _check(
            "image.hold_on",
            "OK" if hold else "WARN",
            f"hold_on_before_generate={hold}",
            critical=False,
        )
    )

    # short LLM / deepseek — import + key presence, no network chat
    try:
        import llm_deepseek

        key_ok = bool(llm_deepseek.api_key())
        avail = bool(llm_deepseek.available()) if hasattr(llm_deepseek, "available") else key_ok
        llm = cfg.get("llm") or {}
        out.append(
            _check(
                "image.deepseek",
                "OK" if avail else "FAIL",
                f"short_backend={llm.get('short_backend')!r} available={avail} key={'yes' if key_ok else 'no'}",
                critical=False,
            )
        )
    except Exception as e:
        out.append(
            _check(
                "image.deepseek",
                "FAIL",
                f"llm_deepseek import/key check failed: {e}",
                critical=False,
            )
        )

    # legs / hands sheets (cheap exists)
    ap = cfg.get("appearance") or {}
    profile = ap.get("profile") or "zhuzhuxiaoye"
    assets = Path(os.path.expanduser(ap.get("assets_dir") or "~/xiaotian-assets/appearance"))
    base = assets / profile / "canonical"
    for name, rel in (
        ("image.sheet_face", "sheet.png"),
        ("image.sheet_body", "body/sheet.png"),
        ("image.sheet_legs", "legs/sheet.png"),
        ("image.sheet_hands", "hands/sheet.png"),
    ):
        p = base / rel
        if p.is_file():
            out.append(
                _check(
                    name,
                    "OK",
                    f"present {p}",
                    critical=False,
                )
            )
        else:
            out.append(
                _check(
                    name,
                    "WARN",
                    f"missing {p}",
                    critical=False,
                )
            )
    return out


# ── 5) Schedule / proactive ──────────────────────────────────────────


def check_schedule(cfg: dict) -> list[dict]:
    out: list[dict] = []
    pc = cfg.get("proactive") or {}
    transport = (pc.get("transport") or "").lower()
    primary = (pc.get("primary_channel") or "").lower()

    # transport expectation
    try:
        from proactive_sender import _resolve_send_plan

        plan, pol = _resolve_send_plan(pc)
        transport_ok = "onebot" in (plan or [])
        out.append(
            _check(
                "schedule.proactive_transport",
                "OK" if transport_ok else "FAIL",
                f"transport={transport!r} primary={primary!r} → plan={plan} policy={pol}",
                detail={"plan": plan, "policy": pol},
            )
        )
    except Exception as e:
        out.append(
            _check(
                "schedule.proactive_transport",
                "FAIL",
                f"cannot resolve plan: {e}",
            )
        )

    # queue readable
    qpath = ROOT / "data" / "proactive_queue.json"
    if qpath.is_file():
        try:
            q = json.loads(qpath.read_text(encoding="utf-8"))
            n_items = len(q.get("items") or []) if isinstance(q, dict) else -1
            out.append(
                _check(
                    "schedule.queue_readable",
                    "OK",
                    f"proactive_queue.json items={n_items} next_check_at={q.get('next_check_at') if isinstance(q, dict) else '?'}",
                    detail={
                        "items": n_items,
                        "next_check_at": q.get("next_check_at") if isinstance(q, dict) else None,
                    },
                )
            )
        except Exception as e:
            out.append(
                _check(
                    "schedule.queue_readable",
                    "FAIL",
                    f"proactive_queue.json unreadable: {e}",
                )
            )
    else:
        out.append(
            _check(
                "schedule.queue_readable",
                "FAIL",
                f"missing {qpath}",
            )
        )

    # outbox readable
    ob = ROOT / "data" / "outbox.db"
    if ob.is_file():
        try:
            con = sqlite3.connect(f"file:{ob}?mode=ro", uri=True)
            pending = con.execute(
                "SELECT COUNT(*) FROM outbox WHERE channel='onebot' "
                "AND status NOT IN ('sent','dropped')"
            ).fetchone()[0]
            heldish = con.execute(
                "SELECT COUNT(*) FROM outbox WHERE channel='onebot' "
                "AND (status LIKE '%hold%' OR lower(COALESCE(reason,'')) LIKE '%hold%')"
            ).fetchone()[0]
            con.close()
            out.append(
                _check(
                    "schedule.outbox",
                    "OK",
                    f"outbox.db readable; onebot pending={pending} holdish={heldish}",
                    detail={"pending": pending, "holdish": heldish},
                    critical=False,
                )
            )
        except Exception as e:
            out.append(
                _check(
                    "schedule.outbox",
                    "WARN",
                    f"outbox.db read fail: {e}",
                    critical=False,
                )
            )
    else:
        out.append(
            _check(
                "schedule.outbox",
                "WARN",
                "outbox.db missing",
                critical=False,
            )
        )

    # life schedule / slot
    life_path = ROOT / "data" / "life_schedule_2026q4.json"
    if life_path.is_file():
        try:
            life = json.loads(life_path.read_text(encoding="utf-8"))
            days_n = len(life.get("days") or {}) if isinstance(life, dict) else 0
            import schedule as sched
            import life_calendar

            now = _now()
            cur = sched.current(now)
            slot = life_calendar.slot_at(now) if hasattr(life_calendar, "slot_at") else None
            out.append(
                _check(
                    "schedule.slot",
                    "OK",
                    f"life_schedule days≈{days_n}; current={cur.get('activity')!r}; "
                    f"slot_bucket={(slot or {}).get('bucket')!r}",
                    detail={"current": cur, "slot": slot},
                    critical=False,
                )
            )
        except Exception as e:
            out.append(
                _check(
                    "schedule.slot",
                    "WARN",
                    f"schedule/slot read issue: {e}",
                    critical=False,
                )
            )
    else:
        out.append(
            _check(
                "schedule.slot",
                "WARN",
                f"missing {life_path.name}",
                critical=False,
            )
        )

    # stuck HOLD from missing HTTP (recent logs)
    hold = _recent_http_holds(HOLD_HTTP_WINDOW_MIN)
    # re-probe cheaply
    onebot_cfg = cfg.get("onebot") or {}
    persona_qq = ((cfg.get("persona") or {}).get("channels") or {}).get("qq") or {}
    http_api = (
        onebot_cfg.get("http_api")
        or persona_qq.get("http_api")
        or "http://127.0.0.1:3000"
    ).rstrip("/")
    http_now_ok, _ = _http_ping(f"{http_api}/get_login_info", timeout=2.0)

    if hold["count"] == 0:
        out.append(
            _check(
                "schedule.hold_http",
                "OK",
                f"no HTTP-related HOLD in last {HOLD_HTTP_WINDOW_MIN}m",
                critical=False,
            )
        )
    elif http_now_ok:
        out.append(
            _check(
                "schedule.hold_http",
                "WARN",
                f"{hold['count']} HTTP-related HOLD in last {HOLD_HTTP_WINDOW_MIN}m "
                f"but NapCat HTTP is up now; latest={hold.get('latest_reason')!r}",
                detail=hold,
                critical=False,
            )
        )
    else:
        out.append(
            _check(
                "schedule.hold_http",
                "FAIL",
                f"{hold['count']} HTTP-related HOLD in last {HOLD_HTTP_WINDOW_MIN}m "
                f"and NapCat HTTP still down; latest={hold.get('latest_reason')!r}",
                detail=hold,
                critical=False,  # process.napcat_http already critical
            )
        )
    return out


def _recent_http_holds(window_min: int) -> dict:
    log = LOG_DIR / "proactive.log"
    result = {"count": 0, "latest_reason": None, "samples": []}
    if not log.is_file():
        return result
    cutoff = _now() - timedelta(minutes=window_min)
    markers = (
        "Connection refused",
        "URLError",
        "RemoteDisconnected",
        "http fail",
        "onebot send fail",
        "NapCat",
    )
    try:
        # read last ~200KB
        data = log.read_bytes()
        if len(data) > 200_000:
            data = data[-200_000:]
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return result
    for line in text.splitlines():
        if '"action": "HOLD"' not in line and "'action': 'HOLD'" not in line:
            if "HOLD" not in line or "onebot" not in line.lower():
                continue
        if not any(m.lower() in line.lower() for m in markers):
            continue
        # timestamp prefix [YYYY-MM-DD HH:MM:SS]
        ts = None
        if line.startswith("["):
            try:
                ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = None
        if ts and ts < cutoff:
            continue
        result["count"] += 1
        # extract reason
        reason = None
        try:
            jstart = line.index("{")
            obj = json.loads(line[jstart:])
            reason = obj.get("reason")
        except Exception:
            reason = line[-160:]
        result["latest_reason"] = reason
        if len(result["samples"]) < 3:
            result["samples"].append({"ts": _iso(ts) if ts else None, "reason": reason})
    return result


# ── 6) Optional extras ───────────────────────────────────────────────


def check_extras(cfg: dict) -> list[dict]:
    out: list[dict] = []
    ig = cfg.get("imagegen") or {}
    media_roots = [
        Path(os.path.expanduser(ig.get("out_dir") or "~/xiaotian-assets/generated")),
        ROOT / "data" / "media",
        Path.home() / "xiaotian-assets",
    ]
    seen = set()
    for root in media_roots:
        root = root.resolve() if root.exists() else root
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not root.exists():
                out.append(
                    _check(
                        "extra.disk",
                        "SKIP",
                        f"path missing {root}",
                        critical=False,
                    )
                )
                continue
            usage = shutil.disk_usage(str(root))
            free_mb = usage.free // (1024 * 1024)
            st = "OK" if free_mb >= DISK_MIN_FREE_MB else "WARN"
            out.append(
                _check(
                    "extra.disk",
                    st,
                    f"{root}: {free_mb}MB free (min {DISK_MIN_FREE_MB})",
                    detail={"path": str(root), "free_mb": free_mb},
                    critical=False,
                )
            )
            break  # one representative is enough
        except Exception as e:
            out.append(
                _check(
                    "extra.disk",
                    "WARN",
                    f"disk_usage fail {root}: {e}",
                    critical=False,
                )
            )

    # bridge / onebot error spikes
    spike = _log_error_spikes(LOG_SPIKE_WINDOW_MIN)
    if spike["checked"] is False:
        out.append(
            _check(
                "extra.log_spike",
                "SKIP",
                "no onebot logs to scan",
                critical=False,
            )
        )
    elif spike["errors"] >= LOG_SPIKE_THRESHOLD:
        out.append(
            _check(
                "extra.log_spike",
                "WARN",
                f"{spike['errors']} error-like lines in last {LOG_SPIKE_WINDOW_MIN}m "
                f"(threshold {LOG_SPIKE_THRESHOLD})",
                detail=spike,
                critical=False,
            )
        )
    else:
        out.append(
            _check(
                "extra.log_spike",
                "OK",
                f"{spike['errors']} error-like lines in last {LOG_SPIKE_WINDOW_MIN}m",
                detail=spike,
                critical=False,
            )
        )
    return out


def _log_error_spikes(window_min: int) -> dict:
    """Count recent bridge/onebot hard errors only (avoid ML load noise)."""
    paths = [LOG_DIR / "onebot.err", LOG_DIR / "onebot.log"]
    cutoff = _now() - timedelta(minutes=window_min)
    # Narrow markers — "Error"/"Connection" alone matches torch/HF noise.
    markers = ("[onebot] err", "Traceback (most recent call last)", "[onebot] probe FAIL")
    errors = 0
    samples: list[str] = []
    checked = False
    for path in paths:
        if not path.is_file():
            continue
        checked = True
        try:
            raw = path.read_bytes()
            # Prefer last 40KB (recent tail); untimestamped .err lines only from last 80 lines
            tail = raw[-40_000:] if len(raw) > 40_000 else raw
            text = tail.decode("utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        # For untimestamped files, only the last 80 lines of the tail count as "recent"
        recent_untouched = set(range(max(0, len(lines) - 80), len(lines)))
        for i, line in enumerate(lines):
            if not any(m in line for m in markers):
                continue
            ts = None
            # "[YYYY-MM-DD HH:MM:SS] ..." or bare "YYYY-MM-DD HH:MM:SS ..."
            chunk = line[1:20] if line.startswith("[") else line[:19]
            if len(chunk) >= 19 and chunk[4] == "-" and chunk[10] == " ":
                try:
                    ts = datetime.strptime(chunk[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    ts = None
            if ts is not None:
                if ts < cutoff:
                    continue
            else:
                if i not in recent_untouched:
                    continue
                try:
                    if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                        continue
                except OSError:
                    continue
            errors += 1
            if len(samples) < 5:
                samples.append(line[:160])
    return {
        "checked": checked,
        "errors": errors,
        "window_min": window_min,
        "samples": samples,
    }


# ── Orchestrator ─────────────────────────────────────────────────────


def run_all(cfg: Optional[dict] = None) -> dict:
    cfg = cfg if cfg is not None else load()
    checks: list[dict] = []
    checks.extend(check_process(cfg))
    checks.extend(check_text(cfg))
    checks.extend(check_voice(cfg))
    checks.extend(check_image(cfg))
    checks.extend(check_schedule(cfg))
    checks.extend(check_extras(cfg))

    crit_fails = [c for c in checks if c.get("critical") and c["status"] == "FAIL"]
    soft_fails = [c for c in checks if (not c.get("critical")) and c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    oks = [c for c in checks if c["status"] == "OK"]
    skips = [c for c in checks if c["status"] == "SKIP"]

    overall = "OK" if not crit_fails else "FAIL"
    exit_code = 0 if not crit_fails else 1

    summary = {
        "ok": overall == "OK",
        "overall": overall,
        "exit_code": exit_code,
        "ts": _iso(_now()),
        "tz_label": "Asia/Shanghai",
        "machine_repo": str(ROOT),
        "counts": {
            "OK": len(oks),
            "FAIL_critical": len(crit_fails),
            "FAIL_soft": len(soft_fails),
            "WARN": len(warns),
            "SKIP": len(skips),
            "total": len(checks),
        },
        "critical_fails": [c["id"] for c in crit_fails],
        "soft_fails": [c["id"] for c in soft_fails],
        "warns": [c["id"] for c in warns],
        "checks": checks,
    }
    return summary


def _human_oneliner(summary: dict) -> str:
    c = summary["counts"]
    if summary["overall"] == "OK":
        extra = []
        if c["FAIL_soft"]:
            extra.append(f"{c['FAIL_soft']} soft-FAIL")
        if c["WARN"]:
            extra.append(f"{c['WARN']} WARN")
        tail = ("; " + ", ".join(extra)) if extra else ""
        return (
            f"QQ pipeline OK — {c['OK']} checks green"
            f"{tail} (critical clear) @ {summary['ts']} Asia/Shanghai"
        )
    fails = ",".join(summary.get("critical_fails") or []) or "?"
    return (
        f"QQ pipeline FAIL — critical: {fails} "
        f"(+{c['FAIL_soft']} soft-FAIL, {c['WARN']} WARN) @ {summary['ts']} Asia/Shanghai"
    )


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv or sys.argv[1:])
    try:
        summary = run_all()
    except Exception as e:
        err = {
            "ok": False,
            "overall": "ERROR",
            "exit_code": 2,
            "ts": _iso(_now()),
            "error": f"{type(e).__name__}: {e}",
        }
        print(f"ERROR\tqq_pipeline_health crashed: {err['error']}", flush=True)
        print(json.dumps(err, ensure_ascii=False, indent=2), flush=True)
        return 2

    # human lines first
    for c in summary["checks"]:
        flag = c["status"]
        crit = "CRIT" if c.get("critical") else "soft"
        print(f"{flag:4} [{crit}] {c['id']}: {c['reason']}", flush=True)
    print("---", flush=True)
    print(_human_oneliner(summary), flush=True)
    print("---", flush=True)
    # machine-readable summary JSON at end
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return int(summary["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
