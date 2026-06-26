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
