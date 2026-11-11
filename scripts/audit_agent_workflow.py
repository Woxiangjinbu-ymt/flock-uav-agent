"""Read-only independent evidence audit of sealed general-agent-v2 local runs.

Uses only standard-library evidence parsers, never imports production Agent,
runner, simulator, classifier, SDK, pickle or a live cost ledger. The shared raw
wire parser is identified by hash in the report. No files inside runs are edited.
Synthetic model choices prove contract integration, not autonomous capability.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import analyze_deepseek_behavior_pilot as wire_parser
from analyze_deepseek_behavior_pilot import (
    archive_analysis, canonical, decode, hashes, harness_analysis, parse, read,
    require, rows, safe, sha,
)


SCOPE = "general_agent_v2_execution_contract"
BUDGET = {"token_limit", "http_attempt_limit", "task_deadline"}
APPLIED = {"final_position", "successful_points_count", "successful_points", "completed_waypoints",
           "current_waypoint_index", "arrival_verified", "within_distance", "distance_after",
           "distance_traveled", "applied_distance", "applied_duration", "applied_heading", "applied_altitude"}
REMAINING = {"distance_remaining", "remaining_distance", "remaining_waypoints",
             "unsuccessful_points_count", "unsuccessful_points", "remaining_duration"}


def inventory(root, *, excluded=()):
    require(not any(p.is_symlink() for p in root.rglob("*")), "Symlink in evidence inventory")
    return {str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and p.name not in excluded and "__pycache__" not in p.parts}


def no_private_input(value, canary):
    """Decode actual policy request bytes, including nested JSON strings."""
    if isinstance(value, bytes):
        require(canary.encode() not in value, "Private checker canary in policy input")
        require(not any(term in value for term in (b"Local controlled response loss", b"synthetic_fault", b"after_public_api_return")),
                "Private fault-injection metadata in policy input")
        try:
            value = parse(value)
        except (ValueError, UnicodeError):
            return
    if isinstance(value, str):
        require(canary not in value, "Private checker canary in policy input")
        require(not any(term in value for term in ("Local controlled response loss", "synthetic_fault", "after_public_api_return")),
                "Private fault-injection metadata in policy input")
        try:
            nested = parse(value)
        except (ValueError, UnicodeError):
            return
        if nested != value:
            no_private_input(nested, canary)
    elif isinstance(value, dict):
        require(not any(value.get(k) not in (None, [], {}) for k in ("execution_check_apis", "related_apis")),
                "Private checker definition in policy input")
        for child in value.values():
            no_private_input(child, canary)
    elif isinstance(value, list):
        for child in value:
            no_private_input(child, canary)


def independent_continuation(worker):
    """Recheck evidence predicates; never trusts the reported category alone."""
    if worker is None:
        return False
    limits, agent = worker["limits"], worker["agent"]
    attempts = limits["attempts"]
    require(limits["admitted_http_attempts"] == len(attempts), "Admitted request count mismatch")
    require(limits["delegated_http_attempts"] == sum(a["delegated"] for a in attempts), "Delegated request count mismatch")
    require([a["attempt_id"] for a in attempts] == list(range(1, len(attempts) + 1)), "Attempt identity mismatch")
    unknown = [a["attempt_id"] for a in attempts if a.get("usage_status") == "unknown"]
    require(limits["unknown_usage_attempts"] == len(unknown) and limits["unknown_usage_attempt_ids"] == unknown,
            "Unknown usage count mismatch")
    total = 0
    valid_usage = not unknown
    for attempt in attempts:
        if not attempt["delegated"]:
            continue
        usage = attempt.get("usage")
        valid = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
        valid = valid and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
        if valid:
            total += usage["total_tokens"]
        valid_usage &= valid and attempt.get("usage_status") in {"known", "known_late"}
    require(limits["known_total_tokens"] == total, "Known token count mismatch")
    if not valid_usage or worker["lifecycle"]["cancelled"]:
        return False
    stop = limits["stop_reason"]
    if stop is None and worker["snapshot"]["score_seconds"] == worker["runtime"]["task_seconds"] and (
            worker["elapsed"] >= worker["runtime"]["task_seconds"] and worker["timer_enabled"] is True
            and worker["clock"] == "time.monotonic"):
        stop = "task_deadline"
    if stop in BUDGET:
        return agent.get("error_type") in ({None, "RequestLimitError", "TaskDeadlineReached"}
            if stop == "task_deadline" else {None, "RequestLimitError"})
    return (stop is None and agent["success"] is True and agent.get("error_type") is None
            and agent["archive_verify"]["complete"] is True)


def audit_state(state, ledger, harness, command, command_id):
    require(state["schema"] == "task-execution-state-v1", "Unknown public state schema")
    require(state["command"] == state["obligations"]["original_command"] == command
            and state["command_id"] == command_id, "Original obligation/command binding mismatch")
    require(state["mission_completion"] == "not_evaluated" and state["obligations"]["interpretation_complete"] is False
            and state["obligations"]["unparsed_obligations"]["source_text"] == command,
            "Public state overclaims whole-task interpretation/completion")
    require(state["lifecycle"]["stopped"] is True and state["lifecycle"]["further_actions_allowed"] is False
            and state["recovery"]["global_stop"] is True and state["recovery"]["automatic_retry"] is False,
            "Final state fails closed lifecycle boundary")
    events = ledger.get("events", [])
    require(len({e["evidence_id"] for e in events}) == len(events), "Duplicate ledger evidence ID")
    source = {e["evidence_id"]: e for e in events}
    projected = state["actions"] + state["tool_calls"]
    require(len(projected) == len(events) and {e["evidence_id"] for e in projected} == set(source),
            "Public state missing/invented call evidence")
    for record in projected:
        original = source[record["evidence_id"]]
        require(record["tool_call_id"] == original.get("tool_call_id") and record["tool_name"] == original.get("tool_name")
                and record["requested"] == original.get("args", {}), "Projected call identity/request mismatch")
        require(record["source"]["evidence_id"] == record["evidence_id"]
                and record["source"]["tool_call_id"] == record["tool_call_id"], "Projected source mismatch")
        receipt = original.get("payload")
        receipt_path = record["source"].get("receipt_path", [])
        for field in receipt_path:
            receipt = receipt[field]
        require(record["receipt"] == receipt, "Projected receipt differs from ledger")
        if record["kind"] == "action" and original.get("status") == "unknown":
            require(record["effect_status"] == "unknown", "Historical unknown action certified by projection")
        elif record["kind"] == "action" and isinstance(receipt, dict) and receipt.get("status") in {"error", "partial_success"}:
            require(record["effect_status"] == {"error": "failure", "partial_success": "partial"}[receipt["status"]],
                    "Explicit failure/partial receipt misclassified")
        for field, allowed in (("applied", APPLIED), ("remaining", REMAINING)):
            value = record[field]
            require(value is None or isinstance(value, dict), "Unknown action amounts must stay null")
            if value is not None:
                require(set(value) <= allowed and isinstance(receipt, dict)
                        and all(receipt.get(k) == v for k, v in value.items()), "Invented applied/remaining amount")
        if isinstance(receipt, dict) and any(k in receipt for k in ("tool_exception", "harness_transport_error")):
            require(record["effect_status"] == "unknown", "Lost receipt incorrectly certified")
    frames = {f["plan_id"]: f for f in harness["frames"]}
    require({f["plan_id"] for f in state["frames"]} == set(frames), "Frame projection inventory mismatch")
    for frame in state["frames"]:
        original = frames[frame["plan_id"]]
        require(all(frame[k] == original.get(k) for k in ("skill", "cursor", "status", "reason")), "Frame state changed in projection")
        require(frame["resumable"] is False and frame["mission_completion"] == "not_evaluated", "Closed frame remains resumable")
    require(all(item["automatic_retry"] is False and item["eligible_now"] is False
                and item["recommendation"] == "stop_no_further_actions" for item in state["recovery"]["items"]),
            "Closed state proposes further recovery actions")
    return {"schema": state["schema"], "original_command_retained": True,
        "mission_completion": state["mission_completion"],
        "effect_status_counts": dict(Counter(r["effect_status"] for r in projected)),
        "action_evidence": [{k: a[k] for k in ("evidence_id", "tool_call_id", "tool_name", "effect_status",
            "effect_reason", "applied", "remaining", "source")} for a in state["actions"]],
        "recovery_categories": dict(Counter(i["category"] for i in state["recovery"]["items"])),
        "closed_recovery_advice_is_stop": True,
        "historical_recovery_rate": None, "rate_status": "not_inferred_from_final_snapshot"}


def audit_trial(run, manifest, trial, row):
    ident = trial["trial_id"]
    folder = safe(run, "trials/" + ident)
    worker, score = row["worker"], row["score"]
    processes = row["processes"]
    require(processes and processes[0]["mode"] == "worker", "Missing worker process")
    for process in processes:
        require(process["trial_id"] == ident and process["exited"] is True
                and process["started_ns"] <= process["ended_ns"], "Unjoined/mismatched process")
        require(sha(safe(run, f"process-logs/{ident}-{process['mode']}.log").read_bytes()) == process["log_sha256"],
                "Process log hash mismatch")
    if worker is None:
        require(score is None and len(processes) == 1 and (processes[0]["kill_reason"] is not None
                or processes[0]["returncode"] != 0), "Unsealed worker was scored or has unexplained absence")
        require(row["termination"]["score_eligible"] is False and row["termination"]["batch_action"] == "stop",
                "Unsealed worker classified as eligible")
        require(row["result"]["independent_evaluation"]["passed"] is None, "Unscored worker counted as failed check")
        notice = read(folder, "termination-required.json", optional=True)
        contract = {"checked": False, "passed": None, "reason": "no_fixed_unsealed_probe_expectation"}
        if ident == "undrained":
            limits = read(folder, "private/request-limits.json")
            boundary = read(folder, "private/score-boundary.json")
            public = rows(safe(folder, "policy/worldlogs/public-api.jsonl"))
            checks = {"explicit_drain_failure_notice": bool(notice and notice["pid"] == processes[0]["pid"]
                and notice["trial_id"] == ident and notice["reason"] == "transport_not_drained"),
                "parent_killed_and_joined": processes[0]["kill_reason"] == "transport_not_drained"
                    and processes[0]["returncode"] != 0 and processes[0]["exited"] is True,
                "transport_still_alive_at_drain_boundary": limits["needs_process_termination"] is True and bool(limits["threads_still_alive"]),
                "never_scored_or_reported_complete": not (folder / "worker-result.json").exists()
                    and not (folder / "evaluation/result.json").exists(),
                "no_action_committed_after_freeze": all(a["ended_seconds"] <= boundary["seconds"]
                    for a in public if a["action"] and a["committed"])}
            contract = {"checked": True, "passed": all(checks.values()), "checks": checks}
        return {"trial_id": ident, "task_id": trial["task_id"], "status": "executed_unsealed", "checker_passed": None,
            "termination": row["termination"], "processes": processes, "behavior": None,
            "termination_notice": notice, "contract": contract}
    require(read(folder, "worker-result.json") == worker, "Worker row differs from sealed source")
    hashes(folder, worker["files"])
    hashes(folder, read(run, "worker-exit-seals/" + ident + ".json"))
    require(processes[0]["pid"] == worker["pid"] and processes[0]["returncode"] == 0
            and processes[0]["kill_reason"] is None, "Worker report process mismatch")
    require(worker["profile_sha256"] == manifest["profile_sha256"] and worker["network_attempts"] == []
            and "online_arm" not in worker, "Nonlocal or wrong-profile worker")
    require(worker["runtime"] == trial["runtime"], "Worker runtime drift")
    require(worker["drained"] is True and worker["limits"]["closed"] is True
            and worker["limits"]["needs_process_termination"] is False and worker["limits"]["threads_still_alive"] == []
            and all(v is True for v in worker["isolation"].values()), "Undrained/unsafe worker was sealed")
    require(read(folder, "private/request-limits.json") == worker["limits"], "Request limit source mismatch")
    require(worker["entry"]["frames"] == worker["entry"]["ledger_events"] == worker["entry"]["ledger_model_requests"] == 0,
            "Nonempty Agent entry")
    require(score is not None and len(processes) == 2 and processes[1]["mode"] == "score", "Joined worker not independently scored")
    require(read(folder, "evaluation/result.json") == score, "Score row differs from source")
    hashes(folder, read(run, "scorer-exit-seals/" + ident + ".json"))
    require(score["worker_result_sha256"] == sha(safe(folder, "worker-result.json").read_bytes())
            and score["snapshot_sha256"] == worker["snapshot"]["sha256"] == sha(safe(folder, "private/score-world.pkl").read_bytes()),
            "Scorer snapshot/worker binding mismatch")
    boundary = read(folder, "private/score-boundary.json")
    require(boundary["snapshot_sha256"] == worker["snapshot"]["sha256"]
            and boundary["state_sha256"] == worker["snapshot"]["state_sha256"]
            and boundary["seconds"] == worker["snapshot"]["score_seconds"], "Score boundary source mismatch")
    require(score["pid"] == processes[1]["pid"] and score["worker_pid"] == worker["pid"] != score["pid"]
            and processes[1]["started_ns"] >= processes[0]["ended_ns"] and processes[1]["returncode"] == 0
            and processes[1]["kill_reason"] is None, "Scorer process independence/order mismatch")
    require(score["network_attempts"] == [] and score["agent_imported"] is False and score["private_canary_present"] is True
            and score["checker_tree_matches_original"] is True and score["checker"]["errors"] == [], "Scorer isolation failed")
    require((row["termination"]["batch_action"] == "continue") == independent_continuation(worker)
            and row["termination"]["score_eligible"] is True, "Termination decision disagrees with independent predicates")
    require(row["result"]["independent_evaluation"]["passed"] is score["checker"]["passed"]
            and row["result"]["public_evidence"]["whole_task"] == "not_evaluated", "Result layers conflated")
    fixture = read(run / "inputs", "fixtures/" + trial["task_id"] + ".json")
    require(worker["initial"]["original_state_sha256"] == fixture["state_sha256"]
            and worker["initial"]["public_startup_sha256"] == fixture["public_startup_sha256"]
            and sha(safe(run / "inputs", "fixtures/" + trial["task_id"] + ".pkl").read_bytes()) == fixture["snapshot_sha256"],
            "Initial fixture mismatch")
    ledger, harness = read(folder, "policy/task-state.json"), read(folder, "policy/harness.json")
    state = audit_state(read(folder, "policy/execution-state.json"), ledger, harness, fixture["command"], worker["entry"]["command_id"])
    archive_name = Path(worker["agent"]["archive_info"]["path"]).name
    archive_file = safe(folder, "policy/modelio/" + archive_name)
    wire = archive_analysis(archive_file)
    require(wire["ownership"][0] == worker["entry"]["command_id"] and wire["archive_end"] is not None
            and wire["usage"]["closed_model_calls"] == wire["usage"]["logical_model_calls"]
            and wire["complete"] is worker["agent"]["archive_verify"]["complete"], "Archive ownership/closure mismatch")
    prompts = []
    for record in rows(archive_file):
        if record["kind"] == "http_request":
            raw = decode(record["data"]["body"])
            no_private_input(raw, manifest["private_canary"])
            prompts.append({"sequence": record["sequence"], "recovery_projection_present": b"task-recovery-prompt-v1" in raw})
    public = rows(safe(folder, "policy/worldlogs/public-api.jsonl"))
    physical_file = safe(folder, "policy/worldlogs/physical-events.jsonl")
    physical = rows(physical_file) if physical_file.exists() else []
    require(len(physical) == worker["physical_cost"]["physical_history_events"]
            and (physical_file.exists() or all(not a.get("history_events") for a in public)),
            "Missing/mismatched physical event evidence")
    require(all(a["phase"] == "task" and a["admin"] is False and not a["endpoint"].startswith("/check/") for a in public),
            "Nonpublic API entered policy trace")
    require(all(a["ended_seconds"] <= boundary["seconds"] for a in public if a["action"] and a["committed"])
            and all(e["effect_seconds"] <= boundary["seconds"] for e in physical if e["committed"]),
            "Committed action/effect occurred after score freeze")
    behavior = harness_analysis(harness, wire, public)
    effects = {}
    for event in physical:
        effects.setdefault(event["request_id"], []).append(event)
    actions = [{"request_id": a["request_id"], "tool": a["endpoint"].rsplit("/", 1)[-1],
        "endpoint": a["endpoint"], "origin": a.get("origin"), "params": a["params"], "body": a["body"],
        "response": a.get("response"), "committed": a["committed"], "error": a.get("error"),
        "physical_events": [{"event_id": e["event_id"], "actor": e["actor"], "committed": e["committed"],
            "history": e["history"]} for e in effects.get(a["request_id"], [])]} for a in public if a["action"]]
    contract = case_checks(ident, actions, wire, behavior, public, harness, worker,
                           read(folder, "private/local-api-fault.json", optional=True))
    if contract["checked"]:
        contract["checks"]["fixed_development_task"] = trial["task_id"] == "c00d8b78"
        contract["passed"] = all(contract["checks"].values())
    return {"trial_id": ident, "task_id": trial["task_id"], "status": "executed_sealed", "checker_passed": score["checker"]["passed"],
        "termination": row["termination"], "worker_sha256": sha(safe(folder, "worker-result.json").read_bytes()),
        "scorer_sha256": sha(safe(folder, "evaluation/result.json").read_bytes()), "processes": processes,
        "state": state, "limits": {k: worker["limits"][k] for k in ("stop_reason", "known_total_tokens", "delegated_http_attempts",
            "unknown_usage_attempts", "closed", "needs_process_termination")}, "model_decisions": wire["decisions"],
        "final_messages": wire["final_messages"], "prompt_projection": prompts, "behavior": behavior, "actions": actions,
        "physical_event_count": len(physical), "worker_elapsed_seconds": processes[0]["elapsed_seconds"],
        "local_api_fault": read(folder, "private/local-api-fault.json", optional=True), "contract": contract}


def case_checks(ident, actions, wire, behavior, public, harness, worker, fault):
    """Fixed local probes: test raw receipts + physical effects, not state labels."""
    checks = {}
    tools = [a["tool"] for a in actions]
    decisions = wire["decisions"]
    controls = behavior["controls"]
    responses = [r for c in controls for r in c["responses"]]
    actors = {d["id"] for a in public if a["endpoint"] == "/drones" and isinstance(a.get("response"), list)
              for d in a["response"] if d.get("name") == "Drone 1"}
    if actions:
        checks["original_named_actor"] = len(actors) == 1 and all(a["endpoint"].split("/")[2] in actors for a in actions)
    def successful(action, physical_event):
        return (action["committed"] is True and (action["response"] or {}).get("status") == "success"
            and any(e["committed"] is True and e["history"]["event"] == physical_event
                    and e["actor"] in actors for e in action["physical_events"]))
    def chain(offset=0):
        tail = actions[offset:]
        return len(tail) == 3 and [a["tool"] for a in tail] == ["take_off", "rotate", "hover"] and all(
            successful(a, event) for a, event in zip(tail, ("take_off", "rotate", "hovering"))) and (
            tail[0]["params"] == {"altitude": 15.0} and tail[1]["params"] == {"heading": 96.0}
            and all(e["history"]["position"]["z"] == 15.0 for a in tail for e in a["physical_events"]))
    def execution(decision):
        values = decision["executions"]
        return values[0] if len(values) == 1 and values[0]["source_relation"] == "unique" and values[0]["tool_call_id_mismatch"] is False else {}
    def between_observation(first, last):
        return any(d["name"] == "get_drone_status" and first < d["ordinal"] < last
                   and execution(d).get("public_output", {}).get("fields", {}).get("position", {}).get("z") == 15
                   for d in decisions)
    if ident == "raw_repair":
        checks["explicit_api_failure_before_repair"] = (tools == ["move_to", "take_off", "rotate", "hover"]
            and actions[0]["response"].get("status") == "error" and not actions[0]["physical_events"])
        checks["one_confirmed_raw_chain_after_failure"] = chain(1) and all(a["origin"]["kind"] == "model_tool" for a in actions)
        checks["status_read_after_failure_before_takeoff"] = any(a.get("origin", {}).get("tool") == "get_drone_status"
            and a.get("method") == "GET" and isinstance(a.get("response"), dict)
            and a["response"].get("position", {}).get("z") == 0
            and actions[0]["request_id"] < a["request_id"] < actions[1]["request_id"] for a in public) if len(actions) >= 2 else False
    elif ident == "unknown_after_commit":
        checks["actual_takeoff_once_then_remaining_actions"] = chain()
        checks["explicit_postcommit_fault"] = bool(fault and fault["kind"] == "response_lost" and fault["after_public_api_return"] is True)
        takeoffs = [d for d in decisions if d["name"] == "take_off"]
        checks["caller_received_lost_receipt_not_success"] = len(takeoffs) == 1 and "Request timed out before response was received" in execution(takeoffs[0]).get("public_output", {}).get("text", "")
        rotates = [d for d in decisions if d["name"] == "rotate"]
        checks["observed_altitude_before_continuation"] = len(takeoffs) == len(rotates) == 1 and between_observation(takeoffs[0]["ordinal"], rotates[0]["ordinal"])
    elif ident == "terminal_resume":
        checks["completion_then_same_plan_rejected"] = (len(responses) == 2 and [r["status"] for r in responses] == ["completed", "rejected"]
            and [r["cursor"] for r in responses] == [3, 3] and responses[0]["plan_id"] == responses[1]["plan_id"]
            and responses[1]["reason"] == "plan_not_resumable" and responses[1]["plan_status"] == "completed")
        checks["single_skill_entry_no_replay"] = chain() and behavior["event_counts"].get("skill_enter") == 1
    elif ident == "validation_repair":
        senses = [d for d in decisions if d["name"] == "sense_nearby_entities"]
        checks["actual_schema_error_then_corrected_call"] = (len(senses) == 2 and execution(senses[0]).get("tool_message_status") == "error"
            and "input_json: Field required" in execution(senses[0]).get("public_output", {}).get("text", "")
            and execution(senses[1]).get("tool_message_status") == "success")
        checks["invalid_call_never_reached_world"] = len(senses) == 2 and not any(a.get("origin", {}).get("tool_call_id") == senses[0]["tool_call_id"] for a in public)
        checks["one_actual_raw_chain"] = chain()
    elif ident == "pause_resume":
        checks["real_pauses_and_cursor_progress"] = (len(responses) == 4
            and [r["status"] for r in responses] == ["paused", "paused", "completed", "rejected"]
            and [r["cursor"] for r in responses] == [1, 2, 3, 3]
            and len({r["plan_id"] for r in responses}) == 1 and behavior["opportunity_count"] == 2
            and all(r["resumable"] is True and r["remaining_s"] > 0 for r in responses[:2]))
        checks["two_effective_resumes_then_terminal_rejection"] = (len(behavior["resume_attempts"]) == 3
            and all(len(r["reentered_skill_sequences"]) == 1 for r in behavior["resume_attempts"][:2])
            and behavior["resume_attempts"][2]["reentered_skill_sequences"] == [])
        checks["three_verified_actions_without_replay"] = chain()
    elif ident == "invalidated_raw":
        checks["paused_then_invalidated_resume_rejected"] = (len(responses) == 2
            and [r["status"] for r in responses] == ["paused", "rejected"]
            and responses[1]["plan_status"] == "needs_replan" and responses[1]["plan_reason"] == "external_action_changed_dependencies"
            and responses[1]["cursor"] == 1 and behavior["event_counts"].get("plan_invalidated") == 1)
        checks["raw_completion_keeps_committed_takeoff"] = chain() and [a["origin"]["kind"] for a in actions] == ["skill", "model_tool", "model_tool"]
        hover = [d for d in decisions if d["name"] == "hover"]
        checks["raw_hover_after_rejection"] = len(hover) == 1 and len(controls) == 2 and hover[0]["ordinal"] > controls[1]["decision_ordinal"]
        checks["resume_did_not_reenter_skill"] = len(behavior["resume_attempts"]) == 1 and behavior["resume_attempts"][0]["reentered_skill_sequences"] == []
    elif ident in {"token_stop", "cancel", "timeout", "deadline"}:
        expected = {"token_stop": "token_limit", "cancel": "cancelled", "timeout": "request_timeout", "deadline": "task_deadline"}[ident]
        checks["expected_request_stop"] = worker["limits"]["stop_reason"] == expected
        checks["only_pre_stop_takeoff_committed"] = len(actions) == 1 and tools == ["take_off"] and successful(actions[0], "take_off")
        checks["no_late_rotation_or_hover"] = all(a["tool"] not in {"rotate", "hover"} for a in actions)
        if ident == "token_stop":
            checks["known_budget_not_unknown_usage"] = worker["limits"]["known_total_tokens"] == 52 and worker["limits"]["unknown_usage_attempts"] == 0
    elif ident == "unknown_usage":
        checks["unknown_usage_rejected_before_action"] = worker["limits"]["stop_reason"] == "unknown_usage" and worker["limits"]["unknown_usage_attempts"] == 1 and actions == []
        checks["model_call_not_delivered_as_valid_choice"] = all(not a["delivered_to_sdk"] for a in worker["limits"]["attempts"])
    elif ident == "after_budget":
        checks["next_authorized_slot_actually_returned"] = worker["agent"]["success"] is True and worker["limits"]["known_total_tokens"] == 26 and bool(wire["final_messages"])
        checks["no_invented_action_success"] = actions == []
    else:
        return {"checked": False, "passed": None, "reason": "no_fixed_probe_expectation_for_this_trial_id"}
    return {"checked": True, "passed": all(checks.values()), "checks": checks,
            "evidence": "raw model output/tool spans + public API receipts + physical history; state projection is not the action oracle"}


def audit_run(run):
    run = Path(run).resolve()
    manifest = read(run, "manifest.json")
    require(sha(safe(run, "manifest.json").read_bytes()) == safe(run, "manifest.sha256").read_text().strip(), "Manifest hash mismatch")
    seal = read(run, "campaign-seal.json")["files"]
    hashes(run, seal)
    require(set(seal) == inventory(run, excluded={"campaign-seal.json"}), "Campaign seal inventory mismatch")
    require(manifest["scope"] == SCOPE and type(manifest["online_authorized_executions"]) is int
            and manifest["online_authorized_executions"] == 0 and manifest["local_model"] == "local-scripted-sdk", "Outside local v2 scope")
    profile = manifest["agent_profile"]
    require(profile["schema_version"] == 2 and profile["profile_id"] == "general-agent-v2-local"
            and profile["agent"]["execution_contract"] == "task_execution_state_v1"
            and manifest["profile_sha256"] == sha(canonical(profile)), "Profile identity/hash mismatch")
    require(profile["scope"] == "local_mock_only" and profile["online_authorized_executions"] == 0
            and profile["execution"]["transport"] == "mock", "Nonlocal profile")
    hashes(run / "source", manifest["sources"])
    hashes(run / "inputs", manifest["inputs"])
    require(inventory(run / "source") == set(manifest["sources"]) and inventory(run / "inputs") == set(manifest["inputs"]),
            "Frozen source/input inventory mismatch")
    require(sha(safe(run, "PROTOCOL_CN.md").read_bytes()) == manifest["protocol_sha256"], "Protocol hash mismatch")
    trials, results, summary = manifest["trials"], read(run, "results.json"), read(run, "summary.json")
    require(0 < len(results) <= len(trials) and [r["trial_id"] for r in results] == [t["trial_id"] for t in trials[:len(results)]],
            "Executed rows are not a nonempty prepared prefix")
    require(len({t["trial_id"] for t in trials}) == len(trials), "Duplicate prepared slot")
    for trial in trials:
        require(trial["runtime"] == profile["runtime"] and trial["scheduler"] == profile["harness"]["scheduler"]
                and trial["watchdog_seconds"] == profile["execution"]["watchdog_seconds"], "Per-slot profile drift")
    require(summary["schema_version"] == 2 and summary["profile_sha256"] == manifest["profile_sha256"]
            and summary["planned_trials"] == len(trials) and summary["worker_count"] == len(results)
            and summary["scorer_count"] == sum(r["score"] is not None for r in results)
            and summary["unstarted_trials"] == [t["trial_id"] for t in trials[len(results):]]
            and summary["provider_calls"] == 0 and summary["online_ready"] is False, "Summary denominator/scope mismatch")
    expected_status = "stopped" if results[-1]["termination"]["batch_action"] == "stop" else "completed"
    require(summary["status"] == expected_status and all(r["termination"]["batch_action"] == "continue" for r in results[:-1]),
            "Campaign continued after a stop or misreported completion")
    require(len(results) == len(trials) or expected_status == "stopped", "Unexplained unstarted slots")
    processes = [p for r in results for p in r["processes"]]
    journal = rows(safe(run, "process-ledger.jsonl"))
    expected_journal = []
    for process in processes:
        expected_journal += [{"event": "start", **{k: process[k] for k in ("mode", "trial_id", "pid", "started_ns")}},
                             {"event": "exit", **process}]
    require(journal == expected_journal, "Process start/exit journal mismatch")
    require(all(b["started_ns"] >= a["ended_ns"] for a, b in zip(processes, processes[1:])), "Overlapping child processes")
    reports = [audit_trial(run, manifest, trial, row) for trial, row in zip(trials, results)]
    for trial in trials[len(results):]:
        ident = trial["trial_id"]
        require(not safe(run, "trials/" + ident).exists()
                and not safe(run, "process-logs/" + ident + "-worker.log").exists(), "Unstarted slot has runtime artifacts")
        reports.append({"trial_id": ident, "task_id": trial["task_id"], "status": "not_run", "checker_passed": None,
                        "behavior": None, "actions": None, "contract": None})
    contracts = [r["contract"] for r in reports if r["status"] != "not_run"]
    return {"run": str(run), "valid": True,
        "contract_passed": all(c["checked"] is True and c["passed"] is True for c in contracts),
        "scope": "scripted_real_agent_sdk_local_asgi_contract_only",
        "campaign_seal_sha256": sha(safe(run, "campaign-seal.json").read_bytes()),
        "manifest_sha256": sha(safe(run, "manifest.json").read_bytes()), "profile_sha256": manifest["profile_sha256"],
        "sealed_files": len(seal), "scheduler": profile["harness"]["scheduler"], "planned_trials": len(trials),
        "executed_trials": len(results), "scored_trials": sum(r["score"] is not None for r in results),
        "not_run_trials": len(trials) - len(results), "status": summary["status"], "provider_calls": 0, "trials": reports}


def audit(runs):
    reports = []
    for run in runs:
        try:
            reports.append(audit_run(run))
        except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
            reports.append({"run": str(Path(run).resolve()), "valid": False,
                            "error": type(exc).__name__ + ": " + str(exc), "trials": None})
    return {"schema_version": 1, "valid": all(r["valid"] for r in reports),
        "contract_passed": all(r.get("contract_passed") is True for r in reports),
        "auditor_sha256": sha(Path(__file__).read_bytes()), "wire_parser_sha256": sha(Path(wire_parser.__file__).read_bytes()),
        "provider_calls_by_auditor": 0, "limitations": ["known development fixtures and scripted model outputs",
            "not natural-model autonomy, generalization, or statistical significance",
            "no rate inferred when an opportunity was not observed", "unstarted is distinct from failure",
            "checker result is distinct from public evidence and Agent return"], "runs": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if any(output.is_relative_to(run.resolve()) for run in args.run):
        parser.error("Output must be outside every original run")
    if output.exists():
        parser.error("Output must be a new file")
    report = audit(args.run)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"valid": report["valid"], "contract_passed": report["contract_passed"],
                     "runs": len(report["runs"]), "output": str(output)}, ensure_ascii=False))
    raise SystemExit(0 if report["valid"] and report["contract_passed"] else 1)


if __name__ == "__main__":
    main()
