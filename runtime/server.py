"""Harness-only admission barrier around the unchanged simulator API.

Requests admitted before the task deadline may finish during drain. All subsequent
agent requests (including reads) are refused. A closed, drained gate plus paused
simulation ticks provides a stable world for the original hidden checks.
"""
import asyncio
import hmac
import math
import os
from pathlib import Path
import sys
import time

from starlette.requests import Request
from starlette.responses import JSONResponse


class SimulationClock:
    def __init__(self):
        self.paused = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    async def run(self, upstream):
        # A tick and a synchronous controller reload must never see each other halfway through.
        while True:
            if not self.paused:
                upstream.target_controller.update_moving_targets()
                upstream.session_controller.sync_current_session_state()
                upstream.environment_controller.simulate_weather_changes()
            await asyncio.sleep(1.0)


class TaskGate:
    def __init__(self, app, admin_key, simulation):
        if not admin_key:
            raise ValueError("Missing harness admin key")
        self.app, self.admin_key, self.simulation = app, admin_key, simulation
        self.lease = None
        self.open = False
        self.deadline = 0.0
        self.active = 0
        self.accepted = self.rejected = 0
        self.drained = asyncio.Event()
        self.drained.set()

    async def control(self, request):
        body = await request.json()
        operation = request.url.path.rsplit("/", 1)[-1]
        if operation == "prepare":
            if self.open or self.active:
                return JSONResponse({"error": "previous task not drained"}, status_code=409)
            self.simulation.resume()
            return JSONResponse({"ready": True})
        if operation == "open":
            seconds = float(body["timeout_seconds"])
            if self.open or self.active or not body.get("lease") or not math.isfinite(seconds) or seconds <= 0:
                return JSONResponse({"error": "invalid admission request"}, status_code=409)
            self.lease = body["lease"]
            self.accepted = self.rejected = 0
            self.deadline = time.monotonic() + seconds
            self.opened_at = time.time()
            self.cutoff_at = self.opened_at + seconds
            self.open = True
            self.simulation.resume()
            return JSONResponse({"lease": self.lease, "opened_at": self.opened_at, "cutoff_at": self.cutoff_at})
        if operation == "close":
            if body.get("lease") != self.lease:
                return JSONResponse({"error": "lease mismatch"}, status_code=409)
            self.open = False  # close admission BEFORE waiting on outstanding handlers
            closed_at, inflight = time.time(), self.active
            try:
                await asyncio.wait_for(self.drained.wait(), timeout=float(body["drain_seconds"]))
            except asyncio.TimeoutError:
                return JSONResponse({"quiescent": False, "inflight": self.active}, status_code=503)
            self.simulation.pause()
            return JSONResponse({"lease": self.lease, "quiescent": True, "inflight_at_close": inflight,
                                 "inflight": self.active, "accepted_requests": self.accepted,
                                 "rejected_requests": self.rejected, "cutoff_at": self.cutoff_at,
                                 "closed_at": closed_at, "frozen_at": time.time(), "simulation_paused": True})
        return JSONResponse({"error": "unknown harness operation"}, status_code=404)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        admin = hmac.compare_digest(headers.get(b"x-api-key", b""), self.admin_key.encode())
        if scope["path"] == "/_reproduction/identity" and scope["method"] == "GET" and admin:
            return await JSONResponse({"instance_id": os.environ.get("REPRO_INSTANCE_ID")})(scope, receive, send)
        if scope["path"].startswith("/_reproduction/"):
            if not admin or scope["method"] != "POST":
                response = JSONResponse({"error": "forbidden"}, status_code=403)
            else:
                response = await self.control(Request(scope, receive))
            return await response(scope, receive, send)
        if admin:
            return await self.app(scope, receive, send)
        lease = headers.get(b"x-reproduction-lease", b"").decode()
        # Upstream constructor reads the current session before its first job.
        bootstrap = self.lease is None and scope["method"] == "GET" and scope["path"] == "/sessions/current"
        if not bootstrap and (not self.open or time.monotonic() >= self.deadline or lease != self.lease):
            self.rejected += 1
            return await JSONResponse({"detail": "Task action window is closed"}, status_code=403)(scope, receive, send)
        self.active += 1
        self.accepted += 1
        self.drained.clear()
        try:
            await self.app(scope, receive, send)
        finally:
            self.active -= 1
            if not self.active:
                self.drained.set()


def main():
    """Serve the same world, admission window and independent checks as the 600-task run."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation/server"))
    from api import server as world
    from config import logging_config
    from config.privilege_keys import ADMIN_PRIVILEGE_KEYS
    from models.session import DEFAULT_REQUEST_HISTORY_LIMIT

    logging_config.setup_logging()
    world.session_controller.set_request_history_limit(DEFAULT_REQUEST_HISTORY_LIMIT)
    clock = SimulationClock()
    app = TaskGate(world.app, os.environ.get("REPRO_ADMIN_KEY", ADMIN_PRIVILEGE_KEYS[0]), clock)

    async def serve():
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, access_log=False))
        async with asyncio.TaskGroup() as tasks:
            ticks = tasks.create_task(clock.run(world))
            try:
                await server.serve()
            finally:
                ticks.cancel()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
