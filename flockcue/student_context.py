"""Apply the student tokenizer in its pinned environment before provider serialization."""
import json
from pathlib import Path
import subprocess
from threading import Lock

from flockcue.recording.compact_receipts import compact_receipts


class StudentContext:
    def __init__(self, fallback_prompt_limit=None, *, compact=False):
        self.compact = compact
        self.fallback_prompt_limit = fallback_prompt_limit
        root = Path(__file__).resolve().parents[1]
        self.worker = subprocess.Popen(
            [str(root / "posttraining/.venv/bin/python"), "-m", "posttraining.trajectories.context_window"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.lock = Lock()

    def close(self):
        self.worker.stdin.close()
        self.worker.wait()
        self.worker.stdout.close()

    def apply(self, payload):
        from flockcue.recording.wire import record_context_window

        messages = payload["messages"]
        with self.lock:
            request = {"messages": messages, "tools": payload.get("tools", []),
                       "fallback_prompt_limit": self.fallback_prompt_limit, "compact_receipts": self.compact}
            self.worker.stdin.write(json.dumps(request) + "\n")
            self.worker.stdin.flush()
            response = self.worker.stdout.readline()
        if not response:
            raise RuntimeError("Student context worker stopped before measuring the request")
        window = json.loads(response)
        if "error" in window:
            record_context_window(window, [])
            raise ValueError(window["error"])
        omitted = [(i, msg) for i, msg in enumerate(messages[:window["start"]])
                   if msg["role"] not in {"system", "user"}]
        if self.compact:
            window["compact_receipts"] = True
        record_context_window(window, omitted)
        dropped = {i for i, _ in omitted}
        visible = [msg for i, msg in enumerate(messages) if i not in dropped]
        payload["messages"] = compact_receipts(visible) if self.compact else visible
        return payload
