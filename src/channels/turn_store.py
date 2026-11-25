"""Durable recovery of unsent turns and immutable delivery plans. Persona: data/persona_dossier.json."""
import contextlib
import dataclasses
import json
import sqlite3
import time
import uuid
from pathlib import Path

from .types import Inbound


def encode_message(msg):
    return dataclasses.asdict(msg)


def decode_message(data):
    data = dict(data)
    raw = dict(data.get('raw') or {})
    if raw.get('burst_messages'):
        raw['burst_messages'] = [decode_message(m) for m in raw['burst_messages']]
    data['raw'] = raw
    return Inbound(**data)


class TurnStore:
    LEASE_SEC = 1800

    def __init__(self):
        import memory
        self.path = memory.CFG['db_path']
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS inbound_turns(
                key TEXT PRIMARY KEY, message TEXT NOT NULL, phase TEXT NOT NULL,
                plan TEXT, owner TEXT, lease REAL NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0)''')
            db.execute("CREATE INDEX IF NOT EXISTS inbound_recovery_due ON inbound_turns(next_attempt,lease) "
                       "WHERE phase NOT IN ('held','complete')")
            db.execute("CREATE INDEX IF NOT EXISTS inbound_held_lease ON inbound_turns(lease) "
                       "WHERE phase='held' AND plan IS NOT NULL")

    @contextlib.contextmanager
    def connect(self):
        with contextlib.closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            yield db

    def claim(self, key, msg, *, create=False, connection=None):
        def claim_in(db):
            row = db.execute('SELECT message,phase,plan,lease,attempts FROM inbound_turns WHERE key=?', (key,)).fetchone()
            now = time.time()
            if row and (row[1] in ('held', 'complete') or row[3] > now):
                return None
            if row is None and not create:
                return None  # Old deliveries have no safe recovery evidence.
            owner = uuid.uuid4().hex
            if row:
                db.execute('UPDATE inbound_turns SET owner=?,lease=?,attempts=attempts+1 WHERE key=?',
                           (owner, now+self.LEASE_SEC, key))
                phase, plan, attempts = row[1], json.loads(row[2]) if row[2] else None, row[4]+1
            else:
                phase, plan, attempts = 'generating', None, 1
                db.execute('INSERT INTO inbound_turns(key,message,phase,owner,lease,attempts) VALUES(?,?,?,?,?,?)',
                           (key, json.dumps(encode_message(msg), ensure_ascii=False, default=str), phase, owner,
                            now+self.LEASE_SEC, attempts))
            return Turn(self, key, owner, phase, plan, attempts)
        if connection is not None:
            result = claim_in(connection)
            connection.commit()
            return result
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return claim_in(db)

    def pending(self, channel, *, bot_account_id=None, limit=1):
        now = time.time()
        with self.connect() as db:
            # A process can die just after a late ACK confirms an otherwise
            # held turn. Promote only an explicitly confirmed, unbookkept piece;
            # unknown sends remain held and can never be blindly repeated.
            for key, raw in db.execute("SELECT key,plan FROM inbound_turns WHERE phase='held' AND plan IS NOT NULL AND lease<=?", (now,)).fetchall():
                plan = json.loads(raw)
                index = next((i for i in range(len(plan['planned'])) if i not in plan.get('bookkept', [])), None)
                all_bookkept = index is None and bool(plan['planned'])
                operation_id = 'bridge:' + plan['turn_id'] + ':' + str(index)
                if all_bookkept or (db.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone() and db.execute(
                        "SELECT 1 FROM receipts WHERE operation_id=? AND status='confirmed'", (operation_id,)).fetchone()):
                    db.execute("UPDATE inbound_turns SET phase='retry_plan' WHERE key=? AND phase='held'", (key,))
            rows = db.execute("SELECT key,message FROM inbound_turns WHERE phase NOT IN ('held','complete') "
                              'AND lease<=? AND next_attempt<=? ORDER BY next_attempt,rowid', (now, now)).fetchall()
        result = []
        for key, raw in rows:
            msg = decode_message(json.loads(raw))
            if msg.channel == channel and (bot_account_id is None or msg.bot_account_id == bot_account_id):
                result.append((key, msg))
                if len(result) >= limit:
                    break
        return result


class Turn:
    def __init__(self, store, key, owner, phase, plan, attempts):
        self.store, self.key, self.owner = store, key, owner
        self.phase, self.plan, self.attempts = phase, plan, attempts

    def update(self, phase, *, plan=None, delay=0, release=True):
        if plan is not None:
            self.plan = plan
        with self.store.connect() as db:
            db.execute('UPDATE inbound_turns SET phase=?,plan=?,lease=?,next_attempt=? WHERE key=? AND owner=?',
                       (phase, json.dumps(self.plan, ensure_ascii=False, default=str) if self.plan else None,
                        0 if release else time.time()+self.store.LEASE_SEC, time.time()+delay, self.key, self.owner))
        self.phase = phase

    def failed(self):
        if self.phase not in ('held', 'complete'):
            self.update('retry_plan' if self.plan else 'retry_generate', delay=min(300, 5*2**min(self.attempts, 6)))

    def bookkept(self, index):
        self.plan.setdefault('bookkept', []).append(index)
        with self.store.connect() as db:
            db.execute('UPDATE inbound_turns SET plan=? WHERE key=? AND owner=?',
                       (json.dumps(self.plan, ensure_ascii=False, default=str), self.key, self.owner))
