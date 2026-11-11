"""Pick which untracked files go into git on 2026-11-10; the rest stay in the outside archive.

Usage: python3 docs/maintenance/select_commit_20261110.py LIST0 > include.list0
LIST0 is the NUL-separated worktree list written next to the archive tarball.
"""
import os
import sys

RAW_DIRS = {"trials", "runs", "policy", "toolchain", "toolchains", "agent-logs", "modelio",
            "server", "source", "fixtures", "inputs", "process-logs", "scorer-exit-seals",
            "evaluation", "__pycache__", ".venv"}
DROP_EXT = {".log", ".pkl", ".pyc", ".zip", ".gz", ".pdf", ".xlsx"}
DROP_TOP = ("tmp/", ".vscode/", "outputs/", "server_logs/")
DATA_ROOTS = ("docs/results/", "docs/audit/", "experiments/")
SMALL = 512 * 1024
HARD_MAX = 20 * 1024 * 1024


def keep(path, size, extra):
    if path in extra:
        return True
    ext = os.path.splitext(path)[1].lower()
    if path.startswith(DROP_TOP) or ext in DROP_EXT or size > HARD_MAX:
        return False
    parts = path.split("/")
    if any(p in RAW_DIRS for p in parts[:-1]):
        return False
    if path.startswith(DATA_ROOTS) and ext in {".json", ".jsonl", ".csv", ".xml"}:
        return size <= SMALL
    return True


def main():
    names = [n for n in open(sys.argv[1], "rb").read().decode().split("\0") if n]
    extra_path = os.path.join(os.path.dirname(__file__), "commit-extra-20261110.txt")
    extra = set()
    if os.path.exists(extra_path):
        extra = {l.strip() for l in open(extra_path) if l.strip() and not l.startswith("#")}
    out = sys.stdout.buffer
    for name in names:
        if os.path.isfile(name) and keep(name, os.path.getsize(name), extra):
            out.write(name.encode() + b"\0")


if __name__ == "__main__":
    main()
