"""Strict, versioned configuration for the first local integrated Agent.

This profile selects existing components; it is not evidence of natural-model
ability or an online provider configuration. No credentials, endpoint or output
directory belongs in the profile. The runner supplies isolated local resources.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path


def _object(value, keys, path):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{path}: expected exactly {', '.join(sorted(keys))}")


def _fixed(value, expected, path):
    # In particular, JSON true must not pass an integer 1 or zero check.
    if type(value) is not type(expected) or value != expected:
        raise ValueError(f"{path}: unsupported Agent profile value")


def _positive(value, maximum, path, *, integer=False):
    types = (int,) if integer else (int, float)
    if type(value) not in types or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{path}: expected finite positive {'integer' if integer else 'number'} <= {maximum}")


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate profile field: {name}")
        result[name] = value
    return result


def _validate(value):
    version = value.get("schema_version") if isinstance(value, dict) else None
    if type(version) is not int or version not in {1, 2, 3, 4, 5}:
        raise ValueError("Unsupported Agent profile version")
    _object(value, {
        "schema_version", "profile_id", "scope", "online_authorized_executions",
        "model", "agent", "harness", "runtime", "execution", "evaluation", "provider_plan",
        "implementation_budget", "deadline_binding",
    }, "profile")
    fixed = {
        "schema_version": version,
        "profile_id": f"general-agent-v{version}-local",
        "scope": "local_mock_only",
        "online_authorized_executions": 0,
        "deadline_binding": "world_started_plus_runtime_task_seconds",
    }
    for key, expected in fixed.items():
        _fixed(value[key], expected, key)
    sections = {
        "model": {"provider": "openai-compatible", "name": "local-scripted-sdk"},
        "agent": {
            "task_progress_mode": "evidence_harness",
            "execution_memory": "evidence_r2",
            "share_blackboard_by_session": False,
            "toolchain_json_recording": True,
            "model_io_archive": True,
        },
        "harness": {
            "revision": "observed_target_v7", "scheduler": "applicability",
            "fixed_k": 4, "static_obstacles": True,
        },
        "evaluation": {
            "scorer": "isolated_process", "result_semantics": "run_agent_check_separate_v1",
        },
        "provider_plan": {
            "status": "not_integrated_not_executed", "thinking_mode": "enabled", "reasoning_effort": "high",
        },
        "implementation_budget": {
            "max_iterations": 600, "recursion_limit": 1800, "deadline_seconds": 500,
            "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16,
        },
    }
    if version >= 2:
        sections["agent"]["execution_contract"] = "task_execution_state_v1"
        scheduler = value.get("harness", {}).get("scheduler")
        if not isinstance(scheduler, str) or scheduler not in {"applicability", "failure", "every_step", "fixed"}:
            raise ValueError("Unsupported execution-contract scheduler")
        sections["harness"]["scheduler"] = scheduler
    if version >= 3:
        layout = value.get("agent", {}).get("input_layout")
        if not isinstance(layout, str) or layout not in {"full_v1", "compact_tools_v1"}:
            raise ValueError("Unsupported local cost-candidate input layout")
        revision = value.get("harness", {}).get("revision")
        if not isinstance(revision, str) or revision not in {"observed_target_v7", "search_progress_v8", "search_confirm_v9"}:
            raise ValueError("Unsupported local cost-candidate search revision")
        sections["agent"]["input_layout"] = layout
        sections["harness"]["revision"] = revision
    if version >= 4:
        contracts = {
            "completion_contract": {"off", "hover_completion_v1"},
            "tool_input_contract": {"string_v1", "json_object_or_string_v1"},
        }
        if version == 5:
            contracts["completion_contract"].add("hover_completion_v2")
            contracts["tool_input_contract"].add("json_object_or_string_v2")
        for key, allowed in contracts.items():
            selected = value.get("agent", {}).get(key)
            if not isinstance(selected, str) or selected not in allowed:
                raise ValueError(f"Unsupported local {key}")
            sections["agent"][key] = selected
    for section, expected in sections.items():
        _object(value[section], expected, section)
        for key, field in expected.items():
            _fixed(value[section][key], field, f"{section}.{key}")
    runtime = value["runtime"]
    _object(runtime, {"task_seconds", "drain_seconds", "request_limits"}, "runtime")
    _positive(runtime["task_seconds"], 500, "runtime.task_seconds")
    _positive(runtime["drain_seconds"], 5, "runtime.drain_seconds")
    limits = runtime["request_limits"]
    maxima = {"max_http_attempts": 20, "max_total_tokens": 100000, "max_output_tokens": 8192}
    _object(limits, {*maxima, "per_request_timeout_seconds"}, "runtime.request_limits")
    for key, maximum in maxima.items():
        _positive(limits[key], maximum, f"runtime.request_limits.{key}", integer=True)
    _positive(limits["per_request_timeout_seconds"], 60, "runtime.request_limits.per_request_timeout_seconds")
    if limits["max_output_tokens"] > limits["max_total_tokens"]:
        raise ValueError("Per-request output limit exceeds episode token stop threshold")
    execution = value["execution"]
    _object(execution, {"worker", "transport", "watchdog_seconds"}, "execution")
    _fixed(execution["worker"], "fresh_process", "execution.worker")
    _fixed(execution["transport"], "mock", "execution.transport")
    _positive(execution["watchdog_seconds"], 515, "execution.watchdog_seconds")


class AgentProfile:
    """Validated snapshot. Accessors return copies and never read the environment.

    Limits may be reduced or adjusted within local caps for boundary probes; any
    change produces a distinct fingerprint. The watchdog is a process safety fuse
    and can be shorter than the simulator's task deadline in local acceptance.
    """

    __slots__ = ("_canonical",)

    def __init__(self, value):
        value = copy.deepcopy(value)
        _validate(value)
        self._canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_dict(cls, value):
        return cls(value)

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object))

    def to_dict(self):
        return json.loads(self._canonical)

    @property
    def fingerprint(self):
        return hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()

    @property
    def local_model(self):
        return self.to_dict()["model"]["name"]

    @property
    def execution_contract(self):
        return self.to_dict()["agent"].get("execution_contract")

    @property
    def watchdog_seconds(self):
        return self.to_dict()["execution"]["watchdog_seconds"]

    def runtime(self):
        return self.to_dict()["runtime"]

    def implementation_budget(self):
        """Existing Agent/skill caps; deadline_seconds is a reference default.

        The active task deadline is world.started + runtime.task_seconds, which
        the natural worker binds to the Harness and API client at episode start.
        """
        return self.to_dict()["implementation_budget"]

    def assert_implementation_budget(self, budget, *, api_calls_per_plan, repair_budget, search_rounds):
        """Fail if imported implementation constants drift from the profile."""
        expected = self.implementation_budget()
        if not isinstance(budget, dict) or set(budget) != set(expected):
            raise ValueError("Implementation budget fields differ from profile")
        for key, reference in expected.items():
            _fixed(budget[key], reference, f"implementation_budget.{key}")
        for key, actual in {
            "api_calls_per_plan": api_calls_per_plan,
            "repair_budget": repair_budget,
            "search_rounds": search_rounds,
        }.items():
            _fixed(actual, expected[key], f"actual_implementation.{key}")

    def agent_kwargs(self):
        value = self.to_dict()
        agent = value["agent"]
        result = {
            "llm_provider": value["model"]["provider"],
            "llm_model": value["model"]["name"],
            "task_progress_mode": agent["task_progress_mode"],
            "share_blackboard_by_session": agent["share_blackboard_by_session"],
            "toolchain_json_recording": agent["toolchain_json_recording"],
            "model_io_archive": agent["model_io_archive"],
        }
        if "input_layout" in agent:
            result["input_layout"] = agent["input_layout"]
        for key in ("completion_contract", "tool_input_contract"):
            if key in agent:
                result[key] = agent[key]
        return result

    def environment(self):
        """Pinned overrides; runner must first remove inherited UAV_* variables.

        In particular the archive destination and any service credentials must
        come from the worker, not UAV_MODEL_IO_ARCHIVE_DIR or UAV_API_KEY.
        """
        value = self.to_dict()
        harness = value["harness"]
        return {
            "UAV_AGENT_INPUT_LAYOUT": value["agent"].get("input_layout", "full_v1"),
            "UAV_COMPLETION_CONTRACT": value["agent"].get("completion_contract", "off"),
            "UAV_TOOL_INPUT_CONTRACT": value["agent"].get("tool_input_contract", "string_v1"),
            "UAV_TASK_EXECUTION_STATE": "1" if self.execution_contract else "0",
            "UAV_TASK_PROGRESS_MODE": value["agent"]["task_progress_mode"],
            "UAV_HARNESS_SCHEDULER": harness["scheduler"],
            "UAV_HARNESS_REVISION": harness["revision"],
            "UAV_HARNESS_FIXED_K": str(harness["fixed_k"]),
            "UAV_HARNESS_STATIC_OBSTACLES": "1",
            "UAV_MODEL_IO_ARCHIVE": "1",
            "UAV_MODEL_REQUEST_AUDIT": "1",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
        }
