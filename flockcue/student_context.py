"""Apply the student tokenizer in its pinned environment before provider serialization."""
import json
from pathlib import Path
import subprocess
from threading import Lock


class StudentContext:
    def __init__(self):
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
            self.worker.stdin.write(json.dumps({"messages": messages, "tools": payload.get("tools", [])}) + "\n")
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
        record_context_window(window, omitted)
        dropped = {i for i, _ in omitted}
        payload["messages"] = [msg for i, msg in enumerate(messages) if i not in dropped]
        return payload
