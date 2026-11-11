"""Offline, billed-as-if-dispatched DeepSeek fixture for the two-scene teacher rehearsal."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import openai

from harness_deepseek_adapter import BASE_URL, SYNTHETIC_KEY, _DeepSeekCandidate, httpx
from harness_live_cost import LiveCostError, LiveCostLedger, LiveCostPolicy, make_cost_transport
from thinking_events.campaign_storage import atomic_write, json_bytes
from thinking_events.sense_example_prepare import SENSE, variant
from .runtime import ScriptedModel


def _interval():
    return {"start_utc": datetime.now(timezone.utc).isoformat(), "start_monotonic_ns": time.monotonic_ns()}


def _finish_interval(interval):
    end = time.monotonic_ns()
    interval.update(end_monotonic_ns=end, elapsed_ns=end - interval["start_monotonic_ns"],
                    end_utc=datetime.now(timezone.utc).isoformat())


def _policy(limits, currency):
    prices = limits["accounts"][currency]
    return LiveCostPolicy(currency, prices["rate_peak_input_cache_miss_per_million"],
                          prices["rate_peak_output_per_million"], limits["max_input_tokens_for_fee_reservation"],
                          limits["max_output_tokens_per_request"], limits["max_request_bytes"])


def create_ledger(path, limits, *, currency="CNY", batch_cap=None):
    cap = limits["accounts"][currency]["batch_cap"] if batch_cap is None else batch_cap
    LiveCostLedger.create(path, _policy(limits, currency), cap).close()


def ledger_snapshot(path, limits, *, currency="CNY", batch_cap=None):
    """Read-only view across scene processes, including an unsettled or blocked attempt."""
    cap = limits["accounts"][currency]["batch_cap"] if batch_cap is None else batch_cap
    return LiveCostLedger(path, _policy(limits, currency), cap).snapshot()


class PilotDeepSeekModel(_DeepSeekCandidate):
    """Apply the registered description before SDK serialization and archive hooks."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        body = super()._get_request_payload(input_, stop=stop, **kwargs)
        if any(t["function"]["name"] == SENSE for t in body.get("tools", [])):
            body = variant(body)
        return body


