"""Harness-only cancellation, job admission, and verified worker completion.

The upstream agent, tools and blackboards remain in the same process. A cancelled
status alone is never treated as proof that execute() returned.
"""
import copy
from contextvars import ContextVar
import hmac
import math
import os
import threading
import time

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse


# Transport metadata for local student inference. It is never part of the model prompt.
STUDENT_WINDOW = ContextVar("flockcue_student_window", default=None)


def install(service, token):
    if not token:
        raise ValueError("Missing harness control token")
    lock = threading.Lock()
    state = {"busy": False, "lease": None}
    finished = {}
    deadlines = {}
    original_process = service.process_agent_command

    class StopCallback(service.CancellationCallback):
        raise_error = True
        run_inline = True

        def _check_cancellation(self):
            if time.monotonic() >= deadlines.get(self.job_id, float("inf")):
                service.jobs[self.job_id].status = service.JobStatus.CANCELLED
            super()._check_cancellation()

        def on_llm_end(self, *args, **kwargs):
            self._check_cancellation()

        def on_tool_end(self, *args, **kwargs):
            self._check_cancellation()

    service.CancellationCallback = StopCallback

    def process(job_id, command):
        event = threading.Event()
        with lock:
            finished[job_id] = event
            lease = state["lease"]
            deadlines[job_id] = state["deadline"]
            task_id, cutoff = state["task_id"], state["cutoff"]
        context = STUDENT_WINDOW.set((task_id, lease, cutoff) if task_id else None)
        try:
            # Each tool closure keeps its task's client and lease even if a stale
            # thread somehow outlives execute(). It cannot use the next window.
            client = copy.copy(service.agent_instance.client)
            client.headers = {**client.headers, "X-Reproduction-Lease": lease}
            service.agent_instance.client = client
            original_process(job_id, command)
        finally:
            STUDENT_WINDOW.reset(context)
            with lock:
                state["busy"] = False
                event.set()

    service.process_agent_command = process

    def authorize(request):
        if not hmac.compare_digest(request.headers.get("x-api-key", ""), token):
            raise HTTPException(403, "Forbidden")

    @service.app.get("/_reproduction/identity")
    async def identity(request: Request):
        authorize(request)
        return {"instance_id": os.environ.get("REPRO_INSTANCE_ID")}

    @service.app.post("/_reproduction/jobs/{job_id}/stop")
    async def stop(job_id: str, request: Request):
        authorize(request)
        if job_id not in service.jobs:
            raise HTTPException(404, "Unknown job")
        job = service.jobs[job_id]
        if job.status not in {service.JobStatus.COMPLETED, service.JobStatus.FAILED}:
            job.status = service.JobStatus.CANCELLED
        return {"stop_requested": True}

    @service.app.get("/_reproduction/jobs/{job_id}/state")
    async def job_state(job_id: str, request: Request):
        authorize(request)
        with lock:
            done = job_id in finished and finished[job_id].is_set()
            busy = state["busy"]
        response = {"job_id": job_id, "worker_finished": done, "busy": busy}
        if done and not busy:
            agent = service.agent_instance
            response["blackboards"] = service.make_json_safe({
                sid: vars(board) for sid, board in getattr(agent, "_session_blackboards", {}).items()})
            response["job"] = service.make_json_safe(service.jobs[job_id])
        return response

    class Admission:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].startswith("/agent/command"):
                headers = dict(scope["headers"])
                authorized = hmac.compare_digest(headers.get(b"x-api-key", b""), token.encode())
                lease = headers.get(b"x-reproduction-lease", b"").decode()
                task_id = headers.get(b"x-reproduction-task", b"").decode()
                try:
                    cutoff = float(headers.get(b"x-reproduction-cutoff", b"nan"))
                except ValueError:
                    cutoff = float("nan")
                with lock:
                    allowed = authorized and lease and math.isfinite(cutoff) and cutoff > time.time() and not state["busy"] and scope["path"] == "/agent/command/async"
                    if allowed:
                        state.update(busy=True, lease=lease, task_id=task_id, cutoff=cutoff,
                                     deadline=time.monotonic() + cutoff - time.time())
                if not allowed:
                    return await JSONResponse({"detail": "Task admission refused"}, status_code=409)(scope, receive, send)
                try:
                    # Starlette awaits the background job before this returns.
                    await self.app(scope, receive, send)
                finally:
                    with lock:
                        if state["lease"] == lease:
                            state["busy"] = False
                return
            await self.app(scope, receive, send)

    service.app.add_middleware(Admission)
