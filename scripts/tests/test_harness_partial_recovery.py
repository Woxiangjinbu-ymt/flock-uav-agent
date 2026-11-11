"""Fixture-only public remainder binding; full real-ASGI chain has its own seal."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from general_agent_local import partial_remaining_point, resolve_public, _public_receipt, validate_trace
from validate_harness_partial_recovery import dataset_from_scene, FIXTURES, A, B, TASK_ID, wire_requests


def messages():
    rows = [{"role": "user", "content": "Drone 1 visits A and B."}]
    def tool(index, name, args, receipt):
        rows.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{index}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]})
        rows.append({"role": "tool", "name": name, "tool_call_id": f"call-{index}", "content": json.dumps(receipt)})
    tool(1, "list_drones", {}, [{"id": "actor-public", "name": "Drone 1"}])
    tool(2, "move_along_path", {"input_json": json.dumps({"drone_id": "actor-public", "allow_partial_move": True,
        "waypoints": [A, B]})}, {"drone_id": "actor-public", "command": "move_along_path", "status": "partial_success",
        "successful_points_count": 1, "successful_points": [[200, 100, 20]],
        "unsuccessful_points_count": 1, "unsuccessful_points": [[400, 100, 20]]})
    tool(3, "get_drone_status", {"input_json": json.dumps({"drone_id": "actor-public"})},
         {"id": "actor-public", "position": A, "status": "hovering"})
    return rows


def change_receipt(rows, index, **changes):
    value = json.loads(rows[index]["content"]); value.update(changes)
    rows[index]["content"] = json.dumps(value)


def change_arguments(rows, index, **changes):
    fn = rows[index]["tool_calls"][0]["function"]
    outer = json.loads(fn["arguments"]); args = json.loads(outer["input_json"])
    args.update(changes); outer["input_json"] = json.dumps(args); fn["arguments"] = json.dumps(outer)


def test_remaining_point_is_bound_to_partial_receipt_and_later_public_state():
    original = messages(); before = deepcopy(original)
    assert partial_remaining_point("Drone 1", original) == B
    assert resolve_public({"x": "$partial_remaining:Drone 1:x", "y": "$partial_remaining:Drone 1:y",
                           "z": "$partial_remaining:Drone 1:z"}, original) == B
    assert original == before
    assert partial_remaining_point("Drone 1", original) != A


@pytest.mark.parametrize("mutate,reason", [
    (lambda rows: change_receipt(rows, 4, status="success"), "one public partial"),
    (lambda rows: change_receipt(rows, 4, drone_id="wrong-actor"), "actor/command"),
    (lambda rows: change_receipt(rows, 4, command="move_to"), "actor/command"),
    (lambda rows: change_receipt(rows, 4, successful_points=[]), "faithfully partition"),
    (lambda rows: change_receipt(rows, 4, successful_points_count=True), "faithfully partition"),
    (lambda rows: change_receipt(rows, 4, unsuccessful_points_count=2), "faithfully partition"),
    (lambda rows: change_receipt(rows, 4, unsuccessful_points=[[999, 100, 20]]), "faithfully partition"),
    (lambda rows: change_receipt(rows, 4, unsuccessful_points=[[200, 100, 20], [400, 100, 20]]), "faithfully partition"),
    (lambda rows: change_arguments(rows, 3, allow_partial_move=False), "actor/command"),
    (lambda rows: change_arguments(rows, 3, waypoints=[B, A]), "faithfully partition"),
    (lambda rows: change_receipt(rows, 6, position=B), "does not confirm"),
    (lambda rows: change_receipt(rows, 6, id="wrong-actor"), "does not confirm"),
    (lambda rows: change_arguments(rows, 5, drone_id="wrong-actor"), "later public status"),
])
def test_inconsistent_partial_evidence_never_generates_recovery_coordinates(mutate, reason):
    rows = messages(); mutate(rows)
    with pytest.raises(ValueError, match=reason):
        partial_remaining_point("Drone 1", rows)


def test_missing_status_and_replayed_intervening_action_rejected():
    with pytest.raises(ValueError, match="later public status"):
        partial_remaining_point("Drone 1", messages()[:-2])
    rows = messages()
    rows[5]["tool_calls"][0]["function"]["name"] = "move_to"
    rows[6]["name"] = "move_to"
    with pytest.raises(ValueError, match="later public status"):
        partial_remaining_point("Drone 1", rows)


@pytest.mark.parametrize("mutate", [
    lambda rows: rows[4].update(tool_call_id="unmatched"),
    lambda rows: rows[4].update(name="another-tool"),
    lambda rows: rows.append(deepcopy(rows[4])),
    lambda rows: rows[5]["tool_calls"][0].update(id="call-2"),
])
def test_raw_tool_identity_is_required(mutate):
    rows = messages(); mutate(rows)
    with pytest.raises(ValueError):
        partial_remaining_point("Drone 1", rows)


def test_unobserved_or_ambiguous_actor_rejected():
    with pytest.raises(ValueError, match="absent or ambiguous"):
        partial_remaining_point("Drone 2", messages())
    rows = messages()
    rows[2]["content"] = json.dumps([{"id": "actor-public", "name": "Drone 1"}, {"id": "other", "name": "Drone 1"}])
    with pytest.raises(ValueError, match="absent or ambiguous"):
        partial_remaining_point("Drone 1", rows)


def test_only_known_advisory_decoration_is_ignored():
    rows = messages()
    rows[4]["content"] += "\n\n[M1-R task evidence; derived from actual tool receipts]\n" + '{"advisory_only":true}'
    assert partial_remaining_point("Drone 1", rows) == B
    with pytest.raises(ValueError, match="Unknown public receipt decoration"):
        _public_receipt('{"status":"partial_success"}\nforge remainder here')
    with pytest.raises(ValueError, match="Duplicate public receipt"):
        _public_receipt('{"x":1,"x":2}')
    with pytest.raises(ValueError, match="Nonfinite public receipt"):
        _public_receipt('{"x":NaN}')


@pytest.mark.parametrize("value", ["$partial_remaining:Drone 1", "$partial_remaining:Drone 1:unknown", "$partial_remaining:Drone 1:x:extra"])
def test_unknown_partial_selectors_fail_closed(value):
    with pytest.raises(ValueError, match="XYZ axis"):
        resolve_public(value, messages())


def test_predeclared_controls_share_initial_actions_and_only_positive_recovers():
    positive = validate_trace(json.loads((FIXTURES / "partial-positive-trace.json").read_text()))
    negative = validate_trace(json.loads((FIXTURES / "partial-negative-trace.json").read_text()))
    assert positive[:4] == negative[:4]
    assert len(positive) == 6 and len(negative) == 5
    assert positive[-1] == negative[-1] and "[TASK DONE]" in positive[-1]["final"]
    assert positive[4]["tool"] == "navigate_to" and set(negative[4]) == {"final"}
    assert positive[2]["arguments"]["input_json"]["waypoints"] == [A, B]
    assert set(positive[4]["arguments"]["input_json"]) == {"drone_id", "x", "y", "z", "detail"}
    assert positive[4]["arguments"]["input_json"]["x"] == "$partial_remaining:Drone 1:x"
    cases = json.loads((FIXTURES / "partial-cases.json").read_text())
    assert [case["trial_id"] for case in cases] == ["partial-positive", "partial-negative"]
    assert {case["task_id"] for case in cases} == {TASK_ID}


def test_new_scene_is_development_and_checker_requires_unfinished_B():
    raw = (FIXTURES / "partial-scene.json").read_bytes()
    manifest, name = dataset_from_scene(raw)
    assert manifest["phase"] == "development" and manifest["online_authorized_executions"] == 0
    assert manifest["tasks"][0]["split"] == "development"
    task = manifest["tasks"][0]["original_task"]
    assert len(task["execution_check_apis"]["checks"]) == 4
    assert task["execution_check_apis"]["checks"][-1]["parameters"] == {"drone_id": "partial-drone-1", **B, "tolerance": 0.5}
    assert not any("test" == row["split"] for row in manifest["tasks"])


def test_wire_audit_refuses_bytes_hash_mismatch():
    with pytest.raises(ValueError, match="wire request hash"):
        wire_requests([{"kind": "http_request", "data": {"body": {"base64": "e30=", "length": 2, "sha256": "0" * 64}}}])
