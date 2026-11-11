"""Offline policy/owned-process checks; no benchmark or provider calls."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "m1_revision2_guard.py"
spec = importlib.util.spec_from_file_location("m1_revision2_guard", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def fixture(n=40, m0=34, m1r2=29):
    reference = {"tasks": [{"session_id": "scene", "task_id": str(i), "passed": i < m0}
                           for i in range(100)]}
    rows = [{"session_id": "scene", "task_id": str(i),
             "status": "scored" if i < n else "not_run",
             "passed": i < m1r2 if i < n else None,
             "task_pass": i < m1r2 if i < n else None} for i in range(100)]
    return rows, reference


@pytest.mark.parametrize("n,m0,m1r2,triggered", [
    (39, 39, 0, False), (40, 34, 30, False), (40, 34, 29, True),
    (41, 34, 30, False), (41, 35, 30, True), (0, 0, 0, False),
    (40, 24, 25, False), (40, 40, 35, True),
    (80, 60, 55, True), (99, 80, 76, False), (99, 80, 75, True),
])
def test_boundaries_and_same_subset(n, m0, m1r2, triggered):
    rows, ref = fixture(n, m0, m1r2)
    before = copy.deepcopy((rows, ref))
    result = guard.paired_snapshot(rows, ref)
    assert result["triggered"] is triggered
    assert result["scored_pairs"] == n
    assert result["m0_passes"] == min(m0, n)
    assert result["unscored"] == 100 - n
    assert (rows, ref) == before


def test_unscored_is_never_imputed_as_failure():
    rows, ref = fixture()
    for row in rows[40:]:
        row.update(status="interrupted", passed=False, task_pass=False)
    result = guard.paired_snapshot(rows, ref)
    assert result["scored_pairs"] == 40 and result["losses"] == 5


@pytest.mark.parametrize("damage", ["missing", "duplicate", "reordered", "invalid_score", "score_disagrees"])
def test_rejects_bad_ledger(damage):
    rows, ref = fixture()
    if damage == "missing":
        rows.pop()
    elif damage == "duplicate":
        rows[1] = rows[0]
    elif damage == "reordered":
        rows.reverse()
    elif damage == "invalid_score":
        rows[0]["passed"] = 1
    else:
        rows[0]["task_pass"] = False
    with pytest.raises(ValueError):
        guard.paired_snapshot(rows, ref)


CHILD = '''import pathlib, signal, sys, time
path = pathlib.Path(sys.argv[1])
def stop(sig, frame):
    (path / "terminated").write_text("graceful")
    sys.exit(2)
signal.signal(signal.SIGTERM, stop)
(path / "ready").write_text("yes")
while True: time.sleep(0.01)
'''


@pytest.mark.parametrize("observer_error", [False, True])
def test_guard_gracefully_stops_only_owned_process_and_saves_reason(tmp_path, observer_error):
    deadline = time.monotonic() + 5
    def observe():
        while not (tmp_path / "ready").exists():
            if time.monotonic() > deadline:
                raise TimeoutError("offline fake child did not start")
            time.sleep(.01)
        if observer_error:
            raise ValueError("injected guard failure")
        n = 41 if (tmp_path / "terminated").exists() else 40
        return guard.paired_snapshot(*fixture(n, 34, 29))
    rc = guard.supervise(tmp_path, [sys.executable, "-B", "-c", CHILD, str(tmp_path)], observe, .01)
    assert rc == 2
    assert (tmp_path / "terminated").read_text() == "graceful"
    state = json.loads((tmp_path / "guard-status.json").read_text())
    assert state["stop_reason"] == ("guard_error" if observer_error else "performance_drop")
    assert state["worker_exit_code"] == 2
    if not observer_error:
        assert json.loads((tmp_path / "stop-trigger.json").read_text())["scored_pairs"] == 40
        assert json.loads((tmp_path / "final-paired-snapshot.json").read_text())["scored_pairs"] == 41


def test_completed_run_does_not_restart_or_signal(tmp_path):
    def observe():
        return guard.paired_snapshot(*fixture(100, 80, 60))
    assert guard.supervise(tmp_path, [sys.executable, "-B", "-c", "import time; time.sleep(.1)"], observe, .01) == 0
    assert not (tmp_path / "stop-trigger.json").exists()
    assert json.loads((tmp_path / "guard-status.json").read_text())["status"] == "completed"