class _RejectAsync(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise RuntimeError("This rehearsal uses a synchronous local fixture")


class BudgetedTeacher:
    """Shared SDK and budget admission for one registered teacher session."""

    def __init__(self, ledger_path, limits, *, currency="CNY", batch_cap=None, per_task_cap=None):
        account = limits["accounts"][currency]
        self.ledger = LiveCostLedger.open(ledger_path, _policy(limits, currency),
                                          account["batch_cap"] if batch_cap is None else batch_cap)
        self.per_task_cap = account["per_task_cap"] if per_task_cap is None else per_task_cap
        self.limits = limits
        self.clients = []
        self.task = None
        self.reason = None
        self.turns = 0
        self.tokens = 0

    def model_factory(self, **kwargs):
        sync = openai.DefaultHttpxClient(transport=httpx.MockTransport(self._handle), trust_env=False,
                                         follow_redirects=False)
        async_client = openai.DefaultAsyncHttpxClient(transport=_RejectAsync(), trust_env=False,
                                                      follow_redirects=False)
        model = PilotDeepSeekModel(model="deepseek-flash", api_key=self.api_key, base_url=self.base_url,
                                   max_tokens=self.limits["max_output_tokens_per_request"],
                                   timeout=self.limits["max_request_seconds"], max_retries=0, temperature=None,
                                   streaming=False, use_responses_api=False, thinking_mode="enabled",
                                   candidate_effort="high", extra_body={"thinking": {"type": "enabled"}},
                                   http_client=sync, http_async_client=async_client)
        self.clients.append(model)
        return model

    def blocked_reason(self):
        state = self.ledger.snapshot()
        return state["stop_reason"] if state["blocked"] else None

    def start_task(self, task_id, deadline):
        if self.blocked_reason():
            return False
        self.ledger.start_trial(task_id, self.per_task_cap)
        self.task = task_id
        self.reason = None
        self.turns = 0
        self.tokens = 0
        self.deadline = deadline
        self.start_count = len(self.ledger.snapshot()["attempts"])
        return True

    def _admit(self, request):
        if self.reason:
            raise LiveCostError(self.reason)
        if self.start_count + self.turns >= self.limits["max_http_attempts_total"]:
            self.reason = "batch_request_limit"
            self.ledger.block(self.reason)
        elif self.turns >= self.limits["max_http_attempts_per_task"]:
            self.reason = "task_request_limit"
        elif self.tokens >= self.limits["max_billed_usage_tokens_per_task_stop_after_response"]:
            self.reason = "task_token_limit"
        elif time.monotonic() >= self.deadline:
            self.reason = "task_deadline"
        if self.reason:
            raise LiveCostError(self.reason)
        try:
            return self.cost_transport.handle_request(request)
        except LiveCostError as exc:
            self.reason = exc.reason
            raise

    def close(self):
        self.ledger.close()
        for model in self.clients:
            model.root_client.close()
            asyncio.run(model.root_async_client.close())
        self.clients.clear()


class TeacherFixture(BudgetedTeacher):
    """A local fixed-response provider sharing the registered pilot ledger."""

    mode = "local_fixed_replies"
    api_key = SYNTHETIC_KEY
    base_url = BASE_URL

    def __init__(self, ledger_path, limits, *, currency="CNY", batch_cap=None, per_task_cap=None,
                 missing_usage=False):
        super().__init__(ledger_path, limits, currency=currency, batch_cap=batch_cap, per_task_cap=per_task_cap)
        self.script = ScriptedModel()
        self.missing_usage = missing_usage

    def begin(self, task_id, command, folder, deadline):
        if not self.start_task(task_id, deadline):
            return False
        self.wire = Path(folder) / "fixture-wire"
        self.wire.mkdir()
        self.timing = Path(folder) / "fixture-timing"
        self.timing.mkdir()
        self.sdk_attempt = 0
        self.script.begin(command, "none", deadline, Path(folder))
        self.cost_transport = make_cost_transport(httpx, httpx.MockTransport(self._reply),
                                                  self.ledger, task_id)
        return True

    def _handle(self, request):
        if self.task is None:
            raise LiveCostError("no_active_teacher_task")
        self.sdk_attempt += 1
        record = {"task_id": self.task, "attempt": self.sdk_attempt, "transport": _interval(), "fixture": None,
                  "request_body_sha256": sha256(request.content).hexdigest(), "response_body_sha256": None,
                  "fixture_dispatched": False, "fixture_turn": None, "sdk_response_received": False,
                  "stop_reason": None}
        timing_path = self.timing / f"attempt-{self.sdk_attempt:02d}.json"
        atomic_write(timing_path, json_bytes(record))
        request.extensions["flockcue_local_timing"] = record
        try:
            response = self._admit(request)
            record["sdk_response_received"] = True
            return response
        except BaseException as exc:
            record["stop_reason"] = self.blocked_reason() or getattr(exc, "reason", type(exc).__name__)
            raise
        finally:
            _finish_interval(record["transport"])
            atomic_write(timing_path, json_bytes(record))
            request.extensions.pop("flockcue_local_timing", None)

    def _reply(self, request):
        record = request.extensions["flockcue_local_timing"]
        record.update(fixture_dispatched=True, fixture_turn=self.turns + 1, fixture=_interval())
        atomic_write(self.timing / f"attempt-{record['attempt']:02d}.json", json_bytes(record))
        try:
            response = self._fixed_reply(request)
            record["response_body_sha256"] = sha256(response.content).hexdigest()
            return response
        finally:
            _finish_interval(record["fixture"])

    def _fixed_reply(self, request):
        payload = json.loads(request.content)
        expected = {"model": "deepseek-flash", "thinking": {"type": "enabled"},
                    "reasoning_effort": "high", "stream": False,
                    "max_tokens": self.limits["max_output_tokens_per_request"]}
        if (request.method != "POST" or str(request.url) != BASE_URL + "/chat/completions"
                or any(payload.get(k) != v for k, v in expected.items()) or "temperature" in payload):
            raise ValueError("Unexpected DeepSeek SDK request")
        if self.script.step != self.turns:
            raise ValueError("Fixture reply is out of sequence")
        self.turns += 1
        stem = self.wire / f"turn-{self.turns:02d}"
        with stem.with_suffix(".request.json").open("xb") as stream:
            stream.write(request.content)
        tool, args = self.script.decision(payload["messages"])
        self.script.step += 1
        message = {"role": "assistant", "reasoning_content": "Fixed rehearsal step. Use the public observations.",
                   "content": "The scripted public-action probe has finished. [TASK DONE]"}
        if tool:
            if tool not in {t["function"]["name"] for t in payload["tools"]}:
                raise ValueError(f"Unadvertised fixture tool: {tool}")
            message.update(content="", tool_calls=[{
                "id": f"fixture-{self.task}-{self.turns}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)}}])
        body = {"id": f"fixture-chat-{self.task}-{self.turns}", "object": "chat.completion", "created": 1,
                "model": "deepseek-flash", "system_fingerprint": "offline-fixed-teacher-v1",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}]}
        if not self.missing_usage:
            body["usage"] = {"prompt_tokens": 1200, "completion_tokens": 32, "total_tokens": 1232,
                             "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 400,
                             "completion_tokens_details": {"reasoning_tokens": 9}}
            self.tokens += body["usage"]["total_tokens"]
        response = httpx.Response(200, json=body, request=request)
        with stem.with_suffix(".response.json").open("xb") as stream:
            stream.write(response.content)
        return response

    def finish(self):
        if self.task is None:
            raise RuntimeError("No active rehearsal task")
        state = self.ledger.snapshot()
        if not state["blocked"]:
            self.ledger.finish_trial(self.task)
        self.cost_transport.close()
        self.task = None
        return {"stop_reason": state["stop_reason"] or self.reason, "blocked": state["blocked"],
                "batch_stopped": state["blocked"], "fixture_dispatches": self.turns,
                "attempts": self.turns, "actual_provider_calls": 0, "actual_fee": "0"}
