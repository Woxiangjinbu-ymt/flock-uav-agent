"""Real local process tests; no network or model requests."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SUPERVISOR = Path(__file__).resolve().parents[1] / "reproduction_process.py"


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="multiuav-supervisor-")
        self.root = Path(self.temporary.name)
        self.processes = []
        self.writers = []

    def tearDown(self):
        for descriptor in self.writers:
            os.close(descriptor)
        for process in self.processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        self.temporary.cleanup()

    def start(self, code):
        reader, writer = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, str(SUPERVISOR), "--parent-fd", str(reader),
                 "--", sys.executable, "-c", code],
                start_new_session=True, pass_fds=(reader,),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        finally:
            os.close(reader)
        self.processes.append(process)
        self.writers.append(writer)
        return process, writer

    def close_writer(self, writer):
        os.close(writer)
        self.writers.remove(writer)

    def marker_pid(self, marker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return int(marker.read_text())
            except (FileNotFoundError, ValueError):
                time.sleep(0.02)
        self.fail("Service did not write its readiness marker")

    def assert_stopped(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                    capture_output=True, text=True)
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                return
            time.sleep(0.02)
        self.fail("Supervised process remained alive")

    def test_normal_service_exit_preserves_exit_code(self):
        process, _ = self.start("raise SystemExit(7)")
        self.assertEqual(process.wait(timeout=5), 7)

    def test_parent_pipe_eof_kills_service_ignoring_term(self):
        marker = self.root / "service.pid"
        process, writer = self.start(
            "import os,pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )
        pid = self.marker_pid(marker)
        self.close_writer(writer)
        self.assertEqual(process.wait(timeout=7), -signal.SIGKILL)
        self.assert_stopped(pid)

    def test_supervisor_term_cleans_service(self):
        marker = self.root / "service.pid"
        process, _ = self.start(
            f"import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )
        pid = self.marker_pid(marker)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=5), 128 + signal.SIGTERM)
        self.assert_stopped(pid)

    def test_normal_exit_cleans_descendants(self):
        marker = self.root / "descendant.pid"
        descendant = (
            f"import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )
        process, _ = self.start(
            f"import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
            f"marker=pathlib.Path({str(marker)!r}); "
            "exec('while not marker.exists(): time.sleep(.01)'); raise SystemExit(7)"
        )
        pid = self.marker_pid(marker)
        self.assertEqual(process.wait(timeout=5), 7)
        self.assert_stopped(pid)


if __name__ == "__main__":
    unittest.main()
