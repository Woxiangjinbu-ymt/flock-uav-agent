"""Read-only audit of the sealed eight-slot real-model execution-contract pilot.

No runner, simulator, SDK, pickle or live ledger is imported. The previous
analyzer supplies versioned byte/archive primitives only; both source hashes
are recorded. An absent event is not a successful recovery opportunity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import json
import math
from pathlib import Path

import analyze_deepseek_behavior_pilot as old

read, safe, sha, rows, require = old.read, old.safe, old.sha, old.rows, old.require
CONDITIONS = (
    ("short_chain", "c74d7020", "fb8971ec", ("baseline", "integrated")),
    ("navigation", "6c3f98ce", "5aabad33", ("integrated", "baseline")),
    ("search", "fdd21809", "be5d648b", ("baseline", "integrated")),
    ("unknown_takeoff", "c74d7020", "fb8971ec", ("integrated", "baseline")),
)
SOURCE_MANIFEST = "6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae"
LEAK_MARKERS = ("synthetic_fault", "after_public_api_return", "Local controlled response loss")
RUNTIME = {"task_seconds": 500, "drain_seconds": 5.0, "request_limits": {
    "max_http_attempts": 12, "max_total_tokens": 200000, "max_output_tokens": 8192,
    "per_request_timeout_seconds": 60.0}}


def expected_trials():
    return [(condition + "-" + arm, condition, task, scene, arm)
            for condition, task, scene, arms in CONDITIONS for arm in arms]


def verify_run(run):
    manifest = read(run, "manifest.json")
    require(manifest.get("schema_version") == 2 and manifest.get("scope") == "deepseek_contract_pilot_v2",
            "Not the eight-slot execution-contract pilot")
    require(type(manifest.get("local_smoke")) is bool, "Missing evidence-kind boundary")
    require(manifest.get("online_contract") == "task_execution_state_v1", "Wrong execution contract")
    require(old.canonical(manifest.get("online_common_runtime")) == old.canonical(RUNTIME), "Changed common runtime budget")
    for key, value in {"model": "deepseek-flash", "thinking": "enabled", "reasoning_effort": "high",
                       "sdk_retries": 0, "batch_budget": "2.00", "trial_budget": "0.50",
                       "input_per_million": "0.3", "output_per_million": "1.2"}.items():
        require(old.canonical(manifest.get("online_pilot", {}).get(key)) == old.canonical(value), "Changed provider/budget contract: " + key)
    expected = expected_trials()
    require([(t["trial_id"], t["condition"], t["task_id"], t["arm"]) for t in manifest["trials"]]
            == [(ident, cond, task, arm) for ident, cond, task, _, arm in expected], "Changed eight-slot order/denominator")
    require(sha(safe(run, "manifest.json").read_bytes()) == safe(run, "manifest.sha256").read_text().strip(),
            "Manifest hash mismatch")
    seal = read(run, "campaign-seal.json")["files"]
    old.hashes(run, seal)
    inventory = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file()
                 and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    require(inventory == set(seal), "Campaign inventory changed")
    old.hashes(run / "source", manifest["sources"])
    old.hashes(run / "inputs", manifest["inputs"])
    require(sha(safe(run, "PROTOCOL_CN.md").read_bytes()) == manifest["protocol_sha256"], "Protocol changed")
    require(sha(safe(run, "inputs/origin-manifest.json").read_bytes()) == SOURCE_MANIFEST,
            "Development source differs from fixed external anchor")
    origin = read(run, "inputs/origin-manifest.json")
    results = read(run, "results.json")
    require(len(results) <= len(expected) and [r["trial_id"] for r in results]
            == [e[0] for e in expected[:len(results)]], "Results must preserve the fixed trial prefix")
    summary = read(run, "summary.json")
    require(summary.get("planned_trials") == 8 and summary.get("started_trials") == len(results)
            and summary.get("scored_trials") == sum(r.get("score") is not None for r in results)
            and summary.get("unstarted_trials") == [e[0] for e in expected[len(results):]],
            "Summary changed planned/started/scored/unrun denominator")
    if len(results) < 8:
        require(summary.get("status") == "stopped" and bool(summary.get("reason")), "Unexplained unrun slots")
    verification = {"manifest_sha256": sha(safe(run, "manifest.json").read_bytes()),
        "campaign_seal_sha256": sha(safe(run, "campaign-seal.json").read_bytes()),
        "campaign_files": len(seal), "source_manifest_sha256": SOURCE_MANIFEST, "trials": {}}
    for trial, (_, condition, task, scene, arm) in zip(manifest["trials"], expected):
        require(old.canonical(trial.get("runtime")) == old.canonical(RUNTIME)
                and type(trial.get("watchdog_seconds")) is int and trial["watchdog_seconds"] == 515, "Changed trial budget")
        require(trial["task_progress_mode"] == ("off" if arm == "baseline" else "evidence_harness")
                and trial["harness_revision"] == ("none" if arm == "baseline" else "observed_target_v7")
                and trial["scheduler"] == "applicability", "Changed arm capabilities")
        fixture = read(run / "inputs", "fixtures/" + task + ".json")
        for name in ("fixtures/" + task + ".json", "fixtures/" + task + ".pkl", "fixtures/" + fixture["timeline_file"]):
            require(sha(safe(run / "inputs", name).read_bytes()) == origin["files"][name], "Input differs from anchored development origin: " + name)
        require(fixture["task_id"] == task and fixture["scene_id"] == scene and fixture["split"] == "development"
                and sha(fixture["command"].encode()) == fixture["command_sha256"], "Fixture identity/command changed")
        if condition == "unknown_takeoff":
            require(trial.get("api_fault") == {"kind": "response_lost_after_success", "method": "POST",
                    "endpoint": "/drones/3d229034/command/take_off", "occurrence": 1}, "Changed semantic fault condition")
        else:
            require(trial.get("api_fault") is None, "Undeclared fault condition")
    for trial, result in zip(manifest["trials"], results):
        ident = trial["trial_id"]
        folder = safe(run, "trials/" + ident)
        worker = read(folder, "worker-result.json", optional=True)
        score = read(folder, "evaluation/result.json", optional=True)
        require(result.get("worker") == worker and result.get("score") == score,
                "Result differs from worker/scorer artifact: " + ident)
        check = {"worker_present": worker is not None, "score_present": score is not None}
        fixture = read(run / "inputs", "fixtures/" + trial["task_id"] + ".json")
        require(all(result.get(k) == trial[k] for k in ("task_id", "arm", "condition")), "Result trial identity mismatch")
        if result is not results[-1]:
            require(result.get("termination", {}).get("batch_action") == "continue", "Execution after declared batch stop")
        for process in result.get("processes", []):
            require(process["log_sha256"] == sha(safe(run, "process-logs/" + ident + "-" + process["mode"] + ".log").read_bytes()),
                    "Process log hash mismatch")
        if worker:
            require(worker["trial_id"] == ident and worker["task_id"] == trial["task_id"], "Wrong worker identity")
            old.hashes(folder, worker["files"])
            old.hashes(folder, read(run, "worker-exit-seals/" + ident + ".json"))
            require(worker["initial"]["original_state_sha256"] == fixture["state_sha256"] and
                    worker["initial"]["public_startup_sha256"] == fixture["public_startup_sha256"], "Initial state mismatch")
            require(worker.get("entry", {}).get("frames") == 0 and worker.get("entry", {}).get("ledger_events") == 0,
                    "Preseeded plan/evidence at entry")
            check["worker_hashes_verified"] = True
        if score:
            require(worker is not None and worker.get("drained") is True, "Scored missing/undrained worker")
            old.hashes(folder, read(run, "scorer-exit-seals/" + ident + ".json"))
            require(score["trial_id"] == ident and score["worker_result_sha256"] == sha(safe(folder, "worker-result.json").read_bytes()),
                    "Score/worker association mismatch")
            require(score["snapshot_sha256"] == worker["snapshot"]["sha256"]
                    == sha(safe(folder, worker["snapshot"]["path"]).read_bytes()), "Scored snapshot mismatch")
            require(score["worker_pid"] == worker["pid"] and score["pid"] != worker["pid"]
                    and score.get("agent_imported") is False, "Scorer not isolated")
            require(score.get("checker_tree_matches_original") is True and score.get("private_canary_present") is True,
                    "Scorer checker/identity mismatch")
            processes = result.get("processes", [])
            require([p["mode"] for p in processes] == ["worker", "score"]
                    and [p["pid"] for p in processes] == [worker["pid"], score["pid"]]
                    and all(p.get("exited") is True and p.get("returncode") == 0 and p.get("kill_reason") is None for p in processes),
                    "Scored process exit association mismatch")
            check["scorer_hashes_and_snapshot_verified"] = True
        verification["trials"][ident] = check
    return manifest, results, verification


def request_views(archive):
    """Keep actual request bytes available locally; export only selected feedback facts."""
    views = []
    for row in rows(archive):
        if row["kind"] != "http_request":
            continue
        raw = old.decode(row["data"]["body"])
        body = old.parse(raw)
        require(body.get("model") == "deepseek-flash" and body.get("thinking") == {"type": "enabled"}
                and body.get("reasoning_effort") == "high" and body.get("max_tokens") == 8192,
                "Actual model request differs from frozen model/thinking/output contract")
        rendered = json.dumps(old.clean(body.get("messages", [])), ensure_ascii=False)
        complete_input = json.dumps(body.get("messages", []), ensure_ascii=False)
        leaks = [marker for marker in LEAK_MARKERS if marker in complete_input]
        require(not leaks, "Private fault identity leaked to model wire: " + ",".join(leaks))
        views.append({"sequence": row["sequence"], "logical_call_id": row["data"]["logical_call_id"],
            "attempt_id": row["data"]["attempt_id"], "body_sha256": sha(raw), "body": body,
            "has_execution_projection": "task-recovery-prompt-v1" in rendered,
            "has_unknown_effect_text": "unknown" in rendered or "timed out before response was received" in rendered})
    return views


def verify_world_logs(public, physical, worker):
    """A zero-event sidecar may be absent; public history and worker counts must agree."""
    request_ids = [r["request_id"] for r in public]
    require(len(request_ids) == len(set(request_ids)), "Duplicate world request ID")
    expected = {(r["request_id"], index): (r, event) for r in public
                for index, event in enumerate(r.get("history_events", []))}
    seen = set()
    for event in physical:
        parts = str(event.get("event_id")).split(":")
        require(len(parts) == 2 and all(p.isdigit() for p in parts), "Invalid physical event ID")
        key = tuple(map(int, parts))
        require(key in expected and key not in seen, "Missing/duplicate physical-public event association")
        request, original = expected[key]
        require(event["request_id"] == request["request_id"] and event["committed"] == request["committed"]
                and event["origin"] == request["origin"] and event["actor"] == original["actor"]
                and event["history"] == original["history"], "Physical event differs from public transaction history")
        seen.add(key)
    require(seen == set(expected), "Physical sidecar missing public transaction events")
    if worker is not None:
        cost = worker.get("physical_cost") or {}
        task_requests = [r for r in public if r.get("phase") == "task" and not r.get("admin")]
        for key, actual in {"api_requests": len(task_requests), "action_attempts": sum(bool(r.get("action")) for r in task_requests),
                            "physical_history_events": sum(e.get("phase") == "task" and e.get("committed") is True for e in physical)}.items():
            require(cost.get(key) == actual, "Worker/world count mismatch: " + key)
    return {"public_requests": len(public), "physical_events": len(physical),
            "public_history_and_sidecar_verified": True, "zero_events_verified": not expected}


def point_distance(point, target):
    if not isinstance(point, dict) or any(type(point.get(k)) not in (int, float) for k in ("x", "y", "z")):
        return None
    return math.sqrt(sum((point[k] - value) ** 2 for k, value in zip(("x", "y", "z"), target)))


def obligations(world, public, fixture, checker, logs_present):
    task = fixture["task_id"]
    actor = {"c74d7020": "3d229034", "6c3f98ce": "87c81cad"}.get(task)
    actions = world["actions"]
    effect = lambda a, name: a["committed"] is True and a["status"] == "success" and any(
        e["actor"] == actor and e["committed"] is True and e["event"] == name for e in a["physical_events"])
    hovers = [a for a in actions if a["actor_id"] == actor and a["tool"] == "hover" and effect(a, "hovering")]
    takeoffs = [a for a in actions if a["actor_id"] == actor and a["tool"] == "take_off" and effect(a, "take_off")]
    result = {"actor_id": actor, "hover_request_ids": [a["request_id"] for a in hovers],
        "takeoff_request_ids": [a["request_id"] for a in takeoffs], "logs_present": logs_present,
        "hover_duration_verified": False, "scope": "fixed_action_and_order_audit_not_general_language_verifier"}
    if task in {"c74d7020", "6c3f98ce"}:
        altitude = 12 if task == "c74d7020" else 14
        target = (454, 380, 14) if task == "6c3f98ce" else None
        reached = [a for a in actions if a["actor_id"] == actor and a["committed"] is True
                   and any(e["committed"] is True and e["actor"] == actor and
                           (point_distance(e["position"], target) is not None and point_distance(e["position"], target) <= 3)
                           for e in a["physical_events"])] if target else takeoffs
        good = [h for h in hovers if any(t["request_id"] < h["request_id"] for t in takeoffs)
                and any(r["request_id"] <= h["request_id"] for r in reached)
                and any(isinstance(e.get("position"), dict) and abs(e["position"].get("z", -999) - altitude) <= 2
                        for e in h["physical_events"])]
        observed = bool(good) if logs_present else None
        result.update(hover_effect_confirmed=bool(hovers) if logs_present else None,
            required_action_order_confirmed=observed, position_evidence_request_ids=[a["request_id"] for a in reached],
            additional_obligation_check=observed)
    else:
        progress = []
        for row in public:
            response = row.get("response")
            if not isinstance(response, dict):
                continue
            candidates = [response, response.get("task_progress"), response.get("statistics", {}).get("task_progress")
                          if isinstance(response.get("statistics"), dict) else None]
            for value in candidates:
                if isinstance(value, dict) and type(value.get("progress_percentage")) in (int, float):
                    progress.append({"request_id": row["request_id"], "progress_percentage": value["progress_percentage"]})
        strict = progress[-1]["progress_percentage"] > 95 if progress else None
        result.update(public_progress_observations=progress, strict_progress_over_95=strict,
                      additional_obligation_check=strict)
    extra = result["additional_obligation_check"]
    result["checker_and_additional_obligation_passed"] = False if checker is False or extra is False else (
        True if checker is True and extra is True else None)
    return result


def decision_for_action(action, decisions):
    origin = action.get("origin") or {}
    if origin.get("kind") == "model_tool":
        return [d for d in decisions if d["tool_call_id"] == origin.get("tool_call_id")]
    if origin.get("kind") == "skill":
        return [d for d in decisions if d["name"] == "run_harness" and any(
            e.get("control_response", {}).get("plan_id") == origin.get("plan_id") for e in d["executions"])]
    return []


def unknown_recovery(probe, world, wire, requests, obligation, public):
    """Require new model input/decision, excluding actions preselected in a parallel batch."""
    result = {"configured": probe is not None, "triggered": bool(probe and probe.get("triggered")),
        "opportunities": 0, "recovery_rate": None, "status": "not_estimable_no_trigger",
        "scope": "observed_fixed_takeoff_loss_and_remaining_hover_only"}
    if not result["triggered"]:
        return result
    fault = probe["fault_record"]
    actual = [a for a in world["actions"] if a["request_id"] == fault["world_request_id"]]
    require(len(actual) == 1, "Fault has no unique public action association")
    action = actual[0]
    require(action["tool"] == "take_off" and action["actor_id"] == fault["actor"]
            and action["status"] == "success" and action["committed"] is True
            and any(e["event"] == "take_off" and e["actor"] == fault["actor"] and e["committed"] is True
                    for e in action["physical_events"]), "Fault lacks actual application evidence")
    decisions = (wire or {}).get("decisions", [])
    candidates = decision_for_action(action, decisions)
    # For a resumed plan, the unknown exit response identifies this particular control call.
    if len(candidates) > 1:
        candidates = [d for d in candidates if any("unknown_action_effect" in str(e.get("control_response", {}).get("reason"))
                                                  for e in d["executions"])]
    require(len(candidates) == 1, "Fault cannot be uniquely linked to a model tool/control decision")
    cause = candidates[0]
    ends = [e["end_sequence"] for e in cause["executions"] if e.get("end_sequence") is not None]
    require(len(ends) == 1, "Fault tool completion is missing/ambiguous")
    following = [r for r in requests if r["sequence"] > ends[0]]
    result.update(world_request_id=action["request_id"], actor_id=fault["actor"],
        original_tool_call_id=cause["tool_call_id"], original_decision_ordinal=cause["ordinal"],
        original_tool_end_sequence=ends[0], next_request=None,
        actual_applied_takeoff_event_count=sum(e["event"] == "take_off" and e["actor"] == fault["actor"] and e["committed"] is True
            for a in world["actions"] for e in a["physical_events"]))
    if not following:
        result["status"] = "not_estimable_no_following_model_input"
        return result
    next_request = following[0]
    messages = next_request["body"].get("messages", [])
    feedback = [m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == cause["tool_call_id"]]
    marker = lambda m: any(s in json.dumps(m.get("content"), ensure_ascii=False) for s in
                          ("timed out before response was received", "unknown_action_effect"))
    seen = len(feedback) == 1 and marker(feedback[0])
    response = [r for r in (wire or {}).get("responses", []) if r["attempt_id"] == next_request["attempt_id"]]
    returned = (len(response) == 1 and response[0].get("status_code") == 200 and response[0].get("response_closed") is True
                and any(d["logical_call_id"] == next_request["logical_call_id"] for d in
                        decisions + (wire or {}).get("final_messages", [])))
    result["next_request"] = {k: next_request[k] for k in
        ("sequence", "logical_call_id", "attempt_id", "body_sha256", "has_execution_projection", "has_unknown_effect_text")}
    result["next_request"].update(fault_feedback_present=seen, actual_model_decision_returned=returned,
        matched_feedback=[old.excerpt(m.get("content"), 1500) for m in feedback])
    new = [d for d in decisions if d["request_sequence"] >= next_request["sequence"]]
    result["subsequent_model_decisions"] = [{k: d[k] for k in ("ordinal", "tool_call_id", "logical_call_id", "name", "arguments", "request_sequence")}
                                            for d in new]
    new_ids = {d["tool_call_id"] for d in new}
    new_actions, preselected = [], []
    for later in world["actions"]:
        if later["request_id"] <= action["request_id"]:
            continue
        linked = decision_for_action(later, decisions)
        if len(linked) != 1:
            continue
        (new_actions if linked[0]["tool_call_id"] in new_ids else preselected).append(later)
    hovers = set(obligation["hover_request_ids"])
    pre_hover = [a["request_id"] for a in world["actions"] if a["request_id"] in hovers
                 and any(d["request_sequence"] < next_request["sequence"] and any(
                     e.get("end_sequence") is not None and e["end_sequence"] < next_request["sequence"]
                     for e in d["executions"]) for d in decision_for_action(a, decisions))]
    remaining = not pre_hover
    result.update(remaining_hover_when_feedback_received=remaining,
        hover_already_completed_request_ids=pre_hover,
        preselected_followup_action_request_ids=[a["request_id"] for a in preselected],
        new_model_action_request_ids=[a["request_id"] for a in new_actions],
        new_model_takeoff_replay_attempt_ids=[a["request_id"] for a in new_actions
                                            if a["tool"] == "take_off" and a["actor_id"] == fault["actor"]],
        new_model_hover_effect_request_ids=[a["request_id"] for a in new_actions if a["request_id"] in hovers],
        program_status_query_request_ids=[r["request_id"] for r in public if r["request_id"] > action["request_id"]
            and r.get("origin", {}).get("kind") == "skill" and r.get("endpoint") == "/drones/" + fault["actor"]],
        new_model_status_query_call_ids=[d["tool_call_id"] for d in new if d["name"] == "get_drone_status"
                                       and isinstance(d["arguments"], dict) and d["arguments"].get("drone_id") == fault["actor"]])
    result["opportunities"] = int(seen and remaining and returned)
    if result["opportunities"]:
        success = (bool(result["new_model_hover_effect_request_ids"])
                   and not result["new_model_takeoff_replay_attempt_ids"]
                   and result["actual_applied_takeoff_event_count"] == 1
                   and obligation["checker_and_additional_obligation_passed"] is True)
        result.update(recovery_rate=int(success), status="observed_remaining_hover_recovery" if success else "opportunity_without_verified_recovery")
    else:
        result["status"] = ("not_estimable_fault_feedback_not_observed" if not seen else
            "not_estimable_no_remaining_hover" if not remaining else "not_estimable_no_returned_model_decision")
    return result


def analyze(run):
    run = Path(run).resolve()
    manifest, results, verification = verify_run(run)
    reports = {}
    for trial in manifest["trials"]:
        ident = trial["trial_id"]
        result = next((r for r in results if r["trial_id"] == ident), None)
        fixture = read(run / "inputs", "fixtures/" + trial["task_id"] + ".json")
        report = {"trial_id": ident, "condition": trial["condition"], "task_id": trial["task_id"],
            "scene_id": fixture["scene_id"], "arm": trial["arm"], "command": fixture["command"],
            "started": result is not None, "status": "not_run", "checker_passed": None}
        reports[ident] = report
        if result is None:
            continue
        folder = safe(run, "trials/" + ident)
        worker, score = result.get("worker"), result.get("score")
        report.update(status="scored" if score else "started_incomplete", result_layers=old.clean(result.get("result")),
            termination=old.clean(result.get("termination")), cost_snapshot=old.clean(result.get("cost_snapshot")),
            worker_elapsed_seconds=worker.get("elapsed") if worker else None,
            checker_passed=score.get("checker", {}).get("passed") if score else None,
            checker_counts={k: score["checker"].get(k) for k in ("checks_passed", "checks_expected", "errors")} if score else None,
            request_limits=old.clean(worker.get("limits")) if worker else read(folder, "private/request-limits.json", optional=True))
        archives = list(safe(folder, "policy/modelio").glob("*.jsonl"))
        require(len(archives) <= 1, "Multiple model archives")
        wire = old.archive_analysis(archives[0]) if archives else None
        requests = request_views(archives[0]) if archives else []
        if wire:
            limits = report.get("request_limits") or {}
            wire["usage"].update(admitted_http_attempts=limits.get("admitted_http_attempts"),
                delegated_http_attempts=limits.get("delegated_http_attempts"))
            if type(limits.get("known_total_tokens")) is int:
                require(limits["known_total_tokens"] == wire["usage"]["total_tokens"], "Limit/wire usage mismatch")
            wire["request_contracts"] = [{"sequence": r["sequence"], "body_sha256": r["body_sha256"],
                "model": r["body"].get("model"), "thinking": r["body"].get("thinking"),
                "reasoning_effort": r["body"].get("reasoning_effort"), "max_tokens": r["body"].get("max_tokens"),
                "has_execution_projection": r["has_execution_projection"]} for r in requests]
            wire["tool_feedback_errors"] = [{"tool_call_id": d["tool_call_id"], "name": d["name"],
                "execution": e} for d in wire["decisions"] for e in d["executions"] if
                e.get("tool_message_status") == "error" or e.get("error_type") or
                (e.get("public_output", {}).get("kind") == "text" and e["public_output"].get("text", "").startswith("Error"))]
        report["model_io"] = wire
        public_path, physical_path = safe(folder, "policy/worldlogs/public-api.jsonl"), safe(folder, "policy/worldlogs/physical-events.jsonl")
        public = rows(public_path) if public_path.exists() else []
        physical = rows(physical_path) if physical_path.exists() else []
        world_verification = verify_world_logs(public, physical, worker)
        world = old.world_analysis(public, physical, fixture)
        world.pop("hover_audit", None)  # The v1 helper has different frozen actor IDs/tasks.
        world["verification"] = world_verification
        world["worker_physical_cost"] = old.clean(worker.get("physical_cost")) if worker else None
        report["world"] = world
        complete_logs = public_path.exists() and (physical_path.exists() or world_verification["zero_events_verified"])
        report["obligations"] = obligations(world, public, fixture, report["checker_passed"], complete_logs)
        report["harness"] = old.harness_analysis(read(folder, "policy/harness.json", optional=True), wire, public)
        state = read(folder, "policy/execution-state.json", optional=True)
        report["execution_state"] = {"present": state is not None,
            "sha256": sha(safe(folder, "policy/execution-state.json").read_bytes()) if state is not None else None,
            "schema": state.get("schema") if state else None,
            "wire_requests_with_projection": sum(r["has_execution_projection"] for r in requests),
            "interpretation": "artifact_is_not_proof_the_model_received_it; wire_presence_reported_separately"}
        probe = read(folder, "private/online-api-fault.json", optional=True)
        report["unknown_recovery"] = unknown_recovery(probe, world, wire, requests, report["obligations"], public)
    costs = old.cost_analysis(run, reports)
    pairs = []
    for condition, task, scene, _ in CONDITIONS:
        b, i = reports[condition + "-baseline"], reports[condition + "-integrated"]
        complete = type(b["checker_passed"]) is bool and type(i["checker_passed"]) is bool
        pair = {"condition": condition, "task_id": task, "scene_id": scene, "complete_pair": complete,
            "baseline_passed": b["checker_passed"], "integrated_passed": i["checker_passed"], "differences_integrated_minus_baseline": None}
        if complete:
            metrics = {"checker_passed": lambda r: int(r["checker_passed"]),
                "known_total_tokens": lambda r: r["model_io"]["usage"]["total_tokens"],
                "delegated_http_attempts": lambda r: r["model_io"]["usage"]["delegated_http_attempts"],
                "action_attempts": lambda r: r["world"]["action_attempts"],
                "api_requests": lambda r: r["world"]["api_requests"],
                "worker_elapsed_seconds": lambda r: r["worker_elapsed_seconds"],
                "tool_feedback_errors": lambda r: len(r["model_io"]["tool_feedback_errors"]),
                "action_non_success_receipts": lambda r: sum(a["status"] != "success" for a in r["world"]["actions"])}
            pair["differences_integrated_minus_baseline"] = {k: f(i) - f(b) if f(i) is not None and f(b) is not None else None for k, f in metrics.items()}
            pair["differences_integrated_minus_baseline"]["conservative_usd"] = str(
                Decimal(i["cost"]["settled_conservative_charge"]) - Decimal(b["cost"]["settled_conservative_charge"]))
        pairs.append(pair)
    grouped = {}
    for arm in ("baseline", "integrated"):
        group = [r for r in reports.values() if r["arm"] == arm]
        scored = [r for r in group if type(r["checker_passed"]) is bool]
        grouped[arm] = {"planned": len(group), "started": sum(r["started"] for r in group), "scored": len(scored),
            "checker_passed": sum(r["checker_passed"] for r in scored),
            "unstarted": [r["trial_id"] for r in group if not r["started"]],
            "warning": "four_conditions_include_a_repeated_task; not_independent_generalization"}
    processes = [p for r in results for p in r.get("processes", [])]
    return {"schema_version": 1, "valid": True, "run": str(run), "local_smoke": manifest["local_smoke"],
        "analyzer_sources": {str(p): sha(p.read_bytes()) for p in (Path(__file__).resolve(), Path(old.__file__).resolve())},
        "verification": verification, "summary": read(run, "summary.json"), "cost": costs,
        "planned": 8, "started": len(results), "scored": sum(r.get("score") is not None for r in results),
        "unstarted": [r["trial_id"] for r in reports.values() if not r["started"]],
        "tasks": 3, "scenes": 3, "conditions": 4, "repetitions_per_condition_arm": 1,
        "by_arm": grouped, "pairs": pairs, "trials": list(reports.values()),
        "wall_time": {"sum_worker_seconds": sum(r.get("worker_elapsed_seconds") or 0 for r in reports.values()),
            "first_process_start_to_last_exit_seconds": (max(p["ended_ns"] for p in processes) - min(p["started_ns"] for p in processes)) / 1e9 if processes else None},
        "limits": ["Known development tasks; one repetition; no significance or generalization claim.",
            "Whole-system package, not single mechanism attribution.",
            "Controlled lost receipt; subsequent decisions are real only for a non-smoke run.",
            "Same-response parallel actions and program-internal reads are not model recovery.",
            "Hover action/event audit does not measure unspecified briefly duration.",
            "Zero opportunity rates are null; unstarted slots are not failures.",
            "Local hashes prove consistency, not an external cryptographic trust root."]}


def cell(value):
    return "未取得/不可估计" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def markdown(report):
    text = ["# 新版 DeepSeek 执行契约 pilot：证据分析", "", "运行目录：`" + report["run"] + "`", ""]
    if not report["valid"]:
        return "\n".join(text + ["**封存或证据核验失败；不输出可信成绩/行为结论。**", "", cell(report["error"]), ""])
    text += ["**本地脚本 smoke；不能作为真实模型行为。**" if report["local_smoke"] else "真实 DeepSeek API + 本地隔离仿真。",
        "", "3 个旧开发任务、3 个场景、4 条件，每条件每臂一次；整合包对照，不支持显著性、单机制因果或泛化结论。",
        "", f"计划 8 槽；启动 {report['started']}；评分 {report['scored']}；未运行 {len(report['unstarted'])}。", "",
        "| 条件 / 臂 | checker | 附加动作审计 | HTTP | 已知 token | 动作 | start/resume/cancel | 程序机会 | unknown剩余机会 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |"]
    for r in report["trials"]:
        wire, harness = r.get("model_io") or {}, r.get("harness") or {}
        controls = harness.get("control_action_counts", {})
        text.append("| " + " | ".join(map(cell, [r["condition"] + " / " + r["arm"],
            r["checker_passed"] if r["started"] else "未运行",
            r.get("obligations", {}).get("additional_obligation_check"),
            wire.get("usage", {}).get("delegated_http_attempts"), wire.get("usage", {}).get("total_tokens"),
            r.get("world", {}).get("action_attempts"),
            "/".join(str(controls.get(k, 0)) for k in ("start", "resume", "cancel")) if r["started"] else "未运行",
            harness.get("opportunity_count"), r.get("unknown_recovery", {}).get("opportunities")])) + " |")
    text += ["", "保守结算 USD " + report["cost"]["settled_conservative_charge"] +
             "；锁定预留 " + cell(report["cost"].get("locked_reservation")) + "。不是供应商账单。", ""]
    for r in report["trials"]:
        text += ["## " + r["trial_id"], "", r["command"], ""]
        if not r["started"]:
            text += ["未运行；不记为任务失败。", ""]
            continue
        limits = r.get("request_limits") or {}
        normal = (r.get("result_layers") or {}).get("run", {}).get("agent_returned_normally") is True
        stop = ("无（正常返回）" if normal else "无已记录停止原因") if "stop_reason" in limits and limits["stop_reason"] is None else cell(limits.get("stop_reason"))
        text += ["状态：" + r["status"] + "；停止原因：" + stop + "；终止决策：" + cell(json.dumps(r.get("termination"), ensure_ascii=False)) + "。", "",
            "| 决策 | logical / 原 call ID | 工具 | 参数 |", "| ---: | --- | --- | --- |"]
        wire = r.get("model_io") or {}
        for d in wire.get("decisions", []):
            text.append("| " + " | ".join(map(cell, [d["ordinal"], d["logical_call_id"] + " / " + str(d["tool_call_id"]), d["name"], json.dumps(d["arguments"], ensure_ascii=False)])) + " |")
        recovery = r["unknown_recovery"]
        text += ["", "unknown 恢复：" + cell(recovery["status"]) + "；机会 " + str(recovery["opportunities"]) +
            "；完成且无重放尝试比率 " + cell(recovery["recovery_rate"]) + "。", "",
            "附加义务证据：" + cell(json.dumps(r["obligations"], ensure_ascii=False)) + "。", "",
            "实际响应 model：" + cell(sorted({str(x.get("response_model")) for x in wire.get("responses", []) if x.get("response_model")})) +
            "；fingerprint：" + cell(sorted({str(x.get("system_fingerprint")) for x in wire.get("responses", []) if x.get("system_fingerprint")})) + "。", ""]
        for final in wire.get("final_messages", []):
            text += ["最终可见文本（不含 reasoning）：" + cell(final["content"]["text"]), ""]
    text += ["动作的时间先后不等于模型恢复。配对差值、完整 call/请求/事件关联及无机会原因见 analysis.json；同一回复预选动作、程序查询、后续新模型动作分别记录。", ""]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(run):
        parser.error("--output must be a nonexistent directory outside --run")
    try:
        report = analyze(run)
    except Exception as exc:
        report = {"schema_version": 1, "valid": False, "run": str(run), "trials": [],
            "error": type(exc).__name__ + ": " + str(exc)}
    output.mkdir(parents=True, exist_ok=False)
    (output / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "REPORT_CN.md").write_text(markdown(report))
    print(json.dumps({"valid": report["valid"], "output": str(output), "error": report.get("error")}, ensure_ascii=False))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
