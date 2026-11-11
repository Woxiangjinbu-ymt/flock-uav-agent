"""Synchronous non-streaming HTTP admission for isolated natural-Agent workers.

This is an enforcing transport, not a fail-open archive observer. Token limits
are measured after complete responses: they are neither precise pre-accounting
nor a hard supplier-cost ceiling. A timed-out I/O thread cannot be killed here;
the caller must drain it or terminate its worker process before scoring/reuse.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field
import hashlib
import json
import math
import threading
import time


class RequestLimitError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class UsageUnavailable(RequestLimitError):
    def __init__(self):
        super().__init__("unknown_usage")


class UnsupportedAsyncTransport(RequestLimitError):
    def __init__(self):
        super().__init__("async_unsupported")


@dataclass(frozen=True)
class RequestLimits:
    max_http_attempts: int
    max_total_tokens: int
    max_output_tokens: int
    per_request_timeout_seconds: float

    def __post_init__(self):
        for value in (self.max_http_attempts, self.max_total_tokens, self.max_output_tokens):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Limits must be positive integers")
        value = self.per_request_timeout_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Request timeout must be finite and positive")


@dataclass
class _Job:
    attempt: dict
    response: object = None
    body: bytes | None = None
    error: BaseException | None = None
    done: threading.Event = field(default_factory=threading.Event)
    delegated: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    settled: bool = False


def _body_record(body):
    return {"base64": base64.b64encode(body).decode("ascii"),
            "sha256": hashlib.sha256(body).hexdigest(), "length": len(body)}


def _usage(body):
    try:
        payload = json.loads(body)
        usage = payload["usage"]
        values = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
            return None
        if values["total_tokens"] != values["prompt_tokens"] + values["completion_tokens"]:
            return None
        return values
    except (ValueError, TypeError, KeyError):
        return None


class RequestLimitController:
    def __init__(self, limits, *, task_deadline, clock=time.monotonic, cancel_check=lambda: False):
        if not isinstance(limits, RequestLimits):
            raise TypeError("Expected RequestLimits")
        if isinstance(task_deadline, bool) or not isinstance(task_deadline, (int, float)) or not math.isfinite(task_deadline):
            raise ValueError("Task deadline must be a finite monotonic timestamp")
        self.limits, self.task_deadline = limits, task_deadline
        self.clock, self.cancel_check = clock, cancel_check
        self._lock = threading.RLock()
        self._jobs = []
        self._active = False
        self._closed = False
        self._stop_reason = None
        self._known_tokens = 0
        self._underlying = None
        self._underlying_closed = False

    def _stop(self, reason):
        if self._stop_reason is None:
            self._stop_reason = reason

    def _interruption(self):
        if self._closed:
            return "closed"
        if self.cancel_check():
            return "cancelled"
        if self.clock() >= self.task_deadline:
            return "task_deadline"
        return None

    def _settle(self, job, *, late=False):
        """Called only by the waiting/draining caller, never by an I/O thread."""
        if job.settled or not job.done.is_set():
            return
        job.settled = True
        row = job.attempt
        row["delegated"] = job.delegated.is_set()
        if job.error is not None:
            row.update(usage_status="unknown" if row["delegated"] else "not_dispatched",
                       error_type=type(job.error).__name__)
            return
        status = job.response.status_code
        row["status_code"] = status
        usage = _usage(job.body)
        row["usage"] = usage
        row["usage_status"] = ("known_late" if late else "known") if usage is not None else "unknown"
        if usage is not None:
            self._known_tokens += usage["total_tokens"]
        if late or usage is None:
            row["late_response" if late else "diagnostic_response"] = {
                "status_code": status, "body": _body_record(job.body),
                "scope": "decoded_buffered_httpx_content_private_diagnostic_not_model_input"}

    def _send(self, underlying, request):
        with self._lock:
            if self._active:
                raise RequestLimitError("concurrent_request")
            reason = self._stop_reason or ("closed" if self._closed else None) or self._interruption()
            if reason is None and len(self._jobs) >= self.limits.max_http_attempts:
                reason = "http_attempt_limit"
            if reason is None and self._known_tokens >= self.limits.max_total_tokens:
                reason = "token_limit"
            if reason:
                self._stop(reason)
                raise RequestLimitError(reason)
            try:
                raw = request.content  # Never consume an unbuffered request stream.
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Request must be an object")
            except Exception:
                self._stop("invalid_request_body")
                raise RequestLimitError("invalid_request_body") from None
            if payload.get("stream", False) is not False:
                self._stop("streaming_unsupported")
                raise RequestLimitError("streaming_unsupported")
            outputs = [payload[key] for key in ("max_tokens", "max_completion_tokens") if key in payload]
            if (not outputs or any(isinstance(value, bool) or not isinstance(value, int)
                    or value <= 0 or value > self.limits.max_output_tokens for value in outputs)
                    or len(set(outputs)) != 1):
                self._stop("invalid_output_limit")
                raise RequestLimitError("invalid_output_limit")
            remaining = self.task_deadline - self.clock()
            if remaining <= 0:
                self._stop("task_deadline")
                raise RequestLimitError("task_deadline")
            timeout = min(self.limits.per_request_timeout_seconds, remaining)
            old_timeouts = request.extensions.get("timeout") or {}
            request.extensions["timeout"] = {
                key: min(timeout, value) if isinstance(value := old_timeouts.get(key), (int, float))
                and not isinstance(value, bool) and math.isfinite(value) and value > 0 else timeout
                for key in ("connect", "read", "write", "pool")}
            row = {"attempt_id": len(self._jobs) + 1, "delegated": False, "delivered_to_sdk": False,
                   "request_body_sha256": hashlib.sha256(raw).hexdigest(), "request_body_length": len(raw),
                   "max_output_tokens": outputs[0], "admitted_timeout_seconds": timeout,
                   "usage": None, "usage_status": "pending", "outcome": "pending"}
            job = _Job(row)
            self._jobs.append(job)
            self._active = True

        # No ContextVar is copied into this thread and no SDK/archive hooks run
        # here. The thread writes only its private outcome, not controller records.
        def work():
            try:
                # A delayed thread must not initiate I/O after the caller has
                # timed out, cancelled or closed this controller.
                with self._lock:
                    reason = self._stop_reason or self._interruption()
                    if reason is None and time.monotonic() >= wall_deadline:
                        reason = "request_timeout"
                    if reason:
                        raise RequestLimitError(reason)
                    job.delegated.set()
                job.response = underlying.handle_request(request)
                if job.response.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/event-stream":
                    raise RequestLimitError("streaming_unsupported")
                job.body = job.response.read()
            except BaseException as exc:
                job.error = exc
            finally:
                job.done.set()

        job.thread = threading.Thread(target=work, name=f"bounded-http-{row['attempt_id']}", daemon=True)
        wall_started = time.monotonic()
        wall_deadline = wall_started + timeout
        try:
            job.thread.start()
            while True:
                reason = self._interruption()
                if reason is None and time.monotonic() >= wall_deadline:
                    reason = "request_timeout"
                if reason:
                    with self._lock:
                        self._stop(reason)
                        row.update(outcome=reason, usage_status="unknown", unknown_at_cutoff=True)
                    raise RequestLimitError(reason)
                if job.done.wait(min(0.01, max(0.0, wall_deadline - time.monotonic()))):
                    # Re-check the admission window after the complete body.
                    reason = self._interruption()
                    if reason is None and time.monotonic() >= wall_deadline:
                        reason = "request_timeout"
                    if reason:
                        with self._lock:
                            self._stop(reason)
                            row.update(outcome=reason, usage_status="unknown", unknown_at_cutoff=True)
                        raise RequestLimitError(reason)
                    break
            with self._lock:
                self._settle(job)
                if job.error is not None:
                    reason = job.error.reason if isinstance(job.error, RequestLimitError) else "transport_error"
                    self._stop(reason)
                    row["outcome"] = reason
                    raise job.error
                reason = self._interruption()
                if reason is None and time.monotonic() >= wall_deadline:
                    reason = "request_timeout"
                if reason:
                    self._stop(reason)
                    row["outcome"] = reason
                    row["late_response"] = {"status_code": job.response.status_code,
                        "body": _body_record(job.body), "scope": "decoded_buffered_httpx_content_private_diagnostic_not_model_input"}
                    raise RequestLimitError(reason)
                if job.response.status_code >= 400:
                    self._stop("http_error")
                    row["outcome"] = "http_error"
                elif row["usage"] is None:
                    self._stop("unknown_usage")
                    row["outcome"] = "unknown_usage"
                    raise UsageUnavailable()
                else:
                    row["outcome"] = "response"
                if self._known_tokens >= self.limits.max_total_tokens:
                    self._stop("token_limit")
                row["delivered_to_sdk"] = True
                return job.response
        except BaseException:
            with self._lock:
                if row["outcome"] == "pending":
                    self._stop("interrupted")
                    row.update(outcome="interrupted", usage_status="unknown", unknown_at_cutoff=True)
            raise
        finally:
            with self._lock:
                row["wait_wall_seconds"] = time.monotonic() - wall_started
                row["delegated"] = job.delegated.is_set()
                self._active = False

    def drain(self, timeout_seconds):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("Drain timeout must be finite and nonnegative")
        with self._lock:
            if self._active:
                raise RequestLimitError("concurrent_request")
            jobs = list(self._jobs)
        until = time.monotonic() + timeout_seconds
        for job in jobs:
            if job.thread is not None and job.thread.ident is not None:
                job.thread.join(max(0.0, until - time.monotonic()))
            if job.done.is_set():
                with self._lock:
                    self._settle(job, late=True)
        return not any(job.thread is not None and job.thread.is_alive() for job in jobs)

    def close(self):
        with self._lock:
            self._closed = True
            if self._active or any(job.thread is not None and job.thread.is_alive() for job in self._jobs):
                return  # Never block by closing an underlying transport still in use.
            if self._underlying is None or self._underlying_closed:
                return
            self._underlying_closed = True
            underlying = self._underlying
        underlying.close()

    def snapshot(self):
        with self._lock:
            alive = [job.attempt["attempt_id"] for job in self._jobs if job.thread is not None and job.thread.is_alive()]
            unknown = [job.attempt["attempt_id"] for job in self._jobs if job.attempt["usage_status"] == "unknown"]
            return {"stop_reason": self._stop_reason, "closed": self._closed,
                    "admitted_http_attempts": len(self._jobs),
                    "delegated_http_attempts": sum(job.delegated.is_set() for job in self._jobs),
                    "known_total_tokens": self._known_tokens, "unknown_usage_attempts": len(unknown),
                    "unknown_usage_attempt_ids": unknown, "threads_still_alive": alive,
                    "needs_process_termination": bool(alive and (self._stop_reason or self._closed)),
                    "reusable": not self._closed and self._stop_reason is None,
                    "token_limit_scope": "measured_post_response_stop_not_hard_supplier_cost_cap",
                    "attempts": copy.deepcopy([job.attempt for job in self._jobs])}


def make_guarded_transport(httpx, underlying, controller):
    if not callable(getattr(underlying, "handle_request", None)):
        raise UnsupportedAsyncTransport()
    with controller._lock:
        if controller._underlying is not None:
            raise ValueError("Controller already owns a transport")
        controller._underlying = underlying

    class GuardedTransport(httpx.BaseTransport):
        def handle_request(self, request):
            return controller._send(underlying, request)

        async def handle_async_request(self, request):
            raise UnsupportedAsyncTransport()

        async def __aenter__(self):
            raise UnsupportedAsyncTransport()

        async def aclose(self):
            raise UnsupportedAsyncTransport()

        def close(self):
            controller.close()

    return GuardedTransport()
