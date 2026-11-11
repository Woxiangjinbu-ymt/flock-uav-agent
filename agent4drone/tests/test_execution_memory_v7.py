"""V7's route-only projection must not alter V5 visit prompts or receipts."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution_memory import SessionEvidenceIndexMemory
from execution_memory_v5 import ExecutionMemoryLedgerV5
from execution_memory_v6 import ExecutionMemoryLedgerV6
from execution_memory_v7 import ExecutionMemoryLedgerV7, supports_compact_route
from task_evidence import encode
from test_execution_memory import ROUTE, path, position, sense
from test_execution_memory_v5 import contact, land, route

VISIT = "Drone 1 must visit Fixed Target 10."
FIXTURES = Path(__file__).resolve().parents[2] / "experiments/execution-memory-mvp-v5/fixtures"


def pair(command, selected=ExecutionMemoryLedgerV5, **kwargs):
    ledgers = [cls(command, command_id="current", index=SessionEvidenceIndexMemory("same"), **kwargs)
               for cls in (selected, ExecutionMemoryLedgerV7)]
    for ledger in ledgers:
        ledger.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    return ledgers


def same_view(a, b, **focus):
    # Dict equality alone would miss a changed serialization order.
    assert json.dumps(a.decision_context(**focus), ensure_ascii=False) == json.dumps(b.decision_context(**focus), ensure_ascii=False)
    a.set_decision_context(**focus)
    b.set_decision_context(**focus)
    assert a.summary() == b.summary()
    assert a.completion_context() == b.completion_context()
    assert a._events == b._events


@pytest.mark.parametrize("case", ["clue-horizontal", "clue-vertical", "control-failed-sense",
                                  "route-interrupted", "route-blocked", "control-complete"])
def test_every_fixture_prefix_readpoint_matches_selected_projection(case):
    fixture = json.loads((FIXTURES / f"{case}.json").read_text())
    is_route = case.startswith("route-") or case == "control-complete"
    assert supports_compact_route(fixture["command"]) is is_route
    parent = ExecutionMemoryLedgerV6 if is_route else ExecutionMemoryLedgerV5
    a, b = [cls(fixture["command"], "controlled-task", index=SessionEvidenceIndexMemory("same"))
            for cls in (parent, ExecutionMemoryLedgerV7)]
    same_view(a, b, decision_kind=fixture["decision_kind"])
    for entry in fixture["prefix"]:
        for ledger in (a, b):
            eid = ledger.begin_tool(entry["tool"], entry["args"])
            ledger.end_tool(eid, entry.get("output"), error=entry.get("error"))
        same_view(a, b, decision_kind=fixture["decision_kind"])


def test_visit_remains_exact_v5_before_and_after_own_receipt_and_false_target_flag():
    a, b = pair(VISIT)
    for step in [position, sense, sense, contact,
                 lambda g: sense(g, [{"id": "t10", "name": "Fixed Target 10", "is_reached": False}])]:
        for g in (a, b): step(g)
        same_view(a, b, actor_id="d1", decision_kind="locate")
    assert b.completion_context()["done"][0]["kind"] == "visit"
    assert "goals" in b.decision_context()  # Original V5 projection is retained after completion too.


@pytest.mark.parametrize("command", [
    "Drone 1 must visit Moving Target 10.",
    "Drone 1 must visit Fixed Target 10. Then photograph it.",
    "Drone 1 must visit Fixed Target 10. Drone 2 must visit Fixed Target 11.",
    "Return home safely.",
])
def test_nonroute_dynamic_multipurpose_and_unparsed_commands_are_exact_v5(command):
    a, b = pair(command)
    for g in (a, b):
        position(g)
        sense(g)
        contact(g)
    assert not supports_compact_route(command)
    same_view(a, b, actor_id="d1", decision_kind="handoff", plan={"done": True})


@pytest.mark.parametrize("command", [
    "Drones Drone 1 perform a marathon flight at 5 meters. " + ROUTE,
    ROUTE + " Then photograph the area.",
    "Keep altitude below 10 meters. " + ROUTE,
    ROUTE + " Drone 2 must visit Fixed Target 10.",
    VISIT + " " + ROUTE,
])
def test_route_with_uncovered_or_mixed_original_clauses_falls_back_to_v5(command):
    a, b = pair(command)
    for g in (a, b):
        route(g)
        land(g)
    assert not supports_compact_route(command)
    same_view(a, b)
    assert b.completion_context()["unverified"]


def test_complete_route_syntax_accepts_whitespace_and_multiple_actors_without_binding():
    command = " \n" + ROUTE + "\n" + ROUTE.replace("Drone 1", "Drone 2") + "\n "
    a, b = [cls(command, "same") for cls in (ExecutionMemoryLedgerV6, ExecutionMemoryLedgerV7)]
    assert supports_compact_route(command)
    same_view(a, b)
    assert b.snapshot()["projection_policy"] == "v6_complete_route"


def test_route_interruption_failure_retry_and_land_keep_selected_semantics():
    a, b = pair(ROUTE, ExecutionMemoryLedgerV6)
    def failed_land(g):
        g.observe_tool("land", {"drone_id": "d1"}, {"command": "land", "drone_id": "d1", "status": "error"})
    steps = [lambda g: path(g, [[10, 20, 5]], partial=True),
             lambda g: g.observe_tool("move_to", {"drone_id": "d1", "x": 40, "y": 50, "z": 5},
                                      {"command": "move_to", "drone_id": "d1", "status": "error"}),
             lambda g: path(g, [[40, 50, 5], [70, 80, 5]]), failed_land,
             lambda g: position(g, xyz=(70, 80, 5)), land]
    for step in steps:
        for g in (a, b): step(g)
        same_view(a, b)
    assert any(r["kind"] == "landing" for r in b.completion_context()["done"])


@pytest.mark.parametrize("fields, conflict", [({}, False), ({"position": [999, 999, 20]}, True)])
def test_route_land_position_unknown_or_conflicting_preserves_v6(fields, conflict):
    a, b = pair(ROUTE, ExecutionMemoryLedgerV6)
    for g in (a, b):
        route(g)
        land(g, **fields)
    same_view(a, b)
    resume = b.decision_context()["resume"]
    landing = next(r for r in resume["done"] if r["kind"] == "landing")
    assert landing["position"] == "unknown"
    assert bool(resume["unverified"]) is conflict
    assert "without coordinates" in resume["policy"]


@pytest.mark.parametrize("command, parent", [(VISIT, ExecutionMemoryLedgerV5), (ROUTE, ExecutionMemoryLedgerV6)])
def test_actor_focus_plan_and_scene_reset_never_switch_projection(command, parent):
    a, b = pair(command, parent)
    for g in (a, b):
        route(g) if command == ROUTE else contact(g)
        land(g)
    for actor in (None, "d1", "d2"):
        same_view(a, b, actor_id=actor, decision_kind="handoff", plan={"done": True, "actor": "d2"})
    for g in (a, b): g.index.clear()
    same_view(a, b, actor_id="d2", decision_kind="locate")
    assert not b.completion_context()["done"]


@pytest.mark.parametrize("cap", [0, 100, 500, 1500, 3900])
@pytest.mark.parametrize("is_route", [False, True])
def test_caps_and_large_tasks_preserve_selected_parent_bytes_and_omissions(cap, is_route):
    command = (" ".join(ROUTE.replace("Drone 1", f"Drone {n}") for n in range(1, 9)) if is_route
               else " ".join(f"Drone {n} must visit Fixed Target {n}." for n in range(1, 25)))
    a, b = pair(command, ExecutionMemoryLedgerV6 if is_route else ExecutionMemoryLedgerV5, max_summary_chars=cap)
    same_view(a, b)
    assert len(b.summary()) <= cap
    assert supports_compact_route(command) is is_route


def test_failed_sense_audit_and_snapshot_do_not_change_records_or_block_retry():
    a, b = pair(VISIT)
    for g in (a, b):
        position(g)
        sense(g, change={"error": "sensor unavailable"})
    same_view(a, b)
    assert b.decision_context()["last_query"][0]["state"] == "failed"
    before = copy.deepcopy(b._events)
    decision = b.record_model_response("Retry", [{"name": "get_nearby_entities", "args": {"drone_id": "d1"}, "id": "retry"}],
                                       context=b.query_decision_context())
    assert decision["sensing_calls"][0]["execution_policy"] == "allowed_unchanged"
    snapshot = b.snapshot()
    assert snapshot["revision"] == "execution-memory-v7"
    assert snapshot["projection_policy"] == "v5_original"
    assert snapshot["completion_memory"] == a.completion_context()
    assert b._events == before
    for g in (a, b): sense(g)
    same_view(a, b)
