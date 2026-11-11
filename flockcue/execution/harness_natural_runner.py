"""Wall-clock episode adapter; transport is injected, scoring runs separately.

This module has no provider configuration lookup and no campaign launcher.
The companion local validation entry point supplies MockTransport only.
"""
from __future__ import annotations
import asyncio
import copy
from contextlib import redirect_stdout, redirect_stderr
import json
import math
import os
from pathlib import Path
import pickle
import re
import threading
import time
import uuid

from harness_episode_local import sha, digest, write, setup_paths, current_task
from validate_harness_episode import check_hashes, safe_path, policy_scan

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
ONLINE_CONTRACT = "task_execution_state_v1"
ONLINE_FAULT_CONTRACT = "take_off_response_loss_after_success_v1"
ONLINE_VARIANT_CONTRACT = "harness_cost_pair_v1"
ONLINE_VARIANT_SCOPE = "deepseek_cost_pilot_v1"
ONLINE_COST_VARIANTS = {
    "control": ("evidence_harness", "observed_target_v7", "full_v1"),
    "candidate": ("evidence_harness", "search_confirm_v9", "compact_tools_v1"),
}
ONLINE_HOVER_VARIANT_CONTRACT = "harness_hover_input_pair_v1"
ONLINE_HOVER_VARIANT_SCOPE = "deepseek_hover_pilot_v1"
ONLINE_HOVER_VARIANTS = {
    "control": ("evidence_harness", "search_confirm_v9", "compact_tools_v1", "off", "string_v1"),
    "candidate": ("evidence_harness", "search_confirm_v9", "compact_tools_v1",
                  "hover_completion_v1", "json_object_or_string_v1"),
}
ONLINE_HOVER_FIXTURES = {
    "state_hover": "original", "explicit_hover": "explicit_hover",
    "unknown_takeoff": "original", "duration_hover": "duration_hover",
}
_HOVER_SELECTORS = frozenset({"completion_contract", "tool_input_contract", "fixture_variant"})
ONLINE_IMPLEMENTATION_BUDGET = {
    "max_iterations": 600, "recursion_limit": 1800, "deadline_seconds": 500,
    "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16,
}


