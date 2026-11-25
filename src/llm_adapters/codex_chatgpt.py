"""The companion runtime's user-authorized ChatGPT adapter."""
import json

from codex_subscription import for_store
from config import load
from personalization_store import Store


def complete(messages, *, model=None, timeout=None):
    scope = load()["personalization"]
    store = Store(scope["root"], environment=scope["environment"], persona=scope["role_id"], relation=scope["relation_id"])
    instructions = "\n".join(m["content"] for m in messages if m["role"] == "system")
    conversation = [m for m in messages if m["role"] != "system"]
    return for_store(store).complete(json.dumps(conversation, ensure_ascii=False), instructions=instructions, timeout=timeout or 120)
