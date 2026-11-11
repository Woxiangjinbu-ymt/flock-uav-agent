"""Local-only audit tests: raw representation, layered truth and recovery costs."""
import base64
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_deepseek_hover_pilot as m

ACTOR = "3d229034"
COMMAND = "Command drone Drone 4 to take off to an altitude of 12 meters and hover in place."
SCHEMA = {"type": "object", "properties": {"input_json": {"type": "string"}}, "required": ["input_json"]}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Analysis tests must not call a provider")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def frozen(tmp_path):
    for name in ("hover_completion.py", "tool_input_contract.py", "harness_integration.py",
                 "template/system_prompt.py", "template/parsing_error.py"):
        dest = tmp_path / "source/agent4drone" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / "agent4drone" / name).read_bytes())
    return tmp_path


def schemas(candidate=False):
    tools = [{"type": "function", "function": {"name": "list_drones", "description": "List public drones.",
        "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "take_off", "description": "Use a JSON string.\n- drone_id: The ID (required)",
        "parameters": copy.deepcopy(SCHEMA)}}]
    if candidate:
        f = tools[1]["function"]
        f["parameters"]["properties"]["input_json"] = copy.deepcopy(m.INPUT_PROPERTY)
        f["description"] = f["description"].replace("JSON string", "JSON object (preferred) or legacy JSON-object string") + m.INPUT_NOTE
    return tools


def test_fixed_eight_slots_two_contracts_and_same_base_layout():
    slots = m.expected_trials()
    assert len(slots) == len({t[0] for t in slots}) == 8
    assert [t[-1] for t in slots] == ["control", "candidate", "candidate", "control", "control", "candidate", "candidate", "control"]
    assert sum(t[1] != "duration_hover" for t in slots) == 6
    assert m.ARMS == {"control": ("off", "string_v1"), "candidate": ("hover_completion_v1", "json_object_or_string_v1")}


@pytest.mark.parametrize("arm", ["control", "candidate"])
def test_frozen_prompt_and_schema_allowlist(frozen, arm):
    material = m.prompt_material(frozen, m.frozen_contract(frozen))
    tools = schemas(arm == "candidate")
    names = [t["function"]["name"] for t in tools]
    body = {"tools": tools, "messages": [{"role": "system", "content": m.expected_prompt(material, names, arm, COMMAND) + "\n\nPublic evidence"},
        {"role": "assistant", "reasoning_content": "DO_NOT_EXPORT_REASONING", "content": ""}]}
    before = copy.deepcopy(body)
    check = m.audit_wire_contract(body, arm, COMMAND, material)
    assert check["verified"] and check["input_json_tool_count"] == 1
    assert body == before and "DO_NOT_EXPORT_REASONING" not in json.dumps(check)
    body["messages"][0]["content"] = body["messages"][0]["content"].replace("Monitor battery levels", "Ignore battery levels")
    with pytest.raises(ValueError, match="prompt differs"):
        m.audit_wire_contract(body, arm, COMMAND, material)


def test_only_frozen_input_changes_are_allowed_between_native_tools():
    a, b = schemas(), schemas(True)
    before = copy.deepcopy((a, b))
    assert m.compare_tool_contracts([a, a], [b])["verified"]
    assert (a, b) == before
    assert m.compare_tool_contracts([], [b])["verified"] is None


@pytest.mark.parametrize("damage", ["description", "noarg", "order", "inner_schema", "within_trial"])
def test_native_tool_semantics_or_permissions_cannot_hide_in_allowlist(damage):
    a, b = schemas(), schemas(True)
    series = [b]
    if damage == "description": b[1]["function"]["description"] += " do arbitrary work"
    elif damage == "noarg": b[0]["function"]["parameters"]["properties"]["extra"] = {"type": "string"}
    elif damage == "order": b.reverse()
    elif damage == "inner_schema": b[1]["function"]["parameters"]["properties"]["input_json"]["anyOf"][1]["additionalProperties"] = False
    else:
        changed = copy.deepcopy(b); changed[1]["function"]["description"] += " drift"; series.append(changed)
    with pytest.raises(ValueError): m.compare_tool_contracts([a], series)


@pytest.mark.parametrize("value,representation", [({"drone_id": ACTOR}, "object"), (json.dumps({"drone_id": ACTOR}), "string"),
    ([], "array"), (3, "scalar"), (True, "scalar"), (None, "null")])
def test_raw_response_type_is_preserved_before_inner_parsing(value, representation):
    result = m.input_representation(json.dumps({"input_json": value}), SCHEMA, "candidate")
    assert result["representation"] == representation
    if representation in {"object", "string"}: assert result["decoded_parameters"] == {"drone_id": ACTOR}
    else: assert "input_value_not_object" in result["argument_findings"]


