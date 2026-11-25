#!/usr/bin/env python3
"""Local console + schedule/location API. Persona SSOT: data/persona_dossier.json."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import secrets
from urllib.parse import parse_qs, urlsplit
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT / "src"))
import location
import ui_schedule
from personalization_store import Store, Conflict
from personalization_schema import ValidationError
from role_registry import identifier

PERSONAS = {"xiaotian", "jisoo"}
CONSOLE_TOKEN = secrets.token_urlsafe(32)
CONSOLE_ENVIRONMENT = os.environ.get("ROLE_ENVIRONMENT", "dev")


def repository_branch():
    """Read the branch of the repository serving this console, never the shell cwd."""
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
        return branch or "detached HEAD"
    except (OSError, subprocess.SubprocessError):
        return None


def _request(path):
    parsed = urlsplit(path)
    query = parse_qs(parsed.query)
    persona = identifier((query.get("persona_id") or ["xiaotian"])[0])
    return parsed.path, persona, query


def _save_routes(persona, routes):
    if not isinstance(routes, list) or len(routes) > 32:
        raise ValueError("invalid routes")
    clean = []
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("invalid route")
        minutes = route.get("minutes")
        if type(minutes) is not int or not 1 <= minutes <= 240:
            raise ValueError("route minutes must be an integer from 1 to 240")
        values = [route.get("from"), route.get("to"), route.get("mode", "步行")]
        if any(not isinstance(v, str) or not v.strip() or len(v) > limit
               for v, limit in zip(values, (80, 80, 40))):
            raise ValueError("invalid route fields")
        source, target, mode = [v.strip() for v in values]
        if source == target:
            raise ValueError("route endpoints must differ")
        clean.append({"from": source, "to": target, "mode": mode, "minutes": minutes})
    path = location.config_path(persona)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            doc = {}
        if not isinstance(doc, dict) or not isinstance(doc.get(persona, {}), dict):
            raise ValueError("invalid location document")
        doc.setdefault(persona, {})["routes"] = clean
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as out:
            json.dump(doc, out, ensure_ascii=False, indent=2)
            out.write("\n")
        os.replace(out.name, path)
    return clean


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def send_head(self):
        # SimpleHTTPRequestHandler shares this static response path between GET and HEAD.
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if query.get("demo", [""])[0] in {"book", "arc"} and query.get("view", ["home"])[0] in {"home", ""}:
                page = (FRONTEND / "gallery-closed.html").open("rb")
                self.send_response(410)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(os.fstat(page.fileno()).st_size))
                self.end_headers()
                return page
        return super().send_head()

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin == "http://" + self.headers.get("Host", ""):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Console-Token")

    def _local_request(self, *, write=False):
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise PermissionError("请从本机控制台地址访问")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            raise PermissionError("人物配置只接受控制台同源请求")
        if write and not secrets.compare_digest(self.headers.get("X-Console-Token", ""), CONSOLE_TOKEN):
            raise PermissionError("控制台会话已更新，请刷新后重试")

    def _personalization(self, persona, *, registering=False):
        store = Store(environment=CONSOLE_ENVIRONMENT, persona=persona,
                      relation="partner" if persona.startswith("companion_") else None)
        if store.is_companion:
            from persona_onboarding import metadata
            metadata(store)
            if not registering and not store.selected()[0]:
                raise PermissionError("请先确认创建伙伴")
        elif persona not in PERSONAS or registering:
            raise ValueError("invalid persona")
        return store

    def _image(self, image):
        data = image.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _onboarding_get(self, path, persona, query):
        self._local_request()
        import persona_onboarding as onboarding
        from codex_subscription import for_store
        if path in {"/api/onboarding", "/api/onboarding/setup"}:
            result = {**onboarding.presets(), "csrf_token": CONSOLE_TOKEN}
            if path == "/api/onboarding":
                result["people"] = onboarding.directory(self._personalization("xiaotian"))
            return self._json(200, result)
        if path in {"/api/onboarding/qq", "/api/onboarding/diagnostics"}:
            from companion_qq import status
            store = self._personalization(persona)
            if not store.is_companion:
                raise ValueError("此角色由现有部署管理 QQ")
            if path.endswith("/diagnostics"):
                return self._json(200, onboarding.diagnostics(store, for_store(store)))
            return self._json(200, status(store))
        store = self._personalization(persona, registering=True)
        if path == "/api/onboarding/state":
            return self._json(200, onboarding.state(store, for_store(store)))
        if path == "/api/onboarding/image":
            from appearance_tasks import asset
            task = onboarding.metadata(store)["task"]
            return self._image(asset(store, task, (query.get("name") or [None])[0]))
        return self._json(404, {"ok": False, "error": "not_found"})

    def _onboarding_post(self, path, persona, payload):
        import persona_onboarding as onboarding
        from codex_subscription import for_store
        if path == "/api/onboarding/begin":
            store = onboarding.begin(self._personalization("xiaotian"), request_id=payload.get("request_id"))
            return self._json(201, onboarding.state(store, for_store(store)))
        if path in ("/api/onboarding/qq/verify", "/api/onboarding/qq/enable", "/api/onboarding/qq/disable"):
            import companion_qq
            store = self._personalization(persona)
            if not store.is_companion:
                raise ValueError("此角色由现有部署管理 QQ")
            action = path.rsplit("/", 1)[1]
            return self._json(200, getattr(companion_qq, action)(store, payload))
        store = self._personalization(persona, registering=True)
        profile = for_store(store)
        name = path.rsplit("/", 1)[1]
        if name == "login":
            return self._json(200, profile.login())
        if name == "cancel_login":
            profile.cancel()
        else:
            onboarding.action(store, name, payload, profile)
        return self._json(200, onboarding.state(store, profile))

    def _json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            path, persona, query = _request(self.path)
            if path == "/api/onboarding" or path.startswith("/api/onboarding/"):
                return self._onboarding_get(path, persona, query)
            if path == "/api/personalization":
                self._local_request()
                return self._json(200, {**self._personalization(persona).state(), "csrf_token": CONSOLE_TOKEN})
            if path == "/api/personalization/appearance":
                self._local_request()
                from appearance_tasks import listing
                return self._json(200, {"tasks": listing(self._personalization(persona))})
            if path in {"/api/personalization/appearance/image", "/api/personalization/applied-image"}:
                self._local_request()
                name = (query.get("name") or [None])[0]
                if path.endswith("applied-image"):
                    from personalization_view import applied_asset
                    image = applied_asset(self._personalization(persona), name, version=(query.get("version") or [None])[0])
                else:
                    from appearance_tasks import asset
                    image = asset(self._personalization(persona), (query.get("task_id") or [None])[0], name)
                return self._image(image)
            raw_date = (query.get("date") or [None])[0]
            date = dt.date.fromisoformat(raw_date) if raw_date else location.local_time().date()
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "persona-console", "branch": repository_branch()})
            if path in {"/api/schedule", "/api/location-config", "/api/location"}:
                self._personalization(persona)
            if path == "/api/schedule":
                value = {"persona_id": persona, **ui_schedule.read(persona)}
                start = (query.get("start") or [None])[0]
                end = (query.get("end") or [None])[0]
                if start or end:
                    if not start or not end or raw_date:
                        raise ValueError("specify either a date or a complete date range")
                    start, end = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
                    if not 0 <= (end - start).days < 42:
                        raise ValueError("schedule range must contain 1 to 42 days")
                # Calendar browsing must not bind image probes for every visible day.
                from preview_mode import preview
                with preview():
                    if start:
                        value["days"] = {}
                        for offset in range((end - start).days + 1):
                            day = start + dt.timedelta(days=offset)
                            value["days"][day.isoformat()] = location.schedule_day(persona, day)
                    elif raw_date:
                        value["day"] = location.schedule_day(persona, date)
                return self._json(200, value)
            if path == "/api/location-config":
                return self._json(200, location.load(persona))
            if path == "/api/location":
                import location_agent
                view, agent = location_agent.view(persona, date,
                    generate_plan=(query.get("plan") or [None])[0] == "1" and not persona.startswith("companion_"),
                    retry=(query.get("retry") or [None])[0] == "1")
                now = location.local_time()
                current = location.current(persona, now, view=view) if date == now.date() else {
                    "status": "unknown", "photo_ok": False, "reason": "different_date"}
                return self._json(200, {**current, "persona_id": persona, "timeline": view, "agent": agent})
        except Conflict as exc:
            return self._json(409, {"ok": False, "error": str(exc)})
        except PermissionError as exc:
            return self._json(403, {"ok": False, "error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except OSError as exc:
            self.log_error("runtime read failed: %s", exc)
            return self._json(500, {"ok": False, "error": "runtime storage unavailable"})
        if path.startswith("/api/"):
            return self._json(404, {"ok": False, "error": "not_found"})
        return super().do_GET()

    def do_POST(self):
        try:
            path, query_persona, _ = _request(self.path)
            persona_actions = {"/api/personalization/" + action for action in ("draft", "preview", "apply", "rollback", "reset")}
            appearance_actions = {"/api/personalization/appearance/" + action for action in ("create", "start", "cancel", "approve")}
            persona_actions |= appearance_actions
            onboarding_action = path.startswith("/api/onboarding/")
            if path not in ("/api/location-config", "/api/schedule") and path not in persona_actions and not onboarding_action:
                return self._json(404, {"ok": False, "error": "not_found"})
            if path in persona_actions or onboarding_action:
                self._local_request(write=True)
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("需要application/json")
            length = int(self.headers.get("Content-Length", "0"))
            limit = 90 * 1024 * 1024 if path in {"/api/personalization/appearance/create", "/api/onboarding/generate"} else 65536
            if not 0 < length <= limit:
                raise ValueError("invalid payload size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("invalid payload")
            persona = payload.get("persona_id", query_persona)
            if onboarding_action:
                return self._onboarding_post(path, identifier(persona), payload)
            store = self._personalization(identifier(persona))
            if store.is_companion:
                self._local_request(write=True)
            if path in persona_actions:
                action = path.rsplit("/", 1)[1]
                if path in appearance_actions:
                    import appearance_tasks
                    if action == "create":
                        result = appearance_tasks.create(store, payload.get("photos"), revision=payload.get("revision"))
                    elif action == "start" and store.is_companion:
                        from codex_subscription import for_store
                        profile = for_store(store)
                        if profile.status()["status"] != "connected":
                            raise ValueError("请在首页连接 ChatGPT 订阅")
                        result = appearance_tasks.start(store, payload.get("task_id"), provider=profile.appearance)
                    else:
                        result = getattr(appearance_tasks, action)(store, payload.get("task_id"))
                elif action == "draft":
                    result = store.save(payload.get("values"), revision=payload.get("revision"), candidate=payload.get("candidate"))
                elif action == "preview":
                    result = store.preview(revision=payload.get("revision"))
                elif action == "reset":
                    result = store.reset(revision=payload.get("revision"), expected_version=payload.get("expected_version"))
                elif action == "apply":
                    result = store.apply(revision=payload.get("revision"), preview_digest=payload.get("preview_digest"))
                else:
                    result = store.rollback(version=payload.get("version"), expected_version=payload.get("expected_version"))
                return self._json(200, result)
            if path == "/api/location-config":
                clean = _save_routes(persona, payload.get("routes"))
                return self._json(200, {"ok": True, "persona_id": persona, "routes": len(clean)})
            clean = ui_schedule.save(persona, day=payload.get("day"), date=payload.get("date"),
                                     blocks=payload.get("blocks"), reset=payload.get("reset") is True,
                                     reference_date=payload.get("reference_date"))
            return self._json(200, {"ok": True, "persona_id": persona, "blocks": len(clean)})
        except Conflict as exc:
            return self._json(409, {"ok": False, "error": str(exc)})
        except ValidationError as exc:
            return self._json(422, {"ok": False, "error": str(exc), "fields": exc.errors})
        except PermissionError as exc:
            return self._json(403, {"ok": False, "error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except OSError as exc:
            self.log_error("runtime write failed: %s", exc)
            return self._json(500, {"ok": False, "error": "runtime storage unavailable"})


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5174)
    ap.add_argument("--data-dir", type=pathlib.Path, help="isolated console data directory for tests")
    ap.add_argument("--environment", choices=("dev", "test"), default="dev")
    args = ap.parse_args()
    if args.data_dir:
        location.CONFIG = args.data_dir / "persona_locations.json"
        ui_schedule.OVERRIDES = args.data_dir / "ui_schedule_overrides.json"
        os.environ["XIAOTIAN_PERSONALIZATION_ROOT"] = str(args.data_dir / "persona_personalization")
    CONSOLE_ENVIRONMENT = args.environment
    os.environ["PERSONALIZATION_ENVIRONMENT"] = args.environment
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Persona console: http://127.0.0.1:{server.server_port}/", flush=True)
    server.serve_forever()
