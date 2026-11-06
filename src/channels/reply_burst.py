"""Collect a conversational turn. Persona SSOT: data/persona_dossier.json.

Quiet window resets on incoming messages; total collection is bounded.
No models, transport or persistence here.
"""
import hashlib
import time
from dataclasses import replace


def collect(first, drain, *, clock=time.monotonic, sleep=time.sleep, activity=None, cfg=None):
    from reply_engagement import options
    opts = options(cfg)
    start = last = clock()
    items = [first]
    while True:
        newer = drain()
        if newer:
            items.extend(newer)
            last = clock()
        now = clock()
        lively = opts["enabled"] and (len(items) >= 3 or
                    (activity or {}).get("level") in ("active", "dense"))
        quiet = float(opts["collect_quiet_sec"]) if lively else (10 if len(items) >= 3 else 6)
        ceiling = float(opts["collect_max_sec"]) if lively else 45
        remaining = min(start + ceiling, last + quiet) - now
        if remaining <= 0:
            return items
        sleep(min(.2, remaining))


def combine(messages):
    if len(messages) == 1:
        return messages[0]
    if len({m.alias_key for m in messages}) != 1:
        raise ValueError('Cannot combine messages from different peers')
    text = '\n'.join(m.text for m in messages)
    key = '|'.join(m.msg_id or m.ts + m.text for m in messages)
    return replace(messages[-1], text=text,
                   msg_id='burst:' + hashlib.sha256(key.encode()).hexdigest(),
                   raw={**messages[-1].raw, 'burst_messages': messages})
