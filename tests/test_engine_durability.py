import asyncio

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
    engine._ensure_store()
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


class _FakeScanner:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def search(self, query):
        self.calls.append(query)
        return list(self.rows)


def test_search_all_skips_queries_already_done_on_resume(monkeypatch):
    engine = ScannerEngine(search_delay=0, sources=["github_code"], use_state=False)
    engine._ensure_store()
    engine._run_id = engine._store.start_or_resume_run("sig")
    scanner = _FakeScanner([{"source": "github_code", "key": "K", "url": "u1", "repo": "a/b"}])
    monkeypatch.setattr(engine, "_build_scanner", lambda source: scanner)
    monkeypatch.setattr(engine, "_queries_for_source", lambda source, cq: ["q1", "q2"])

    # Pre-mark q1 as already done for this run -> only q2 should be searched.
    engine._store.mark_query_done(engine._run_id, "github_code", "q1")
    discovered = asyncio.run(engine._search_all(["codeq"]))

    assert scanner.calls == ["q2"]  # q1 skipped
    assert len(discovered) == 1  # findings rebuilt from the store
    assert discovered[0]["url"] == "u1"
