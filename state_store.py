"""Durable SQLite state for long-running scans: checkpoint/resume,
cross-run finding dedup + liveness caching, and rate-limit windows.

Synchronous on purpose: the engine is a single-threaded async pipeline, so
coroutines serialize naturally and indexed lookups are sub-millisecond.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid

SCHEMA_VERSION = 1

LIVE = "live"
DEAD = "dead"
ERROR = "error"
NOBALANCE = "nobalance"
_TRUSTED = {DEAD, NOBALANCE}  # statuses we never re-verify

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    run_id TEXT PRIMARY KEY, config_sig TEXT,
    started_at REAL, finished_at REAL, status TEXT);
CREATE TABLE IF NOT EXISTS progress(
    run_id TEXT, source TEXT, query TEXT, done_at REAL,
    PRIMARY KEY(run_id, source, query));
CREATE TABLE IF NOT EXISTS findings(
    digest TEXT PRIMARY KEY, source TEXT, key TEXT, url TEXT,
    repo TEXT, file TEXT, status TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS run_findings(
    run_id TEXT, digest TEXT, PRIMARY KEY(run_id, digest));
CREATE TABLE IF NOT EXISTS key_liveness(
    key TEXT PRIMARY KEY, status TEXT, checked_at REAL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


class StateStore:
    def __init__(self, path: str = "results/state.db", use_state: bool = True):
        self.path = path if use_state else ":memory:"
        if use_state:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = self._connect(self.path)
        self._init_schema()

    def _connect(self, path: str) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("SELECT count(*) FROM sqlite_master")  # smoke-test readability
            return conn
        except sqlite3.DatabaseError:
            if path != ":memory:" and os.path.exists(path):
                os.rename(path, f"{path}.corrupt-{int(time.time())}")
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def start_or_resume_run(self, config_sig: str, resume: bool = False) -> str:
        if resume:
            row = self._conn.execute(
                "SELECT run_id FROM runs WHERE config_sig=? AND status='running' "
                "ORDER BY started_at DESC LIMIT 1",
                (config_sig,),
            ).fetchone()
            if row:
                return row["run_id"]
        run_id = f"run-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        self._conn.execute(
            "INSERT INTO runs(run_id, config_sig, started_at, status) VALUES(?,?,?,'running')",
            (run_id, config_sig, time.time()),
        )
        self._conn.commit()
        return run_id

    def finish_run(self, run_id: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status='done', finished_at=? WHERE run_id=?",
            (time.time(), run_id),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
