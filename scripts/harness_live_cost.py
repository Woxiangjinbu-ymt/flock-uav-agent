"""Durable conservative cost admission at one serial synchronous transport.

Prices/context limits are explicit inputs from the reviewed provider profile.
Request bytes are an admission limit, not a tokenizer or a billing guarantee.
Every delegated request first fsyncs a full-context reservation. Unknown outcomes
retain that reservation and permanently stop this ledger; there is no resume API.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import threading


class LiveCostError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _money(value, *, positive=False):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
        raise ValueError("Money/rates require finite unsigned decimal text")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError("Rate must be positive; budget must be nonnegative")
    return amount


def _integer(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0) or value > 10**12:
        raise ValueError("Expected a bounded nonnegative integer")
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise ValueError("Invalid trial identifier")
    return value


def _text(value):
    return format(value, "f")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique)


@dataclass(frozen=True)
class LiveCostPolicy:
    currency: str
    input_per_million: str
    output_per_million: str
    max_input_tokens: int
    max_output_tokens: int
    max_request_bytes: int = 200000

    def __post_init__(self):
        if self.currency not in {"CNY", "USD"}:
            raise ValueError("Only reviewed CNY/USD pricing is supported")
        _money(self.input_per_million, positive=True)
        _money(self.output_per_million, positive=True)
        for value in (self.max_input_tokens, self.max_output_tokens, self.max_request_bytes):
            _integer(value, positive=True)
        if self.max_output_tokens > self.max_input_tokens:
            raise ValueError("Output cap exceeds input/context bound")

    def cost(self, input_tokens, output_tokens):
        _integer(input_tokens); _integer(output_tokens)
        if input_tokens > self.max_input_tokens or output_tokens > self.max_output_tokens:
            raise ValueError("Usage exceeds the reviewed provider bounds")
        with localcontext() as ctx:
            ctx.prec = 180
            return (_money(self.input_per_million) * input_tokens
                    + _money(self.output_per_million) * output_tokens) / Decimal(1000000)

    @property
    def request_reservation(self):
        return self.cost(self.max_input_tokens, self.max_output_tokens)

    def validate_request(self, request):
        try:
            raw = request.content
            if not isinstance(raw, bytes) or not raw:
                raise ValueError("Request is not buffered bytes")
            raw.decode("utf-8", errors="strict")
            # Neither bound is an estimate of the actual prompt token count.
            if len(raw) > min(self.max_request_bytes, self.max_input_tokens):
                raise ValueError("Request exceeds conservative byte admission limit")
            body = _json(raw)
            if not isinstance(body, dict) or body.get("stream", False) is not False:
                raise ValueError("Only nonstreaming JSON requests are supported")
            outputs = [body[k] for k in ("max_tokens", "max_completion_tokens") if k in body]
            if not outputs or any(type(n) is not int or not 0 < n <= self.max_output_tokens for n in outputs) or len(set(outputs)) != 1:
                raise ValueError("Output cap missing, invalid or inconsistent")
            if body.get("n", 1) != 1 or type(body.get("n", 1)) is not int:
                raise ValueError("Multiple completions are not supported")
            return raw, outputs[0]
        except Exception as exc:
            raise LiveCostError("invalid_request:" + type(exc).__name__) from None


def _fsync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LiveCostLedger:
    """Append-only journal + durable head, with per-operation process/thread locks.

    Only one trial can be active. Reopening an unfinished trial blocks the batch,
    including a full-line truncated journal detected by its separate durable head.
    The head is a crash/tamper detector, not a cryptographic external trust root.
    """
    def __init__(self, path, policy, batch_budget):
        if not isinstance(policy, LiveCostPolicy):
            raise TypeError("Expected LiveCostPolicy")
        self.path = Path(path).absolute()
        self.policy = policy
        self.batch_budget = _text(_money(batch_budget))
        self._mutex = threading.RLock()
        self._closed = False
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._head_path = self.path.with_name(self.path.name + ".head.json")
        self._blocked_path = self.path.with_name(self.path.name + ".blocked.json")

    @classmethod
    def create(cls, path, policy, batch_budget):
        book = cls(path, policy, batch_budget)
        book.path.parent.mkdir(parents=True, exist_ok=True)
        with book._locked():
            if any(p.exists() for p in (book.path, book._head_path, book._blocked_path)):
                raise FileExistsError("Existing ledger cannot be overwritten")
            with book.path.open("xb") as stream:
                stream.flush(); os.fsync(stream.fileno())
            book._append([], {"kind": "created", "policy": asdict(policy), "batch_budget": book.batch_budget})
        return book

    @classmethod
    def open(cls, path, policy, batch_budget):
        book = cls(path, policy, batch_budget)
        with book._locked():
            events, state = book._load()
            if state["active_trial"] is not None and not state["blocked"]:
                book._append_block(events, "unfinished_trial_after_reopen")
        return book

    @contextmanager
    def _locked(self):
        with self._mutex:
            if self._closed:
                raise LiveCostError("ledger_closed")
            for candidate in (self.path, self._head_path, self._lock_path, self._blocked_path):
                if candidate.is_symlink():
                    raise LiveCostError("ledger_symlink_forbidden")
            with self._lock_path.open("a+b") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _marker(self, reason):
        try:
            with self._blocked_path.open("xb") as stream:
                stream.write(_canonical({"reason": reason}) + b"\n")
                stream.flush(); os.fsync(stream.fileno())
            _fsync_directory(self.path.parent)
        except FileExistsError:
            pass

    def _append(self, events, event):
        row = {"sequence": len(events) + 1, "previous_sha256": events[-1]["sha256"] if events else None, "event": event}
        row["sha256"] = _sha(_canonical(row))
        try:
            with self.path.open("ab") as stream:
                stream.write(_canonical(row) + b"\n")
                stream.flush(); os.fsync(stream.fileno())
            temporary = self._head_path.with_name(self._head_path.name + ".tmp")
            with temporary.open("xb") as stream:
                stream.write(_canonical({"sequence": row["sequence"], "sha256": row["sha256"]}) + b"\n")
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self._head_path)
            _fsync_directory(self.path.parent)
        except BaseException:
            self._marker("ledger_write_incomplete")
            raise
        events.append(row)
        return self._replay(events)

    def _load(self):
        try:
            raw = self.path.read_bytes()
            if not raw or not raw.endswith(b"\n"):
                raise ValueError("Truncated journal")
            events = [_json(line) for line in raw.splitlines()]
            for index, row in enumerate(events):
                if set(row) != {"sequence", "previous_sha256", "event", "sha256"}:
                    raise ValueError("Invalid journal fields")
                expected = {k: v for k, v in row.items() if k != "sha256"}
                if (type(row["sequence"]) is not int or row["sequence"] != index + 1
                        or row["previous_sha256"] != (events[index - 1]["sha256"] if index else None)
                        or row["sha256"] != _sha(_canonical(expected))):
                    raise ValueError("Journal chain changed")
            head = _json(self._head_path.read_bytes())
            if head != {"sequence": len(events), "sha256": events[-1]["sha256"]}:
                raise ValueError("Journal differs from durable head")
            state = self._replay(events)
            if self._blocked_path.exists() and not state["blocked"]:
                state.update(blocked=True, stop_reason="durable_block_marker", locked_reservation=state["reserved"])
                for attempt in state["attempts"].values():
                    if attempt["status"] == "reserved":
                        attempt.update(status="locked", stop_reason="durable_block_marker")
                if state["active_trial"] is not None:
                    state["trials"][state["active_trial"]]["status"] = "blocked"
            return events, state
        except Exception as exc:
            self._marker("ledger_corrupt_or_configuration_changed")
            if isinstance(exc, LiveCostError):
                raise
            raise LiveCostError("ledger_corrupt_or_configuration_changed") from exc

    def _replay(self, events):
        first = events[0]["event"]
        if first != {"kind": "created", "policy": asdict(self.policy), "batch_budget": self.batch_budget}:
            raise LiveCostError("ledger_policy_or_budget_mismatch")
        state = {"currency": self.policy.currency, "policy": asdict(self.policy), "batch_budget": self.batch_budget,
                 "settled_conservative_charge": "0", "reserved": "0", "active_trial": None,
                 "blocked": False, "stop_reason": None, "trials": {}, "attempts": {},
                 "automatic_resume": False, "hard_cap_claimed": False,
                 "price_scope": "full_context_reservation_and_cache_miss_usage_accounting_not_supplier_bill"}
        with localcontext() as ctx:
            ctx.prec = 180
            for row in events[1:]:
                event = row["event"]
                kind = event["kind"]
                if state["blocked"]:
                    raise ValueError("Mutation after block")
                if kind == "trial_started":
                    trial_id = _id(event["trial_id"])
                    if state["active_trial"] is not None or trial_id in state["trials"]:
                        raise ValueError("Duplicate or concurrent trial")
                    _money(event["trial_budget"])
                    state["trials"][trial_id] = {"budget": event["trial_budget"], "status": "active", "attempt_ids": [], "settled_conservative_charge": "0"}
                    state["active_trial"] = trial_id
                elif kind == "reserved":
                    trial_id = event["trial_id"]
                    trial = state["trials"][trial_id]
                    number = len(trial["attempt_ids"]) + 1
                    ident = trial_id + ":" + str(number)
                    bound = self.policy.request_reservation
                    if (state["active_trial"] != trial_id or Decimal(state["reserved"]) != 0
                            or event["attempt_id"] != number or event["reservation_id"] != ident
                            or event["reserved_upper_bound"] != _text(bound)
                            or not re.fullmatch(r"[0-9a-f]{64}", event["request_body_sha256"])
                            or type(event["request_body_length"]) is not int
                            or not 0 < event["request_body_length"] <= min(self.policy.max_request_bytes, self.policy.max_input_tokens)
                            or type(event["max_output_tokens"]) is not int
                            or not 0 < event["max_output_tokens"] <= self.policy.max_output_tokens
                            or Decimal(trial["settled_conservative_charge"]) + bound > Decimal(trial["budget"])
                            or Decimal(state["settled_conservative_charge"]) + bound > Decimal(state["batch_budget"])):
                        raise ValueError("Invalid reservation")
                    attempt = {**event, "status": "reserved"}
                    state["attempts"][ident] = attempt
                    trial["attempt_ids"].append(ident)
                    state["reserved"] = _text(bound)
                elif kind == "settled":
                    attempt = state["attempts"][event["reservation_id"]]
                    if attempt["status"] != "reserved" or state["active_trial"] != attempt["trial_id"]:
                        raise ValueError("Attempt already closed")
                    usage = event["usage"]
                    if set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens"} or type(usage["total_tokens"]) is not int or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
                        raise ValueError("Invalid usage")
                    if usage["completion_tokens"] > attempt["max_output_tokens"]:
                        raise ValueError("Output usage exceeds actual request")
                    charge = self.policy.cost(usage["prompt_tokens"], usage["completion_tokens"])
                    if event["charge"] != _text(charge) or charge > Decimal(attempt["reserved_upper_bound"]):
                        raise ValueError("Settlement exceeds reservation")
                    attempt.update(status="settled", usage=usage, settled_conservative_charge=event["charge"], response_body_sha256=event["response_body_sha256"])
                    trial = state["trials"][attempt["trial_id"]]
                    trial["settled_conservative_charge"] = _text(Decimal(trial["settled_conservative_charge"]) + charge)
                    state["settled_conservative_charge"] = _text(Decimal(state["settled_conservative_charge"]) + charge)
                    state["reserved"] = "0"
                elif kind == "trial_finished":
                    trial = state["trials"][event["trial_id"]]
                    if state["active_trial"] != event["trial_id"] or Decimal(state["reserved"]) != 0:
                        raise ValueError("Unfinished attempt or duplicate trial finish")
                    trial["status"] = "finished"
                    state["active_trial"] = None
                elif kind == "blocked":
                    state.update(blocked=True, stop_reason=event["reason"])
                    for attempt in state["attempts"].values():
                        if attempt["status"] == "reserved":
                            attempt.update(status="locked", stop_reason=event["reason"])
                    if state["active_trial"] is not None:
                        state["trials"][state["active_trial"]]["status"] = "blocked"
                else:
                    raise ValueError("Unknown ledger transition")
            state["locked_reservation"] = state["reserved"] if state["blocked"] else "0"
            state["unallocated_budget"] = _text(Decimal(state["batch_budget"]) - Decimal(state["settled_conservative_charge"]) - Decimal(state["reserved"]))
        return state

    def _open_state(self, state):
        if state["blocked"]:
            raise LiveCostError("batch_blocked:" + str(state["stop_reason"]))

    def _append_block(self, events, reason):
        self._marker(reason)
        return self._append(events, {"kind": "blocked", "reason": reason})

    def block(self, reason):
        if not isinstance(reason, str) or not reason:
            raise ValueError("Block reason required")
        with self._locked():
            events, state = self._load()
            return state if state["blocked"] else self._append_block(events, reason)

    def start_trial(self, trial_id, trial_budget):
        trial_id, budget = _id(trial_id), _text(_money(trial_budget))
        with self._locked():
            events, state = self._load(); self._open_state(state)
            if state["active_trial"] is not None or trial_id in state["trials"]:
                raise LiveCostError("duplicate_or_unfinished_trial")
            return self._append(events, {"kind": "trial_started", "trial_id": trial_id, "trial_budget": budget})

    def reserve(self, trial_id, request):
        raw, output = self.policy.validate_request(request)
        with self._locked():
            events, state = self._load(); self._open_state(state)
            if state["active_trial"] != trial_id:
                raise LiveCostError("inactive_trial")
            if Decimal(state["reserved"]) != 0:
                raise LiveCostError("concurrent_or_unsettled_request")
            trial = state["trials"][trial_id]
            with localcontext() as ctx:
                ctx.prec = 180
                bound = self.policy.request_reservation
                reason = ("trial_budget_insufficient" if Decimal(trial["settled_conservative_charge"]) + bound > Decimal(trial["budget"])
                          else "batch_budget_insufficient" if Decimal(state["settled_conservative_charge"]) + bound > Decimal(state["batch_budget"]) else None)
            if reason:
                self._append_block(events, reason)
                raise LiveCostError(reason)
            number = len(trial["attempt_ids"]) + 1
            event = {"kind": "reserved", "trial_id": trial_id, "attempt_id": number,
                "reservation_id": trial_id + ":" + str(number), "reserved_upper_bound": _text(bound),
                "request_body_sha256": _sha(raw), "request_body_length": len(raw), "max_output_tokens": output}
            self._append(events, event)
            return copy.deepcopy(event)

    def settle(self, reservation_id, body, *, status_code=200):
        with self._locked():
            events, state = self._load(); self._open_state(state)
            attempt = state["attempts"].get(reservation_id)
            if not attempt or attempt["status"] != "reserved":
                raise LiveCostError("attempt_not_reserved_or_already_settled")
            normal_http = type(status_code) is int and 200 <= status_code < 300
            try:
                if not normal_http:
                    raise ValueError("HTTP error")
                if not isinstance(body, bytes):
                    raise ValueError("Expected complete response bytes")
                payload = _json(body)
                usage = {key: payload["usage"][key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                for number in usage.values():
                    _integer(number)
                if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
                    raise ValueError("Usage sum mismatch")
                if usage["completion_tokens"] > attempt["max_output_tokens"]:
                    raise ValueError("Usage exceeds request output cap")
                charge = self.policy.cost(usage["prompt_tokens"], usage["completion_tokens"])
            except Exception:
                reason = "http_error_unknown_cost" if not normal_http else "unknown_or_invalid_usage"
                self._append_block(events, reason)
                raise LiveCostError(reason) from None
            return self._append(events, {"kind": "settled", "reservation_id": reservation_id,
                "usage": usage, "charge": _text(charge), "response_body_sha256": _sha(body)})

    def assert_dispatchable(self, reservation_id):
        """Recheck after fsync: an outer timeout may have blocked meanwhile."""
        with self._locked():
            _, state = self._load(); self._open_state(state)
            attempt = state["attempts"].get(reservation_id)
            if not attempt or attempt["status"] != "reserved" or state["active_trial"] != attempt["trial_id"]:
                raise LiveCostError("reservation_not_dispatchable")

    def finish_trial(self, trial_id):
        with self._locked():
            events, state = self._load(); self._open_state(state)
            if state["active_trial"] != trial_id:
                raise LiveCostError("trial_not_active_or_already_finished")
            if Decimal(state["reserved"]) != 0:
                self._append_block(events, "finish_with_unsettled_request")
                raise LiveCostError("finish_with_unsettled_request")
            return self._append(events, {"kind": "trial_finished", "trial_id": trial_id})

    def snapshot(self):
        with self._locked():
            return copy.deepcopy(self._load()[1])

    def close(self):
        if self._closed:
            return
        with self._locked():
            events, state = self._load()
            if state["active_trial"] is not None and not state["blocked"]:
                self._append_block(events, "trial_closed_unfinished")
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self.block("worker_exception:" + kind.__name__)
        self.close()


def make_cost_transport(httpx, underlying, ledger, trial_id):
    """Place INSIDE RequestLimitController; call ledger.block on outer timeout.

    There are no retries here. Caller must also construct its SDK with retries=0.
    Late completion cannot settle a reservation after the outer controller blocks.
    """
    if not callable(getattr(underlying, "handle_request", None)):
        raise TypeError("A synchronous transport is required")
    _id(trial_id)

    class CostTransport(httpx.BaseTransport):
        def handle_request(self, request):
            reservation = ledger.reserve(trial_id, request)
            try:
                ledger.assert_dispatchable(reservation["reservation_id"])
                response = underlying.handle_request(request)
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/event-stream":
                    raise LiveCostError("streaming_response_unsupported")
                body = response.read()
                ledger.settle(reservation["reservation_id"], body, status_code=response.status_code)
                return response
            except BaseException as exc:
                ledger.block("transport_exception:" + type(exc).__name__)
                raise

        def close(self):
            underlying.close()

    return CostTransport()
