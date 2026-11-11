"""Own a service process group for as long as the runner's pipe stays open.

Launch this module in a new session, passing only the pipe's read descriptor.
The runner keeps its write descriptor: even SIGKILL or a crash closes it, so
this supervisor can terminate the service and its descendants. No command,
environment, or credential contents are written to the log by this module.
"""
from __future__ import annotations

import argparse
import os
import select
import signal
import subprocess
import sys
import time


def group_members(group):
    """Return live PGID members keyed by PID, with stable start-time identity."""
    result = subprocess.run(["ps", "-axo", "pid=,pgid=,lstart=,stat="],
                            capture_output=True, text=True, timeout=3, check=True)
    members = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 8:
            continue
        pid, pgid = map(int, fields[:2])
        if pgid == group and not fields[-1].startswith("Z"):
            members[pid] = " ".join(fields[2:-1])
    return members


def process_identity(pid):
    """Read a PID's start time; a transient R/S state is not its identity."""
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
                            capture_output=True, text=True, timeout=3)
    if result.returncode == 1:
        return None
    result.check_returncode()
    fields = result.stdout.split()
    return " ".join(fields[:-1]) if fields and not fields[-1].startswith("Z") else None


def _parent_closed(descriptor):
    readable, _, _ = select.select([descriptor], [], [], 0)
    return bool(readable) and os.read(descriptor, 4096) == b""


def _group_has_live_members(group, timeout):
    """Inspect only process IDs and state; never read command lines or envs."""
    try:
        # A separate session keeps this short-lived inspection process out of
        # the group we are checking. Zombies cannot execute further actions.
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True,
            timeout=timeout, start_new_session=True, check=True,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3:
                continue
            pid, pgid, state = fields
            if int(pgid) == group and int(pid) != os.getpid() and not state.startswith("Z"):
                return True
        return False
    except (OSError, ValueError, subprocess.SubprocessError):
        # An unavailable process listing must not let live descendants escape.
        return True


def supervise(parent_fd, argv, grace_seconds=3.0):
    group = os.getpid()
    if os.getpgrp() != group:
        raise RuntimeError("Supervisor must be launched in its own process group")
    os.set_inheritable(parent_fd, False)
    requested_signal = None

    def request_stop(signum, _frame):
        nonlocal requested_signal
        if requested_signal is None:
            requested_signal = signum

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)

    child = None
    try:
        # Avoid starting a service if the runner already vanished during exec.
        if _parent_closed(parent_fd) or requested_signal is not None:
            return 128 + (requested_signal or signal.SIGTERM)
        child = subprocess.Popen(argv, close_fds=True)
        try:
            while child.poll() is None and requested_signal is None:
                if _parent_closed(parent_fd):
                    requested_signal = signal.SIGTERM
                    break
                select.select([parent_fd], [], [], 0.1)
            normal_exit = child.poll() is not None and requested_signal is None
            exit_code = child.returncode if normal_exit else 128 + (requested_signal or signal.SIGTERM)
        except BaseException:
            # A local supervision failure must still reap the owned service.
            exit_code = 2
        # The handler above absorbs our own TERM so we can enforce the grace
        # period. All services and their ordinary subprocesses share this group.
        os.killpg(group, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            child.poll()  # Reap our direct child before inspecting descendants.
            if not _group_has_live_members(group, min(1, max(.001, deadline - time.monotonic()))):
                return exit_code if exit_code >= 0 else 128 - exit_code
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        # Includes this supervisor deliberately: a stubborn service must not
        # outlive its owner even when the original runner is already dead.
        os.killpg(group, signal.SIGKILL)
        return 128 + signal.SIGKILL  # Unreachable unless the signal is delayed.
    finally:
        os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-fd", required=True, type=int)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not command:
        parser.error("A service command is required")
    try:
        return supervise(args.parent_fd, command)
    except (OSError, RuntimeError):
        print("Service supervisor failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
