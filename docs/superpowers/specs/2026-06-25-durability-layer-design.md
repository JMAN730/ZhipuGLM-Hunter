# Durability Layer for Long-Running Scans — Design

- **Date:** 2026-06-25
- **Status:** Approved (design)
- **Component:** ZhipuGLM Hunter

## Problem / motivation

Long single scans (`ultimate_scan.py`, ~12h) and continuous `marathon_scan.py` runs currently:

1. **Lose all progress on a crash** — there is no checkpoint, so a process that dies mid-run
   restarts from scratch.
2. **Repeat work across runs/cycles** — each run re-searches and re-verifies keys it has already
   checked, wasting both GitHub search budget and Zhipu `/models` calls.
3. **Handle rate limits bluntly** — scanners back off on 403/429 with a fixed delay rather than
   reading GitHub's reset headers.

**Goal:** one durable state layer that adds checkpoint/resume, persistent cross-run dedup + liveness
caching, and header-aware rate limiting — via thin, well-bounded hooks into the existing async
pipeline, with no change to the disclosure orientation.

**Non-goals:** balance/billing inspection (explicitly out — see `CLAUDE.md`); changing disclosure
semantics; non-GitHub sources; per-page checkpoint granularity.

## Re-check policy (decided)

Once a key's liveness is known:

- **Re-verify keys last seen LIVE** on each new run/cycle — catches rotation, so we stop pinging
  owners who already fixed their leak. Cost is low (live keys are rare).
- **Trust keys confirmed DEAD** and skip them.
- **Never cache a transient failure** (rate-limit / timeout / network) as dead — only a definitive
  authenticated "invalid" verdict counts as dead.

## Architecture

New top-level module **`state_store.py`** exposing a `StateStore` class, owning one `sqlite3`
connection to `results/state.db` (WAL mode). Calls are **synchronous**: the engine is a
single-threaded async pipeline, so coroutines serialize naturally and indexed lookups are
sub-millisecond — no thread hazard, no executor needed. `sqlite3` is stdlib, so **no new runtime
dependency**.

`results/disclosed.json` (the disclosure ledger) is **left untouched** and remains the per-repo
dedup authority.

### Schema

All tables `CREATE TABLE IF NOT EXISTS`, with a `schema_version` value in `meta` for future
migration.

| Table | Key | Columns / purpose |
|---|---|---|
| `runs` | `run_id` PK | `config_sig, started_at, finished_at, status` — identifies a scan for resume. |
| `progress` | `(run_id, source, query)` PK | `done_at` — a completed search slice; resume skips these. |
| `findings` | `digest` PK | `source, key, url, repo, status, first_seen, last_seen`. `digest` = `MD5(source:key:url)` from `base.py`. Cross-run dedup + audit. |
| `run_findings` | `(run_id, digest)` PK | Links a run to findings it saw, so a resumed run rebuilds its candidate set without re-searching. |
| `key_liveness` | `key` PK | `status, checked_at` — drives the re-check policy. |
| `meta` | `key` PK | `value` — `schema_version` and rate-limit windows (e.g. `block_until:github_search`). |

### `StateStore` interface

```
start_or_resume_run(config_sig, resume) -> run_id
is_query_done(run_id, source, query) -> bool
mark_query_done(run_id, source, query)
record_finding(run_id, finding)
iter_run_findings(run_id) -> Iterable[finding]
should_verify(key) -> bool
cached_liveness(key) -> status | None
upsert_liveness(key, status)
get_block_until(resource) -> float | None
set_block_until(resource, ts)
finish_run(run_id)
close()
```

## Control flow

### Run identity

`run()` computes `config_sig` = a stable hash of `(sorted sources, query-set identity, scan_pages)`,
then calls `start_or_resume_run(config_sig, resume)`:

- `resume=True` **and** a run with the same `config_sig` is still `status='running'` → reuse that
  `run_id` and preload its `run_findings`.
- otherwise → mint a new timestamped `run_id`.

### Search phase — hooks in `_search_all`, per `(source, query)`

1. `if store.is_query_done(run_id, source, query): continue` (resume skip).
2. else `results = await scanner.search(query)` → for each finding `store.record_finding(run_id,
   finding)` → then `store.mark_query_done(run_id, source, query)`.
3. The findings writes and the progress mark **commit in one transaction**, so a crash never leaves
   a query marked done with its findings missing.

