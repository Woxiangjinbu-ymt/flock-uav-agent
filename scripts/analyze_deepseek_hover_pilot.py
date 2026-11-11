"""Read-only audit of the frozen eight-slot hover/input-contract comparison.

The analyzer reads JSON and archived HTTP bytes. It never starts a worker,
imports the simulator/SDK, loads pickle, calls a provider, or exports reasoning.
Runtime completion, common public evidence, and isolated truth remain separate.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import copy
from decimal import Decimal
import json
import math
from pathlib import Path
import re

import analyze_deepseek_cost_pilot as cost

old, contract = cost.old, cost.contract
read, safe, sha, rows, require = old.read, old.safe, old.sha, old.rows, old.require
CONDITIONS = (("state_hover", "original"), ("explicit_hover", "explicit_hover"),
              ("unknown_takeoff", "original"), ("duration_hover", "duration_hover"))
ARMS = {"control": ("off", "string_v1"),
        "candidate": ("hover_completion_v1", "json_object_or_string_v1")}
INPUT_DESCRIPTION = ("A JSON object (preferred), or a legacy string encoding that same object. "
    "Arrays/scalars, duplicate keys and nonfinite numbers are invalid. "
    "Use the tool's documented parameter names and values; no fields are inferred.")
INPUT_NOTE = ("\nInput representation: input_json accepts a JSON object (preferred) "
    "or a legacy string encoding that object. Parameter meaning and required "
    "fields above are unchanged; no missing values are inferred.")
INPUT_PROPERTY = {"anyOf": [{"type": "string"}, {"additionalProperties": True, "type": "object"}],
                  "description": INPUT_DESCRIPTION}
MARKER_RULE = ("1. Use [TASK DONE] only when the scoped hover completion evidence is verified; "
               "otherwise state that completion is unverified without this marker.")


def expected_trials():
    return [(condition + "-" + arm, condition, variant, arm)
        for index, (condition, variant) in enumerate(CONDITIONS)
        for arm in (("control", "candidate") if index % 2 == 0 else ("candidate", "control"))]


def verify_run(run):
    # This coordinator's verify is deliberately pure JSON/hash/cost replay. Do
    # not replace it with general_agent/worker/scorer entry points.
    import deepseek_hover_pilot as pilot
    verified = pilot.verify(run)
    manifest = read(run, "manifest.json")
    require(manifest.get("schema_version") == 3 and manifest.get("scope") == "deepseek_hover_pilot_v1"
            and manifest.get("online_variant_contract") == "harness_hover_input_pair_v1", "Wrong hover pilot scope")
    require([(t["trial_id"], t["condition"], t["fixture_variant"], t["arm"]) for t in manifest["trials"]]
            == expected_trials(), "Changed eight-slot order/denominator")
    for trial in manifest["trials"]:
        require(trial["task_id"] == "c74d7020" and trial["task_progress_mode"] == "evidence_harness"
                and trial["input_layout"] == "compact_tools_v1" and trial["harness_revision"] == "search_confirm_v9"
                and (trial["completion_contract"], trial["tool_input_contract"]) == ARMS[trial["arm"]],
                "Changed comparison arm")
    return manifest, read(run, "results.json"), {"coordinator": verified,
        "manifest_sha256": sha(safe(run, "manifest.json").read_bytes()),
        "campaign_seal_sha256": sha(safe(run, "campaign-seal.json").read_bytes())}


def frozen_contract(run):
    """Load only the sealed, pure public-evidence assessor, never Agent code."""
    path = safe(run, "source/agent4drone/hover_completion.py")
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            require(all(n.name in {"math", "re"} for n in node.names), "Unexpected completion-module dependency")
        if isinstance(node, ast.ImportFrom):
            require(node.module == "__future__", "Unexpected completion-module dependency")
    namespace = {"__name__": "_frozen_hover_completion"}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


def frozen_input_rules(run):
    """Extract pure rule functions without importing Pydantic/tool machinery."""
    tree = ast.parse(safe(run, "source/agent4drone/tool_input_contract.py").read_text())
    selected = [node for node in tree.body if
        (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "INPUT_JSON_CONTRACTS" for t in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name in {"validate_input_json_contract", "tool_input_rules"})]
    require(len(selected) == 3, "Missing frozen input rule definitions")
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<frozen-input-rules>", "exec"), namespace)
    return namespace["tool_input_rules"]("json_object_or_string_v1")


def prompt_material(run, completion):
    template, parsing = cost.frozen_prompt_templates(run)
    return {"template": template, "parsing": parsing,
        "harness": cost._string_assignment(safe(run, "source/agent4drone/harness_integration.py"), "HARNESS_RULES"),
        "input_rules": frozen_input_rules(run), "completion_rules": completion["completion_rules"]}


def expected_prompt(material, names, arm, command):
    prompt = material["template"].format(parsing_error_template=material["parsing"], tool_lines="\n".join("- " + n for n in names))
    if arm == "candidate":
        start, end = prompt.index("TOOL INPUT RULES:\n"), prompt.index("\nAVAILABLE TOOLS:\n")
        prompt = prompt[:start] + ("TOOL INPUT RULES:\n- Tools without parameters can be called directly.\n"
            + material["input_rules"].rstrip()
            + "\n- On a tool-input validation error, correct the representation and required fields before retrying.\n") + prompt[end:]
        prompt = prompt.replace("1. Always end the final answer with [TASK DONE].", MARKER_RULE, 1)
    prompt += "\n\n" + material["harness"]
    if arm == "candidate":
        prompt += "\n\n" + material["completion_rules"](command)
    return prompt


def audit_wire_contract(body, arm, command, material):
    native = body.get("tools")
    require(isinstance(native, list) and bool(native), "Missing native tool definitions")
    names, parameter_tools = [], []
    for tool in native:
        require(isinstance(tool, dict) and tool.get("type") == "function", "Invalid native tool type")
        f = tool.get("function", {})
        name, params = f.get("name"), f.get("parameters")
        require(isinstance(name, str) and name and name not in names and isinstance(params, dict)
                and isinstance(f.get("description"), str) and bool(f["description"]), "Incomplete/duplicate native tool")
        names.append(name)
        properties = params.get("properties", {})
        if "input_json" in properties:
            require(set(properties) == {"input_json"} and params.get("required") == ["input_json"], "Changed tool envelope")
            if arm == "candidate":
                require(properties["input_json"] == INPUT_PROPERTY and f["description"].endswith(INPUT_NOTE),
                        "Candidate schema/description does not advertise the frozen object|string contract")
            else:
                require(properties["input_json"] == {"type": "string"}, "Control schema is not legacy string")
            parameter_tools.append(name)
    systems = [m for m in body.get("messages", []) if m.get("role") == "system"]
    require(len(systems) == 1 and isinstance(systems[0].get("content"), str), "Expected one text system message")
    system = systems[0]["content"]
    expected = expected_prompt(material, names, arm, command)
    require(system.startswith(expected), "Actual operating/input/completion prompt differs from frozen arm")
    require(system.count(cost.MARKER) == 1 and system.count(cost.TAIL) == 1, "Ambiguous tool catalog")
    suffix = system[len(expected):]
    require(not suffix or suffix.startswith("\n\n"), "Unexpected system suffix boundary")
    return {"verified": True, "arm": arm, "layout": "compact_tools_v1", "native_tool_count": len(names),
        "input_json_tool_count": len(parameter_tools), "native_tools_sha256": sha(old.canonical(native)),
        "system_sha256": sha(system.encode()), "system_utf8_bytes": len(system.encode()),
        "dynamic_evidence_suffix_utf8_bytes": len(suffix.encode()), "reasoning_exported": False}


def compare_tool_contracts(control, candidate):
    if not control or not candidate:
        return {"verified": None, "reason": "no_dispatched_request_in_one_or_both_arms"}
    for series in (control, candidate):
        require(all(v == series[0] for v in series), "Native tools drifted within a trial")
    expected = copy.deepcopy(control[0])
    changed = []
    for item in expected:
        f = item["function"]
        properties = f["parameters"].get("properties", {})
        if "input_json" not in properties:
            continue
        require(properties["input_json"] == {"type": "string"}, "Control input schema changed")
        properties["input_json"] = copy.deepcopy(INPUT_PROPERTY)
        f["description"] = re.sub("JSON string", "JSON object (preferred) or legacy JSON-object string",
                                  f["description"], flags=re.I) + INPUT_NOTE
        changed.append(f["name"])
    require(expected == candidate[0], "Native tool difference outside input-representation allowlist")
    return {"verified": True, "tool_names_order_permissions_and_inner_parameter_meaning_preserved": True,
        "input_representation_adapted_tools": changed, "requests_checked": len(control) + len(candidate)}


def strict_json(text):
    value = old.parse(text)
    def check(v):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("Nonfinite JSON number")
        if isinstance(v, dict):
            for child in v.values(): check(child)
        elif isinstance(v, list):
            for child in v: check(child)
    check(value)
    return value


def input_representation(raw, schema, arm):
    """Classify response arguments before inner-string normalization loses type."""
    result = {"representation": "outer_invalid", "parameter_tool": "input_json" in schema.get("properties", {}),
              "argument_findings": [], "decoded_parameters": None}
    if not isinstance(raw, str):
        result["argument_findings"].append("function_arguments_not_string")
        return result
    try:
        outer = strict_json(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        reason = "duplicate_keys" if "Duplicate" in str(exc) else "nonfinite" if "Nonfinite" in str(exc) else "malformed_json"
        result["argument_findings"].append("outer_" + reason)
        return result
    if not isinstance(outer, dict):
        result["argument_findings"].append("outer_not_object")
        return result
    if not result["parameter_tool"]:
        result["representation"] = "no_input_json_parameter"
        return result
    if "input_json" not in outer:
        result.update(representation="missing")
        result["argument_findings"].append("missing_input_json")
        return result
    if set(outer) != {"input_json"}:
        result["argument_findings"].append("extra_outer_fields")
    value = outer["input_json"]
    result["representation"] = ("object" if isinstance(value, dict) else "string" if isinstance(value, str)
        else "array" if isinstance(value, list) else "null" if value is None else "scalar")
    if arm == "control" and not isinstance(value, str):
        result["argument_findings"].append("legacy_string_type_mismatch")
    if isinstance(value, str):
        try:
            value = strict_json(value)
        except (ValueError, TypeError, RecursionError) as exc:
            reason = "duplicate_keys" if "Duplicate" in str(exc) else "nonfinite" if "Nonfinite" in str(exc) else "malformed_json"
            result["argument_findings"].append("inner_" + reason)
            return result
    if not isinstance(value, dict):
        result["argument_findings"].append("input_value_not_object")
    else:
        result["decoded_parameters"] = value
    return result


def semantic_findings(name, params, description):
    """Bounded, documented-field checks; never invent defaults or run a tool."""
    if not isinstance(params, dict):
        return []
    if name == "run_harness":
        action = params.get("action")
        if set(params) - {"action", "skill", "plan_id"}:
            return ["unexpected_control_fields"]
        if not isinstance(action, str) or action not in {"start", "resume", "cancel"}:
            return ["invalid_control_action"]
        wanted = {"action", "skill" if action == "start" else "plan_id"}
        if set(params) != wanted or not isinstance(params.get("skill" if action == "start" else "plan_id"), str):
            return ["missing_or_invalid_control_fields"]
    required = re.findall(r"(?m)^\s*-\s*([A-Za-z_]\w*):[^\n]*\(required\)", description)
    return ["missing_required_field:" + key for key in required if key not in params or params[key] is None]


def feedback_errors(executions):
    result = []
    for e in executions:
        public = e.get("public_output", {})
        fields = public.get("fields", {})
        text = public.get("text", "")
        status = fields.get("status")
        error = (e.get("tool_message_status") == "error" or e.get("error_type")
                 or text.startswith("Error") or status in {"rejected", "error", "failed", "needs_replan"}
                 or fields.get("outcome") in {"unknown", "error", "blocked", "partial"} or bool(fields.get("error")))
        if not error:
            continue
        rendered = " ".join(str(v) for v in (text, fields.get("reason"), fields.get("message"), fields.get("error")))
        if "Input should be a valid string" in rendered:
            category = "input_representation_type_error"
        elif any(s in rendered for s in ("input_json must", "Error parsing JSON input", "input_json: Field required", "Extra inputs are not permitted")):
            category = "input_envelope_or_json_error"
        elif any(s in rendered for s in ("is required", "are required", "Unexpected control fields", "Expected start, resume or cancel",
                                        "Start requires only", "Resume/cancel require only", "must be a", "must be one of")):
            category = "tool_parameter_semantic_error"
        elif fields.get("outcome") == "unknown" or any(s in rendered for s in ("timed out before response was received", "unknown_action_effect")):
            category = "unknown_execution_effect"
        elif fields.get("outcome") in {"blocked", "partial"}:
            category = "environment_or_partial_execution"
        else:
            category = "other_tool_failure_unclassified"
        result.append({"category": category, "tool_span_id": e["tool_span_id"],
            "tool_message_status": e.get("tool_message_status"), "public_feedback": public})
    return result


def input_audit(archive, wire, requests, arm):
    request_by_id = {r["attempt_id"]: r for r in requests}
    chunks = {}
    for row in rows(archive):
        if row["kind"] == "http_response_chunk":
            chunks.setdefault(row["data"]["attempt_id"], []).append(old.decode(row["data"]["body"]))
    calls = []
    for response in wire["responses"]:
        attempt = response["attempt_id"]
        raw_response = b"".join(chunks.get(attempt, []))
        try:
            payload = strict_json(raw_response)
        except (ValueError, TypeError, RecursionError):
            continue  # Already retained in model_io.issues, never counted as zero errors.
        if not isinstance(payload, dict):
            continue
        advertised = {t["function"]["name"]: t["function"] for t in request_by_id[attempt]["body"].get("tools", [])}
        for choice_index, choice in enumerate(payload.get("choices", [])):
            message = choice.get("message", {})
            for index, call in enumerate(message.get("tool_calls", []) or []):
                f = call.get("function", {}); name = f.get("name")
                declaration = advertised.get(name, {})
                finding = input_representation(f.get("arguments"), declaration.get("parameters", {}), arm)
                matching = [d for d in wire["decisions"] if d["attempt_id"] == attempt
                            and d["choice_index"] == choice.get("index") and d["tool_call_id"] == call.get("id")]
                executions = matching[0]["executions"] if len(matching) == 1 else []
                params = finding.pop("decoded_parameters")
                entry = {"call_key": [attempt, choice_index, index], "raw_tool_call_id": call.get("id"),
                    "logical_call_id": response["logical_call_id"], "request_sequence": response["request_sequence"],
                    "tool": name, "advertised": name in advertised,
                    "raw_arguments_sha256": sha(f["arguments"].encode()) if isinstance(f.get("arguments"), str) else None,
                    **finding, "semantic_findings": semantic_findings(name, params, declaration.get("description", "")),
                    "unique_execution_join": len(matching) == 1, "execution_spans": len(executions),
                    "feedback_errors": feedback_errors(executions),
                    "backend_effect_not_inferred_from_tool_message": True}
                # Inner names/values are already in model_io.decisions; do not export raw reasoning.
                calls.append(entry)
    parameters = [c for c in calls if c["parameter_tool"]]
    errors = Counter(e["category"] for c in calls for e in c["feedback_errors"])
    return {"observed_response_tool_calls": len(calls), "input_json_tool_calls": len(parameters),
        "representation_counts": dict(Counter(c["representation"] for c in parameters)),
        "argument_finding_counts": dict(Counter(f for c in calls for f in c["argument_findings"])),
        "semantic_finding_counts": dict(Counter(f for c in calls for f in c["semantic_findings"])),
        "feedback_error_counts": dict(errors),
        "calls_with_feedback_error": sum(bool(c["feedback_errors"]) for c in calls),
        "calls_without_tool_execution": sum(c["execution_spans"] == 0 for c in calls),
        "unexecuted_invalid_argument_calls": sum(bool(c["argument_findings"]) and c["execution_spans"] == 0 for c in calls),
        "calls": calls, "denominator": "each response tool-call position once; replayed request histories excluded",
        "semantics": "static argument findings are separate from actual runtime rejection; no broad semantic correctness claim"}


def unknown_history(action, cause, task_state, execution_state):
    """Join the original receipt to its preserved unknown execution record."""
    source = action.get("origin") or {}
    candidates = []
    for item in (execution_state or {}).get("actions", []):
        if item.get("tool_name") != "take_off" or item.get("requested", {}).get("drone_id") != action["actor_id"]:
            continue
        if source.get("kind") == "skill":
            match = bool(source.get("plan_id")) and item.get("source", {}).get("plan_id") == source["plan_id"]
        else:
            match = item.get("tool_call_id") == cause["tool_call_id"]
        if match:
            candidates.append(item)
    events = (task_state or {}).get("events", [])
    joined = []
    for item in candidates:
        matches = [e for e in events if e.get("evidence_id") == item.get("evidence_id")
                   and e.get("tool_name") == "take_off" and e.get("args", {}).get("drone_id") == action["actor_id"]
                   and e.get("tool_call_id") == item.get("tool_call_id")]
        if len(matches) == 1:
            joined.append({"evidence_id": item.get("evidence_id"), "raw_tool_call_id": item.get("tool_call_id"),
                "ledger_status": matches[0].get("status"), "execution_effect_status": item.get("effect_status"),
                "applied": item.get("applied"), "receipt_status": item.get("receipt_status")})
    preserved = (len(candidates) == len(joined) == 1 and joined[0]["ledger_status"] == "unknown"
                 and joined[0]["execution_effect_status"] == "unknown" and joined[0]["applied"] is None)
    return {"preserved": preserved, "matching_original_action_count": len(candidates), "records": joined,
        "task_state_present": task_state is not None, "execution_state_present": execution_state is not None}


def recovery_audit(probe, world, wire, requests, public, common, task_state=None, execution_state=None):
    result = {"configured": probe is not None, "triggered": bool(probe and probe.get("triggered")),
        "opportunities": 0, "recovery_rate": None, "status": "not_estimable_no_trigger",
        "scope": "fixed_unknown_takeoff; fresh model state verification and no replay, no redundant hover requirement"}
    if not result["triggered"]:
        return result
    fault = probe["fault_record"]
    applied = [a for a in world["actions"] if a["request_id"] == fault["world_request_id"]]
    require(len(applied) == 1 and applied[0]["tool"] == "take_off" and applied[0]["committed"] is True
            and applied[0]["status"] == "success" and applied[0]["actor_id"] == fault["actor"]
            and any(e["event"] == "take_off" and e["actor"] == fault["actor"] and e["committed"] is True
                    for e in applied[0]["physical_events"]), "Unknown fault has no unique applied takeoff")
    decisions = (wire or {}).get("decisions", [])
    causes = contract.decision_for_action(applied[0], decisions)
    if len(causes) > 1:
        causes = [d for d in causes if any("unknown_action_effect" in str(e.get("control_response", {}).get("reason")) for e in d["executions"])]
    require(len(causes) == 1, "Ambiguous unknown-effect source decision")
    cause = causes[0]
    ends = [e["end_sequence"] for e in cause["executions"] if e.get("end_sequence") is not None]
    require(len(ends) == 1, "Unknown tool lacks one end record")
    following = [r for r in requests if r["sequence"] > ends[0]]
    actor = fault["actor"]
    result.update(original_tool_call_id=cause["tool_call_id"], original_tool_end_sequence=ends[0],
        actor_id=actor, world_request_id=fault["world_request_id"])
    result["historical_unknown"] = unknown_history(applied[0], cause, task_state, execution_state)
    if not following:
        result["status"] = "not_estimable_no_following_model_input"
        return result
    request = following[0]
    feedback = [m for m in request["body"].get("messages", []) if m.get("role") == "tool" and m.get("tool_call_id") == cause["tool_call_id"]]
    seen = len(feedback) == 1 and any(s in str(feedback[0].get("content")) for s in
        ("timed out before response was received", "unknown_action_effect"))
    responses = [r for r in (wire or {}).get("responses", []) if r["attempt_id"] == request["attempt_id"]]
    returned = (len(responses) == 1 and responses[0].get("status_code") == 200
        and responses[0].get("response_closed") is True
        and any(d["logical_call_id"] == request["logical_call_id"] for d in decisions + (wire or {}).get("final_messages", [])))
    new = [d for d in decisions if d["request_sequence"] >= request["sequence"]]
    new_ids = {(d["logical_call_id"], d["tool_call_id"]) for d in new}
    query_ids = [d["tool_call_id"] for d in new if d["name"] == "get_drone_status"
        and isinstance(d["arguments"], dict) and d["arguments"].get("drone_id") == actor
        and any(e.get("end_sequence") is not None and e.get("tool_message_status") != "error"
                and e.get("public_output", {}).get("fields", {}).get("id") == actor for e in d["executions"])]
    new_actions, preselected = [], []
    for action in world["actions"]:
        if action["request_id"] <= applied[0]["request_id"]:
            continue
        linked = contract.decision_for_action(action, decisions)
        if len(linked) == 1:
            (new_actions if (linked[0]["logical_call_id"], linked[0]["tool_call_id"]) in new_ids else preselected).append(action)
    replay = [a["request_id"] for a in new_actions if a["actor_id"] == actor and a["tool"] == "take_off"]
    all_replay = [a["request_id"] for a in world["actions"] if a["request_id"] > applied[0]["request_id"]
                  and a["actor_id"] == actor and a["tool"] == "take_off"]
    physical_takeoffs = sum(e["event"] == "take_off" and e["actor"] == actor and e["committed"] is True
        for a in world["actions"] for e in a["physical_events"])
    result.update(feedback_seen_in_next_request=seen, new_model_response_returned=returned,
        next_request={k: request[k] for k in ("sequence", "logical_call_id", "attempt_id", "body_sha256")},
        new_model_status_query_call_ids=query_ids, new_model_takeoff_replay_attempt_ids=replay,
        all_subsequent_takeoff_replay_attempt_ids=all_replay,
        preselected_takeoff_replay_attempt_ids=[a["request_id"] for a in preselected if a["actor_id"] == actor and a["tool"] == "take_off"],
        preselected_followup_action_request_ids=[a["request_id"] for a in preselected],
        program_status_query_request_ids=[r["request_id"] for r in public if r["request_id"] > applied[0]["request_id"]
            and r.get("origin", {}).get("kind") == "skill" and r.get("endpoint") == "/drones/" + actor],
        actual_applied_takeoff_event_count=physical_takeoffs)
    result["opportunities"] = int(seen and returned)
    if result["opportunities"]:
        success = (bool(query_ids) and not all_replay and physical_takeoffs == 1
                   and common.get("task_completed") is True and result["historical_unknown"]["preserved"])
        result.update(recovery_rate=int(success), status="verified_state_recovery_without_replay" if success else "opportunity_without_verified_recovery")
    else:
        result["status"] = "not_estimable_feedback_or_returned_decision_missing"
    return result


def completion_views(agent, score, common, arm):
    online = agent.get("completion_assessment") if agent else None
    consistency = None
    if arm == "candidate" and agent is not None:
        require(isinstance(online, dict), "Candidate result lacks online completion assessment")
        require({k: online.get(k) for k in common} == common and agent.get("task_completed") == common.get("task_completed"),
                "Candidate online assessment differs from common frozen public assessor")
        consistency = True
    elif arm == "control" and agent is not None:
        require(online is None and "task_completed" not in agent, "Control unexpectedly enabled completion contract")
    if score is not None:
        truth = score.get("unified_completion")
        require(isinstance(truth, dict) and truth.get("schema") == "hover-pilot-independent-v1", "Missing independent hover truth")
        require((type(truth.get("task_completed")) is bool and truth.get("status") ==
                 ("satisfied" if truth["task_completed"] else "not_satisfied"))
                or (truth.get("task_completed") is None and truth.get("status") == "not_evaluable"), "Invalid independent tri-state truth")
    return {"independent_truth": old.clean(score.get("unified_completion")) if score else None,
        "common_public_assessment": common, "candidate_online_assessment": old.clean(online),
        "candidate_online_matches_common": consistency,
        "auxiliary_original_checker": old.clean(score.get("checker")) if score else None,
        "normal_agent_return": agent.get("success") if agent else None,
        "completion_marker_present_in_model_output": "[TASK DONE]" in str(agent.get("model_output", agent.get("output", ""))) if agent else None,
        "output_sha256": sha(str(agent.get("output", "")).encode()) if agent else None}


def aggregate(group):
    started = [r for r in group if r["started"]]
    result = {"planned": len(group), "started": len(started), "unstarted": [r["trial_id"] for r in group if not r["started"]],
        "scored": sum(r.get("completion", {}).get("independent_truth") is not None for r in started),
        "public_evidence_verified": sum(r.get("completion", {}).get("common_public_assessment", {}).get("task_completed") is True for r in started)}
    # The isolated evaluator uses a tri-state truth; retain raw per-trial truth
    # and expose only documented bool fields here, never coerce None to false.
    truth = [(r.get("completion", {}).get("independent_truth") or {}).get("task_completed") for r in started]
    result.update(independent_truth_verified=sum(v is True for v in truth), independent_truth_false=sum(v is False for v in truth),
                  independent_truth_unverified=sum(v is None for v in truth))
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "known_usage_responses", "delegated_http_attempts"):
        values = [(r.get("model_io") or {}).get("usage", {}).get(key) for r in started]
        known = [v for v in values if type(v) is int]
        result[key] = sum(known) if known else None
        result[key + "_observed_trials"] = len(known)
    result["usage_complete_trials"] = sum((r.get("model_io") or {}).get("usage", {}).get("all_delegated_responses_have_usage") is True for r in started)
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        usages = [(r.get("model_io") or {}).get("usage", {}) for r in started]
        known = [u[key] for u in usages if type(u.get(key)) is int]
        result[key + "_known_sum"] = sum(known) if known else None
        result[key + "_reported_responses"] = sum(u.get(key + "_reported_responses", 0) for u in usages)
    for key in ("action_attempts", "api_requests"):
        values = [(r.get("world") or {}).get(key) for r in started]
        known = [v for v in values if type(v) is int]
        result[key] = sum(known) if known else None
    elapsed = [r["worker_elapsed_seconds"] for r in started if type(r.get("worker_elapsed_seconds")) in (int, float)]
    result["worker_elapsed_seconds_known_sum"] = sum(elapsed) if elapsed else None
    charges = [r.get("cost", {}).get("settled_conservative_charge") for r in started]
    result["conservative_usd_known_sum"] = str(sum((Decimal(v) for v in charges if v is not None), Decimal(0))) if any(v is not None for v in charges) else None
    for key in ("representation_counts", "argument_finding_counts", "semantic_finding_counts", "feedback_error_counts"):
        counts = Counter()
        for r in started: counts.update(r.get("input_audit", {}).get(key, {}))
        result[key] = dict(counts)
    opportunities = sum(r.get("unknown_recovery", {}).get("opportunities", 0) for r in started)
    recovered = sum(r.get("unknown_recovery", {}).get("recovery_rate") == 1 for r in started)
    result.update(unknown_recovery_opportunities=opportunities, unknown_recovery_completed=recovered,
                  unknown_recovery_rate=recovered / opportunities if opportunities else None)
    return result


def analyze(run):
    run = Path(run).resolve()
    manifest, result_rows, verification = verify_run(run)
    completion = frozen_contract(run)
    material = prompt_material(run, completion)
    reports, tool_series = {}, {}
    for trial in manifest["trials"]:
        ident, arm = trial["trial_id"], trial["arm"]
        result = next((r for r in result_rows if r["trial_id"] == ident), None)
        variant = trial["fixture_variant"]
        prefix = "" if variant == "original" else "variants/" + variant + "/"
        fixture = read(run / "inputs", prefix + "fixtures/c74d7020.json")
        require(fixture["scene_id"] == "fb8971ec", "Changed hover scene")
        report = {"trial_id": ident, "condition": trial["condition"], "fixture_variant": variant,
            "arm": arm, "command": fixture["command"], "started": result is not None,
            "supported_completion_condition": trial["condition"] != "duration_hover"}
        reports[ident] = report
        if result is None:
            continue
        folder = safe(run, "trials/" + ident)
        worker, score = result.get("worker"), result.get("score")
        agent = read(folder, "policy/agent-result.json", optional=True)
        state = read(folder, "policy/task-state.json", optional=True)
        common = completion["assess_hover_completion"](fixture["command"], state or {})
        report["completion"] = completion_views(agent, score, common, arm)
        report.update(result_layers=old.clean(result.get("result")), termination=old.clean(result.get("termination")),
            worker_elapsed_seconds=worker.get("elapsed") if worker else None,
            request_limits=old.clean(worker.get("limits")) if worker else read(folder, "private/request-limits.json", optional=True))
        paths = list(safe(folder, "policy/modelio").glob("*.jsonl"))
        require(len(paths) <= 1, "Multiple model archives")
        wire = old.archive_analysis(paths[0]) if paths else None
        requests = contract.request_views(paths[0]) if paths else []
        if wire:
            limits = report.get("request_limits") or {}
            usage = wire["usage"]
            usage.update(admitted_http_attempts=limits.get("admitted_http_attempts"), delegated_http_attempts=limits.get("delegated_http_attempts"))
            if usage["known_usage_responses"] == 0 and limits.get("delegated_http_attempts") != 0:
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"): usage[k] = None
            if type(limits.get("known_total_tokens")) is int and usage["total_tokens"] is not None:
                require(limits["known_total_tokens"] == usage["total_tokens"], "Limit/wire usage mismatch")
            usage["all_delegated_responses_have_usage"] = usage["known_usage_responses"] == limits.get("delegated_http_attempts")
            wire["request_contracts"] = [{"attempt_id": r["attempt_id"], "body_sha256": r["body_sha256"],
                **audit_wire_contract(r["body"], arm, fixture["command"], material)} for r in requests]
            require(all(r["has_execution_projection"] for r in requests), "Missing execution recovery projection on actual wire")
            actual = {r["attempt_id"] for r in wire["responses"] if r.get("status_code") is not None}
            tool_series[ident] = [r["body"]["tools"] for r in requests if r["attempt_id"] in actual]
            wire["actual_request_bytes_with_observed_response"] = sum(r["request_bytes"] for r in wire["responses"] if r["attempt_id"] in actual)
            report["input_audit"] = input_audit(paths[0], wire, requests, arm)
        report["model_io"] = wire
        public_path = safe(folder, "policy/worldlogs/public-api.jsonl")
        physical_path = safe(folder, "policy/worldlogs/physical-events.jsonl")
        public, physical = rows(public_path) if public_path.exists() else [], rows(physical_path) if physical_path.exists() else []
        world = old.world_analysis(public, physical, fixture)
        world.pop("hover_audit", None)
        world["verification"] = contract.verify_world_logs(public, physical, worker)
        report["world"] = world
        probe = read(folder, "private/online-api-fault.json", optional=True)
        execution_state = read(folder, "policy/execution-state.json", optional=True)
        report["unknown_recovery"] = recovery_audit(probe, world, wire, requests, public, common, state, execution_state)
    costs = old.cost_analysis(run, reports)
    pairs = []
    for condition, _ in CONDITIONS:
        control, candidate = (reports[condition + "-" + arm] for arm in ARMS)
        metrics = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "delegated_http_attempts"):
            a = (control.get("model_io") or {}).get("usage", {}).get(key)
            b = (candidate.get("model_io") or {}).get("usage", {}).get(key)
            metrics[key] = b - a if type(a) is int and type(b) is int else None
        ca, cb = control.get("cost", {}).get("settled_conservative_charge"), candidate.get("cost", {}).get("settled_conservative_charge")
        metrics["conservative_usd"] = str(Decimal(cb) - Decimal(ca)) if ca is not None and cb is not None else None
        pairs.append({"condition": condition, "both_started": control["started"] and candidate["started"],
            "control_completion": control.get("completion"), "candidate_completion": candidate.get("completion"),
            "candidate_minus_control": metrics,
            "native_contract_allowlist": compare_tool_contracts(tool_series.get(control["trial_id"], []), tool_series.get(candidate["trial_id"], []))})
    groups = {}
    for label, selected in (("all_8", list(reports.values())),
                            ("supported_6", [r for r in reports.values() if r["supported_completion_condition"]]),
                            ("duration_2", [r for r in reports.values() if not r["supported_completion_condition"]])):
        groups[label] = {"all": aggregate(selected), **{arm: aggregate([r for r in selected if r["arm"] == arm]) for arm in ARMS}}
    same_success = [condition for condition, _ in CONDITIONS if condition != "duration_hover" and all(
        (reports[condition + "-" + arm].get("completion", {}).get("independent_truth") or {}).get("task_completed") is True
        for arm in ARMS)]
    matched = {"conditions": same_success, "pairs": len(same_success),
        "scope": "descriptive_matched_success_subset_excluding_duration; report alongside full fixed denominator, not causal attribution",
        **{arm: aggregate([reports[c + "-" + arm] for c in same_success]) for arm in ARMS}}
    _, _, after = verify_run(run)
    require(after == verification, "Sealed source changed during analysis")
    return {"schema_version": 1, "valid": True, "run": str(run), "local_smoke": manifest["local_smoke"],
        "verification": verification, "planned": 8, "started": len(result_rows),
        "scored": sum(r.get("score") is not None for r in result_rows), "groups": groups,
        "pairs": pairs, "trials": list(reports.values()), "cost": costs, "same_success_supported_subset": matched,
        "analyzer_sources": {str(p): sha(p.read_bytes()) for p in (Path(__file__).resolve(), Path(cost.__file__).resolve(),
            Path(contract.__file__).resolve(), Path(old.__file__).resolve())},
        "limits": ["Known development scene, one repetition per condition/arm; not a generalization or significance claim.",
            "Both arms use compact_tools_v1/search_confirm_v9; completion and input contracts change together.",
            "Independent truth, common public assessment, online candidate result and auxiliary old checker are separate.",
            "Duration is unsupported; report its two slots separately, never add them as ordinary successes.",
            "Recovery requires a new model response after seeing feedback; parallel preselected/program calls do not prove model recovery.",
            "Argument findings, observed rejection, and backend effects are distinct; object duplicate keys may be collapsed upstream.",
            "Token counts are actual response usage; missing coverage is not zero and request bytes are not token estimates.",
            "Conservative cache-miss accounting is not the supplier invoice; no hidden reasoning is exported."]}


def markdown(report):
    text = ["# DeepSeek 悬停与输入契约同期对照", "",
        f"计划 {report['planned']} 槽，启动 {report['started']}，评分 {report['scored']}。" +
        ("本批为本地合成 SDK smoke，不是自然模型结果。" if report["local_smoke"] else "本批为真实模型小规模开发验证。"), "",
        "| 槽 | 正常返回 | 独立真值 | 公开证据完成 | 原辅助 checker | 实际 token | input_json object/string |", "|---|---:|---|---|---|---:|---|"]
    cell = contract.cell
    for r in report["trials"]:
        c = r.get("completion", {}); counts = r.get("input_audit", {}).get("representation_counts", {})
        text.append("| " + " | ".join(map(cell, [r["trial_id"], c.get("normal_agent_return"),
            (c.get("independent_truth") or {}).get("task_completed"), c.get("common_public_assessment", {}).get("task_completed"),
            (c.get("auxiliary_original_checker") or {}).get("passed"), (r.get("model_io") or {}).get("usage", {}).get("total_tokens"),
            f"{counts.get('object', 0)} / {counts.get('string', 0)}" if r["started"] else None])) + " |")
    for label, group in report["groups"].items():
        text += ["", f"## {label}", "", "```json", json.dumps(group, ensure_ascii=False, indent=2), "```"]
    text += ["", "## 两臂同样成功的支持任务子集", "", "仅作配对描述，与完整分母并列；排除时长任务。", "",
             "```json", json.dumps(report["same_success_supported_subset"], ensure_ascii=False, indent=2), "```"]
    for r in report["trials"]:
        if not r["started"]: continue
        text += ["", "## " + r["trial_id"], "", "输入错误：`" + json.dumps(r.get("input_audit", {}).get("feedback_error_counts", {}), ensure_ascii=False) + "`。",
            "恢复：`" + r.get("unknown_recovery", {}).get("status", "unknown") + "`。",
            "公开完成证据：`" + ", ".join(r.get("completion", {}).get("common_public_assessment", {}).get("reasons", [])) + "`。"]
    text += ["", "## 解释边界", ""] + ["- " + line for line in report["limits"]]
    return "\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    require(output != run and not output.is_relative_to(run), "Analysis output must be outside the sealed run")
    if output.exists():
        raise FileExistsError("Existing analyses must not be overwritten")
    report = analyze(run)
    output.mkdir(parents=True)
    (output / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "REPORT_CN.md").write_text(markdown(report))
    print(json.dumps({"valid": report["valid"], "planned": report["planned"], "started": report["started"],
                      "scored": report["scored"], "provider_calls": 0, "output": str(output)}))


if __name__ == "__main__":
    main()
