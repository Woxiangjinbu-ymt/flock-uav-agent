"""Ownership-safe cleanup of real local process groups; no network/model calls."""
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reproduction as r
import reproduction_process as supervisor


class ChildTests(unittest.TestCase):
    def test_lost_supervisor_does_not_leave_registered_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "ready"
            code = f"import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
            child = r.Child([sys.executable, "-c", code], root, root / "log", os.environ.copy())
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
                child.capture_group_members()
                service_pid = int(marker.read_text())
                os.kill(child.process.pid, signal.SIGKILL)
                child.process.wait(timeout=3)
                self.assertIsNotNone(supervisor.process_identity(service_pid))
                child.stop()
                self.assertFalse(supervisor.group_members(child.process.pid))
                child.stop()  # A reaped/reused PID must never be signalled again.
            finally:
                child.stop()

    def test_unverified_group_is_not_signalled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = r.Child([sys.executable, "-c", "pass"], root, root / "log", os.environ.copy())
            child.process.wait(timeout=5)
            with patch.object(supervisor, "group_members", return_value={999999: "unrelated"}), \
                    patch.object(r.os, "killpg") as kill:
                with self.assertRaisesRegex(RuntimeError, "ownership"):
                    child.stop()
                child.stop()
                kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