def test_object_legacy_mismatch_does_not_turn_into_candidate_parameter_error():
    raw = json.dumps({"input_json": {"drone_id": ACTOR}})
    assert "legacy_string_type_mismatch" in m.input_representation(raw, SCHEMA, "control")["argument_findings"]
    assert m.input_representation(raw, SCHEMA, "candidate")["argument_findings"] == []
    assert not m.input_representation("{}", {"properties": {}}, "control")["parameter_tool"]


@pytest.mark.parametrize("raw,reason", [
    ('{"input_json":{"x":1,"x":2}}', "outer_duplicate_keys"),
    ('{"input_json":{"x":1e9999}}', "outer_nonfinite"),
    ('{"input_json":"{\\"x\\":NaN}"}', "inner_nonfinite"),
    ('{"input_json":"{\\"x\\":1,\\"x\\":2}"}', "inner_duplicate_keys"),
    ('{"input_json":"{"}', "inner_malformed_json"),
    ('{"input_json":', "outer_malformed_json"),
    ('[]', "outer_not_object"), ('{}', "missing_input_json"),
    ('{"input_json":{},"extra":1}', "extra_outer_fields"), (None, "function_arguments_not_string"),
])
def test_invalid_json_and_envelopes_are_classified_not_silently_repaired(raw, reason):
    assert reason in m.input_representation(raw, SCHEMA, "candidate")["argument_findings"]


def execution(text=None, fields=None, status="success"):
    return {"tool_span_id": "s1", "tool_message_status": status,
        "public_output": {"kind": "text", "text": text} if text is not None else {"kind": "object", "fields": fields or {}}}


@pytest.mark.parametrize("event,category", [
    (execution("Error invoking tool: input_json: Input should be a valid string", status="error"), "input_representation_type_error"),
    (execution("Error: input_json must represent an object", status="error"), "input_envelope_or_json_error"),
    (execution("Error: drone_id is required"), "tool_parameter_semantic_error"),
    (execution(fields={"status": "rejected", "reason": "Unexpected control fields"}), "tool_parameter_semantic_error"),
    (execution("Error during takeoff: Request timed out before response was received"), "unknown_execution_effect"),
    (execution(fields={"outcome": "partial"}), "environment_or_partial_execution"),
    (execution("Error: unknown problem"), "other_tool_failure_unclassified"),
])
def test_error_taxonomy_includes_json_semantic_rejection_and_keeps_unknown_separate(event, category):
    assert m.feedback_errors([event])[0]["category"] == category


def test_semantic_parameter_errors_do_not_get_claimed_fixed_by_object_acceptance():
    assert m.semantic_findings("run_harness", {"action": "start", "skill": "simple_chain", "steps": []}, "") == ["unexpected_control_fields"]
    assert m.semantic_findings("get_drone_status", {}, "- drone_id: Exact ID (required)") == ["missing_required_field:drone_id"]
    assert m.semantic_findings("take_off", {"drone_id": ACTOR}, "- altitude: Height (optional, default: 10)") == []


@pytest.mark.parametrize("value", [[], {}, None, True, 1, "invalid"])
def test_json_valid_but_semantically_invalid_control_action_does_not_crash_audit(value):
    assert m.semantic_findings("run_harness", {"action": value, "skill": "simple_chain"}, "") == ["invalid_control_action"]


def test_input_audit_counts_response_calls_once_and_includes_unexecuted_invalid_call(tmp_path):
    raw_calls = [
        {"id": "noarg", "function": {"name": "list_drones", "arguments": "{}"}},
        {"id": "object", "function": {"name": "take_off", "arguments": json.dumps({"input_json": {"drone_id": ACTOR}})}},
        {"id": "bad", "function": {"name": "take_off", "arguments": "{"}},
    ]
    body = {"choices": [{"index": 0, "message": {"role": "assistant", "reasoning_content": "NEVER_EXPORT_REASONING", "tool_calls": raw_calls}}]}
    raw = json.dumps(body).encode()
    archive = tmp_path / "archive.jsonl"
    archive.write_text(json.dumps({"kind": "http_response_chunk", "data": {"attempt_id": "a1", "body": {
        "base64": base64.b64encode(raw).decode(), "length": len(raw), "sha256": m.sha(raw)}}}) + "\n")
    wire = {"responses": [{"attempt_id": "a1", "logical_call_id": "m1", "request_sequence": 4}],
        "decisions": [{"attempt_id": "a1", "choice_index": 0, "tool_call_id": c["id"], "executions": []} for c in raw_calls]}
    request = {"attempt_id": "a1", "body": {"tools": schemas(), "messages": [{"role": "assistant", "tool_calls": raw_calls}]}}
    before = archive.read_bytes()
    audit = m.input_audit(archive, wire, [request], "candidate")
    assert audit["observed_response_tool_calls"] == 3 and audit["input_json_tool_calls"] == 2
    assert audit["representation_counts"] == {"object": 1, "outer_invalid": 1}
    assert audit["unexecuted_invalid_argument_calls"] == 1
    assert audit["calls"][1]["raw_tool_call_id"] == "object"
    assert "NEVER_EXPORT_REASONING" not in json.dumps(audit) and archive.read_bytes() == before


