# Durability Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give long-running and continuous scans a durable SQLite state layer so they resume after a crash, never re-verify keys they've already checked, and back off intelligently when GitHub rate-limits them.

**Architecture:** A new top-level `state_store.py` (`StateStore`, stdlib `sqlite3`, WAL) records run progress, findings (cross-run dedup), and key liveness. `scanner_engine.py` consults it via thin hooks in `_search_all` (resume) and the verify loop (re-check gate). A `RateLimiter` in `scanners/base.py` makes the three GitHub search scanners honor `X-RateLimit-Reset` / `Retry-After`, persisting the block window so a restart respects it.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, `aiohttp`, `asyncio`, `pytest`, `ruff`.

## Global Constraints

- Python `>=3.10`; ruff `line-length = 120`, lint select `E, F, W, I`.
- **No new runtime dependency** — `sqlite3` is stdlib. `pyproject.toml` dependencies stay `aiohttp`, `requests`.
- **All tests are offline** (no network). Use `:memory:` SQLite and fake sessions; never hit GitHub or Zhipu.
- **Liveness-only orientation is unchanged** — never add balance/billing inspection. The verify path stays a `GET /models` check.
- Disclosure stays **off by default, dry-run by default**; `results/disclosed.json` remains the per-repo dedup authority and is **not** touched by this work.
- `results/` is gitignored except `.gitkeep`, so `results/state.db` needs no new gitignore entry.
- Every commit message ends with the trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

**Setup (run once if the worktree has no venv):**
```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```
All test/lint commands below use `.venv/bin/pytest` and `.venv/bin/ruff`.

---

### Task 1: Shared finding digest helper

**Files:**
- Modify: `scanners/base.py` (add `finding_digest`, reuse it inside `dedup_results`)
- Test: `tests/test_base_helpers.py` (append)

**Interfaces:**
- Produces: `finding_digest(result: dict) -> str` — MD5 hex of `f"{source}:{key}:{url}"`, the same identity `dedup_results` already uses.

- [ ] **Step 1: Write the failing test** — append to `tests/test_base_helpers.py`:

```python
from scanners.base import finding_digest


def test_finding_digest_is_stable_and_keyed_on_source_key_url():
    a = {"source": "github_code", "key": "K", "url": "u1", "repo": "a/b"}
    b = {"source": "github_code", "key": "K", "url": "u1", "repo": "DIFFERENT"}
    c = {"source": "github_issues", "key": "K", "url": "u1"}
    assert finding_digest(a) == finding_digest(b)   # repo is not part of identity
    assert finding_digest(a) != finding_digest(c)   # source is
    assert len(finding_digest(a)) == 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_base_helpers.py::test_finding_digest_is_stable_and_keyed_on_source_key_url -v`
Expected: FAIL with `ImportError: cannot import name 'finding_digest'`.

- [ ] **Step 3: Add the helper and reuse it in `dedup_results`** — in `scanners/base.py`, add this function above `dedup_results`:

```python
def finding_digest(result: dict) -> str:
    raw = f"{result.get('source', '')}:{result.get('key', '')}:{result.get('url', '')}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()
```

Then change the body of `dedup_results` to call it (replace the inline `hashlib.md5(...)` expression):

```python
def dedup_results(results: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for result in results:
        digest = finding_digest(result)
        if digest not in seen:
            seen.add(digest)
            out.append(result)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_base_helpers.py -v`
Expected: PASS (new test plus all existing base-helper tests still green).

- [ ] **Step 5: Commit**

```bash
git add scanners/base.py tests/test_base_helpers.py
git commit -m "refactor: extract finding_digest helper in base.py" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: StateStore module — schema, run lifecycle, corruption fallback

**Files:**
- Create: `state_store.py`
- Modify: `pyproject.toml:25` (add `state_store` to `py-modules`)
- Test: `tests/test_state_store.py`

**Interfaces:**
- Produces:
  - `StateStore(path: str = "results/state.db", use_state: bool = True)` — `use_state=False` ⇒ in-memory DB.
  - `start_or_resume_run(config_sig: str, resume: bool = False) -> str` (returns `run_id`)
  - `finish_run(run_id: str) -> None`
  - `close() -> None`
  - module constants `LIVE="live"`, `DEAD="dead"`, `ERROR="error"`, `NOBALANCE="nobalance"`, `SCHEMA_VERSION=1`

- [ ] **Step 1: Write the failing tests** — create `tests/test_state_store.py`:

```python
import sqlite3

from state_store import StateStore


def _store(tmp_path, use_state=True):
    return StateStore(path=str(tmp_path / "state.db"), use_state=use_state)


def test_start_run_mints_id_and_resume_reuses_running_run(tmp_path):
    s = _store(tmp_path)
    first = s.start_or_resume_run("sigA", resume=False)
    again = s.start_or_resume_run("sigA", resume=True)
    assert again == first  # resume reattaches to the still-running run
    s.finish_run(first)
    fresh = s.start_or_resume_run("sigA", resume=True)
    assert fresh != first  # finished run is not resumed
    s.close()


def test_resume_without_flag_always_mints_new(tmp_path):
    s = _store(tmp_path)
    a = s.start_or_resume_run("sig", resume=False)
    b = s.start_or_resume_run("sig", resume=False)
    assert a != b
    s.close()