After the loop, `discovered = list(store.iter_run_findings(run_id))` — one source of truth unifying
resumed + fresh findings. `_group_keys` is unchanged.

### Verify phase — gate in `_verify_all_async` / `_verify_one`

For each grouped key, gate with `store.should_verify(key)`:

- **True** (never-seen / `live` / `error`) → network `/models` as today, then
  `store.upsert_liveness(key, status)`.
- **False** (cached `dead`) → **skip the network call**, synthesize
  `{valid: False, reason: "dead (cached)"}`.

`_maybe_disclose` still fires only on a fresh confirmed-live result. Disclosure dedup
(`disclosed.json`) prevents re-notifying when a live key is re-verified on a later cycle.

### Liveness status mapping

| HTTP / outcome | Cached status | `should_verify` |
|---|---|---|
| `200 /models` | `live` | True (re-check each run) |
| `401 invalid_key` | `dead` | False (trusted) |
| `429` / timeout / network / 5xx | `error` | True (retry) |
| `402 insufficient_balance` | terminal non-live (carries current behavior; not disclosed) | False |

Critical correctness point: a rate-limit or timeout is cached as `error`, **never** as `dead`.

### Rate limiter

A small `RateLimiter` in `scanners/base.py` wraps GitHub `/search` requests (in the scanners) and
`_request_verify` (in the engine). It reads `X-RateLimit-Remaining` / `X-RateLimit-Reset` and
`Retry-After`, sleeps until reset (capped) instead of a blind fixed delay, persists the window via
`store.set_block_until(resource, reset_ts)`, and honors `get_block_until` on restart so a resumed run
does not immediately hammer a still-limited endpoint. This is the **only** change touching the
scanners.

### Stop conditions

`_should_stop(valid_count)` counts cached-live keys too, so `max_valid_keys` stays consistent across
a resume.

## Error handling & degraded modes

- **Crash-safety:** per-query transaction + WAL; resume is exact to the last committed query;
  single-row upserts are atomic — no torn writes the way a half-flushed JSON file can be.
- **Corruption:** if `state.db` is unreadable on open, log a warning, move the bad file aside
  (`state.db.corrupt-<ts>`), and continue with a fresh DB — a bad state file never aborts a scan.
- **`use_state=False`:** use an in-memory `:memory:` DB so the engine code path stays uniform and
  tests stay offline.
- **Transient verify failures** are cached as `error` and retried next time; never cached as dead.

## Configuration / flags

`ScannerEngine(..., state_db="results/state.db", resume=False, use_state=True)`.

`deep_scan.py` / `marathon_scan.py` gain `--resume` and `--no-state`. Default: durability **on**
(`state.db` auto-created), resume **opt-in**. In marathon mode each cycle is a new `run_id`, but
`findings` / `key_liveness` are global, so cycles never re-verify dead keys; `--resume` continues a
crashed cycle.

`results/state.db` is covered by the existing `results/` gitignore (except `.gitkeep`).

## Testing (offline, `tmp_path`; consistent with the project's offline-test rule)

`tests/test_state_store.py`:

- **resume:** mark queries done → `is_query_done` filters them; `iter_run_findings` rebuilds the
  candidate set.
- **dedup:** `record_finding` twice → one `findings` row + `run_findings` link; digest stable across
  runs.
- **re-check policy:** `live` → `should_verify` True; `dead` → False; `error` → True
  (the rate-limited ≠ dead case).
- **liveness mapping:** `401`→`dead`, `429`/timeout→`error`, `200`→`live`.
- **rate-limit persistence:** `set/get_block_until` honored across a reopen.
- **corruption:** open on a junk file → falls back fresh without raising.

Engine integration (extend `tests/test_engine_sources.py`, monkeypatched scanner + `:memory:`
store):

- a done query is skipped on resume;
- a cached-dead key triggers **no** network verify.

## Packaging

Add `state_store` to the `pyproject.toml` py-modules list (alongside `scanner_engine`). No new
runtime dependency.

## Rollout / impact

- New module + thin hooks in `scanner_engine.py` (`_search_all`, the verify gate, run identity) +
  one shared `RateLimiter` in `scanners/base.py`. Scanners are otherwise unchanged.
- `disclosed.json` and the JSON/CSV/MD output formats are unchanged.
- Behavior on a fresh `state.db` matches today (the first run does full work; later runs benefit from
  the cache and from resume).
