"""Offline state-machine and real LangChain integration tests; no provider calls."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackboard import PerceptionBlackboard
from harness_core import HarnessInterrupted, HarnessRuntime
from harness_skills import make_skill_factory, search_supported
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, public_progress
from test_task_progress_integration import ScriptedModel, call, make_agent

SIMPLE = "Drone Drone 3 takes off to 14 meters altitude, then rotates to face heading 353 degrees and hovers."
SEARCH = "take off necessary drones and  assigned them to all targets (within task radius) until the session task progress exceeds 95%."


class PublicWorld:
    def __init__(self, *, blocked=False):
        self.calls = []
        self.moves = 0
        self.blocked = blocked
        self.fail = None
        self.drones = {f"d{i}": {"id": f"d{i}", "name": f"Drone {i+2}", "status": "idle",
                                  "position": {"x": i * 100.0, "y": 100.0, "z": 0.0},
                                  "heading": 0.0, "battery_level": 100.0, "perceived_radius": 50.0}
                       for i in (1, 2)}
        self.obstacles = [{"id": "o1", "name": "Obstacle", "entity_type": "circle",
                           "position": [20, 20, 0], "radius": 3, "height": 0}]
        self.after_call = lambda name: None

    def _call(self, name, **args):
        self.calls.append((name, copy.deepcopy(args)))
        if name == "list_drones":
            result = list(self.drones.values())
        elif name == "get_current_session":
            percent = 96 if self.moves >= 9 else 95
            result = {"id": "s1", "statistics": {"task_progress": {
                "task_type": "target_assignment", "progress_percentage": percent,
                "details": {"total_targets": 100, "visited_targets": percent, "unvisited_targets": 100-percent}}}}
        elif name == "get_drone_status":
            result = self.drones[args["drone_id"]]
        elif name == "get_nearby_entities":
            result = {"targets": [], "drones": [], "obstacles": self.obstacles}
        else:
            d = self.drones[args["drone_id"]]
            result = {"status": "success", "command": name, "drone_id": d["id"]}
            if name in {"take_off", "change_altitude"}:
                d["position"]["z"] = args["altitude"]
                d["status"] = "hovering"
            elif name == "rotate":
                d["heading"] = args["heading"]
            elif name == "hover":
                d["status"] = "hovering"
            elif name == "move_to":
                if self.blocked:
                    self.blocked = False
                    result = {"status": "error", "message": "Path blocked by obstacle", "drone_id": d["id"]}
                else:
                    d["position"] = {k: args[k] for k in ("x", "y", "z")}
                    self.moves += 1
            if self.fail == name:
                self.fail = None
                raise TimeoutError("reply lost after applying action")
        result = copy.deepcopy(result)
        self.after_call(name)
        return result

    def __getattr__(self, name):
        if name not in {"list_drones", "get_current_session", "get_drone_status", "get_nearby_entities",
                        "take_off", "change_altitude", "rotate", "hover", "move_to"}:
            raise AttributeError(name)
        return lambda **args: self._call(name, **args)


def runtime(command=SIMPLE, scheduler="applicability", *, world=None, cancel=lambda: None, clock=lambda: 0):
    world = world or PublicWorld()
    board = PerceptionBlackboard("s1")
    board.ingest_nearby("d1", {"obstacles": world.obstacles, "targets": []})
    index = SessionEvidenceIndexR2("s1")
    ledger = TaskEvidenceLedgerR2(command, "task1", index=index)
    board.task_evidence_index = index
    def invoke(item):
        if item.tool == "get_mission_progress":
            return public_progress(world.get_current_session(), "s1")
        return getattr(world, item.tool)(**item.args)
    result = HarnessRuntime(task_id="task1", command=command, scene_id="s1", scene_epoch=0,
                            ledger=ledger, invoke=invoke, skill_factory=make_skill_factory(board),
                            scope=lambda: (board.session_id, index.epoch), started=0,
                            scheduler=scheduler, fixed_k=2, cancel_check=cancel, clock=clock)
    return result, world, board


def finish(r, skill):
    response = r.control("start", skill=skill)
    slices = 1
    while response["status"] == "paused":
        response = r.control("resume", plan_id=response["plan_id"])
        slices += 1
        assert slices < 2000
    return response, slices


@pytest.mark.parametrize("command", [SIMPLE,
    "Drone Drone 3 should take off to 10 meters, then climb to 30 meters altitude and hover.",
    "Command drone Drone 3 to take off to an altitude of 14 meters and hover in place."])
def test_four_schedulers_have_identical_effects_public_calls_and_plan_source(command):
    runs = []
    for policy in sorted(HarnessRuntime.POLICIES):
        r, w, _ = runtime(command, policy)
        response, slices = finish(r, "simple_chain")
        assert response["status"] == "completed"
        assert response["mission_completion"] == "not_evaluated"
        assert r.frames[response["plan_id"]].source_obligations[0]["text"] == command
        assert len(r.ledger.snapshot()["events"]) == len(w.calls)
        assert all(e["end_sequence"] is not None for e in r.ledger.snapshot()["events"])
        runs.append((w.calls, w.drones, slices))
    assert all((x[0], x[1]) == (runs[0][0], runs[0][1]) for x in runs)
    assert len({x[2] for x in runs}) > 1


@pytest.mark.parametrize("policy", sorted(HarnessRuntime.POLICIES))
def test_public_search_is_resumable_handles_blockage_and_requires_strict_threshold(policy):
    r, w, _ = runtime(SEARCH, policy, world=PublicWorld(blocked=True))
    response, slices = finish(r, "public_search")
    assert response["status"] == "completed"
    assert response["state"]["progress_percent"] == 96
    assert response["state"]["outcomes"] == {"blocked": 1, "completed": 9}
    assert response["cursor"] == 10  # 95% at entry is not success.
    if policy in {"every_step", "fixed", "failure"}:
        assert slices > 1
    assert sum(e["kind"] == "public_call_start" for e in r.log.events) == len(w.calls)


def test_search_scheduling_changes_no_public_action_or_observation_sequence():
    calls = []
    for policy in sorted(HarnessRuntime.POLICIES):
        r, w, _ = runtime(SEARCH, policy, world=PublicWorld(blocked=True))
        finish(r, "public_search")
        calls.append(w.calls)
    assert all(c == calls[0] for c in calls)


def test_transport_unknown_observes_once_and_never_replays_action():
    w = PublicWorld(); w.fail = "take_off"
    r, _, _ = runtime(world=w)
    response, _ = finish(r, "simple_chain")
    assert response["reason"] == "unknown_action_effect_observed_do_not_replay"
    assert [name for name, _ in w.calls] == ["list_drones", "get_drone_status", "take_off", "get_drone_status"]
    old = copy.deepcopy(w.calls)
    assert r.control("resume", plan_id=response["plan_id"])["status"] == "needs_replan"
    assert r.control("start", skill="simple_chain")["status"] == "rejected"
    assert w.calls == old


@pytest.mark.parametrize("damage", ["wrong_id", "duplicate_name", "missing_position", "nan_battery", "already_airborne"])
def test_actor_and_public_state_guards_prevent_first_action(damage):
    w = PublicWorld()
    if damage == "wrong_id": w.drones["d1"]["id"] = "wrong"
    if damage == "duplicate_name": w.drones["d2"]["name"] = "Drone 3"
    if damage == "missing_position": w.drones["d1"]["position"] = None
    if damage == "nan_battery": w.drones["d1"]["battery_level"] = float("nan")
    if damage == "already_airborne": w.drones["d1"]["position"]["z"] = 10
    r, _, _ = runtime(world=w)
    response, _ = finish(r, "simple_chain")
    assert response["status"] == "needs_replan"
    assert not any(n in {"take_off", "rotate", "hover"} for n, _ in w.calls)


def test_task_clause_and_other_task_plan_cannot_be_dropped_or_reused():
    r, w, _ = runtime(SIMPLE + " Then take a photo.")
    assert r.control("start", skill="simple_chain")["status"] == "unsupported"
    assert w.calls == []
    r, w, _ = runtime(scheduler="every_step")
    r.control("start", skill="simple_chain")
    before = len(w.calls)
    assert r.control("resume", plan_id="previous-task:p1")["status"] == "rejected"
    assert len(w.calls) == before
    assert not search_supported(SEARCH + " Take photos.")


def test_paused_plan_rechecks_actual_state_without_trusting_old_receipt():
    r, w, _ = runtime(scheduler="every_step")
    response = r.control("start", skill="simple_chain")
    w.drones["d1"]["position"]["x"] += 10
    response = r.control("resume", plan_id=response["plan_id"])
    assert response["reason"] == "actor_state_changed_between_units"
    assert not any(n == "rotate" for n, _ in w.calls)


@pytest.mark.parametrize("related", [True, False])
def test_external_mutation_invalidation_is_actor_local(related):
    r, w, _ = runtime(scheduler="every_step")
    response = r.control("start", skill="simple_chain")
    r.external_begin("raw", "move_to", {"drone_id": "d1" if related else "d2"}, True)
    r.external_end("raw")
    result = r.control("resume", plan_id=response["plan_id"])
    assert result["status"] == ("needs_replan" if related else "paused")
    assert any(n == "rotate" for n, _ in w.calls) is not related


def test_scene_reset_cancel_and_deadline_prevent_further_io():
    r, w, board = runtime(scheduler="every_step")
    response = r.control("start", skill="simple_chain")
    board.clear()
    before = len(w.calls)
    assert r.control("resume", plan_id=response["plan_id"])["reason"] == "scene_scope_changed"
    assert len(w.calls) == before
    for reason in ("cancel", "deadline"):
        now = [0]
        w = PublicWorld()
        def cancel():
            if reason == "cancel" and len(w.calls) >= 3: raise RuntimeError("cancel requested")
        r, _, _ = runtime(world=w, cancel=cancel, clock=lambda: now[0])
        if reason == "deadline":
            w.after_call = lambda name: now.__setitem__(0, 500 if name == "take_off" else 0)
        with pytest.raises((RuntimeError, HarnessInterrupted)):
            r.control("start", skill="simple_chain")
        assert [n for n, _ in w.calls][-1] == "take_off"
        assert r.ledger.snapshot()["events"][-1]["end_sequence"] is not None


def test_partial_effect_and_wrong_actor_do_not_advance_cursor():
    for receipt in ({"status": "partial_success", "drone_id": "d1"},
                    {"status": "success", "drone_id": "d2"}):
        r, w, _ = runtime()
        original = r.invoke
        r.invoke = lambda item: receipt if item.tool == "take_off" else original(item)
        result, _ = finish(r, "simple_chain")
        assert result["status"] == "needs_replan" and result["cursor"] == 0


def test_no_fake_tool_effects_when_policy_or_api_budget_exits():
    r, w, _ = runtime(scheduler="every_step")
    response = r.control("start", skill="simple_chain")
    frame = r.frames[response["plan_id"]]
    frame.api_budget = frame.api_calls
    response = r.control("resume", plan_id=frame.plan_id)
    assert response["reason"] == "api_budget_exhausted"
    assert len(w.calls) == frame.api_calls
    assert len(r.ledger.snapshot()["events"]) == frame.api_calls


def graph_agent(monkeypatch, policy="applicability", replies=None):
    monkeypatch.setenv("UAV_HARNESS_SCHEDULER", policy)
    agent, _ = make_agent("evidence_harness", replies or [
        call("run_harness", "commit", action="start", skill="simple_chain"), AIMessage(content="Public skill completed. [TASK DONE]")])
    agent.client = PublicWorld()
    return agent


@pytest.mark.parametrize("stream", [False, True])
def test_real_agent_graph_records_plan_gates_model_calls_and_public_receipts(monkeypatch, stream):
    a = graph_agent(monkeypatch)
    result = a.execute(SIMPLE, **({"step_callback": lambda _: None} if stream else {}))
    assert result["success"] is True
    h = result["harness"]
    assert h["frames"][0]["status"] == "completed"
    assert len(a.llm.requests) == 2
    assert sum(x["kind"] == "model_request_start" for x in h["events"]) == 2
    assert sum(x["kind"] == "model_request_end" for x in h["events"]) == 2
    assert len(a._task_progress.snapshot()["events"]) == len(a.client.calls)
    assert any(x["kind"] == "plan_commit" for x in h["events"])


def test_real_graph_rejects_harness_in_parallel_tool_batch(monkeypatch):
    first = call("run_harness", "commit", action="start", skill="simple_chain")
    first.tool_calls += call("get_drone_status", "raw", drone_id="u1").tool_calls
    a = graph_agent(monkeypatch, replies=[first, AIMessage(content="No committed plan.")])
    result = a.execute(SIMPLE)
    assert not result["success"]  # No [TASK DONE]; rejection must not certify completion.
    assert result["harness"]["frames"] == []
    assert a.client.calls == []


def test_model_error_and_new_command_have_separate_log_scopes(monkeypatch):
    a = graph_agent(monkeypatch, replies=[AIMessage(content="No skill selected.")])
    first = a.execute("Unsupported task")
    second = a.execute("Another unsupported task")  # scripted model exhausts replies
    assert first["harness"]["task_id"] != second["harness"]["task_id"]
    assert any(e["kind"] == "model_request_end" and e["error_type"] for e in second["harness"]["events"])


def test_transport_attempts_share_logical_request_without_exposing_headers():
    r, _, _ = runtime()
    ident = r.model_start(0)
    for _ in range(2):
        request = httpx.Request("POST", "https://offline.invalid/chat/completions", headers={"Authorization": "secret"})
        record = {"body_sha256": "hash"}
        r.wire_start(request, record)
        assert record["model_request_id"] == ident
        r.wire_response(httpx.Response(429, request=request))
    r.model_end(ident, error="RateLimitError")
    assert sum(e["kind"] == "http_attempt_start" for e in r.log.events) == 2
    assert "secret" not in json.dumps(r.snapshot())


def test_public_http_deadline_is_opt_in(monkeypatch):
    import time
    import uav_api_client
    seen = []
    def request(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [])
    monkeypatch.setattr(uav_api_client.requests, "request", request)
    client = uav_api_client.UAVAPIClient("http://offline.invalid")
    client.list_drones()
    assert "timeout" not in seen[-1]
    client._harness_deadline = time.monotonic() + 2
    client.list_drones()
    assert 0 < seen[-1]["timeout"] <= 2
    client._harness_deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError): client.list_drones()
    assert len(seen) == 2


def test_busy_control_cannot_duplicate_a_concurrent_start():
    r, w, _ = runtime()
    entered, release = threading.Event(), threading.Event()
    original = r.invoke
    def slow(item):
        if item.tool == "list_drones":
            entered.set()
            assert release.wait(3)
        return original(item)
    r.invoke = slow
    output = []
    thread = threading.Thread(target=lambda: output.append(r.control("start", skill="simple_chain")))
    thread.start()
    try:
        assert entered.wait(3)
        assert r.control("start", skill="simple_chain")["status"] == "busy"
    finally:
        release.set(); thread.join(3)
    assert output[0]["status"] == "completed"
    assert sum(n == "take_off" for n, _ in w.calls) == 1


def test_local_search_takeoff_failure_keeps_other_actor_available():
    r, w, _ = runtime(SEARCH)
    original = r.invoke
    def deny_one(item):
        if item.tool == "take_off" and item.args["drone_id"] == "d1":
            return {"status": "error", "message": "Insufficient battery", "drone_id": "d1"}
        return original(item)
    r.invoke = deny_one
    result, _ = finish(r, "public_search")
    assert result["status"] == "completed"
    assert any(name == "move_to" and args["drone_id"] == "d2" for name, args in w.calls)
    assert not any(name == "move_to" and args["drone_id"] == "d1" for name, args in w.calls)


def test_local_repair_budget_is_enforced():
    r, w, _ = runtime(SEARCH, world=PublicWorld(blocked=True))
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.repair_budget = 0
        return frame, generator
    r.skill_factory = limited
    result, _ = finish(r, "public_search")
    assert result["reason"] == "local_repair_budget_exhausted"
    assert sum(n == "move_to" for n, _ in w.calls) == 1


def test_search_takeoff_receipt_cannot_replace_public_effect_verification():
    r, w, _ = runtime(SEARCH)
    original = r.invoke
    def false_success(item):
        if item.tool == "take_off":
            return {"status": "success", "drone_id": item.args["drone_id"]}
        return original(item)
    r.invoke = false_success
    result, _ = finish(r, "public_search")
    assert result["reason"] == "search_takeoff_effect_mismatch"
    assert not any(name == "move_to" for name, _ in w.calls)
    assert result["evidence_refs"]
    assert result["state"]["last_action"]["tool"] == "take_off"


def test_search_already_at_goal_needs_no_obstacle_model_or_motion():
    r, w, board = runtime(SEARCH)
    w.moves = 9
    board.obstacles.clear()
    result, slices = finish(r, "public_search")
    assert result["status"] == "completed"
    assert slices == 1
    assert w.calls == [("get_current_session", {})]


def test_actual_async_agent_middleware_and_tool_execution(monkeypatch):
    import time
    a = graph_agent(monkeypatch)
    a.blackboard = a._blackboard_for_current_command()
    a.tools = a._create_tools(a.blackboard)
    a._harness_task_started = time.monotonic()
    a._harness_cancel_check = lambda: None
    a._initialize_task_progress(SIMPLE, "async-task")
    graph = a._create_agent_runtime()
    response = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": SIMPLE}]}))
    assert response["messages"][-1].content.endswith("[TASK DONE]")
    assert next(iter(a._harness.frames.values())).status == "completed"
    assert sum(e["kind"] == "model_request_end" for e in a._harness.log.events) == 2


def test_real_execute_checks_service_cancellation_between_inner_calls(monkeypatch):
    a = graph_agent(monkeypatch)
    from langchain_core.callbacks import BaseCallbackHandler
    class Callback(BaseCallbackHandler):
        def _check_cancellation(self):
            if len(a.client.calls) >= 3:
                raise RuntimeError("fixture cancellation")
    result = a.execute(SIMPLE, callbacks=[Callback()])
    assert not result["success"]
    assert [name for name, _ in a.client.calls] == ["list_drones", "get_drone_status", "take_off"]
    assert result["harness"]["frames"][0]["status"] == "interrupted"


def test_optional_response_hooks_preserve_existing_clients_and_transport():
    from model_request_audit import attach_request_hooks, request_record
    r, _, _ = runtime()
    r.model_start(0)
    sync = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}, request=request)), timeout=17)
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}, request=request)), timeout=19)
    model = SimpleNamespace(root_client=SimpleNamespace(_client=sync), root_async_client=SimpleNamespace(_client=async_client))
    def start(request): r.wire_start(request, request_record(request))
    async def astart(request): start(request)
    async def aend(response): r.wire_response(response)
    attach_request_hooks(model, start, astart, r.wire_response, aend)
    sync.post("https://offline.invalid/chat/completions", json={"model": "test", "messages": []})
    async def send():
        await async_client.post("https://offline.invalid/chat/completions", json={"model": "test", "messages": []})
        await async_client.aclose()
    asyncio.run(send())
    assert sync.timeout.read == 17 and async_client.timeout.read == 19
    assert model.root_client._client is sync
    assert sum(e["kind"] == "http_response_headers" for e in r.log.events) == 2
    sync.close()
