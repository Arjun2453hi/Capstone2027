"""Local durable task memory and bounded rendered-evidence cache."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def proposal_id(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


class WorkspaceStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workspace.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    source TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'pending', updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    payload TEXT NOT NULL);
            """)
        for run in self.runs():
            if run["status"] in ("queued", "running"):
                self.update_run(run["id"], status="interrupted", message="Server restarted. Start a new run; completed proposals are retained.")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_proposals(self, items, source):
        ids = []
        with self.lock, self.connect() as db:
            for item in items:
                key = proposal_id(item)
                db.execute("INSERT OR IGNORE INTO proposals(id,payload,source,updated) VALUES (?,?,?,?)",
                           (key, json.dumps(item, ensure_ascii=False), source, time.time()))
                ids.append(key)
            # Retain approved edits. Prune the oldest unapproved proposals.
            db.execute("DELETE FROM proposals WHERE decision != 'approved' AND id NOT IN (SELECT id FROM proposals ORDER BY updated DESC LIMIT 300)")
        return ids

    def remove_unapproved(self, source):
        """Drop proposals from one source (e.g. "saved") unless approved."""
        with self.lock, self.connect() as db:
            return db.execute("DELETE FROM proposals WHERE source=? AND decision != 'approved'", (source,)).rowcount

    def proposals(self):
        with self.lock, self.connect() as db:
            return [{"id": r["id"], "source": r["source"], "decision": r["decision"], **json.loads(r["payload"])}
                    for r in db.execute("SELECT * FROM proposals ORDER BY updated DESC, id")]

    def decide(self, key, decision):
        with self.lock, self.connect() as db:
            return db.execute("UPDATE proposals SET decision=?,updated=? WHERE id=?", (decision, time.time(), key)).rowcount

    def create_run(self, kind, **fields):
        key = uuid4().hex
        run = {"id": key, "kind": kind, "status": "queued", "created_at": time.time(), **fields}
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?)", (key, json.dumps(run), time.time()))
            # Keep bounded history, but never evict active tasks.
            rows = db.execute("SELECT id,payload FROM runs ORDER BY updated DESC").fetchall()
            for row in rows[100:]:
                if json.loads(row["payload"])["status"] not in ("queued", "running"):
                    db.execute("DELETE FROM events WHERE run_id=?", (row["id"],))
                    db.execute("DELETE FROM runs WHERE id=?", (row["id"],))
        return run

    def update_run(self, key, **fields):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (key,)).fetchone()
            if row:
                run = {**json.loads(row[0]), **fields}
                db.execute("UPDATE runs SET payload=?,updated=? WHERE id=?", (json.dumps(run), time.time(), key))

    def runs(self):
        with self.lock, self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM runs ORDER BY updated DESC LIMIT 110")]

    def event(self, key, event, **fields):
        with self.lock, self.connect() as db:
            payload = {"event": event, "at": time.time(), **fields}
            db.execute("INSERT INTO events(run_id,payload) VALUES (?,?)", (key, json.dumps(payload)))
            db.execute("DELETE FROM events WHERE run_id=? AND seq NOT IN (SELECT seq FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 150)", (key, key))

    def events(self, key):
        with self.lock, self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE run_id=? ORDER BY seq", (key,))]


class EvidenceCache:
    """TTL + LRU + byte budget. Cache PNG bytes, never open PDF/PPTX handles."""
    def __init__(self, max_bytes=12_000_000, max_entries=12, ttl_seconds=300, clock=time.monotonic):
        self.max_bytes, self.max_entries, self.ttl = max_bytes, max_entries, ttl_seconds
        self.clock, self.items, self.lock = clock, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            self._expire()
            found = self.items.get(key)
            if found:
                self.items.move_to_end(key)
                return found[1]
        return None

    def put(self, key, value):
        if len(value) > self.max_bytes:
            return
        with self.lock:
            self._expire()
            self.items.pop(key, None)
            self.items[key] = (self.clock(), value)
            while len(self.items) > self.max_entries or sum(len(v[1]) for v in self.items.values()) > self.max_bytes:
                self.items.popitem(last=False)

    def _expire(self):
        for key, (created, _) in list(self.items.items()):
            if self.clock() - created >= self.ttl:
                del self.items[key]
