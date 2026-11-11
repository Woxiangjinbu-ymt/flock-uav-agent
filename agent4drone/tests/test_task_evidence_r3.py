"""Offline semantic and real-graph checks; no provider or simulator calls."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2
from task_evidence_r3 import TaskEvidenceLedgerR3, explicit_routes
from test_task_progress_integration import make_agent, call, request_dump
from test_task_evidence_r2 import metadata, read_progress, relative, bind_actor


def route(actor="Drone 1", points=((10, 20, 5), (40, 50, 5), (70, 80, 5))):
    coords = ["(" + ", ".join(map(str, p)) + ")" for p in points]
    return f"{actor} route: fly to and start at {coords[0]}, then waypoints {', '.join(coords[1:-1])}, then end {coords[-1]} and land."


def ledger(command=None, **kwargs):
    result = TaskEvidenceLedgerR3(command or route(), "current", **kwargs)
    result.observe_tool("list_drones", {}, [{"id": "d1", "name": "Drone 1"}, {"id": "d2", "name": "Drone 2"}])
    return result


def nav(g, goal=(10, 20, 5), actual=None, actor="d1", change=None, wrapped=False, arg_change=None):
    actual = list(actual or goal)
    args = dict(drone_id=actor, **dict(zip("xyz", goal)))
    args.update(arg_change or {})
    payload = dict(success=True, arrival_verified=True, outcome="completed", drone_id=actor,
                   final_position=dict(zip("xyz", actual)), distance_remaining=math.dist(goal, actual))
    payload.update(change or {})
    return g.observe_tool("navigate_to_and_sense" if wrapped else "navigate_to", args,
                          {"navigation_result": payload} if wrapped else payload, "nav-call")


def path(g, points, partial=False, change=None, actor="d1", wrapped=False):
    payload = dict(command="move_along_path", drone_id=actor,
                   status="partial_success" if partial else "success",
                   successful_points=points, successful_points_count=len(points),
                   unsuccessful_points=[[99, 99, 5]] if partial else [],
                   unsuccessful_points_count=int(partial))
    payload.update(change or {})
    return g.observe_tool("move_along_path_and_sense" if wrapped else "move_along_path",
                          {"drone_id": actor, "waypoints": [[10, 20, 5], [40, 50, 5], [70, 80, 5]]},
                          {"move_result": payload} if wrapped else payload, "path-call")


def view(g):
    return g.route_projection()[0]


def test_source_spans_actor_order_and_signed_decimal_coordinates():
    command = route(points=((-1.5, 2, 3), (4, -.5, 3), (7, 8, 3))) + " " + route("Drone 2")
    routes, status = explicit_routes(command)
    assert status == "recognized_coordinate_routes" and len(routes) == 2
    for r in routes:
        assert command[slice(*r["actor_source_span"])] == r["required_actor"]
        assert [g["ordinal"] for g in r["goals"]] == [1, 2, 3]
        for g in r["goals"]:
            assert tuple(json.loads("[" + command[slice(*g["source_span"])][1:-1] + "]")) == tuple(g["point"])


@pytest.mark.parametrize("command", [
    "If weather permits, " + route(), "Do not execute " + route(), "Example: " + route(),
    '"' + route() + '"', route().replace("then waypoints", "skip waypoints"),
    route().replace("(40, 50, 5)", "(40, 50)"), route().replace("40, 50, 5", "NaN, 50, 5"),
    route().replace("40, 50, 5", "true, 50, 5"), route().replace("then end", "then take a photo and end"),
    route() + " " + route(), route() + " Unless blocked.",
    "Drone 1 route: (10, 20, 5), and an unspecified point.",
    "Drone 1 executes delivery route: take off, move, land.",
])
def test_unsupported_text_preserves_r2_summary(command):
    g = TaskEvidenceLedgerR3(command)
    assert g.routes == []
    assert g.summary() == TaskEvidenceLedgerR2(command).summary()


def test_parser_limits_fail_closed_without_partial_extraction():
    assert not explicit_routes(" ".join(route(f"Drone {i}") for i in range(9)))[0]
    assert not explicit_routes(route(points=[(i, 0, 5) for i in range(65)]))[0]
    assert not explicit_routes("x" * 32001)[0]


@pytest.mark.parametrize("wrapped", [False, True])
def test_gap_remains_visible_after_later_arrival_and_landing(wrapped):
    g = ledger(); nav(g, wrapped=wrapped); nav(g, (70, 80, 5), wrapped=wrapped)
    g.observe_tool("land", {"drone_id": "d1"}, {"status": "success", "drone_id": "d1"})
    r = view(g)
    assert r["ordered_prefix"] == 1 and r["coordinates_with_receipt"] == 2
    assert r["next_unconfirmed"]["point"] == [40, 50, 5]
    summary = json.loads(g.summary())
    assert summary["routes"][0]["later_observed_ordinals"] == [3]
    assert summary["mission_completion"] == "not_evaluated"
    nav(g, (40, 50, 5))
    assert view(g)["ordered_prefix"] == 2  # Earlier out-of-order endpoint does not prove sequence.
    nav(g, (70, 80, 5))
    assert view(g)["state"] == "coordinate_sequence_evidenced"
    assert json.loads(g.summary())["mission_completion"] == "not_evaluated"


@pytest.mark.parametrize("partial,wrapped", [(True, False), (False, False), (True, True)])
def test_only_actual_successful_path_prefix_counts(partial, wrapped):
    g = ledger(); path(g, [[10, 20, 5], [40, 50, 5]], partial=partial, wrapped=wrapped)
    r = view(g)
    assert r["ordered_prefix"] == 2 and r["next_unconfirmed"]["point"] == [70, 80, 5]
    proof = r["goals"][1]["ordered_evidence"]
    assert proof["actor_id"] == "d1" and proof["command_id"] == "current"
    source = g.snapshot()["events"][-1]["payload"]
    for key in proof["receipt_path"]:
        source = source[key]
    assert source == proof["actual_point"]


@pytest.mark.parametrize("change", [
    {"status": "error"}, {"success": False}, {"error": "invalid"}, {"drone_id": "other"},
    {"command": "plan_path"}, {"successful_points_count": 2}, {"successful_points_count": True},
    {"successful_points": [[10, 20]]}, {"successful_points": [[True, 20, 5]]},
    {"successful_points": [[float("inf"), 20, 5]]},
])
def test_invalid_path_does_not_prove_requested_goals(change):
    g = ledger(); path(g, [[10, 20, 5]], change=change)
    assert view(g)["ordered_prefix"] == 0


@pytest.mark.parametrize("change", [
    {"success": False}, {"arrival_verified": False}, {"outcome": "partial"},
    {"status": "error"}, {"error": "contradiction"}, {"drone_id": "wrong"},
    {"distance_remaining": .5}, {"distance_remaining": True}, {"distance_remaining": float("nan")},
    {"final_position": {"x": True, "y": 20, "z": 5}},
])
def test_invalid_navigation_does_not_infer_arrival(change):
    g = ledger(); nav(g, change=change)
    assert view(g)["ordered_prefix"] == 0


@pytest.mark.parametrize("tolerance", [True, -1, 0, float("inf"), "2"])
def test_invalid_public_tolerance_does_not_confirm(tolerance):
    g = ledger()
    if isinstance(tolerance, float) and not math.isfinite(tolerance):
        # The existing shared ledger rejects non-JSON arguments even before R3.
        with pytest.raises(ValueError):
            nav(g, arg_change={"arrival_tolerance": tolerance})
    else:
        nav(g, arg_change={"arrival_tolerance": tolerance})
    assert view(g)["ordered_prefix"] == 0


def test_public_tolerance_is_bound_to_exact_requested_goal_and_three_dimensions():
    import uav_langchain_tools
    assert uav_langchain_tools._DEFAULT_WAYPOINT_ARRIVAL_TOLERANCE == 1.0
    g = ledger(); nav(g, actual=(10.5, 20, 5))
    assert view(g)["ordered_prefix"] == 1
    g = ledger(); nav(g, actual=(10, 20, 6.01))
    assert view(g)["ordered_prefix"] == 0
    nav(g, actual=(10, 20, 6.01), arg_change={"arrival_tolerance": 2})
    assert view(g)["ordered_prefix"] == 1
    g = ledger(); nav(g, goal=(10.5, 20, 5), actual=(10.5, 20, 5), arg_change={"arrival_tolerance": 2})
    assert view(g)["ordered_prefix"] == 0  # Another nearby requested goal cannot lend its radius.


def test_nested_sensing_and_unexecuted_requests_are_not_proof():
    g = ledger()
    nav(g, change={"success": False, "arrival_verified": False, "outcome": "blocked",
                   "waypoints": [[10, 20, 5]], "nearby": {"successful_points": [[10, 20, 5]]},
                   "direct_move_result": {"status": "error", "successful_points": [[10, 20, 5]]}})
    g.observe_tool("get_drone_status", {"drone_id": "d1"}, {"id": "d1", "name": "Drone 1", "position": dict(x=10, y=20, z=5)})
    assert view(g)["ordered_prefix"] == 0


def test_fallback_endpoint_is_not_double_counted_for_repeated_coordinate():
    g = ledger(route(points=((10, 20, 5), (10, 20, 5), (70, 80, 5))))
    donor = ledger(); path(donor, [[10, 20, 5]])
    nav(g, change={"last_move_result": donor.snapshot()["events"][-1]["payload"]})
    assert view(g)["ordered_prefix"] == 1
    nav(g)
    assert view(g)["ordered_prefix"] == 2


@pytest.mark.parametrize("tool,message", [
    ("move_to", "Drone moved to position (10.00, 20.00, 5.00), heading: 0°"),
    ("move_towards", "Drone moved 40.00m to position (10.00, 20.00, 5.00), heading: 0°"),
])
def test_direct_message_positions_are_actual_actor_bound_receipts(tool, message):
    g = ledger()
    g.observe_tool(tool, {"drone_id": "d1"}, {"command": tool, "drone_id": "d1", "status": "success", "message": message})
    assert view(g)["ordered_prefix"] == 1
    g = ledger()
    g.observe_tool(tool, {"drone_id": "d1"}, {"command": tool, "drone_id": "wrong", "status": "success", "message": message})
    assert view(g)["ordered_prefix"] == 0


@pytest.mark.parametrize("same_actor", [False, True])
def test_same_actor_concurrency_is_uncertain_other_actor_is_independent(same_actor):
    g = ledger(); donor = ledger(); nav(donor)
    receipt = donor.snapshot()["events"][-1]["payload"]
    eid = g.begin_tool("navigate_to", {"drone_id": "d1", "x": 10, "y": 20, "z": 5})
    other = g.begin_tool("hover", {"drone_id": "d1" if same_actor else "d2"})
    g.end_tool(eid, receipt); g.end_tool(other, {"status": "success"})
    assert view(g)["ordered_prefix"] == int(not same_actor)


def test_wrong_actor_ambiguous_identity_new_command_and_clear_do_not_transfer_progress():
    index = SessionEvidenceIndexR2("s1")
    g = ledger(index=index); nav(g, actor="d2")
    assert view(g)["ordered_prefix"] == 0
    nav(g); assert view(g)["ordered_prefix"] == 1
    fresh = TaskEvidenceLedgerR3(route(), "next", index=index)
    assert view(fresh)["actor_id"] == "d1" and view(fresh)["ordered_prefix"] == 0
    g.observe_tool("list_drones", {}, [{"id": "another", "name": "Drone 1"}])
    assert view(g)["identity_state"] == "ambiguous" and view(g)["ordered_prefix"] == 0
    fresh.index.clear()
    assert view(fresh)["actor_id"] is None and view(fresh)["ordered_prefix"] == 0


def test_r2_contact_and_aggregate_semantics_survive_inheritance():
    command = "Drone 2 flies to Fixed Target 10. Continue until session task progress exceeds 95%."
    arms = [cls(command, "current", index=SessionEvidenceIndexR2("s1")) for cls in (TaskEvidenceLedgerR2, TaskEvidenceLedgerR3)]
    for g in arms:
        bind_actor(g); relative(g); read_progress(g)
    assert arms[0].summary() == arms[1].summary()
    assert arms[0].aggregate_projection() == arms[1].aggregate_projection()
    assert arms[0].projection() == arms[1].projection()


def test_large_route_summary_has_explicit_omissions_and_full_snapshot():
    g = ledger(" ".join(route(f"Drone {i}", [(j, j, 5) for j in range(64)]) for i in range(8)))
    summary = json.loads(g.summary())
    assert len(g.summary()) <= 3900 and len(g.snapshot()["task_evidence"]["coordinate_routes"]) == 8
    assert len(summary["routes"]) + summary["routes_omitted"] == 8
    assert summary["omitted"] == len(g.requirements)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("command", ["Take a photo.", "Continue until session task progress exceeds 95%."])
def test_unaffected_real_graph_preserves_r2_inputs_tools_and_call_count(stream, command):
    replies = [call("get_drone_status", "read", drone_id="u1"), AIMessage(content="Done [TASK DONE]")]
    r2, a2 = make_agent("evidence_r2", replies); r3, a3 = make_agent("evidence_r3", replies)
    for agent in (r2, r3):
        agent.client = SimpleNamespace(get_current_session=lambda: pytest.fail("Unrequested API call"))
    kwargs = {"step_callback": lambda _: None} if stream else {}
    assert r2.execute(command, **kwargs) == r3.execute(command, **kwargs)
    assert request_dump(r2) == request_dump(r3) and r2.llm.bound_tools == r3.llm.bound_tools
    assert a2 == a3 and len(r3.llm.requests) == 2


@pytest.mark.parametrize("stream", [False, True])
def test_real_graph_gap_feedback_stable_prefix_and_no_automatic_retry_or_final_veto(stream):
    replies = [call("get_drone_status", "identity", drone_id="u1"),
               call("navigate_to", "start", drone_id="u1", x=10, y=20, z=5),
               call("navigate_to", "skipped", drone_id="u1", x=70, y=80, z=5),
               AIMessage(content="Done [TASK DONE]"), AIMessage(content="Done [TASK DONE]")]
    agent, executed = make_agent("evidence_r3", replies)

    @tool
    def navigate_to(input_json: str) -> str:
        """Move to the requested point in this offline fixture."""
        args = json.loads(input_json); executed.append(("navigate_to", args))
        return json.dumps(dict(drone_id=args["drone_id"], success=True, arrival_verified=True,
                               outcome="completed", distance_remaining=0, final_position={k: args[k] for k in "xyz"}))

    original_tools = agent._create_tools
    agent._create_tools = lambda b: [*original_tools(b), navigate_to]
    kwargs = {"step_callback": lambda _: None} if stream else {}
    agent.execute(route(), **kwargs)
    g = agent._task_progress
    assert view(g)["next_unconfirmed"]["point"] == [40, 50, 5]
    assert '"later_observed_ordinals":[3]' in str(agent.llm.requests[-1][-1].content)
    assert all(r["previous_prefix_preserved"] for r in g.model_requests[1:])
    assert len(agent.llm.requests) == 4 and len(executed) == 3
    assert not g.snapshot()["observer_errors"] and len(g.snapshot()["events"]) == 3
    assert all(r["variant"] == "evidence_r3" for r in g.model_requests)
    agent.execute(route())
    assert agent._task_progress is not g and view(agent._task_progress)["ordered_prefix"] == 0


def test_shadow_observes_without_injecting_tools_or_model_input():
    replies = [call("get_drone_status", "read", drone_id="u1"), AIMessage(content="Done [TASK DONE]")]
    off, a = make_agent("off", replies); shadow, b = make_agent("evidence_r3_shadow", replies)
    off.execute(route()); shadow.execute(route())
    assert request_dump(off) == request_dump(shadow) and a == b
    assert off.llm.bound_tools == shadow.llm.bound_tools
    assert shadow._task_progress.routes


def test_mode_is_explicit_opt_in_and_environment_can_be_overridden(monkeypatch):
    import uav_agent
    from uav_agent import UAVControlAgent

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_session(self):
            return {"id": "s1"}

    monkeypatch.setattr(uav_agent, "UAVAPIClient", Client)
    for method in ("_create_llm", "_create_agent_runtime"):
        monkeypatch.setattr(UAVControlAgent, method, lambda *a, **kw: None)
    monkeypatch.setattr(UAVControlAgent, "_create_tools", lambda *a: [])
    monkeypatch.delenv("UAV_TASK_PROGRESS_MODE", raising=False)
    assert UAVControlAgent().task_progress_mode == "off"
    monkeypatch.setenv("UAV_TASK_PROGRESS_MODE", "evidence_r3")
    assert UAVControlAgent().task_progress_mode == "evidence_r3"
    assert UAVControlAgent(task_progress_mode="evidence_r2").task_progress_mode == "evidence_r2"
    assert UAVControlAgent(task_progress_mode="evidence_r3_shadow").task_progress_mode == "evidence_r3_shadow"


def test_actual_sdk_wire_hashes_route_feedback_and_prefix_without_network(monkeypatch):
    import openai
    import uav_agent
    from openai import _base_client
    from langchain_openai import ChatOpenAI
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    requests = []

    def respond(req):
        requests.append(req)
        first = len(requests) == 1
        message = {"role": "assistant", "content": "Unfinished [TASK DONE]"}
        if first:
            message = {"role": "assistant", "content": "", "tool_calls": [{"id": "identity", "type": "function",
                       "function": {"name": "get_drone_status", "arguments": json.dumps({"input_json": '{"drone_id":"u1"}'})}}]}
        return httpx.Response(200, json={"id": f"mock-{len(requests)}", "object": "chat.completion", "created": 0,
            "model": "mock", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if first else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    sync_client = openai.DefaultHttpxClient(transport=httpx.MockTransport(respond))
    async_client = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(uav_agent, "ChatOpenAI", lambda **kw: ChatOpenAI(http_client=sync_client, http_async_client=async_client, **kw))
    agent, actions = make_agent("evidence_r3", [])
    agent._model_request_audit_enabled = True
    agent._model_wire_requests, agent._model_wire_errors = [], []
    agent.llm = agent._create_llm("openai-compatible", "mock", "offline-key", "https://offline.invalid/v1", 0.1)
    try:
        agent.execute(route())
        assert len(requests) == 2 and len(actions) == 1 and not agent._model_wire_errors
        assert len(agent._model_wire_requests) == 2
        for actual, audit in zip(requests, agent._model_wire_requests):
            assert audit["body_sha256"] == hashlib.sha256(actual.content).hexdigest()
            assert "offline-key" not in json.dumps(audit)
        old, new = agent._model_wire_requests
        assert new["common_prefix_messages"] == new["previous_message_count"]
        assert old["tools_sha256"] == new["tools_sha256"]
        body = json.loads(requests[-1].content)
        assert "next_unconfirmed" in str(body) and "source_span" in str(body)
        assert all(t["function"]["name"] != "get_mission_progress" for t in body["tools"])
    finally:
        sync_client.close()
        asyncio.run(async_client.aclose())
