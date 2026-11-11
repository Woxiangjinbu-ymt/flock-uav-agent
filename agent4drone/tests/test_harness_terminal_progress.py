"""One bounded public progress refresh at candidate exhaustion; no simulator/model."""
import copy
from dataclasses import asdict
import socket
from types import SimpleNamespace

import pytest

from test_minimal_harness import SEARCH, finish
from test_harness_blocked_candidate import ENDPOINT, SECOND, THIRD, EndpointWorld, SmallCandidatePolicy, advance, move_points
from test_harness_flight_state import FlightWorld, actor_calls, reject_without_effect
from test_harness_shared_endpoint import make as shared_runtime, obstacle
import harness_skills
from harness_core import HarnessInterrupted, HarnessRuntime
from harness_integration import install_harness


V5 = "shared_endpoint_v5"
V6 = "terminal_progress_v6"


def normalized_progress(percent=100):
    return {"session_id": "s1", "progress_percentage": percent,
            "source_endpoint": "/sessions/current",
            "scope": "public_session_aggregate_not_full_task_verdict"}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in terminal-progress tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


class ExhaustedPolicy(SmallCandidatePolicy):
    def __init__(self, drones, obstacles):
        super().__init__(drones, obstacles)
        self.covered = set().union(*self.candidate_cover)


class OneMovePolicy(SmallCandidatePolicy):
    def __init__(self, drones, obstacles):
        super().__init__(drones, obstacles)
        self.candidates = [SECOND]
        self.candidate_cover = [frozenset({0})]
        self.covered = set()


@pytest.fixture
def exhausted(monkeypatch):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", ExhaustedPolicy)


class ProgressWorld(FlightWorld):
    def __init__(self, final=100, initial=95):
        super().__init__("hovering", 20)
        self.drones.pop("d2")
        self.obstacles = [obstacle()]
        self.initial, self.final = initial, final
        self.progress_reads = 0

    def _call(self, name, **args):
        if name == "get_current_session":
            self.progress_reads += 1
            value = self.initial if self.progress_reads == 1 else self.final
            if isinstance(value, Exception):
                self.calls.append((name, copy.deepcopy(args)))
                self.after_call(name)
                raise value
            result = {"id": "s1", "statistics": {"task_progress": {
                "task_type": "other", "progress_percentage": value}}}
            return self._reply(name, args, result)
        return super()._call(name, **args)


def make(world=None, *, revision=V6, static=True, scheduler="applicability", clock=lambda: 0):
    return shared_runtime(world or ProgressWorld(), revision=revision, static=static,
                          scheduler=scheduler, clock=clock)


def limit_api(r, budget):
    factory = r.skill_factory
    def limited(*args):
        frame, generator = factory(*args)
        frame.api_budget = budget
        return frame, generator
    r.skill_factory = limited