def common(verified=False):
    return {"schema": "hover-completion-v1", "task_completed": True if verified else None,
            "status": "verified" if verified else "unverified", "reasons": [], "completion_claim_accepted": verified}


def truth(completed=True):
    return {"schema": "hover-pilot-independent-v1", "task_completed": completed,
            "status": "not_evaluable" if completed is None else "satisfied" if completed else "not_satisfied"}


def test_world_truth_can_pass_while_public_evidence_is_unverified_and_auxiliary_passes():
    a = {"success": True, "output": "Done. [TASK DONE]"}
    result = m.completion_views(a, {"unified_completion": truth(), "checker": {"passed": True}}, common(), "control")
    assert result["independent_truth"]["task_completed"] is True
    assert result["common_public_assessment"]["task_completed"] is None
    assert result["auxiliary_original_checker"]["passed"] is True
    assert result["candidate_online_matches_common"] is None


def test_candidate_online_consistency_and_duration_null():
    c = common(); a = {"success": True, "task_completed": None, "completion_assessment": {**c, "completion_marker_present": True}}
    result = m.completion_views(a, {"unified_completion": truth(None)}, c, "candidate")
    assert result["candidate_online_matches_common"] is True
    assert result["independent_truth"]["task_completed"] is None
    a["task_completed"] = True
    with pytest.raises(ValueError, match="differs"): m.completion_views(a, None, c, "candidate")


def recovery_case():
    world = {"actions": [{"request_id": 1, "tool": "take_off", "actor_id": ACTOR,
        "origin": {"kind": "model_tool", "tool_call_id": "take"}, "committed": True, "status": "success",
        "physical_events": [{"event": "take_off", "actor": ACTOR, "committed": True}]}]}
    wire = {"decisions": [
        {"tool_call_id": "take", "logical_call_id": "m1", "name": "take_off", "request_sequence": 1,
         "executions": [{"end_sequence": 9}], "arguments": {"drone_id": ACTOR}},
        {"tool_call_id": "status", "logical_call_id": "m2", "name": "get_drone_status", "request_sequence": 10,
         "executions": [{"end_sequence": 19, "tool_message_status": "success", "public_output": {"fields": {"id": ACTOR}}}], "arguments": {"drone_id": ACTOR}}],
        "responses": [{"attempt_id": "a2", "status_code": 200, "response_closed": True}], "final_messages": []}
    request = {"sequence": 10, "logical_call_id": "m2", "attempt_id": "a2", "body_sha256": "sha",
        "body": {"messages": [{"role": "tool", "tool_call_id": "take", "content": "Error during takeoff: Request timed out before response was received"}]}}
    task_state = {"events": [{"evidence_id": "e1", "tool_call_id": "take", "tool_name": "take_off", "args": {"drone_id": ACTOR}, "status": "unknown"}]}
    execution_state = {"actions": [{"evidence_id": "e1", "tool_call_id": "take", "tool_name": "take_off", "requested": {"drone_id": ACTOR}, "effect_status": "unknown", "applied": None, "receipt_status": "unknown"}]}
    return {"triggered": True, "fault_record": {"world_request_id": 1, "actor": ACTOR}}, world, wire, [request], [], common(True), task_state, execution_state


def test_unknown_recovery_state_query_without_redundant_hover_is_success():
    result = m.recovery_audit(*recovery_case())
    assert result["opportunities"] == result["recovery_rate"] == 1
    assert result["new_model_status_query_call_ids"] == ["status"]
    assert result["new_model_takeoff_replay_attempt_ids"] == []


