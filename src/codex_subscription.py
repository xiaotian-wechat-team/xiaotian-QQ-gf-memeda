"""Local ChatGPT authorization and isolated Codex calls for new companions."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from personalization_store import checked

AUTH_CONFIG = ["-c", 'cli_auth_credentials_store="file"', "-c", 'forced_login_method="chatgpt"']
ISOLATED_EXEC_CONFIG = [*AUTH_CONFIG, "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                        "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "unified_exec",
                        "--disable", "enable_mcp_apps", "--enable", "skip_host_skill_discovery",
                        "-c", 'approval_policy="never"', "-c", 'web_search="disabled"']
BUNDLED_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")


class Unavailable(ValueError):
    pass


def remaining_time(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Unavailable("请求已超时，请稍后重试")
    return remaining


def lock_before(lock, deadline):
    while True:
        remaining_time(deadline)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(min(0.05, remaining_time(deadline)))


def executable():
    command = shutil.which("codex")
    if not command and os.access(BUNDLED_CODEX, os.X_OK):
        command = str(BUNDLED_CODEX)
    if not command:
        raise Unavailable("本机找不到 Codex CLI，请安装后重试")
    return command


def child_environment():
    # No provider API keys or credentials are inherited from the workbench host.
    keep = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy"}
    return {key: value for key, value in os.environ.items() if key in keep}


class AppServer:
    """One private stdio session keeps the official OAuth callback alive."""

    def __init__(self, profile, *, deadline=None):
        self.inbox = queue.Queue()
        self.events = {}
        self.sequence = 0
        self.process = subprocess.Popen(
            [executable(), "app-server", *AUTH_CONFIG], env=profile.environment(), cwd=profile.directory,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )
        threading.Thread(target=self._read, name="companion-oauth", daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "persona_runtime", "title": "Persona Runtime", "version": "1.0"}},
                      deadline=deadline)
            self._send({"method": "initialized", "params": {}})
        except Exception:
            self.close(deadline=deadline)
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "id" in message:
                    self.inbox.put(message)
                elif message.get("method") == "account/login/completed":
                    event = message["params"]
                    self.events[event.get("loginId")] = bool(event.get("success"))
        finally:
            self.inbox.put(None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params, *, deadline=None):
        self.sequence += 1
        try:
            self._send({"id": self.sequence, "method": method, "params": params})
            timeout = 6 if deadline is None else min(6, remaining_time(deadline))
            response = self.inbox.get(timeout=timeout)
        except (OSError, queue.Empty) as exc:
            raise Unavailable("Codex 登录服务响应超时，请重试") from exc
        if not response or response.get("id") != self.sequence or "result" not in response:
            raise Unavailable("Codex 登录服务未能完成请求，请重试")
        return response["result"]

    def close(self, *, deadline=None):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                timeout = 2 if deadline is None else min(2, max(0, deadline - time.monotonic()))
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


class Profile:
    def __init__(self, directory):
        self.directory = checked(directory)
        self.server = None
        self.pending = None
        self.guard = threading.Lock()

    def environment(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        return {**child_environment(), "CODEX_HOME": str(self.directory)}

    def _rpc(self, *, deadline=None):
        if os.environ.get("XIAOTIAN_TEST"):
            raise Unavailable("测试环境未配置 ChatGPT 测试适配器")
        if self.server is None or self.server.process.poll() is not None:
            if self.server is not None:
                self.server.close()
            self.server = AppServer(self, deadline=deadline)
            self.pending = None
        return self.server

    def close(self):
        with self.guard:
            if self.server:
                self.server.close()
                self.server = None
                self.pending = None

    def status(self, *, deadline=None):
        if deadline is None:
            self.guard.acquire()
        elif not self.guard.acquire(timeout=remaining_time(deadline)):
            raise Unavailable("等待 ChatGPT 授权检查超时，请重试")
        try:
            try:
                rpc = self._rpc(deadline=deadline)
                result = rpc.call("account/read", {"refreshToken": False}, deadline=deadline)
            except (Unavailable, OSError) as exc:
                if self.server:
                    self.server.close(deadline=deadline)
                    self.server = None
                    self.pending = None
                return {"status": "unavailable", "message": str(exc) if isinstance(exc, Unavailable) else "无法启动本机 Codex"}
            account = result.get("account") or {}
            if self.pending:
                complete = rpc.events.pop(self.pending["loginId"], None)
                if complete is None:
                    return {"status": "pending", "auth_url": self.pending["authUrl"]}
                self.pending = None
                if not complete:
                    return {"status": "signed_out", "message": "这次授权未完成，请重新登录"}
            if account.get("type") == "chatgpt":
                return {"status": "connected", "plan": account.get("planType")}
            return {"status": "signed_out"}
        finally:
            self.guard.release()

    def login(self):
        with self.guard:
            rpc = self._rpc()
            if self.pending and self.pending["loginId"] not in rpc.events:
                return {"status": "pending", "auth_url": self.pending["authUrl"]}
            result = rpc.call("account/login/start", {"type": "chatgpt"})
            url = urlsplit(result.get("authUrl", ""))
            if (result.get("type") != "chatgpt" or not result.get("loginId") or url.scheme != "https"
                    or url.hostname not in {"auth.openai.com", "chatgpt.com", "auth.chatgpt.com"} or url.username or url.password):
                raise Unavailable("Codex 未返回有效的 ChatGPT 授权链接")
            self.pending = result
            return {"status": "pending", "auth_url": result["authUrl"]}

    def cancel(self):
        with self.guard:
            if self.pending and self.server and self.server.process.poll() is None:
                self.server.call("account/login/cancel", {"loginId": self.pending["loginId"]})
            self.pending = None

    @contextmanager
    def inference(self, deadline):
        if os.environ.get("XIAOTIAN_TEST"):
            raise Unavailable("测试环境禁止真实 GPT 调用")
        env = self.environment()
        with os.fdopen(os.open(self.directory / ".inference.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as lock:
            lock_before(lock, deadline)
            status = self.status(deadline=deadline)
            if status["status"] != "connected":
                raise Unavailable(status.get("message") or "请在工作台连接自己的 ChatGPT 订阅后重试")
            yield env

    def complete(self, prompt, *, instructions=None, schema=None, timeout=180):
        deadline = time.monotonic() + timeout
        with self.inference(deadline) as env, tempfile.TemporaryDirectory(prefix="persona-text-") as folder:
            workspace = Path(folder)
            output = workspace / "response.txt"
            args = [executable(), "exec", *ISOLATED_EXEC_CONFIG, "--disable", "image_generation", "-o", str(output)]
            if instructions:
                args.extend(["-c", "developer_instructions=" + json.dumps(instructions, ensure_ascii=False)])
            if schema is not None:
                contract = workspace / "response.schema.json"
                contract.write_text(json.dumps(schema), encoding="utf-8")
                args.extend(["--output-schema", str(contract)])
            try:
                result = subprocess.run([*args, "-"], input=prompt, text=True, cwd=workspace,
                                        env=env, capture_output=True, timeout=remaining_time(deadline))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise Unavailable("GPT 推理未完成，请检查授权后重试") from exc
            if result.returncode or not output.is_file():
                raise Unavailable("GPT 未返回结果，请检查 ChatGPT 授权、额度与网络")
            message = output.read_text(encoding="utf-8").strip()
            if not message:
                raise Unavailable("GPT 返回了空结果，请重试")
            remaining_time(deadline)
            return message

    def appearance(self, _stage, prompt, refs, output, _cfg):
        deadline = time.monotonic() + 900
        with self.inference(deadline) as env, tempfile.TemporaryDirectory(prefix="persona-image-") as workspace:
            image_dir = checked(self.directory / "generated_images")
            # The inference lock covers both snapshots and the generating process.
            existing = set(image_dir.rglob("*.png"))
            args = [executable(), "exec", "--json", *ISOLATED_EXEC_CONFIG, "--enable", "image_generation"]
            for ref in refs:
                args.extend(["-i", str(checked(ref))])
            try:
                result = subprocess.run([*args, "--", "-"], input=prompt + "\n使用内置生图工具生成一张图后停止。",
                                        text=True, capture_output=True, env=env, cwd=workspace, timeout=remaining_time(deadline))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise Unavailable("生图未完成，请检查 ChatGPT 连接后重试") from exc
            if result.returncode:
                raise Unavailable("生图失败，请检查 ChatGPT 授权、额度与网络后重试")
            produced = {path for path in image_dir.rglob("*.png") if path.is_file()} - existing
            if not produced:
                raise Unavailable("此阶段没有返回新生成的 PNG，请重试当前阶段")
            latest = max(map(checked, produced), key=lambda path: (path.stat().st_mtime_ns, str(path)))
            remaining_time(deadline)
            shutil.copyfile(latest, checked(output))


_profiles = {}
_profiles_guard = threading.Lock()


def for_store(store):
    path = store.destination / "onboarding" / "codex"
    with _profiles_guard:
        if path not in _profiles:
            _profiles[path] = Profile(path)
            atexit.register(_profiles[path].close)
        return _profiles[path]