def assert_no_completion(result):
    assert result["status"] != "completed"
    assert "public_stop_condition" not in result["state"]
    assert result["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("final", [95.0001, 96, 100])
def test_exhaustion_refresh_accepts_only_strictly_above_threshold(exhausted, final):
    r, w, _ = make(ProgressWorld(final))
    result = advance(r)
    assert result["status"] == "completed" and not result["resumable"]
    assert result["state"]["progress_percent"] == final
    assert result["state"]["public_stop_condition"] == "session_progress_strictly_above_95"
    assert result["mission_completion"] == "not_evaluated"
    assert [name for name, _ in w.calls] == ["get_current_session", "list_drones", "get_drone_status", "get_current_session"]
    assert w.progress_reads == 2 and not actor_calls(w, "d1", "move_to", "take_off")
    frame = r.frames[result["plan_id"]]
    assert frame.api_calls == 4 and frame.cursor == 0 and frame.repairs == 0
    check = result["state"]["terminal_progress_check"]
    assert check == {"trigger": "no_public_candidate", "cached_progress_percent": 95,
                     "status": "confirmed", "progress_percent": final,
                     "evidence_ref": frame.evidence_refs[-1]}
    event = next(e for e in r.ledger.snapshot()["events"] if e["evidence_id"] == check["evidence_ref"])
    assert event["tool_name"] == "get_mission_progress" and event["payload"]["progress_percentage"] == final
    reject_without_effect(r, w, frame)


@pytest.mark.parametrize("final", [0, 33, 94.9, 95])
def test_valid_noncomplete_refresh_keeps_no_candidate_reason(exhausted, final):
    r, w, _ = make(ProgressWorld(final))
    result = advance(r)
    assert result["status"] == "needs_replan" and result["reason"] == "no_public_candidate"
    assert result["state"]["progress_percent"] == final and w.progress_reads == 2
    assert_no_completion(result)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_v5_keeps_cached_progress_and_does_not_make_terminal_read(exhausted):
    r, w, _ = make(ProgressWorld(100), revision=V5)
    result = advance(r)
    assert result["reason"] == "no_public_candidate" and result["state"]["progress_percent"] == 95
    assert w.progress_reads == 1 and len(w.calls) == 3
    assert_no_completion(result)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("scheduler", sorted(HarnessRuntime.POLICIES))
def test_tail_refresh_after_real_selected_move_changes_no_actions(monkeypatch, scheduler):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", OneMovePolicy)
    runs = []
    for revision in (V5, V6):
        r, w, _ = make(ProgressWorld(), revision=revision, scheduler=scheduler)
        result, _ = finish(r, "public_search")
        runs.append((r, w, result))
    old, ow, before = runs[0]
    new, nw, after = runs[1]
    assert before["reason"] == "no_public_candidate" and before["state"]["progress_percent"] == 95
    assert after["status"] == "completed" and after["state"]["progress_percent"] == 100
    assert ow.calls == nw.calls[:-1] and nw.calls[-1] == ("get_current_session", {})
    assert move_points(ow) == move_points(nw) == [SECOND]
    assert ow.drones == nw.drones
    assert before["cursor"] == after["cursor"] == 1
    assert old.frames[before["plan_id"]].repair_budget == new.frames[after["plan_id"]].repair_budget == 16
    reject_without_effect(new, nw, new.frames[after["plan_id"]])


@pytest.mark.parametrize("payload", [None, [], {}, {"error": "read failed"},
    {"progress_percentage": None}, {"progress_percentage": True}, {"progress_percentage": "100"},
    {"progress_percentage": -1}, {"progress_percentage": 101},
    {"progress_percentage": float("nan")}, {"progress_percentage": float("inf")},
    {"harness_transport_error": "TimeoutError"}])
def test_invalid_normalized_terminal_payload_never_completes_or_retries(exhausted, payload):
    r, w, _ = make()
    invoke = r.invoke
    def malformed(call):
        result = invoke(call)
        if call.tool == "get_mission_progress" and w.progress_reads == 2:
            if isinstance(payload, dict) and "progress_percentage" in payload:
                return {**normalized_progress(), **copy.deepcopy(payload)}
            return copy.deepcopy(payload)
        return result
    r.invoke = malformed
    result = advance(r)
    assert result["reason"] == "public_progress_unavailable" and w.progress_reads == 2
    assert result["state"]["progress_percent"] == 95
    assert result["state"]["terminal_progress_check"]["status"] == "unavailable"
    assert_no_completion(result)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("updates", [
    {"error": "failed"}, {"harness_transport_error": "TimeoutError"},
    {"status": "error"}, {"status": "partial_success"}, {"status": "unknown"},
    {"success": False}, {"success": 1}, {"partial_success": True}, {"partial_success": 0},
    {"session_id": "other"}, {"session_id": None},
    {"source_endpoint": "/private/check"}, {"source_endpoint": None},
    {"scope": "whole_task_checker"}, {"scope": None},
])
def test_error_or_wrong_provenance_with_numeric_100_is_rejected_locally(exhausted, updates):
    r, w, _ = make()
    invoke = r.invoke
    def conflicting(call):
        result = invoke(call)
        if call.tool == "get_mission_progress" and w.progress_reads == 2:
            return {**normalized_progress(), **updates}
        return result
    r.invoke = conflicting
    result = advance(r)
    assert result["reason"] == "public_progress_unavailable"
    assert result["state"]["progress_percent"] == 95
    check = result["state"]["terminal_progress_check"]
    assert check["status"] == "unavailable" and check["evidence_ref"] == r.frames[result["plan_id"]].evidence_refs[-1]
    assert w.progress_reads == 2
    assert_no_completion(result)
    reject_without_effect(r, w, r.frames[result["plan_id"]])


@pytest.mark.parametrize("value", [None, "100", True, float("nan"), -1, 101])
def test_invalid_public_session_adapter_payload_is_not_completion(exhausted, value):
    r, w, _ = make(ProgressWorld(value))
    result = advance(r)
    assert result["reason"] == "public_progress_unavailable" and w.progress_reads == 2
    assert_no_completion(result)


def test_transport_exception_consumes_one_read_budget_and_never_retries(exhausted):
    r, w, _ = make(ProgressWorld(TimeoutError("terminal progress unavailable")))
    result = advance(r)
    frame = r.frames[result["plan_id"]]
    assert result["reason"] == "public_progress_unavailable" and frame.api_calls == 4
    assert w.progress_reads == 2 and len(w.calls) == 4
    event = next(e for e in reversed(r.log.events) if e["kind"] == "public_call_end")
    assert event["effect"] == "unknown" and event["error_type"] == "TimeoutError"
    assert_no_completion(result)
    reject_without_effect(r, w, frame)


@pytest.mark.parametrize("budget,reason,reads", [(3, "api_budget_exhausted", 1), (4, "skill_public_stop_condition", 2)])
def test_no_extra_api_admission_beyond_budget_and_last_slot_is_usable(exhausted, budget, reason, reads):
    r, w, _ = make()
    limit_api(r, budget)
    result = advance(r)
    frame = r.frames[result["plan_id"]]
    assert result["reason"] == reason and frame.api_calls == budget and len(w.calls) == budget
    assert frame.api_budget == budget and frame.deadline == 500
    assert w.progress_reads == reads
    if budget == 3:
        assert_no_completion(result)
        assert result["state"]["terminal_progress_check"] == {
            "trigger": "no_public_candidate", "cached_progress_percent": 95,
            "status": "awaiting_public_read"}
    else:
        assert result["status"] == "completed"
        assert result["state"]["terminal_progress_check"]["status"] == "confirmed"
    reject_without_effect(r, w, frame)


@pytest.mark.parametrize("change", ["epoch", "deadline", "cancel"])
@pytest.mark.parametrize("when", ["before_terminal_read", "after_terminal_read"])
def test_terminal_read_remains_under_scope_deadline_and_cancel_guards(exhausted, change, when):
    now, cancelled = [0], [False]
    r, w, board = make(clock=lambda: now[0])
    def cancel_check():
        if cancelled[0]:
            raise RuntimeError("cancelled by test")
    r.cancel_check = cancel_check
    def interrupt(name):
        hit = (when == "before_terminal_read" and name == "get_drone_status" or
               when == "after_terminal_read" and name == "get_current_session" and w.progress_reads == 2)
        if hit:
            if change == "epoch":
                board.clear()
            elif change == "deadline":
                now[0] = 500
            else:
                cancelled[0] = True
    w.after_call = interrupt
    if change == "epoch":
        result = advance(r)
        assert result["reason"] == "scene_scope_changed"
        assert_no_completion(result)
    else:
        with pytest.raises(HarnessInterrupted if change == "deadline" else RuntimeError):
            advance(r)
    frame = next(iter(r.frames.values()))
    assert frame.status != "completed" and "public_stop_condition" not in frame.state
    assert frame.state["progress_percent"] == 95
    assert w.progress_reads == (1 if when == "before_terminal_read" else 2)
    assert frame.deadline == 500 and not actor_calls(w, "d1", "move_to", "take_off")
    assert len(r.ledger.snapshot()["events"]) == len(w.calls)


def test_already_complete_entry_does_not_do_duplicate_terminal_refresh(exhausted):
    r, w, _ = make(ProgressWorld(initial=100))
    result = advance(r)
    assert result["status"] == "completed" and w.calls == [("get_current_session", {})]
    assert w.progress_reads == 1
    reject_without_effect(r, w, r.frames[result["plan_id"]])


def test_v6_preserves_static_contract_rejection_before_any_api(exhausted):
    r, w, _ = make(static=False)
    result = advance(r)
    assert result["reason"] == "shared_endpoint_requires_static_obstacle_contract"
    assert w.calls == [] and r.frames[result["plan_id"]].api_calls == 0
    assert_no_completion(result)


def test_v6_inherits_shared_mask_source_and_cross_actor_exclusion(monkeypatch):
    monkeypatch.setattr(harness_skills, "PublicSearchPolicy", SmallCandidatePolicy)
    r, w, _ = make(EndpointWorld(actors=2), scheduler="every_step")
    result = advance(r)
    source = copy.deepcopy(result["state"]["shared_blocked_endpoints"])
    result = advance(r, result)
    assert move_points(w, "d1") == [ENDPOINT] and move_points(w, "d2") == [THIRD]
    assert result["state"]["shared_blocked_endpoints"] == source
    assert source[0]["actor"] == "d1" and source[0]["action"]["receipt"]["drone_id"] == "d1"
    assert result["state"]["shared_endpoint_scope"]["altitude"] == 20


@pytest.mark.parametrize("env,enabled", [(None, False), ("true", False), ("1", True)])
def test_v6_install_keeps_exact_static_flag_and_legacy_default(monkeypatch, env, enabled):
    monkeypatch.setenv("UAV_HARNESS_REVISION", V6)
    if env is None:
        monkeypatch.delenv("UAV_HARNESS_STATIC_OBSTACLES", raising=False)
    else:
        monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", env)
    old, w, board = make()
    agent = SimpleNamespace(blackboard=board, client=w, tools=[], _harness_task_started=0,
                            _harness_cancel_check=lambda: None)
    install_harness(agent, old.ledger)
    assert agent._harness.revision == V6 and agent._harness.static_obstacle_contract is enabled
    assert "Entry contract v2 is enabled" in agent.tools[0].description
    assert "static-obstacle" in agent.tools[0].description
    assert w.calls == []
    monkeypatch.delenv("UAV_HARNESS_REVISION")
    install_harness(agent, old.ledger)
    assert agent._harness.revision == "legacy"


def test_nonexhausting_v5_v6_have_identical_public_calls_and_frames():
    runs = []
    for revision in (V5, V6):
        r, w, _ = make(FlightWorld("idle", 57, blocked=True), revision=revision)
        result, _ = finish(r, "public_search")
        assert result["status"] == "completed"
        runs.append((w.calls, w.drones, asdict(r.frames[result["plan_id"]])))
    assert runs[0] == runs[1]
