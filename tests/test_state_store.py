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
