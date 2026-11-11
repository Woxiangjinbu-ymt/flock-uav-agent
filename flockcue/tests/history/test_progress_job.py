"""Detached study owner uses fake commands, never a model or API key."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

# These tests also run in detached, materialized legacy workspaces.
REPOSITORY = next(p for p in Path(__file__).resolve().parents if (p / "scripts").is_dir())
sys.path.insert(0, str((REPOSITORY / "scripts")))
from m1_progress_job import run_job


def test_success_sequences_both_arms_and_records_exit_codes(tmp_path):
    assert run_job(tmp_path, [("m0", [sys.executable, "-c", "print('first')"]),
                              ("m1", [sys.executable, "-c", "print('second')"])]) == 0
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["status"] == "completed"
    assert [s["exit_code"] for s in state["stages"]] == [0, 0]
    assert state["finished_at"] and state["automatic_retries"] == 0


def test_failed_arm_does_not_dispatch_next_arm(tmp_path):
    assert run_job(tmp_path, [("m0", [sys.executable, "-c", "raise SystemExit(7)"]),
                              ("m1", [sys.executable, "-c", "raise RuntimeError('must not run')"])]) == 2
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["status"] == "failed"
    assert len(state["stages"]) == 1 and state["stages"][0]["exit_code"] == 7
    assert not (tmp_path / "m1.log").exists()


def test_owner_sigterm_stops_child_and_preserves_signal(tmp_path):
    module = REPOSITORY / "compat/scripts"
    code = (f"import sys; sys.path.insert(0, {str(module)!r}); from m1_progress_job import run_job; "
            f"raise SystemExit(run_job({str(tmp_path)!r}, [('m0', [sys.executable, '-c', 'import time; time.sleep(60)'])]))")
    owner = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state_path = tmp_path / "status.json"
            if state_path.exists():
                state = json.loads(state_path.read_text())
                if state.get("stages") and state["stages"][0].get("pid"):
                    break
            time.sleep(.02)
        else:
            raise AssertionError("fake child did not start")
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout=5) == 2
        state = json.loads(state_path.read_text())
        assert state["status"] == "interrupted" and state["received_signal"] == signal.SIGTERM
        assert state["stages"][0]["exit_code"] == -signal.SIGTERM
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=5)
