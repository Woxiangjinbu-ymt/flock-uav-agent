"""Isolated real ASGI simulator, complete object-graph snapshots and relative time.

Pickles are generated locally from trusted fixtures, hash checked before loading,
and never supplied by the model. They include controller-only state absent from
the public Session JSON (history_status, timers and moving-target phase).
"""
from __future__ import annotations

import copy
from enum import Enum
import hashlib
import importlib
import json
from pathlib import Path
import pickle
import random
import sys
import threading
import time

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
sys.path[:0] = [str(ROOT / "agent4drone"), str(ROOT / "server")]
# The two locked environments use the same Python 3.11 ABI. Agent dependencies
# take precedence; the server environment supplies rendering-only pygame imports.
sys.path.extend(str(p) for p in (ROOT / "server/.venv/lib").glob("python*/site-packages"))
EPOCH = 1802649600.0
CONTROLLERS = ("drone_controller", "target_controller", "obstacle_controller",
               "environment_controller", "session_controller")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    tmp.replace(path)


class SimulatorClock:
    def __init__(self):
        self.relative = 0.0

    def time(self):
        return EPOCH + self.relative

    def __getattr__(self, name):
        return getattr(time, name)


class World:
    def __init__(self, seed=20261016):
        from api import server
        from fastapi.testclient import TestClient
        self.server = server
        self.clock = SimulatorClock()
        self.rng = random.Random(seed)
        self.id_rng = random.Random(seed ^ 0x51D)
        # Patch module bindings, never the process-wide time or random modules.
        for prefix in ("models", "controllers"):
            for path in (ROOT / "server" / prefix).glob("*.py"):
                module = importlib.import_module(f"{prefix}.{path.stem}")
                if hasattr(module, "time"):
                    module.time = self.clock
                if hasattr(module, "generate_random_id"):
                    module.generate_random_id = lambda: f"{self.id_rng.getrandbits(32):08x}"
        server.time = self.clock
        import controllers.environment_controller as weather
        weather.random = self.rng
        self.client = TestClient(server.app)  # No lifespan/background simulator thread.
        self.lock = threading.RLock()
        self.started = None
        self.tick = 0
        self.timeline = []
        self.events = []
        self.agent_headers = {"X-API-Key": server.ROLE_SECRETS[server.UserRole.AGENT]}
        self.admin_headers = {"X-API-Key": server.ROLE_SECRETS[server.UserRole.ADMIN]}

    def import_session(self, raw):
        from models.session import Session
        sc = self.server.session_controller
        sc._clear_all_data()
        sc.sessions.clear()
        sc.current_session_id = None
        session = Session.from_dict(copy.deepcopy(raw))
        sc.sessions[session.id] = session
        sc.set_current_session(session.id)
        self.server.target_controller.last_update_time = self.clock.time()
        return session

    def snapshot(self):
        # Preserve references/cycles between controllers in one serialized graph.
        return pickle.dumps({"controllers": {k: getattr(self.server, k) for k in CONTROLLERS},
                             "clock": self.clock.relative, "rng": self.rng.getstate(),
                             "tick": self.tick, "id_rng": self.id_rng.getstate()}, protocol=5)

    def restore(self, blob, expected_sha=None):
        if expected_sha is not None and sha(blob) != expected_sha:
            raise ValueError("Full-state snapshot checksum mismatch")
        state = pickle.loads(blob)
        for name, controller in state["controllers"].items():
            setattr(self.server, name, controller)
        self.clock.relative = state["clock"]
        self.rng.setstate(state["rng"])
        self.id_rng.setstate(state["id_rng"])
        self.tick = state["tick"]
        self.started = None
        self.events = []
        # Equality is structural: pickle byte memoization need not be canonical.
        if self.state_digest() != digest(self.inspect_blob(blob)):
            raise ValueError("Restored controller graph differs from snapshot")

    @staticmethod
    def inspect_blob(blob):
        def plain(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {str(k): plain(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [plain(v) for v in value]
            if isinstance(value, set):
                return sorted([plain(v) for v in value], key=str)
            if hasattr(value, "__dict__"):
                # Controllers form cycles; represent their cross references by name.
                return {k: ("@" + type(v).__name__ if k.endswith("controller") else plain(v))
                        for k, v in vars(value).items()}
            return value
        return plain(pickle.loads(blob))

    def state_digest(self):
        return digest(self.inspect_blob(self.snapshot()))

    def bake_timeline(self, seconds=501):
        original = self.snapshot()
        timeline = []
        for tick in range(1, seconds + 1):
            self.clock.relative = float(tick)
            self.server.target_controller.update_moving_targets()
            self.server.environment_controller.simulate_weather_changes()
            timeline.append({"second": tick,
                "targets": {k: {a: copy.deepcopy(v) for a, v in vars(t).items()
                                 if a not in {"is_reached", "reached_by", "last_tracked_at"}}
                            for k, t in self.server.target_controller.targets.items()},
                "environments": copy.deepcopy(self.server.environment_controller.environments),
                "rng": self.rng.getstate()})
        self.restore(original)
        self.timeline = timeline
        return pickle.dumps(timeline, protocol=5)

    def advance(self, relative):
        relative = max(self.clock.relative, float(relative))
        while self.tick < min(int(relative), len(self.timeline)):
            event = self.timeline[self.tick]
            self.clock.relative = float(event["second"])
            for key, attrs in event["targets"].items():
                target = self.server.target_controller.targets[key]
                for name, value in attrs.items():
                    setattr(target, name, copy.deepcopy(value))
            self.server.environment_controller.environments = copy.deepcopy(event["environments"])
            self.rng.setstate(event["rng"])
            self.server.target_controller.last_update_time = self.clock.time()
            self.server.session_controller.sync_current_session_state()
            self.tick += 1
        self.clock.relative = relative

    def start(self):
        self.started = time.monotonic()

    def request(self, method, endpoint, *, admin=False, **kwargs):
        with self.lock:
            if self.started is not None:
                self.advance(time.monotonic() - self.started)
            kwargs.pop("timeout", None)
            kwargs.pop("headers", None)
            response = self.client.request(method, endpoint,
                headers=self.admin_headers if admin else self.agent_headers, **kwargs)
            response.raise_for_status()
            result = response.json() if response.status_code != 204 else None
            if getattr(self, "audit_path", None):
                with self.audit_path.open("a") as stream:
                    stream.write(json.dumps({"relative_seconds": self.clock.relative, "method": method,
                        "endpoint": endpoint, "params": kwargs.get("params"), "admin": admin,
                        "response": result}, ensure_ascii=False) + "\n")
            return result

    def public_seed(self):
        drones = self.request("GET", "/drones")
        return {"drones": drones, "nearby": {d["id"]: self.request("GET", f"/drones/{d['id']}/nearby")
                                             for d in drones}}
