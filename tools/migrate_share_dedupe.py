#!/usr/bin/env python3
"""Backfill share dedupe only; never generate, open UI, or send messages.

Persona SSOT: data/persona_dossier.json. Only contact demo_contact.
Run from repository root with .venv/bin/python tools/migrate_share_dedupe.py.
"""
import fcntl
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import memory
import share_dedupe as sd
from proactive_sender import QUEUE, load_q
from channels import outbox_store


def main():
    cid = 'demo_contact'
    memory.cdir(cid)  # enforce configured whitelist
    if not (memory.cdir(cid) / 'STATE.json').is_file():
        raise RuntimeError('Existing demo_contact STATE.json required; refusing empty migration')
    with QUEUE.with_suffix('.worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        items = list(load_q().get('items', []))
        if outbox_store.DB_PATH.is_file():
            for channel in ('ilink', 'onebot'):
                for row in outbox_store.load(channel).get('items', []):
                    if str(row.get('contact') or 'demo_contact') != cid:
                        continue
                    original = row.get('proactive_round')
                    if original and row.get('status') in ('sent', 'pending', 'hold', '补发', None):
                        items.append({**original, 'status': 'sent' if row.get('status') == 'sent' else 'hold'})
        # A confirmed historical delivery takes precedence over uncertain retries.
        items.sort(key=lambda i: i.get('status') != 'sent')
        before = len(memory.read_state(cid).get(sd.STATE_KEY, {}))
        state = sd.migrate(cid, items)
        print(json.dumps({'contact': cid, 'before': before,
                          'after': len(state.get(sd.STATE_KEY, {})), 'sent_messages': 0}))


if __name__ == '__main__':
    main()