def test_corrupt_db_is_moved_aside_and_reopened(tmp_path):
    db = tmp_path / "state.db"
    db.write_text("this is not a sqlite file")
    s = StateStore(path=str(db), use_state=True)  # must not raise
    rid = s.start_or_resume_run("sig")
    assert rid
    assert list(tmp_path.glob("state.db.corrupt-*"))  # bad file preserved
    s.close()


def test_memory_mode_uses_in_memory_db(tmp_path):
    s = StateStore(path=str(tmp_path / "nope.db"), use_state=False)
    s.start_or_resume_run("sig")
    assert not (tmp_path / "nope.db").exists()
    s.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'state_store'`.

- [ ] **Step 3: Create `state_store.py`** with the schema, lifecycle, and corruption handling:

```python
"""Durable SQLite state for long-running scans: checkpoint/resume,
cross-run finding dedup + liveness caching, and rate-limit windows.

Synchronous on purpose: the engine is a single-threaded async pipeline, so
coroutines serialize naturally and indexed lookups are sub-millisecond.
"""

from __future__ import annotations

import os
import sqlite3
import time

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
        run_id = f"run-{int(time.time() * 1000)}-{os.getpid()}"
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
```

- [ ] **Step 4: Add `state_store` to packaging** — in `pyproject.toml`, change line 25:

```toml
py-modules = ["scanner_engine", "state_store"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add state_store.py tests/test_state_store.py pyproject.toml
git commit -m "feat: StateStore schema, run lifecycle, corruption fallback" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: StateStore — progress checkpointing

**Files:**
- Modify: `state_store.py` (add `is_query_done`, `mark_query_done`)
- Test: `tests/test_state_store.py` (append)

**Interfaces:**
- Produces: `is_query_done(run_id, source, query) -> bool`, `mark_query_done(run_id, source, query) -> None`

- [ ] **Step 1: Write the failing test** — append to `tests/test_state_store.py`:

```python
def test_progress_marks_and_reads_per_run_source_query(tmp_path):
    s = _store(tmp_path)
    rid = s.start_or_resume_run("sig")
    assert s.is_query_done(rid, "github_code", "q1") is False
    s.mark_query_done(rid, "github_code", "q1")
    assert s.is_query_done(rid, "github_code", "q1") is True
    # different query / source / run are independent
    assert s.is_query_done(rid, "github_code", "q2") is False
    assert s.is_query_done(rid, "github_commits", "q1") is False
    assert s.is_query_done("other-run", "github_code", "q1") is False
    s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_state_store.py::test_progress_marks_and_reads_per_run_source_query -v`
Expected: FAIL with `AttributeError: 'StateStore' object has no attribute 'is_query_done'`.

- [ ] **Step 3: Implement** — add to `StateStore` (before `close`):

```python
    def is_query_done(self, run_id: str, source: str, query: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM progress WHERE run_id=? AND source=? AND query=?",
            (run_id, source, query),
        ).fetchone()
        return row is not None

    def mark_query_done(self, run_id: str, source: str, query: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO progress(run_id, source, query, done_at) VALUES(?,?,?,?)",
            (run_id, source, query, time.time()),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_state_store.py::test_progress_marks_and_reads_per_run_source_query -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add state_store.py tests/test_state_store.py
git commit -m "feat: StateStore progress checkpointing" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: StateStore — findings dedup + run rebuild

**Files:**
- Modify: `state_store.py` (add `record_finding`, `iter_run_findings`; import `finding_digest`)
- Test: `tests/test_state_store.py` (append)

**Interfaces:**
- Consumes: `scanners.base.finding_digest` (Task 1).
- Produces: `record_finding(run_id, finding: dict) -> None`, `iter_run_findings(run_id) -> list[dict]` (each dict has `source, key, url, repo, file`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_state_store.py`:

```python
def test_findings_dedup_by_digest_and_rebuild_for_run(tmp_path):
    s = _store(tmp_path)
    rid = s.start_or_resume_run("sig")
    f1 = {"source": "github_code", "key": "K", "url": "u1", "repo": "a/b", "file": ".env"}
    f2 = {"source": "github_code", "key": "K", "url": "u2", "repo": "a/b", "file": ".env"}
    s.record_finding(rid, f1)
    s.record_finding(rid, f1)  # exact duplicate -> collapses
    s.record_finding(rid, f2)  # same key, different url -> distinct location
    rebuilt = s.iter_run_findings(rid)
    assert len(rebuilt) == 2
    assert {r["url"] for r in rebuilt} == {"u1", "u2"}
    assert all(set(r) >= {"source", "key", "url", "repo", "file"} for r in rebuilt)
    s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_state_store.py::test_findings_dedup_by_digest_and_rebuild_for_run -v`
Expected: FAIL with `AttributeError: ... 'record_finding'`.

- [ ] **Step 3: Implement** — add the import at the top of `state_store.py` (after `import time`):

```python
from scanners.base import finding_digest
```

Then add to `StateStore`:

```python
    def record_finding(self, run_id: str, finding: dict) -> None:
        digest = finding_digest(finding)
        now = time.time()
        self._conn.execute(
            "INSERT INTO findings(digest, source, key, url, repo, file, status, first_seen, last_seen) "
            "VALUES(?,?,?,?,?,?,'',?,?) "
            "ON CONFLICT(digest) DO UPDATE SET last_seen=excluded.last_seen",
            (
                digest,
                finding.get("source", ""),
                finding.get("key", ""),
                finding.get("url", ""),
                finding.get("repo", ""),
                finding.get("file", ""),
                now,
                now,
            ),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO run_findings(run_id, digest) VALUES(?,?)",
            (run_id, digest),
        )
        self._conn.commit()

    def iter_run_findings(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT f.source, f.key, f.url, f.repo, f.file "
            "FROM findings f JOIN run_findings r ON r.digest = f.digest "
            "WHERE r.run_id = ?",
            (run_id,),
        ).fetchall()
        return [
            {"source": r["source"], "key": r["key"], "url": r["url"], "repo": r["repo"], "file": r["file"]}
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_state_store.py::test_findings_dedup_by_digest_and_rebuild_for_run -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add state_store.py tests/test_state_store.py
git commit -m "feat: StateStore findings dedup and per-run rebuild" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: StateStore — liveness cache + re-check gate

**Files:**
- Modify: `state_store.py` (add `cached_liveness`, `upsert_liveness`, `should_verify`)
- Test: `tests/test_state_store.py` (append)

**Interfaces:**
- Produces: `cached_liveness(key) -> str | None`, `upsert_liveness(key, status: str) -> None`, `should_verify(key) -> bool` (False only for `dead`/`nobalance`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_state_store.py`:

```python
from state_store import DEAD, ERROR, LIVE, NOBALANCE


def test_recheck_policy_live_and_error_reverify_dead_is_trusted(tmp_path):
    s = _store(tmp_path)
    assert s.should_verify("never-seen") is True   # unknown -> verify
    s.upsert_liveness("k_live", LIVE)
    s.upsert_liveness("k_dead", DEAD)
    s.upsert_liveness("k_err", ERROR)
    s.upsert_liveness("k_nob", NOBALANCE)
    assert s.should_verify("k_live") is True    # re-check live each run
    assert s.should_verify("k_err") is True     # transient -> retry (NOT dead)
    assert s.should_verify("k_dead") is False   # trusted dead
    assert s.should_verify("k_nob") is False    # trusted terminal
    assert s.cached_liveness("k_dead") == DEAD
    # status is overwritten on re-check, not duplicated
    s.upsert_liveness("k_live", DEAD)
    assert s.cached_liveness("k_live") == DEAD
    s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_state_store.py::test_recheck_policy_live_and_error_reverify_dead_is_trusted -v`
Expected: FAIL with `ImportError`/`AttributeError` for the new names.

- [ ] **Step 3: Implement** — add to `StateStore`:

```python
    def cached_liveness(self, key: str) -> str | None:
        row = self._conn.execute("SELECT status FROM key_liveness WHERE key=?", (key,)).fetchone()
        return row["status"] if row else None

    def upsert_liveness(self, key: str, status: str) -> None:
        self._conn.execute(
            "INSERT INTO key_liveness(key, status, checked_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at",
            (key, status, time.time()),
        )
        self._conn.commit()

    def should_verify(self, key: str) -> bool:
        return self.cached_liveness(key) not in _TRUSTED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_state_store.py::test_recheck_policy_live_and_error_reverify_dead_is_trusted -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add state_store.py tests/test_state_store.py
git commit -m "feat: StateStore liveness cache and re-check gate" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: StateStore — rate-limit window persistence

**Files:**
- Modify: `state_store.py` (add `get_block_until`, `set_block_until`)
- Test: `tests/test_state_store.py` (append)

**Interfaces:**
- Produces: `get_block_until(resource: str) -> float | None`, `set_block_until(resource: str, ts: float) -> None`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_state_store.py`:

```python
def test_block_until_persists_across_reopen(tmp_path):
    path = str(tmp_path / "state.db")
    s = StateStore(path=path, use_state=True)
    assert s.get_block_until("github_search") is None
    s.set_block_until("github_search", 1_700_000_123.5)
    s.close()
    s2 = StateStore(path=path, use_state=True)  # reopen
    assert s2.get_block_until("github_search") == 1_700_000_123.5
    assert s2.get_block_until("other") is None
    s2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_state_store.py::test_block_until_persists_across_reopen -v`
Expected: FAIL with `AttributeError: ... 'set_block_until'`.

- [ ] **Step 3: Implement** — add to `StateStore`:

```python
    def get_block_until(self, resource: str) -> float | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key=?", (f"block_until:{resource}",)
        ).fetchone()
        return float(row["value"]) if row else None

    def set_block_until(self, resource: str, ts: float) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"block_until:{resource}", str(ts)),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run all StateStore tests**

Run: `.venv/bin/pytest tests/test_state_store.py -v`
Expected: PASS (all StateStore tests green).

- [ ] **Step 5: Commit**

```bash
git add state_store.py tests/test_state_store.py
git commit -m "feat: StateStore rate-limit window persistence" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: RateLimiter + BaseScanner rate-limit-aware GET

**Files:**
- Modify: `scanners/base.py` (add `import asyncio`, `import aiohttp`; add `RateLimiter`; add `rate_limiter` param + `_rl_get_items` to `BaseScanner`)
- Test: `tests/test_rate_limiter.py`

**Interfaces:**
- Consumes: optional `StateStore` (Task 6) for persistence — duck-typed (`get_block_until`/`set_block_until`).
- Produces:
  - `RateLimiter(resource="github_search", store=None, max_wait=900.0)` with `compute_block_until(status, headers, now) -> float` (static, pure), `note_response(status, headers) -> None`, `async wait_if_blocked() -> None`.
  - `BaseScanner.__init__(..., rate_limiter=None)`; `BaseScanner._rl_get_items(session, url) -> list[dict]`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_rate_limiter.py`:

```python
from scanners.base import RateLimiter


def test_compute_block_until_reads_retry_after():
    now = 1000.0
    assert RateLimiter.compute_block_until(429, {"Retry-After": "30"}, now) == 1030.0


def test_compute_block_until_reads_ratelimit_reset_when_remaining_zero():
    now = 1000.0
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1200"}
    assert RateLimiter.compute_block_until(200, headers, now) == 1200.0


def test_compute_block_until_no_block_when_quota_remains():
    headers = {"X-RateLimit-Remaining": "57", "X-RateLimit-Reset": "1200"}
    assert RateLimiter.compute_block_until(200, headers, 1000.0) == 0.0


def test_compute_block_until_falls_back_on_bare_403():
    assert RateLimiter.compute_block_until(403, {}, 1000.0) == 1060.0


def test_note_response_persists_block_to_store():
    class FakeStore:
        def __init__(self):
            self.calls = []
            self._v = None

        def get_block_until(self, r):
            return self._v

        def set_block_until(self, r, ts):
            self.calls.append((r, ts))
            self._v = ts

    store = FakeStore()
    rl = RateLimiter(resource="github_search", store=store)
    rl.note_response(429, {"Retry-After": "0"})   # 0s -> no future block
    rl.note_response(429, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"})
    assert store.calls and store.calls[-1][0] == "github_search"
    assert store.calls[-1][1] == 9999999999.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rate_limiter.py -v`
Expected: FAIL with `ImportError: cannot import name 'RateLimiter'`.

- [ ] **Step 3: Implement** — in `scanners/base.py`, add `import asyncio` and `import aiohttp` to the imports, then add the `RateLimiter` class after the `github_api_headers` helper:

```python
class RateLimiter:
    """Header-aware GitHub rate-limit gate. Optionally persists the block
    window via a StateStore so a restarted run honors an in-flight limit."""

    def __init__(self, resource: str = "github_search", store=None, max_wait: float = 900.0):
        self.resource = resource
        self._store = store
        self._max_wait = max_wait
        self._blocked_until = 0.0
        if store is not None:
            persisted = store.get_block_until(resource)
            if persisted:
                self._blocked_until = persisted

    @staticmethod
    def compute_block_until(status: int, headers, now: float) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after is not None:
            try:
                return now + float(retry_after)
            except ValueError:
                pass
        if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
            try:
                return float(headers["X-RateLimit-Reset"])
            except ValueError:
                pass
        if status in (403, 429):
            return now + 60.0
        return 0.0

    def note_response(self, status: int, headers) -> None:
        block = self.compute_block_until(status, headers, time.time())
        if block > self._blocked_until:
            self._blocked_until = block
            if self._store is not None:
                self._store.set_block_until(self.resource, block)

    async def wait_if_blocked(self) -> None:
        delay = self._blocked_until - time.time()
        if delay > 0:
            await asyncio.sleep(min(delay, self._max_wait))
```

Then add a `rate_limiter` parameter to `BaseScanner.__init__` (extend the signature and store it):

```python
    def __init__(
        self,
        concurrency: int = 10,
        timeout: int = 15,
        extra_bad_patterns: list[str] | None = None,
        session=None,
        rate_limiter: "RateLimiter | None" = None,
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.extra_bad = extra_bad_patterns or []
        self._session = session
        self._rate_limiter = rate_limiter
        self._stop_requested = False
        self.results: list[dict] = []
```

And add the shared request method to `BaseScanner` (e.g. after `_add_result`):

```python
    async def _rl_get_items(self, session, url: str) -> list[dict]:
        """Rate-limit-aware GET of a GitHub /search endpoint; returns items[]."""
        for attempt in range(3):
            if self._rate_limiter is not None:
                await self._rate_limiter.wait_if_blocked()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if self._rate_limiter is not None:
                        self._rate_limiter.note_response(resp.status, resp.headers)
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("items", [])
                    if resp.status in {403, 429}:
                        if self._rate_limiter is not None:
                            await self._rate_limiter.wait_if_blocked()
                        else:
                            await asyncio.sleep(5 * (attempt + 1))
                        continue
                    return []
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(1 + attempt)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rate_limiter.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scanners/base.py tests/test_rate_limiter.py
git commit -m "feat: header-aware RateLimiter and shared scanner GET" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Route the three scanners through `_rl_get_items`

**Files:**
- Modify: `scanners/github_code.py:70-86`, `scanners/github_commits.py:66-81`, `scanners/github_issues.py:66-81` (replace each `_search_page` body)
- Test: `tests/test_scanners_github.py` (append)

**Interfaces:**
- Consumes: `BaseScanner._rl_get_items` (Task 7).
- Produces: each scanner's `_search_page(session, query, page)` now delegates to `_rl_get_items` and hits its own endpoint (`/search/code`, `/search/commits`, `/search/issues`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_scanners_github.py`:

```python
import asyncio

from scanners.github_code import GitHubCodeScanner
from scanners.github_commits import GitHubCommitsScanner
from scanners.github_issues import GitHubIssuesScanner


def test_search_page_delegates_to_rl_get_items_with_endpoint(monkeypatch):
    captured = {}

    async def fake_rl_get_items(self, session, url):
        captured["url"] = url
        return [{"ok": True}]

    monkeypatch.setattr("scanners.base.BaseScanner._rl_get_items", fake_rl_get_items)

    for scanner, fragment in [
        (GitHubCodeScanner(), "/search/code?"),
        (GitHubCommitsScanner(), "/search/commits?"),
        (GitHubIssuesScanner(), "/search/issues?"),
    ]:
        items = asyncio.run(scanner._search_page(session=None, query="zhipu", page=1))
        assert items == [{"ok": True}]
        assert fragment in captured["url"]
        assert "q=zhipu" in captured["url"] and "page=1" in captured["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scanners_github.py::test_search_page_delegates_to_rl_get_items_with_endpoint -v`
Expected: FAIL — current `_search_page` does its own `session.get` and `session=None` raises / does not match the delegation assertion.

- [ ] **Step 3: Replace `_search_page` in `scanners/github_code.py`** (lines 70-86) with:

```python
    async def _search_page(self, session: aiohttp.ClientSession, query: str, page: int) -> list[dict]:
        params = urllib.parse.urlencode({"q": query, "per_page": self.per_page, "page": page})
        url = f"{self.BASE}/search/code?{params}"
        return await self._rl_get_items(session, url)
```

- [ ] **Step 4: Replace `_search_page` in `scanners/github_commits.py`** (lines 66-81) with the same body but endpoint `"/search/commits"`:

```python
    async def _search_page(self, session: aiohttp.ClientSession, query: str, page: int) -> list[dict]:
        params = urllib.parse.urlencode({"q": query, "per_page": self.per_page, "page": page})
        url = f"{self.BASE}/search/commits?{params}"
        return await self._rl_get_items(session, url)
```

- [ ] **Step 5: Replace `_search_page` in `scanners/github_issues.py`** (lines 66-81) with endpoint `"/search/issues"`:

```python
    async def _search_page(self, session: aiohttp.ClientSession, query: str, page: int) -> list[dict]:
        params = urllib.parse.urlencode({"q": query, "per_page": self.per_page, "page": page})
        url = f"{self.BASE}/search/issues?{params}"
        return await self._rl_get_items(session, url)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scanners_github.py -v`
Expected: PASS (new delegation test plus existing scanner tests still green).

- [ ] **Step 7: Commit**

```bash
git add scanners/github_code.py scanners/github_commits.py scanners/github_issues.py tests/test_scanners_github.py
git commit -m "refactor: route GitHub search scanners through RateLimiter" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Engine foundation — store wiring, config signature, liveness mapping

**Files:**
- Modify: `scanner_engine.py` (imports; `__init__` params; `_config_sig`; module-level `liveness_status`)
- Test: `tests/test_engine_durability.py`

**Interfaces:**
- Consumes: `StateStore` (Tasks 2-6), `RateLimiter` (Task 7).
- Produces:
  - `ScannerEngine(..., state_db="results/state.db", resume=False, use_state=True)` setting `self._store: StateStore`, `self._run_id = None`, `self._rate_limiter`, `self._verify_limiter`.
  - `ScannerEngine._config_sig(code_queries: list[str]) -> str`
  - module function `liveness_status(result: dict) -> str` mapping a verify result to `live`/`dead`/`nobalance`/`error`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_engine_durability.py`:

```python
from scanner_engine import ScannerEngine, liveness_status
from state_store import DEAD, ERROR, LIVE, NOBALANCE


def test_liveness_status_maps_verify_results():
    assert liveness_status({"valid": True}) == LIVE
    assert liveness_status({"valid": False, "reason": "invalid_key"}) == DEAD
    assert liveness_status({"valid": False, "reason": "insufficient_balance"}) == NOBALANCE
    assert liveness_status({"valid": False, "reason": "rate_limited"}) == ERROR
    assert liveness_status({"valid": False, "reason": "timeout"}) == ERROR
    assert liveness_status({"valid": False, "reason": "HTTP_500"}) == ERROR


def test_engine_uses_memory_store_when_use_state_false(tmp_path):
    engine = ScannerEngine(use_state=False, state_db=str(tmp_path / "x.db"))
    rid = engine._store.start_or_resume_run("sig")
    assert rid
    assert not (tmp_path / "x.db").exists()


def test_config_sig_is_stable_and_sensitive_to_inputs():
    a = ScannerEngine(sources=["github_code"], scan_pages=2)
    b = ScannerEngine(sources=["github_code"], scan_pages=2)
    c = ScannerEngine(sources=["github_code"], scan_pages=3)
    assert a._config_sig(["q1", "q2"]) == b._config_sig(["q1", "q2"])
    assert a._config_sig(["q1", "q2"]) != c._config_sig(["q1", "q2"])
    assert a._config_sig(["q1"]) != a._config_sig(["q1", "q2"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_engine_durability.py -v`
Expected: FAIL with `ImportError: cannot import name 'liveness_status'`.

- [ ] **Step 3: Implement** — in `scanner_engine.py`:

Add imports near the top (after `import json`):

```python
import hashlib
```

Add the mapping function near the other module-level helpers (e.g. after `parse_zhipu_models_response`):

```python
def liveness_status(result: dict) -> str:
    """Map a verify result to a cached liveness status (see state_store)."""
    from state_store import DEAD, ERROR, LIVE, NOBALANCE

    if result.get("valid"):
        return LIVE
    reason = result.get("reason", "")
    if reason == "invalid_key":
        return DEAD
    if reason == "insufficient_balance":
        return NOBALANCE
    return ERROR
```

Extend `ScannerEngine.__init__`'s signature with three params (add after `disclose_max_repo_age_days`):

```python
        state_db: str = "results/state.db",
        resume: bool = False,
        use_state: bool = True,
```

And, at the end of `__init__` (after the disclosure block), create the store, run id, and limiters:

```python
        from state_store import StateStore
        from scanners.base import RateLimiter

        self.state_db = state_db
        self.resume = resume
        self.use_state = use_state
        self._store = StateStore(path=state_db, use_state=use_state)
        self._run_id: str | None = None
        self._rate_limiter = RateLimiter("github_search", store=self._store)
        self._verify_limiter = RateLimiter("zhipu_verify", store=self._store)
```

Add the config-signature method to `ScannerEngine`:

```python
    def _config_sig(self, code_queries: list[str]) -> str:
        payload = json.dumps(
            {"sources": sorted(self.sources), "pages": self.scan_pages, "queries": list(code_queries)},
            sort_keys=True,
        )
        return hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_engine_durability.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scanner_engine.py tests/test_engine_durability.py
git commit -m "feat: engine store wiring, config signature, liveness mapping" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Engine search phase — resume hooks

**Files:**
- Modify: `scanner_engine.py` (`_build_scanner` passes `rate_limiter`; `_search_all` uses the store; `run` opens the run id)
- Modify: `tests/test_engine_sources.py:39-62` (the two `_search_all` tests must seed a store + run id)
- Test: `tests/test_engine_durability.py` (append a resume-skip test)

**Interfaces:**
- Consumes: `self._store`, `self._run_id`, `self._rate_limiter` (Task 9).
- Produces: `_search_all` records findings, marks/skip queries, and returns `self._store.iter_run_findings(self._run_id)`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_engine_durability.py`:

```python
import asyncio

from scanner_engine import ScannerEngine


class _FakeScanner:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def search(self, query):
        self.calls.append(query)
        return list(self.rows)


def test_search_all_skips_queries_already_done_on_resume(monkeypatch):
    engine = ScannerEngine(search_delay=0, sources=["github_code"], use_state=False)
    engine._run_id = engine._store.start_or_resume_run("sig")
    scanner = _FakeScanner([{"source": "github_code", "key": "K", "url": "u1", "repo": "a/b"}])
    monkeypatch.setattr(engine, "_build_scanner", lambda source: scanner)
    monkeypatch.setattr(engine, "_queries_for_source", lambda source, cq: ["q1", "q2"])

    # Pre-mark q1 as already done for this run -> only q2 should be searched.
    engine._store.mark_query_done(engine._run_id, "github_code", "q1")
    discovered = asyncio.run(engine._search_all(["codeq"]))

    assert scanner.calls == ["q2"]          # q1 skipped
    assert len(discovered) == 1             # findings rebuilt from the store
    assert discovered[0]["url"] == "u1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_engine_durability.py::test_search_all_skips_queries_already_done_on_resume -v`
Expected: FAIL — current `_search_all` ignores the store and re-runs every query.

- [ ] **Step 3: Update `_build_scanner`** in `scanner_engine.py` to pass the shared limiter — add `rate_limiter=self._rate_limiter` to each constructor call:

```python
    def _build_scanner(self, source: str):
        if source == "github_commits":
            return GitHubCommitsScanner(
                concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages,
                rate_limiter=self._rate_limiter,
            )
        if source == "github_issues":
            return GitHubIssuesScanner(
                concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages,
                rate_limiter=self._rate_limiter,
            )
        return GitHubCodeScanner(
            concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages,
            rate_limiter=self._rate_limiter,
        )
```

- [ ] **Step 4: Rewrite `_search_all`** (lines 296-311) to use the store:

```python
    async def _search_all(self, code_queries: list[str]) -> list[dict]:
        for source in self.sources:
            if self._should_stop():
                break
            scanner = self._build_scanner(source)
            queries = self._queries_for_source(source, code_queries)
            for idx, query in enumerate(queries, start=1):
                if self._should_stop():
                    break
                if self._store.is_query_done(self._run_id, source, query):
                    self.log(f"resume-skip [{source}] {query}")
                    continue
                self.log(f"search [{source}] [{idx}/{len(queries)}] {query}")
                for row in await scanner.search(query):
                    self._store.record_finding(self._run_id, row)
                self._store.mark_query_done(self._run_id, source, query)
                self.progress_callback(idx, len(queries), f"search:{source}")
                if idx < len(queries) and self.search_delay > 0:
                    await asyncio.sleep(self.search_delay)
        return self._store.iter_run_findings(self._run_id)
```

- [ ] **Step 5: Open the run id in `run`** — in `scanner_engine.py`'s `run`, set `self._run_id` before searching. Replace the first two lines of the method body:

```python
    def run(self, queries: list[str] | None = None) -> list[dict]:
        self._start_time = time.time()
        query_list = queries or load_queries()
        self._run_id = self._store.start_or_resume_run(self._config_sig(query_list), resume=self.resume)
        discovered = asyncio.run(self._search_all(query_list))
```

(Leave the rest of `run` unchanged for now; Task 11 adds `finish_run`/`close`.)

- [ ] **Step 6: Fix the two existing `_search_all` tests** in `tests/test_engine_sources.py`. They call `_search_all` directly, so they now need a store + run id, and the engine should run offline. Update both tests to construct with `use_state=False` and seed a run id:

In `test_search_all_aggregates_across_sources_and_dedups`, change the construction and add the run-id line:

```python
    engine = ScannerEngine(search_delay=0, sources=["github_code", "github_issues"], use_state=False)
    engine._run_id = engine._store.start_or_resume_run("sig")
```

In `test_search_all_dedups_identical_locations`, likewise:

```python
    engine = ScannerEngine(search_delay=0, sources=["github_code"], use_state=False)
    engine._run_id = engine._store.start_or_resume_run("sig")
```

(The assertions still hold: cross-source/url findings stay distinct; identical locations collapse — now via the store's digest dedup.)

- [ ] **Step 7: Run the affected tests**

Run: `.venv/bin/pytest tests/test_engine_durability.py tests/test_engine_sources.py -v`
Expected: PASS (resume-skip test plus the two updated multi-source tests).

- [ ] **Step 8: Commit**

```bash
git add scanner_engine.py tests/test_engine_sources.py tests/test_engine_durability.py
git commit -m "feat: checkpoint/resume hooks in engine search phase" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: Engine verify phase — re-check gate, liveness writes, finish/close

**Files:**
- Modify: `scanner_engine.py` (`_request_verify` uses `_verify_limiter`; `_verify_all_async` gates on `should_verify`; `run` calls `finish_run`/`close`)
- Test: `tests/test_engine_durability.py` (append)

**Interfaces:**
- Consumes: `self._store.should_verify/cached_liveness/upsert_liveness` (Task 5), `liveness_status` (Task 9), `self._verify_limiter` (Task 9).
- Produces: verify loop skips cached-dead keys (no network), records liveness for verified keys, and `run` finalizes the run.

- [ ] **Step 1: Write the failing test** — append to `tests/test_engine_durability.py`:

```python
from state_store import DEAD


def test_verify_skips_cached_dead_keys_without_network(monkeypatch):
    engine = ScannerEngine(concurrency=2, use_state=False)
    engine._store.upsert_liveness("deadkey", DEAD)  # already known dead

    verified_keys = []

    async def fake_verify_one(self, session, key, semaphore):
        verified_keys.append(key)
        return {"valid": False, "provider": "zhipu", "reason": "invalid_key"}

    monkeypatch.setattr(ScannerEngine, "_verify_one", fake_verify_one)

    grouped = {
        "deadkey": {"repos": [{"source": "github_code", "repo": "a/b", "url": "u1", "file": ""}]},
        "freshkey": {"repos": [{"source": "github_code", "repo": "c/d", "url": "u2", "file": ""}]},
    }
    results = engine.verify_keys(grouped)

    assert verified_keys == ["freshkey"]                       # dead key never hit the network
    by_key = {r["key"]: r for r in results}
    assert by_key["deadkey"]["valid"] is False
    assert "cached" in by_key["deadkey"]["reason"]
    assert set(by_key) == {"deadkey", "freshkey"}              # both still reported
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_engine_durability.py::test_verify_skips_cached_dead_keys_without_network -v`
Expected: FAIL — current verify loop calls `_verify_one` for every key, so `verified_keys == ["deadkey", "freshkey"]`.

- [ ] **Step 3: Add rate-limiting to `_request_verify`** — in `scanner_engine.py`, wrap the request (lines 329-341):

```python
    async def _request_verify(self, session: aiohttp.ClientSession, api_key: str, path: str) -> tuple[int, dict | None]:
        url = f"{PROVIDER_CONFIG['base']}{path}"
        headers = {"Authorization": f"Bearer {api_key}"}
        await self._verify_limiter.wait_if_blocked()
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                self._verify_limiter.note_response(resp.status, resp.headers)
                data = None
                if resp.content_type == "application/json":
                    data = await resp.json()
                return resp.status, data
        except asyncio.TimeoutError:
            return -1, None
        except Exception as exc:
            return -2, {"error": str(exc)[:80]}
```

- [ ] **Step 4: Gate the verify loop** — in `_verify_all_async` (lines 387-433), partition each batch into to-verify vs cached-skip and record liveness. Replace the batch loop body:

```python
            batch = items[start : start + self.concurrency]
            decided = [(key, info, self._store.should_verify(key)) for key, info in batch]
            to_verify = [(key, info) for key, info, sv in decided if sv]
            cached = [(key, info) for key, info, sv in decided if not sv]

            verified = await asyncio.gather(
                *(self._verify_one(session, key, semaphore) for key, _info in to_verify)
            )

            for (key, info), result in zip(to_verify, verified):
                self._store.upsert_liveness(key, liveness_status(result))
                if result.get("valid"):
                    valid_count += 1
                    self.log(f"verify {redact_key(key)} -> {format_balance_log(result, self.usd_cny_rate)}")
                    self._maybe_disclose(key, info, result)
                else:
                    self.log(f"verify {redact_key(key)} -> {result.get('reason', '?')}")
                results.append(self._result_row(key, info, result))
                self.progress_callback(len(results), len(items), "verify")

            for key, info in cached:
                status = self._store.cached_liveness(key)
                result = {"valid": False, "provider": "zhipu", "reason": f"{status} (cached)"}
                self.log(f"verify {redact_key(key)} -> {result['reason']} (skipped)")
                results.append(self._result_row(key, info, result))
                self.progress_callback(len(results), len(items), "verify")
```

- [ ] **Step 5: Extract the result-row builder** — to keep the loop DRY, add this helper to `ScannerEngine` (it is the dict that the old loop appended, lifted verbatim):

```python
    def _result_row(self, key: str, info: dict, result: dict) -> dict:
        total_balance = result.get("total_balance", 0.0)
        primary_currency = result.get("primary_currency", "CNY")
        return {
            "key": key,
            "key_redacted": redact_key(key),
            "valid": result.get("valid", False),
            "balance": total_balance,
            "balance_details": result.get("balance_details", []),
            "primary_currency": primary_currency,
            "balance_usd": convert_to_usd(total_balance, primary_currency, self.usd_cny_rate),
            "balance_cny": convert_to_cny(total_balance, primary_currency, self.usd_cny_rate),
            "balance_unavailable": result.get("balance_unavailable", False),
            "reason": result.get("reason", ""),
            "provider": result.get("provider", "zhipu"),
            "provider_note": result.get("provider_note", ""),
            "repos": info["repos"],
            "verified_at": datetime.now().isoformat(),
        }
```

- [ ] **Step 6: Finalize the run** — in `run`, after `results = self.verify_keys(grouped)` and before `self.save_results(results)`, add:

```python
        if self._run_id is not None:
            self._store.finish_run(self._run_id)
        self._store.close()
```

- [ ] **Step 7: Run the durability + full suite**

Run: `.venv/bin/pytest tests/test_engine_durability.py -v && .venv/bin/pytest -q`
Expected: PASS — the cached-dead test passes and the entire suite is green.

- [ ] **Step 8: Commit**

```bash
git add scanner_engine.py tests/test_engine_durability.py
git commit -m "feat: re-check gate and liveness writes in engine verify phase" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: CLI flags — `--resume` and `--no-state`

**Files:**
- Modify: `deep_scan.py` (argparse + `ScannerEngine(...)` call)
- Modify: `marathon_scan.py` (argparse + `ScannerEngine(...)` call)

**Interfaces:**
- Consumes: `ScannerEngine(..., resume=..., use_state=...)` (Task 9).
- Produces: `--resume` (default off) and `--no-state` (default off ⇒ state on) on both scripts.

- [ ] **Step 1: Add the flags in `deep_scan.py`** — next to the existing `add_argument` calls for the parser, add:

```python
    parser.add_argument("--resume", action="store_true",
                        help="Resume the most recent unfinished run with the same config (skip done queries).")
    parser.add_argument("--no-state", action="store_true",
                        help="Disable the durable state DB (no checkpoint/dedup/liveness cache).")
```

- [ ] **Step 2: Pass them to the engine in `deep_scan.py`** — add these keyword arguments to the `ScannerEngine(...)` construction:

```python
        resume=args.resume,
        use_state=not args.no_state,
```

- [ ] **Step 3: Mirror both edits in `marathon_scan.py`** — add the identical two `add_argument` calls to its parser and the identical `resume=args.resume, use_state=not args.no_state` kwargs to its `ScannerEngine(...)` construction.

- [ ] **Step 4: Verify the flags parse and the suite is green**

Run: `.venv/bin/python deep_scan.py --help | grep -E "resume|no-state" && .venv/bin/python marathon_scan.py --help | grep -E "resume|no-state"`
Expected: both `--resume` and `--no-state` appear in each script's help.

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: all tests pass; ruff reports no issues.

- [ ] **Step 5: Commit**

```bash
git add deep_scan.py marathon_scan.py
git commit -m "feat: --resume and --no-state CLI flags" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Checkpoint/resume → Tasks 2, 3, 10. ✓
- Cross-run findings dedup + per-run rebuild → Tasks 1, 4, 10. ✓
- Liveness cache + re-check policy (live re-verify, dead trusted, transient→error retry, 402→nobalance) → Tasks 5, 9, 11. ✓
- Header-aware rate limiting + persisted block window honored on restart → Tasks 6, 7, 8 (GitHub search) and 11 (`_request_verify`). ✓
- SQLite/WAL store, corruption fallback, `:memory:` mode → Task 2. ✓
- Config knobs `state_db`/`resume`/`use_state` + CLI flags → Tasks 9, 12. ✓
- Packaging (`py-modules`) → Task 2. ✓
- `disclosed.json` untouched, liveness-only unchanged → no task modifies them (verified by full-suite run in Task 11). ✓

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases" — every code step contains the actual code and every test step the actual asserts. ✓

**3. Type consistency:** `finding_digest(dict)->str`, `should_verify(key)->bool`, `liveness_status(dict)->str`, `start_or_resume_run(sig, resume)->str`, `iter_run_findings(run_id)->list[dict]`, `_result_row(key, info, result)->dict`, `RateLimiter.compute_block_until(status, headers, now)->float` are used identically wherever referenced across tasks. Status constants `LIVE/DEAD/ERROR/NOBALANCE` come from `state_store` everywhere. ✓