def _exact_json(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def validate_online_fault(spec):
    if (not isinstance(spec, dict) or set(spec) != {"kind", "method", "endpoint", "occurrence"}
            or spec["kind"] != "response_lost_after_success" or spec["method"] != "POST"
            or not isinstance(spec["endpoint"], str)
            or re.fullmatch(r"/drones/[A-Za-z0-9_-]+/command/take_off", spec["endpoint"]) is None
            or type(spec["occurrence"]) is not int or spec["occurrence"] != 1):
        raise ValueError("Unsupported explicit online API fault")
    return copy.deepcopy(spec)


def assert_online_implementation_budget(budget, *, api_calls_per_plan, repair_budget, search_rounds):
    if not _exact_json(budget, ONLINE_IMPLEMENTATION_BUDGET) or any(
            type(actual) is not int or actual != ONLINE_IMPLEMENTATION_BUDGET[key]
            for key, actual in {"api_calls_per_plan": api_calls_per_plan,
                               "repair_budget": repair_budget, "search_rounds": search_rounds}.items()):
        raise ValueError("Online implementation budget drift")


def online_environment(online):
    """The new contract pins its prompt switch independently for each arm."""
    result = {"UAV_TASK_PROGRESS_MODE": online["task_progress_mode"],
        "UAV_HARNESS_REVISION": online["harness_revision"], "UAV_MODEL_IO_ARCHIVE": "1",
        "UAV_MODEL_REQUEST_AUDIT": "1", "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"}
    if online.get("execution_contract"):
        result.update(UAV_TASK_EXECUTION_STATE="1" if online["harness_enabled"] else "0",
            UAV_HARNESS_SCHEDULER="applicability", UAV_HARNESS_FIXED_K="4", UAV_HARNESS_STATIC_OBSTACLES="1")
    if online.get("variant_contract") in {ONLINE_VARIANT_CONTRACT, ONLINE_HOVER_VARIANT_CONTRACT}:
        result["UAV_AGENT_INPUT_LAYOUT"] = online["input_layout"]
    if online.get("variant_contract") == ONLINE_HOVER_VARIANT_CONTRACT:
        result.update(UAV_COMPLETION_CONTRACT=online["completion_contract"],
                      UAV_TOOL_INPUT_CONTRACT=online["tool_input_contract"])
    return result


def online_variant_agent_options(online):
    """Bind explicit variant fields at construction, independent of inherited env."""
    if online is None:
        return {}
    contract = online.get("variant_contract")
    if contract == ONLINE_HOVER_VARIANT_CONTRACT:
        return {key: online[key] for key in ("input_layout", "completion_contract", "tool_input_contract")}
    if contract == ONLINE_VARIANT_CONTRACT:
        return {"input_layout": online["input_layout"]}
    return {}


class SuccessfulTakeoffReceiptLoss:
    """One declared API-level lost receipt; private evidence never enters policy.

    Call while holding the world's request lock after the genuine API returned.
    The same class sits below raw tools and skills, without checking their origin.
    """
    def __init__(self, spec, record_path):
        self.spec = validate_online_fault(spec)
        self.record_path = Path(record_path)
        self.matched_returns = 0
        self.successful_submissions = 0
        self.fault_record = None

    def snapshot(self):
        return {"schema": ONLINE_FAULT_CONTRACT, "configured": self.spec,
            "matched_returns": self.matched_returns, "successful_submissions": self.successful_submissions,
            "triggered": self.fault_record is not None, "fault_record": copy.deepcopy(self.fault_record),
            "scope": "private_controlled_fault_audit_never_model_input"}

    def save(self):
        # A durable trigger is written before raising; final counts may be
        # updated before the worker seals this file. Other episode files retain
        # their existing exclusive-create policy.
        from validate_harness_episode import write as atomic_write
        atomic_write(self.record_path, self.snapshot())

    def after_response(self, method, endpoint, response, request_record):
        from command_results import command_outcome
        if method.upper() != self.spec["method"] or endpoint != self.spec["endpoint"]:
            return
        self.matched_returns += 1
        if (not isinstance(response, dict) or response.get("status") != "success"
                or command_outcome(response) != "completed"):
            return
        record = request_record
        if (not isinstance(record, dict) or record.get("method", "").upper() != method.upper()
                or record.get("endpoint") != endpoint or record.get("committed") is not True
                or record.get("admin") is not False or record.get("action") is not True
                or record.get("phase") != "task" or command_outcome(record.get("response")) != "completed"):
            raise ValueError("Successful fault candidate lacks a matching committed public request")
        actor = endpoint.split("/")[2]
        applied = [event for event in record.get("history_events", [])
                   if event.get("actor") == actor and event.get("history", {}).get("event") == "take_off"]
        if not applied:
            raise ValueError("Successful takeoff fault candidate lacks physical application evidence")
        self.successful_submissions += 1
        if self.fault_record is not None or self.successful_submissions != self.spec["occurrence"]:
            return
        self.fault_record = {"kind": "response_lost_after_success", "method": method.upper(),
            "endpoint": endpoint, "actor": actor, "successful_submission_ordinal": self.successful_submissions,
            "after_public_api_return": True, "committed": True, "synthetic_fault": True,
            "actual_receipt": copy.deepcopy(response), "world_request_id": record["request_id"],
            "origin": copy.deepcopy(record.get("origin")), "application_events": copy.deepcopy(applied)}
        self.save()
        raise TimeoutError("Request timed out before response was received")


def admitted_client_class(base_client, world, life, *, online_probe=None, local_fault=None, private=None):
    """Shared public API admission; the legacy local probe keeps its semantics."""
    fault_counts = {}
    class AdmittedClient(base_client):
        def _request(self, *args, **kwargs):
            method = args[0] if args else kwargs.get("method", "")
            endpoint = args[1] if len(args) > 1 else kwargs.get("endpoint", "")
            if online_probe is not None:
                # Parallel model tools must not associate one actor's returned
                # receipt with another actor's latest world request.
                with world.lock:
                    life.check()
                    previous = len(world.requests)
                    response = super()._request(*args, **kwargs)
                    if len(world.requests) != previous + 1:
                        raise ValueError("Online API probe requires one recorded public request")
                    online_probe.after_response(method, endpoint, response, world.requests[-1])
                    return response
            life.check()
            response = super()._request(*args, **kwargs)
            if local_fault and endpoint.endswith("/command/" + local_fault["command"]):
                command = local_fault["command"]
                fault_counts[command] = fault_counts.get(command, 0) + 1
                if fault_counts[command] == local_fault["occurrence"]:
                    write(Path(private) / "local-api-fault.json", {"kind": "response_lost", "endpoint": endpoint,
                        "after_public_api_return": True, "synthetic_fault": True})
                    raise TimeoutError("Request timed out before response was received")
            return response
    return AdmittedClient


def task_request_allowed(method, endpoint, admin=False):
    if admin:
        return False
    if method.upper() == "GET":
        return endpoint in {"/drones", "/sessions/current", "/environments/current", "/targets/waypoints"} or any(
            re.fullmatch(pattern, endpoint) for pattern in (
                r"/drones/[^/]+(?:/nearby)?", r"/sessions/[^/]+/(?:data|task-progress)",
                r"/targets/[^/]+", r"/obstacles/[^/]+"))
    commands = "take_off|land|move_to|move_along_path|change_altitude|hover|rotate|move_towards|return_home|set_home|calibrate|charge|take_photo|send_message|broadcast"
    return method.upper() == "POST" and (endpoint in {"/obstacles/collision/check", "/obstacles/collision/path"}
        or re.fullmatch(r"/drones/[^/]+/command/(?:" + commands + ")", endpoint) is not None)


class EpisodeLifecycle:
    """Cancellation requests stop admission immediately; freeze is linearized."""
    def __init__(self, world):
        self.world = world
        self.cancel_event = threading.Event()
        self.requested_at = None
        self.effective_at = None
        self._lock = threading.Lock()

    def request_cancel(self):
        with self._lock:
            if self.requested_at is not None:
                return
            self.requested_at = time.monotonic()
            self.cancel_event.set()
        with self.world.lock:
            self.world.freeze_score(reason="cancellation_effective")
            self.effective_at = time.monotonic()

    def check(self):
        if self.cancel_event.is_set():
            raise RuntimeError("episode_cancelled")
        self.world.check_admission()

    def snapshot(self):
        return {"requested_at": self.requested_at, "effective_at": self.effective_at,
                "cancelled": self.cancel_event.is_set(),
                "semantics": "freeze after acquiring world lock; in-flight actions are not retroactively cancelled"}


def _fixture_prefix(manifest, trial):
    """Only the named hover pilot may select a sealed public-command derivative."""
    scope, contract = manifest.get("scope"), manifest.get("online_variant_contract")
    if scope == ONLINE_HOVER_VARIANT_SCOPE or contract == ONLINE_HOVER_VARIANT_CONTRACT:
        if (scope != ONLINE_HOVER_VARIANT_SCOPE or contract != ONLINE_HOVER_VARIANT_CONTRACT
                or manifest.get("online_contract") != ONLINE_CONTRACT
                or not isinstance(manifest.get("online_pilot"), dict) or "agent_profile" in manifest):
            raise ValueError("Hover fixture requires its explicit online scope and contracts")
        condition = trial.get("condition")
        variant = trial.get("fixture_variant")
        if (not isinstance(condition, str) or condition not in ONLINE_HOVER_FIXTURES
                or variant != ONLINE_HOVER_FIXTURES[condition]):
            raise ValueError("Hover condition and fixture variant disagree")
        return "fixtures/" if variant == "original" else "variants/" + variant + "/fixtures/"
    if _HOVER_SELECTORS.intersection(trial):
        raise ValueError("Hover selectors require the explicit hover variant contract")
    return "fixtures/"


def read_inputs(run, trial_id):
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if sha(manifest_path.read_bytes()) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    if ROOT != (run / "source").resolve():
        raise ValueError("Use the frozen runner source")
    check_hashes(ROOT, manifest["sources"])
    study = Path(manifest["study"])
    check_hashes(study, manifest["inputs"])
    trial = next(t for t in manifest["trials"] if t["trial_id"] == trial_id)
    if Path(trial_id).name != trial_id:
        raise ValueError("Invalid trial identifier")
    prefix = _fixture_prefix(manifest, trial)
    fixture_name, blob_name = prefix + trial["task_id"] + ".json", prefix + trial["task_id"] + ".pkl"
    fixture = json.loads(safe_path(study, fixture_name).read_text())
    timeline_name = prefix + fixture["timeline_file"]
    if not {fixture_name, blob_name, timeline_name} <= set(manifest["inputs"]):
        raise ValueError("Selected fixture dependencies must all be sealed")
    blob = safe_path(study, blob_name).read_bytes()
    timeline = safe_path(study, timeline_name).read_bytes()
    if sha(blob) != fixture["snapshot_sha256"] or sha(timeline) != fixture["timeline_sha256"]:
        raise ValueError("Fixture hashes disagree")
    if sha(fixture["command"].encode()) != fixture["command_sha256"]:
        raise ValueError("Task command changed")
    return manifest, trial, fixture, blob, timeline


def online_arm_configuration(manifest, trial, model_factory):
    """Validate explicit pilot arm without widening the local-only AgentProfile.

    Credentials, endpoint selection and spending authorization belong to the
    caller's frozen online coordinator/factory. This adapter never loads them.
    """
    if "online_pilot" not in manifest:
        if any(key in manifest for key in ("online_contract", "online_common_runtime",
                                           "online_implementation_budget", "online_fault_contract",
                                           "online_variant_contract")):
            raise ValueError("Online contract requires explicit online pilot configuration")
        return None
    if not isinstance(manifest["online_pilot"], dict) or "agent_profile" in manifest:
        raise ValueError("Online pilot requires a separate explicit configuration")
    if not callable(model_factory):
        raise ValueError("Online pilot requires an explicit model factory")
    if not isinstance(manifest.get("model"), str) or not manifest["model"].strip():
        raise ValueError("Online pilot requires a frozen model identity")
    variant_pair = "online_variant_contract" in manifest
    hover_pair = manifest.get("online_variant_contract") == ONLINE_HOVER_VARIANT_CONTRACT
    if variant_pair:
        required_scope = {ONLINE_VARIANT_CONTRACT: ONLINE_VARIANT_SCOPE,
                          ONLINE_HOVER_VARIANT_CONTRACT: ONLINE_HOVER_VARIANT_SCOPE}.get(
                              manifest["online_variant_contract"] if isinstance(manifest["online_variant_contract"], str) else None)
        if (required_scope is None or manifest.get("scope") != required_scope
                or manifest.get("online_contract") != ONLINE_CONTRACT):
            raise ValueError("Unsupported explicit online variant contract/scope")
    elif manifest.get("scope") in {ONLINE_VARIANT_SCOPE, ONLINE_HOVER_VARIANT_SCOPE}:
        raise ValueError("Variant pilot requires explicit online variant contract")
    expected = ONLINE_HOVER_VARIANTS if hover_pair else ONLINE_COST_VARIANTS if variant_pair else {
        "baseline": ("off", "none"),
        "integrated": ("evidence_harness", "observed_target_v7"),
    }

    def validate_selectors(item):
        _fixture_prefix(manifest, item)
        arm = item.get("arm")
        if not isinstance(arm, str) or arm not in expected:
            raise ValueError("Unsupported online pilot arm")
        selected = (item.get("task_progress_mode"), item.get("harness_revision"))
        if variant_pair:
            selected += (item.get("input_layout"),)
            if hover_pair:
                selected += (item.get("completion_contract"), item.get("tool_input_contract"))
        elif "input_layout" in item:
            raise ValueError("Online input layout requires explicit variant contract")
        if selected != expected[arm] or item.get("scheduler") != "applicability":
            raise ValueError("Online pilot arm and runtime selectors disagree")

    validate_selectors(trial)
    arm = trial["arm"]
    harness_enabled = variant_pair or arm == "integrated"
    result = {"arm": arm, "task_progress_mode": trial["task_progress_mode"],
            "harness_revision": trial["harness_revision"], "harness_enabled": harness_enabled,
            "model": manifest["model"]}
    if variant_pair:
        result.update(variant_contract=manifest["online_variant_contract"], input_layout=trial["input_layout"])
    if hover_pair:
        result.update(completion_contract=trial["completion_contract"], tool_input_contract=trial["tool_input_contract"],
                      fixture_variant=trial["fixture_variant"])
    if "online_contract" not in manifest:
        if trial.get("api_fault") is not None or any(key in manifest for key in (
                "online_common_runtime", "online_implementation_budget", "online_fault_contract")):
            raise ValueError("Online fault/runtime configuration requires explicit new contract")
        return result
    if manifest["online_contract"] != ONLINE_CONTRACT:
        raise ValueError("Unsupported explicit online contract")
    from harness_request_limits import RequestLimits
    runtime = manifest.get("online_common_runtime")
    if not isinstance(runtime, dict) or set(runtime) != {"task_seconds", "drain_seconds", "request_limits"}:
        raise ValueError("Online contract requires a frozen common runtime")
    for key in ("task_seconds", "drain_seconds"):
        value = runtime[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("Online runtime values must be finite positive numbers")
    try:
        RequestLimits(**runtime["request_limits"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid common online request limits") from exc
    if not _exact_json(manifest.get("online_implementation_budget"), ONLINE_IMPLEMENTATION_BUDGET):
        raise ValueError("Online contract implementation budget changed")
    trials = manifest.get("trials")
    if not isinstance(trials, list) or not trials or trial not in trials:
        raise ValueError("Online contract requires its frozen trial inventory")
    for item in trials:
        if variant_pair:
            validate_selectors(item)
        elif _HOVER_SELECTORS.intersection(item):
            raise ValueError("Hover selectors require the explicit hover variant contract")
        if not _exact_json(item.get("runtime"), runtime):
            raise ValueError("Online trial/runtime differs from common runtime")
        if item.get("api_fault") is not None:
            if manifest.get("online_fault_contract") != ONLINE_FAULT_CONTRACT:
                raise ValueError("Online fault requires explicit fault contract")
            validate_online_fault(item["api_fault"])
    if "online_fault_contract" in manifest and manifest["online_fault_contract"] != ONLINE_FAULT_CONTRACT:
        raise ValueError("Unsupported online fault contract")
    result.update(execution_contract=ONLINE_CONTRACT,
        execution_state_prompt=harness_enabled, execution_state_artifact=True,
        implementation_budget=copy.deepcopy(ONLINE_IMPLEMENTATION_BUDGET),
        deadline_binding="world_started_plus_runtime_task_seconds")
    return result


def public_task_state(agent, command, command_id):
    ledger = getattr(agent, "_task_progress", None)
    if ledger is not None:
        return ledger.snapshot()
    return {"command": command, "command_id": command_id, "mode": "off",
            "whole_task": "not_evaluated", "uninterpreted_requirements": command,
            "task_evidence": {"requirements": [], "aggregate_requirements": []},
            "observer_errors": [], "evidence_enabled": False}


def public_execution_state(agent, command, command_id, lifecycle):
    from task_execution_state import build_task_execution_state
    ledger, harness = getattr(agent, "_task_progress", None), getattr(agent, "_harness", None)
    return build_task_execution_state(command, command_id,
        ledger.snapshot() if ledger is not None else None,
        harness.snapshot() if harness is not None else None, lifecycle)


def run_worker(run, trial_id, transport_factory, *, network_attempts=None,
               model_factory=None, on_request_stop=None):
    manifest, trial, fixture, blob, timeline = read_inputs(run, trial_id)
    online = online_arm_configuration(manifest, trial, model_factory)
    if on_request_stop is not None and not callable(on_request_stop):
        raise ValueError("on_request_stop must be callable")
    profile = None
    if "agent_profile" in manifest:
        from agent_profile import AgentProfile
        profile = AgentProfile.from_dict(manifest["agent_profile"])
        if profile.fingerprint != manifest["profile_sha256"] or trial["runtime"] != profile.runtime():
            raise ValueError("Profile/runtime mismatch")
    folder = run / "trials" / trial_id
    folder.mkdir(parents=True, exist_ok=False)
    policy, private = folder / "policy", folder / "private"
    policy.mkdir(); private.mkdir()
    with (private / "worker-console.log").open("x") as console, redirect_stdout(console), redirect_stderr(console):
        setup_paths(policy)
        from harness_full100_world import FullWorld, TaskDeadlineReached
        from harness_full100_online import agent_class
        from harness_v2_online import client_class, BUDGET
        from validate_harness_observed_target import validate_static_timeline, obstacle_geometry_audit
        from harness_request_limits import RequestLimits, RequestLimitController, make_guarded_transport
        from model_io_archive import verify_archive
        from langchain_openai import ChatOpenAI
        from langchain_core.callbacks import BaseCallbackHandler
        if profile is not None:
            from harness_core import DecisionFrame
            from harness_skills import MAX_ROUNDS
            profile.assert_implementation_budget(BUDGET,
                api_calls_per_plan=DecisionFrame.__dataclass_fields__["api_budget"].default,
                repair_budget=DecisionFrame.__dataclass_fields__["repair_budget"].default,
                search_rounds=MAX_ROUNDS)
        if online is not None and online.get("execution_contract"):
            from harness_core import DecisionFrame
            from harness_skills import MAX_ROUNDS
            assert_online_implementation_budget(BUDGET,
                api_calls_per_plan=DecisionFrame.__dataclass_fields__["api_budget"].default,
                repair_budget=DecisionFrame.__dataclass_fields__["repair_budget"].default,
                search_rounds=MAX_ROUNDS)
        import openai
        from openai import _base_client
        import uav_agent
        import toolchain_recorder
        httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
        toolchain_recorder.TOOLCHAIN_LOG_DIR = policy / "toolchain"
        def no_settings(*args, **kwargs):
            raise RuntimeError("Implicit provider configuration forbidden in isolated worker")
        uav_agent.load_llm_settings = no_settings
        (policy / "worldlogs").mkdir()

        class LoggedWorld(FullWorld):
            def request(self, method, endpoint, *, admin=False, **kwargs):
                if self.phase == "task" and not task_request_allowed(method, endpoint, admin):
                    raise PermissionError("Task outside public static-writer contract")
                return super().request(method, endpoint, admin=admin, **kwargs)

            def _append(self, name, value):
                destination = policy / "worldlogs" if self.phase == "task" and not value.get("admin", False) else private
                name = name if destination != private else "preparation-" + name
                with (destination / name).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")

        runtime = trial["runtime"]
        world = LoggedWorld(output=private, deadline_seconds=runtime["task_seconds"])
        world.restore(blob, fixture["snapshot_sha256"])
        world.timeline = pickle.loads(timeline)
        timeline_contract = validate_static_timeline(world.timeline)
        transport = getattr(world.client, "_transport", None)
        if (type(transport).__module__ != "starlette.testclient" or type(transport).__name__ != "_TestClientTransport"
                or getattr(transport, "app", None) is not world.server.app or world.client.portal is not None):
            raise ValueError("Requires exclusive local ASGI transport without lifespan")
        geometry_before = obstacle_geometry_audit(world, blob)
        initial = {"original_state_sha256": world.state_digest(), "public_startup_sha256": digest(world.public_seed())}
        if initial != {"original_state_sha256": fixture["state_sha256"], "public_startup_sha256": fixture["public_startup_sha256"]}:
            raise ValueError("Initial state mismatch")
        world.restore(blob, fixture["snapshot_sha256"])
        tree = current_task(world, fixture)["execution_check_apis"]
        initial["original_checker_tree_sha256"] = digest(tree)
        tree["_episode_private_canary"] = manifest["private_canary"]
        initial["derived_state_sha256"] = world.state_digest()
        life = EpisodeLifecycle(world)
        # No virtual clock, precommitted plan or public-seed board injection.
        world.start(timer=True)
        controller = RequestLimitController(RequestLimits(**runtime["request_limits"]),
            task_deadline=world.started + world.deadline_seconds, cancel_check=life.cancel_event.is_set)
        underlying = transport_factory(httpx, {"request_cancel": life.request_cancel,
                                               "deadline": controller.task_deadline})
        guarded = make_guarded_transport(httpx, underlying, controller)
        sync = openai.DefaultHttpxClient(transport=guarded, trust_env=False,
            **({"follow_redirects": False} if online is not None else {}))

        class RejectAsync(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                raise RuntimeError("async_unsupported_in_this_runner")
        asynchronous = openai.DefaultAsyncHttpxClient(transport=RejectAsync(), trust_env=False)
        construct_model = model_factory or ChatOpenAI
        uav_agent.ChatOpenAI = lambda **kwargs: construct_model(http_client=sync, http_async_client=asynchronous,
            max_retries=0, max_tokens=runtime["request_limits"]["max_output_tokens"], streaming=False, **kwargs)
        os.environ.update(UAV_HARNESS_SCHEDULER=trial["scheduler"], UAV_HARNESS_REVISION="observed_target_v7",
                          UAV_HARNESS_STATIC_OBSTACLES="1", UAV_TASK_PROGRESS_MODE="evidence_harness")
        if profile is not None:
            os.environ.update(profile.environment())
        if online is not None:
            if online.get("execution_contract"):
                for key in list(os.environ):
                    if key.startswith("UAV_"):
                        os.environ.pop(key)
            os.environ.update(online_environment(online))
        BaseAgent = agent_class(world, policy, harness_enabled=online["harness_enabled"] if online else True)
        BaseClient = client_class(world)
        api_fault = trial.get("api_fault")
        online_probe = (SuccessfulTakeoffReceiptLoss(api_fault, private / "online-api-fault.json")
                        if api_fault is not None and online is not None and online.get("execution_contract") else None)
        if api_fault is not None and online_probe is None and (profile is None or not profile.execution_contract or online is not None):
            raise ValueError("API fault probes require the explicit local execution contract")
        AdmittedClient = admitted_client_class(BaseClient, world, life, online_probe=online_probe,
            local_fault=None if online_probe is not None else api_fault, private=private)
        uav_agent.UAVAPIClient = AdmittedClient
        entry = {}
        class NaturalAgent(BaseAgent):
            def _initialize_task_progress(self, command, command_id):
                super()._initialize_task_progress(command, command_id)
                harness, ledger = getattr(self, "_harness", None), self._task_progress
                entry.update(command_id=command_id, board=self.blackboard.full(),
                    frames=len(harness.frames) if harness is not None else 0,
                    ledger_events=len(ledger._events) if ledger is not None else 0,
                    ledger_model_requests=len(ledger.model_requests) if ledger is not None else 0)
                if harness is not None and harness.frames:
                    raise AssertionError("Natural entry already has a committed plan")
        class Cancellation(BaseCallbackHandler):
            def _check_cancellation(self):
                if life.cancel_event.is_set():
                    raise RuntimeError("episode_cancelled")
        agent, result, error = None, None, None
        try:
            agent_options = profile.agent_kwargs() if profile is not None else {
                "llm_provider": "openai-compatible", "llm_model": online["model"] if online else manifest["local_model"],
                "share_blackboard_by_session": False,
                "task_progress_mode": online["task_progress_mode"] if online else "evidence_harness",
                "toolchain_json_recording": True, "model_io_archive": True}
            agent_options.update(online_variant_agent_options(online))
            agent = NaturalAgent(base_url="http://testserver", llm_api_key="synthetic-local-key",
                llm_base_url="https://offline.invalid/v1", temperature=0.1, verbose=False,
                model_io_archive_dir=policy / "modelio", **agent_options)
            result = agent.execute(fixture["command"], callbacks=[Cancellation()], step_callback=lambda steps: None)
        except BaseException as exc:
            error = type(exc).__name__
        finally:
            world.freeze_score(reason="execution_returned")
            # A cost controller can lock the outstanding reservation before
            # draining a late transport response. No provider state is read here.
            stop_reason = controller.snapshot()["stop_reason"]
            if stop_reason and on_request_stop is not None:
                on_request_stop(stop_reason)
        if agent is None or agent._model_io_archive is None:
            raise RuntimeError("Agent failed before auditable initialization: " + str(error))
        archive = agent._model_io_archive
        archive_path = Path(archive.info()["path"])
        archive_at_close = sha(archive_path.read_bytes())
        world_at_close = (sha(world.score_blob), world.state_digest(), len(world.requests), len(world.physical_events))
        drained = controller.drain(runtime["drain_seconds"])
        limits = controller.snapshot()
        if online_probe is not None:
            online_probe.save()
        harness = getattr(agent, "_harness", None)
        write(policy / "harness.json", harness.snapshot() if harness is not None else {
            "enabled": False, "frames": [], "events": [], "whole_task": "not_evaluated"})
        if profile is not None or online is not None:
            write(policy / "task-state.json", public_task_state(agent, fixture["command"], entry.get("command_id")))
        if (profile is not None and profile.execution_contract) or (online is not None and online.get("execution_contract")):
            write(policy / "execution-state.json", public_execution_state(agent,
                fixture["command"], entry.get("command_id"),
                {"closed": True, "stop_reason": limits["stop_reason"], "cancelled": life.snapshot()["cancelled"]}))
        write(policy / "agent-result.json", result or {"success": False, "error_type": error})
        write(private / "lifecycle.json", life.snapshot())
        if not drained or limits["needs_process_termination"]:
            write(private / "request-limits.json", limits)
            # No completed manifest. Parent must kill and join; a score snapshot
            # existing on disk is insufficient to authorize scoring this worker.
            write(folder / "termination-required.json", {"trial_id": trial_id, "pid": os.getpid(),
                "reason": "transport_not_drained", "archive_closed": archive.info()["closed"],
                "score_available": world.score_blob is not None})
            console.flush()
            threading.Event().wait()
            raise AssertionError("Unreachable after parent termination")
        controller.close()
        sync.close()
        asyncio.run(asynchronous.aclose())
        limits = controller.snapshot()
        write(private / "request-limits.json", limits)
        geometry_after = obstacle_geometry_audit(world, world.score_blob)
        if geometry_before != geometry_after:
            raise ValueError("Obstacle geometry changed during static episode")
        write(private / "static-obstacles.json", {"before": geometry_before, "after": geometry_after,
              "timeline": timeline_contract, "task_writer_guard": True, "exclusive_local_asgi": True})
        late_rejected = False
        try:
            world.request("POST", "/drones/not-dispatched/command/hover")
        except TaskDeadlineReached:
            late_rejected = True
        unchanged = world_at_close == (sha(world.score_blob), world.state_digest(), len(world.requests), len(world.physical_events))
        public_rows = [r for r in world.requests if r["phase"] == "task" and not r["admin"]]
        actions = [{"request_id": r["request_id"], "tool": r.get("origin", {}).get("tool"),
                    "endpoint": r["endpoint"], "params": r["params"], "body": r["body"],
                    "committed": r["committed"], "status": (r.get("response") or {}).get("status")}
                   for r in public_rows if r["action"]]
        scan = policy_scan([p for p in policy.rglob("*") if p.is_file()], manifest["private_canary"])
        boundary = json.loads((private / "score-boundary.json").read_text())
        report = {"schema_version": 1, "status": "completed", "trial_id": trial_id,
            "task_id": trial["task_id"], "pid": os.getpid(), "instance_nonce": uuid.uuid4().hex,
            "initial": initial, "entry": entry, "runtime": runtime, "budget_reference": BUDGET,
            "elapsed": world.elapsed(), "clock": "time.monotonic", "timer_enabled": True,
            "lifecycle": life.snapshot(), "limits": limits, "drained": drained,
            "agent": {"success": bool(result and result.get("success")), "error_type": error,
                      "archive_info": archive.info(), "archive_verify": verify_archive(archive_path),
                      "harness_closed": harness._closed if harness is not None else True},
            "isolation": {"archive_unchanged_after_drain": archive_at_close == sha(archive_path.read_bytes()),
                          "world_unchanged_after_drain": unchanged, "late_action_rejected": late_rejected, **scan},
            "network_attempts": list(network_attempts) if network_attempts is not None else None,
            "physical_cost": world.cost(), "actions": actions,
            "snapshot": {"path": "private/score-world.pkl", "sha256": sha(world.score_blob),
                         "state_sha256": boundary["state_sha256"], "score_seconds": world.score_seconds}}
        if profile is not None:
            report["profile_sha256"] = profile.fingerprint
        if online is not None:
            report["online_arm"] = online
            report["agent"]["harness_enabled"] = online["harness_enabled"]
        check_hashes(ROOT, manifest["sources"])
        report["files"] = {str(p.relative_to(folder)): sha(p.read_bytes()) for d in (private, policy)
                           for p in d.rglob("*") if p.is_file() and p.name != "worker-console.log"}
        console.flush()
        write(folder / "worker-result.json", report)
        return report


def run_scorer(run, trial_id, *, network_attempts=None):
    manifest, trial, fixture, _, _ = read_inputs(run, trial_id)
    folder = run / "trials" / trial_id
    result_path = folder / "worker-result.json"
    result = json.loads(result_path.read_text())
    if (result["status"] != "completed" or result["trial_id"] != trial_id or result["task_id"] != trial["task_id"]
            or result["snapshot"]["path"] != "private/score-world.pkl"
            or not result["drained"] or result["limits"]["needs_process_termination"]):
        raise ValueError("Only a drained worker may be scored")
    check_hashes(folder, result["files"])
    exit_seal = json.loads((run / "worker-exit-seals" / (trial_id + ".json")).read_text())
    check_hashes(folder, exit_seal)
    snapshot = safe_path(folder, result["snapshot"]["path"])
    blob = snapshot.read_bytes()
    if sha(blob) != result["snapshot"]["sha256"]:
        raise ValueError("Snapshot changed")
    evaluation = folder / "evaluation"
    evaluation.mkdir()
    with (evaluation / "scorer-console.log").open("x") as console, redirect_stdout(console), redirect_stderr(console):
        setup_paths(evaluation)
        from harness_full100_world import FullWorld
        class ScoringWorld(FullWorld):
            def _append(self, name, value):
                with (evaluation / "private-api.jsonl").open("a") as stream:
                    stream.write(json.dumps(value, default=str) + "\n")
        world = ScoringWorld()
        world.restore(blob, result["snapshot"]["sha256"])
        world.phase = "scoring"
        task = world.request("GET", "/sessions/current/tasks/" + trial["task_id"], admin=True)
        write(evaluation / "private-task.json", task)
        tree = task["execution_check_apis"]
        original = {k: v for k, v in tree.items() if k != "_episode_private_canary"}
        checker = world.evaluate(trial["task_id"], blob)
        import sys
        report = {"trial_id": trial_id, "pid": os.getpid(), "worker_pid": result["pid"], "checker": checker,
            "worker_result_sha256": sha(result_path.read_bytes()), "snapshot_sha256": sha(blob),
            "private_canary_present": tree.get("_episode_private_canary") == manifest["private_canary"],
            "checker_tree_matches_original": digest(original) == result["initial"]["original_checker_tree_sha256"],
            "agent_imported": "uav_agent" in sys.modules,
            "network_attempts": list(network_attempts) if network_attempts is not None else None}
        if (manifest.get("scope") == ONLINE_HOVER_VARIANT_SCOPE
                and manifest.get("online_variant_contract") == ONLINE_HOVER_VARIANT_CONTRACT):
            from hover_pilot_evaluation import evaluate_snapshot
            report["unified_completion"] = evaluate_snapshot(world, fixture, trial, run, folder)
            report["agent_imported"] = "uav_agent" in sys.modules
        write(evaluation / "result.json", report)
        return report