@pytest.mark.parametrize("damage", ["feedback", "response", "no_request", "no_trigger", "parallel_query", "program_only", "replay", "no_public_confirmation", "history_rewritten", "preselected_replay"])
def test_no_opportunity_or_false_recovery_is_not_promoted(damage):
    case = list(recovery_case())
    if damage == "feedback": case[3][0]["body"]["messages"] = []
    elif damage == "response": case[2]["responses"][0]["status_code"] = None
    elif damage == "no_request": case[3] = []
    elif damage == "no_trigger": case[0]["triggered"] = False
    elif damage == "parallel_query": case[2]["decisions"][1]["request_sequence"] = 1
    elif damage == "program_only":
        case[2]["decisions"].pop();case[2]["final_messages"] = [{"logical_call_id": "m2"}]
        case[4].append({"request_id": 2, "endpoint": "/drones/" + ACTOR, "origin": {"kind": "skill"}})
    elif damage in {"replay", "preselected_replay"}:
        retry = copy.deepcopy(case[1]["actions"][0]);retry.update(request_id=2, committed=False, status="error", physical_events=[], origin={"kind": "model_tool", "tool_call_id": "replay"})
        case[1]["actions"].append(retry)
        decision = copy.deepcopy(case[2]["decisions"][1]);decision.update(tool_call_id="replay", name="take_off")
        if damage == "preselected_replay": decision["request_sequence"] = 1
        case[2]["decisions"].append(decision)
    elif damage == "history_rewritten": case[7]["actions"][0]["effect_status"] = "completed"
    else: case[5] = common()
    result = m.recovery_audit(*case)
    assert result["recovery_rate"] != 1
    if damage in {"feedback", "response", "no_request", "no_trigger"}:
        assert result["opportunities"] == 0 and result["recovery_rate"] is None


def test_missing_actual_effect_cannot_use_probe_as_truth():
    case = recovery_case();case[1]["actions"][0]["physical_events"] = []
    with pytest.raises(ValueError, match="applied takeoff"):m.recovery_audit(*case)


def test_aggregation_keeps_missing_cost_usage_and_duration_distinct():
    a = m.aggregate([{"trial_id": "unrun", "started": False}])
    assert a["planned"] == 1 and a["started"] == 0 and a["total_tokens"] is None
    assert a["unknown_recovery_rate"] is None and a["conservative_usd_known_sum"] is None
    b = m.aggregate([{"trial_id": "duration", "started": True, "completion": {"independent_truth": truth(None)},
        "model_io": {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                               "known_usage_responses": 1, "delegated_http_attempts": 2,
                               "all_delegated_responses_have_usage": False}},
        "cost": {"settled_conservative_charge": "0.0001"}}])
    assert b["scored"] == 1 and b["independent_truth_unverified"] == 1 and b["independent_truth_false"] == 0
    assert b["total_tokens"] == 12 and b["usage_complete_trials"] == 0


def test_analyzer_import_does_not_import_provider_simulator_or_pickle():
    code = "import sys; sys.path.insert(0, 'scripts'); import analyze_deepseek_hover_pilot; "
    code += "assert not any(n in sys.modules for n in ['pickle', 'openai', 'langchain', 'harness_natural_runner', 'harness_v2_world', 'harness_full100_world'])"
    completed = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_zero_dispatch_full_analysis_is_read_only_and_keeps_eight_six_two_denominators(tmp_path, monkeypatch):
    import deepseek_hover_pilot as pilot
    from harness_live_cost import LiveCostLedger
    run = tmp_path / "zero"
    pilot.prepare(run, local_smoke=True)
    monkeypatch.setattr(pilot, "credential", lambda *_: pytest.fail("Analysis must not read credentials"))
    with LiveCostLedger.create(run / "cost-ledger.jsonl", pilot.cost_policy(), pilot.PILOT["batch_budget"]) as book:
        snapshot = book.snapshot()
    pilot.write(run / "cost-summary.json", snapshot)
    pilot.write(run / "results.json", [])
    pilot.write(run / "summary.json", {"status": "stopped", "reason": "test_zero_dispatch", "planned_trials": 8,
        "started_trials": 0, "scored_trials": 0, "unstarted_trials": [t["trial_id"] for t in pilot.trials()]})
    inventory = {str(p.relative_to(run)): m.sha(p.read_bytes()) for p in run.rglob("*") if p.is_file()}
    pilot.write(run / "campaign-seal.json", {"files": inventory})
    before = {str(p.relative_to(run)): m.sha(p.read_bytes()) for p in run.rglob("*") if p.is_file()}
    report = m.analyze(run)
    after = {str(p.relative_to(run)): m.sha(p.read_bytes()) for p in run.rglob("*") if p.is_file()}
    assert after == before
    assert report["planned"] == 8 and report["started"] == report["scored"] == 0
    assert [report["groups"][g]["all"]["planned"] for g in ("all_8", "supported_6", "duration_2")] == [8, 6, 2]
    assert report["groups"]["all_8"]["control"]["total_tokens"] is None
    assert report["same_success_supported_subset"]["pairs"] == 0
    assert "两臂同样成功" in m.markdown(report)
    code = "import sys;sys.path.insert(0,'scripts');import analyze_deepseek_hover_pilot as m;from pathlib import Path;"
    code += "m.analyze(Path(sys.argv[1]));assert not any(n in sys.modules for n in ['pickle','openai','harness_natural_runner','harness_v2_world'])"
    checked = subprocess.run([sys.executable, "-B", "-c", code, str(run)], cwd=ROOT, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
