"""One local worker per verified companion QQ binding."""
from __future__ import annotations

import fcntl
import json
import os
import time

from companion_qq import _active, _registry_lock, _scope
from channels.onebot_qq import OneBotQQRunner
from personalization_runtime import process_started
from personalization_store import checked, timestamp, write_json


class CompanionRunner(OneBotQQRunner):
    def __init__(self, store, data):
        super().__init__(data["endpoint"], data["token"], dry=False, reconnect=False)
        self.store = store
        self.data = data
        self.connected = False
        self.started = process_started(os.getpid())

    def _uri(self):
        return self.ws_uri

    def record(self, state):
        self._record_active("qq-worker.json", state=state, pid=os.getpid(), started=self.started)

    def _record_active(self, filename, **details):
        with _registry_lock(self.store):
            if _active(self.store):
                write_json(self.store.directory / filename, dict(generation=self.data["generation"], at=timestamp(), **details))

    def on_open(self, ws):
        self.connected = False
        self.record("verifying")
        super().on_open(ws)

    def on_message(self, ws, message):
        try:
            event = json.loads(message)
        except ValueError:
            return
        if not isinstance(event, dict) or not _active(self.store):
            return
        if event.get("echo") == "login":
            if event.get("status") != "ok" or event.get("retcode") != 0 or str((event.get("data") or {}).get("user_id")) != self.data["bot"]:
                self.record("account_changed")
                self._stop = True
                ws.close()
                return
            self.connected = True
            self.record("connected")
        elif not self.connected or event.get("post_type") not in {"message", None}:
            return
        if event.get("post_type") == "message" and (str(event.get("self_id")) != self.data["bot"]
                or event.get("message_type") != "private" or str(event.get("user_id")) != self.data["peer"]):
            return
        if event.get("post_type") == "message" and isinstance(event.get("message"), list):
            text_segments = [segment for segment in event["message"] if isinstance(segment, dict) and segment.get("type") == "text"]
            if not text_segments:
                return
            event["message"] = text_segments
            message = json.dumps(event, ensure_ascii=False)
        super().on_message(ws, message)

    def _send_outbound(self, out, account=""):
        if (not _active(self.store) or not self.connected or (out.bot_account_id or account) != self.data["bot"]
                or out.peer_id != self.data["peer"]):
            raise PermissionError("当前 QQ 绑定已失效")
        # A recovered delivery may contain text generated before a later apply.
        version = (out.meta or {}).get("persona_version")
        try:
            receipt = super()._send_outbound(out, account)
        except Exception as exc:
            self._receipt(getattr(exc, "delivery_status", "failed"), version)
            raise
        self._receipt("confirmed", version, message_id=(receipt.get("data") or {}).get("message_id"))
        return receipt

    def _handle_event(self, ev, inbound=None):
        if not _active(self.store):
            return
        try:
            super()._handle_event(ev, inbound)
        except Exception:
            if _active(self.store):
                self._receipt("reply_failed", None)
            raise

    def _receipt(self, state, version, **details):
        self._record_active("qq-receipt.json", state=state, version=version,
                            peer=self.data["peer"], bot=self.data["bot"], **details)

    def _recover_pending(self):
        if not _active(self.store):
            self._stop = True
            if self._ws:
                self._ws.close()
            return
        if self.connected:
            super()._recover_pending()


def main():
    store = _scope()
    data = _active(store)
    if not data:
        return
    lock_path = checked(store.directory / (".qq-" + data["generation"] + ".lock"))
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        runner = CompanionRunner(store, data)
        delay = 1
        while _active(store) and not runner._stop:
            runner.record("connecting")
            runner.run()
            if runner._stop:
                break
            runner.connected = False
            runner.record("reconnecting")
            for _ in range(delay * 4):
                if not _active(store):
                    return
                time.sleep(0.25)
            delay = min(15, delay * 2)


if __name__ == "__main__":
    main()
