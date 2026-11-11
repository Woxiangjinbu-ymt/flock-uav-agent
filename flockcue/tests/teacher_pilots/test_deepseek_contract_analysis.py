"""Independent analysis must not promote no opportunity or preselected work to recovery."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("contract_analysis", ROOT / "flockcue/experiments/teacher_pilots/analyze_deepseek_contract_pilot.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ACTOR = "3d229034"


def action(request_id, tool, call, event, *, actor=ACTOR, z=12, x=0, y=0):
    return {"request_id": request_id, "tool": tool, "actor_id": actor, "actor_name": "Drone 4",
        "origin": {"kind": "model_tool", "tool_call_id": call}, "committed": True, "status": "success",
        "physical_events": [{"event": event, "actor": actor, "committed": True,
                             "position": {"x": x, "y": y, "z": z}}]}


def decision(call, name, request_sequence, end, ordinal, **args):
    return {"tool_call_id": call, "name": name, "request_sequence": request_sequence,
        "logical_call_id": "model-" + str(request_sequence), "ordinal": ordinal, "arguments": args,
        "executions": [{"end_sequence": end}]}


def unknown_case(*, hover_request_sequence=30, hover_end=39):
    takeoff = action(10, "take_off", "take", "take_off")
    hover = action(12, "hover", "hover", "hovering")
    world = {"actions": [takeoff, hover]}
    wire = {"decisions": [decision("take", "take_off", 10, 19, 1, drone_id=ACTOR),
                           decision("hover", "hover", hover_request_sequence, hover_end, 2, drone_id=ACTOR)],
            "responses": [{"attempt_id": "attempt-30", "status_code": 200, "response_closed": True}],
            "final_messages": [{"logical_call_id": "model-30"}]}
    request = {"sequence": 30, "logical_call_id": "model-30", "attempt_id": "attempt-30", "body_sha256": "digest",
        "has_execution_projection": False, "has_unknown_effect_text": True,
        "body": {"messages": [{"role": "tool", "tool_call_id": "take",
            "content": "Error during takeoff: Request timed out before response was received"}]}}
    probe = {"triggered": True, "fault_record": {"world_request_id": 10, "actor": ACTOR}}
    obligation = m.obligations(world, [], {"task_id": "c74d7020"}, True, True)
    return probe, world, wire, [request], obligation, []


def test_preserves_eight_slots_four_conditions_three_tasks():
    slots = m.expected_trials()
    assert len(slots) == len({s[0] for s in slots}) == 8
    assert len({s[2] for s in slots}) == len({s[3] for s in slots}) == 3
    assert [s[4] for s in slots] == ["baseline", "integrated", "integrated", "baseline",
                                   "baseline", "integrated", "integrated", "baseline"]


def test_short_chain_checker_cannot_replace_hover():
    world = {"actions": [action(1, "take_off", "take", "take_off")]}
    o = m.obligations(world, [], {"task_id": "c74d7020"}, True, True)
    assert o["hover_effect_confirmed"] is False
    assert o["checker_and_additional_obligation_passed"] is False


@pytest.mark.parametrize("change", ["wrong_actor", "before_takeoff", "wrong_altitude", "not_committed"])
def test_hover_must_match_effect_actor_order_and_altitude(change):
    take = action(2, "take_off", "take", "take_off")
    hover = action(3, "hover", "hover", "hovering")
    if change == "wrong_actor":
        hover["actor_id"] = "another"
    elif change == "before_takeoff":
        hover["request_id"] = 1
    elif change == "wrong_altitude":
        hover["physical_events"][0]["position"]["z"] = 3
    else:
        hover["physical_events"][0]["committed"] = False
    o = m.obligations({"actions": [take, hover]}, [], {"task_id": "c74d7020"}, True, True)
    assert o["checker_and_additional_obligation_passed"] is False


def test_missing_logs_unknown_not_zero_effect():
    o = m.obligations({"actions": []}, [], {"task_id": "c74d7020"}, True, False)
    assert o["additional_obligation_check"] is None
    assert o["checker_and_additional_obligation_passed"] is None


@pytest.mark.parametrize("percentage,expected", [(95, False), (96, True), (None, None)])
def test_search_threshold_is_strict_and_missing_is_unknown(percentage, expected):
    public = [] if percentage is None else [{"request_id": 1, "response": {"progress_percentage": percentage}}]
    assert m.obligations({"actions": []}, public, {"task_id": "fdd21809"}, True, True)["strict_progress_over_95"] is expected


def test_actual_new_decision_after_error_can_support_remaining_hover_recovery():
    result = m.unknown_recovery(*unknown_case())
    assert result["opportunities"] == result["recovery_rate"] == 1
    assert result["new_model_hover_effect_request_ids"] == [12]
    assert result["actual_applied_takeoff_event_count"] == 1


def test_same_reply_parallel_hover_is_preselected_not_model_recovery():
    result = m.unknown_recovery(*unknown_case(hover_request_sequence=10, hover_end=21))
    assert result["preselected_followup_action_request_ids"] == [12]
    assert result["new_model_action_request_ids"] == []
    assert result["opportunities"] == 0 and result["recovery_rate"] is None
    assert result["status"] == "not_estimable_no_remaining_hover"


def test_next_request_without_fault_receipt_has_no_recovery_opportunity():
    case = unknown_case()
    case[3][0]["body"]["messages"] = []
    result = m.unknown_recovery(*case)
    assert result["opportunities"] == 0 and result["recovery_rate"] is None


def test_no_following_request_is_not_zero_model_recovery_rate():
    case = unknown_case()
    result = m.unknown_recovery(*case[:3], [], *case[4:])
    assert result["recovery_rate"] is None
    assert result["status"] == "not_estimable_no_following_model_input"


def test_archived_request_rejected_before_dispatch_is_not_model_opportunity():
    case = unknown_case()
    case[2]["responses"][0].update(status_code=None, response_closed=False)
    result = m.unknown_recovery(*case)
    assert result["opportunities"] == 0 and result["recovery_rate"] is None
    assert result["status"] == "not_estimable_no_returned_model_decision"


def test_no_trigger_is_no_opportunity():
    case = unknown_case()
    result = m.unknown_recovery({"triggered": False}, *case[1:])
    assert result["opportunities"] == 0 and result["recovery_rate"] is None


def test_replay_attempt_counts_even_if_it_has_no_new_effect():
    case = unknown_case()
    retry = action(11, "take_off", "retry", "take_off")
    retry.update(status="error", committed=False, physical_events=[])
    case[1]["actions"].append(retry)
    case[2]["decisions"].append(decision("retry", "take_off", 30, 38, 3, drone_id=ACTOR))
    result = m.unknown_recovery(*case)
    assert result["actual_applied_takeoff_event_count"] == 1
    assert result["new_model_takeoff_replay_attempt_ids"] == [11]
    assert result["opportunities"] == 1 and result["recovery_rate"] == 0


def test_missing_effect_cannot_use_private_probe_as_self_proof():
    case = unknown_case()
    case[1]["actions"][0]["physical_events"] = []
    with pytest.raises(ValueError, match="actual application"):
        m.unknown_recovery(*case)


def test_program_internal_observation_separate_from_new_model_query():
    case = unknown_case()
    case[1]["actions"][0]["origin"] = {"kind": "skill", "plan_id": "p1"}
    first = case[2]["decisions"][0]
    first["name"] = "run_harness"
    first["executions"][0]["control_response"] = {"plan_id": "p1", "reason": "unknown_action_effect_observed_do_not_replay"}
    case[3][0]["body"]["messages"][0]["content"] = '{"reason":"unknown_action_effect_observed_do_not_replay"}'
    case[5].append({"request_id": 11, "endpoint": "/drones/" + ACTOR, "origin": {"kind": "skill", "plan_id": "p1"}})
    result = m.unknown_recovery(*case)
    assert result["program_status_query_request_ids"] == [11]
    assert result["new_model_status_query_call_ids"] == []


def write_request(tmp_path, content="ordinary feedback", **changes):
    body = {"model": "deepseek-flash", "thinking": {"type": "enabled"}, "reasoning_effort": "high", "max_tokens": 8192,
            "messages": [{"role": "assistant", "reasoning_content": "SECRET_REASONING", "content": None},
                         {"role": "tool", "tool_call_id": "a", "content": content}]}
    body.update(changes)
    raw = json.dumps(body).encode()
    row = {"kind": "http_request", "sequence": 1, "data": {"logical_call_id": "m1", "attempt_id": "a1",
           "body": {"base64": base64.b64encode(raw).decode(), "length": len(raw), "sha256": m.sha(raw)}}}
    file = tmp_path / "archive.jsonl"
    file.write_text(json.dumps(row) + "\n")
    return file


@pytest.mark.parametrize("marker", m.LEAK_MARKERS)
def test_actual_wire_cannot_expose_fault_identity(tmp_path, marker):
    with pytest.raises(ValueError, match="leaked"):
        m.request_views(write_request(tmp_path, marker))


def test_actual_wire_model_configuration_verified(tmp_path):
    with pytest.raises(ValueError, match="frozen model"):
        m.request_views(write_request(tmp_path, thinking={"type": "disabled"}))


def test_fault_identity_in_reasoning_input_also_rejected_without_exporting_reasoning(tmp_path):
    file = write_request(tmp_path, messages=[{"role": "assistant", "reasoning_content": "synthetic_fault", "content": None}])
    with pytest.raises(ValueError, match="leaked"):
        m.request_views(file)


def test_absent_zero_event_sidecar_is_known_zero_from_public_history_and_worker():
    public = [{"request_id": 1, "phase": "task", "admin": False, "action": False, "history_events": []}]
    worker = {"physical_cost": {"api_requests": 1, "action_attempts": 0, "physical_history_events": 0}}
    assert m.verify_world_logs(public, [], worker)["zero_events_verified"] is True


def test_absent_sidecar_cannot_hide_a_public_physical_event():
    public = [{"request_id": 1, "history_events": [{"actor": ACTOR, "history": {"event": "take_off"}}]}]
    with pytest.raises(ValueError, match="missing public"):
        m.verify_world_logs(public, [], None)


def test_evidence_hash_mismatch_rejected(tmp_path):
    (tmp_path / "scorer.json").write_text('{"passed":false}\n')
    with pytest.raises(ValueError, match="Hash mismatch"):
        m.old.hashes(tmp_path, {"scorer.json": m.sha(b'{"passed":true}\n')})


def test_fault_unknown_association_is_not_guessed():
    case = unknown_case()
    case[2]["decisions"][0]["tool_call_id"] = "different"
    with pytest.raises(ValueError, match="uniquely linked"):
        m.unknown_recovery(*case)
