"""Budgeted real HTTP teacher, exercised by substituting an IPv4 loopback peer."""
from contextlib import contextmanager, nullcontext
from decimal import Decimal
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import threading

from deepseek_cost_pilot import private_payload
from harness_deepseek_adapter import SYNTHETIC_KEY, httpx
from harness_live_cost import LiveCostLedger, make_cost_transport
from harness_request_limits import RequestLimitController, RequestLimitError, RequestLimits, make_guarded_transport
from thinking_events.campaign_storage import atomic_write, json_bytes
from .teacher_network import allow_registered_transport, endpoint
from .teacher_transport import BudgetedTeacher, _finish_interval, _interval, _policy


@contextmanager
def exclusive_worker(batch_root):
    """A detached worker keeps this lease even if the outer coordinator is killed."""
    with (Path(batch_root) / ".service-worker.lock").open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A teacher worker is still active; recovery cannot alter its ledger") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def recover_ledger(path, limits, *, currency, batch_cap, abandoned_scene=False, locked=False):
    """Reopen the existing journal after quiescence; never emit or replay a request."""
    path = Path(path)
    with nullcontext() if locked else exclusive_worker(path.parent):
        with LiveCostLedger.open(path, _policy(limits, currency), batch_cap) as ledger:
            if abandoned_scene and not ledger.snapshot()["blocked"]:
                ledger.block("unverified_scene_after_restart")
            return ledger.snapshot()


class TeacherService(BudgetedTeacher):
    def __init__(self, path, limits, config):
        self.mode = config["transport"]
        self.sense_interface = config.get("sense_interface", "B")
        if self.sense_interface not in ("B", "C"):
            raise ValueError("Unknown registered sensing interface")
        self.base_url = endpoint(config)
        currency = config["currency"]
        account = limits["accounts"][currency]
        for field in ("batch_cap", "per_task_cap"):
            amount = Decimal(config[field])
            if not amount.is_finite() or not 0 < amount <= Decimal(account[field]):
                raise ValueError("Teacher budget outside registered pilot cap")
        self.api_key = SYNTHETIC_KEY if self.mode == "loopback_http" else os.environ.pop("DEEPSEEK_API_KEY", "")
        if not self.api_key.strip() or (self.mode == "deepseek" and self.api_key == SYNTHETIC_KEY):
            raise ValueError("Missing DeepSeek service credential")
        self.api_key = self.api_key.strip()
        super().__init__(path, limits, currency=currency, batch_cap=config["batch_cap"],
                         per_task_cap=config["per_task_cap"])

    def begin(self, task_id, command, folder, deadline):
        if not self.start_task(task_id, deadline):
            return False
        self.sdk_attempt = 0
        self.wire = Path(folder) / "provider-wire"
        self.timing = Path(folder) / "provider-timing"
        self.wire.mkdir()
        self.timing.mkdir()
        bounds = RequestLimits(self.limits["max_http_attempts_per_task"],
                               self.limits["max_billed_usage_tokens_per_task_stop_after_response"],
                               self.limits["max_output_tokens_per_request"], self.limits["max_request_seconds"])
        self.controller = RequestLimitController(bounds, task_deadline=deadline)
        raw = _ProviderHTTP(self)
        billed = make_cost_transport(httpx, raw, self.ledger, task_id)
        self.cost_transport = make_guarded_transport(httpx, billed, self.controller)
        return True

    def _validate(self, request):
        if request.method != "POST" or str(request.url) != self.base_url + "/chat/completions":
            raise ValueError("Invalid teacher endpoint")
        body = json.loads(request.content)
        if (private_payload(body) or body.get("model") != self.limits["teacher_model"] or
                body.get("thinking") != {"type": self.limits["teacher_thinking"]} or
                body.get("reasoning_effort") != self.limits["reasoning_effort"] or body.get("stream") is not False or
                body.get("max_tokens") != self.limits["max_output_tokens_per_request"] or "temperature" in body):
            raise ValueError("Invalid teacher request or private evaluation fields")

    def _handle(self, request):
        self._validate(request)
        self.sdk_attempt += 1
        number = self.sdk_attempt
        data = {"task_id": self.task, "attempt": number, "transport": _interval(), "dispatched": False,
                "sdk_received": False, "request_body_sha256": sha256(request.content).hexdigest(),
                "response_body_sha256": None, "status_code": None, "model": None, "system_fingerprint": None,
                "usage": None, "stop_reason": None}
        event = self.timing / f"attempt-{number:02d}.json"
        stem = self.wire / f"turn-{number:02d}"
        atomic_write(stem.with_suffix(".request.json"), request.content)
        atomic_write(event, json_bytes(data))
        lock = threading.Lock()
        request.extensions["teacher_capture"] = (event, stem, data, lock)
        try:
            response = self._admit(request)
            with lock:
                self.tokens += data["usage"]["total_tokens"]
                data["sdk_received"] = True
            return response
        except Exception as exc:
            if isinstance(exc, RequestLimitError) and exc.reason in {"task_deadline", "request_timeout"}:
                self.ledger.block(exc.reason)
            self.reason = self.blocked_reason() or getattr(exc, "reason", type(exc).__name__)
            with lock:
                data["stop_reason"] = self.reason
            raise
        finally:
            with lock:
                _finish_interval(data["transport"])
                atomic_write(event, json_bytes(data))

    def finish(self):
        self.controller.drain(1.0)
        self.cost_transport.close()
        if self.controller.snapshot()["threads_still_alive"]:
            self.ledger.block("inflight_teacher_transport_at_close")
            # Prevent a still-running HTTP thread from modifying a sealed scene. No next task or score
            # is launched for this worker; the supervisor retains the incomplete scene for recovery.
            raise RuntimeError("A provider request remained in flight after the task stopped")
        state = self.ledger.snapshot()
        trial = state["trials"][self.task]
        if not state["blocked"]:
            self.ledger.finish_trial(self.task)
        self.task = None
        return {"stop_reason": state["stop_reason"] or self.reason, "batch_stopped": state["blocked"],
                "attempts": self.turns, "sdk_attempts": self.sdk_attempt,
                "external_provider_calls": self.turns if self.mode == "deepseek" else 0,
                "actual_fee": None if self.mode == "deepseek" else "0",
                "settled_conservative_charge": trial["settled_conservative_charge"],
                "locked_reservation": state["locked_reservation"]}


class _ProviderHTTP(httpx.BaseTransport):
    """The endpoint guard and durable wire capture run inside each prepaid reservation."""

    def __init__(self, teacher):
        self.teacher = teacher
        self.raw = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request):
        teacher = self.teacher
        event, stem, data, lock = request.extensions.pop("teacher_capture")
        with lock:
            teacher.turns += 1
            data["dispatched"] = True
            atomic_write(event, json_bytes(data))
        with allow_registered_transport(teacher.base_url):
            response = self.raw.handle_request(request)
            try:
                body = response.read()
            finally:
                response.close()
        atomic_write(stem.with_suffix(".response.json"), body)
        with lock:
            data.update(response_body_sha256=sha256(body).hexdigest(), status_code=response.status_code)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                payload = {}
            if isinstance(payload, dict):
                data.update(model=payload.get("model"), system_fingerprint=payload.get("system_fingerprint"),
                            usage=payload.get("usage"))
            atomic_write(event, json_bytes(data))
        return response

    def close(self):
        self.raw.close()
