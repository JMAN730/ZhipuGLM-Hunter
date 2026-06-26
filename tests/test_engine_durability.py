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
