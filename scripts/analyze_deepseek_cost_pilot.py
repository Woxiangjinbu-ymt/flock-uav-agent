"""Read-only analysis of the frozen ten-slot integrated cost comparison.

No provider, SDK, simulator, pickle or production ledger is imported. Actual
wire usage and decisions are linked to sealed effects; reasoning is not exported.
"""
from __future__ import annotations

import argparse
import ast
import copy
from decimal import Decimal
import json
from pathlib import Path

import analyze_deepseek_contract_pilot as contract

old = contract.old
read, safe, sha, rows, require = old.read, old.safe, old.sha, old.rows, old.require
request_views = contract.request_views
verify_world_logs = contract.verify_world_logs
obligations = contract.obligations
unknown_recovery = contract.unknown_recovery
cell = contract.cell
CONDITIONS = (
    ("short_chain", "c74d7020", "fb8971ec", ("control", "candidate")),
    ("navigation", "6c3f98ce", "5aabad33", ("candidate", "control")),
    ("search", "fdd21809", "be5d648b", ("control", "candidate")),
    ("unknown_takeoff", "c74d7020", "fb8971ec", ("candidate", "control")),
    ("search_complete", "b1ad4f3e", "35ce64d8", ("control", "candidate")),
)
SOURCE_MANIFEST = "6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae"
EXTRA_SOURCE_MANIFEST = "8156cc40a1920b418ac9091772870beececc47910ea00f16ff0c6d8cfb706180"
LEAK_MARKERS = contract.LEAK_MARKERS
RUNTIME = contract.RUNTIME
ARMS = {"control": ("full_v1", "observed_target_v7"),
        "candidate": ("compact_tools_v1", "search_confirm_v9")}
MARKER = "AVAILABLE TOOLS:\n"
TAIL = "\n\nBe concise, safe, and operationally precise."


def expected_trials():
    return [(condition + "-" + arm, condition, task, scene, arm)
            for condition, task, scene, arms in CONDITIONS for arm in arms]


def verify_run(run):
    manifest = read(run, "manifest.json")
    require(manifest.get("schema_version") == 2 and manifest.get("scope") == "deepseek_cost_pilot_v1",
            "Not the ten-slot execution-contract pilot")
    require(type(manifest.get("local_smoke")) is bool, "Missing evidence-kind boundary")
    require(manifest.get("online_contract") == "task_execution_state_v1", "Wrong execution contract")
    require(manifest.get("online_variant_contract") == "harness_cost_pair_v1", "Wrong cost comparison contract")
    auth = manifest.get("authorization", {})
    local = manifest["local_smoke"]
    require(auth.get("source") == "user_explicit_deepseek_cost_pilot_authorization_20261103"
            and type(auth.get("episodes")) is int and auth["episodes"] == (0 if local else 10)
            and type(auth.get("max_http_attempts")) is int and auth["max_http_attempts"] == (0 if local else 120),
            "Changed limited authorization")
    require(old.canonical(manifest.get("online_common_runtime")) == old.canonical(RUNTIME), "Changed common runtime budget")
    for key, value in {"model": "deepseek-flash", "thinking": "enabled", "reasoning_effort": "high",
                       "sdk_retries": 0, "batch_budget": "2.00", "trial_budget": "0.50",
                       "input_per_million": "0.3", "output_per_million": "1.2"}.items():
        require(old.canonical(manifest.get("online_pilot", {}).get(key)) == old.canonical(value), "Changed provider/budget contract: " + key)
    expected = expected_trials()
    require([(t["trial_id"], t["condition"], t["task_id"], t["arm"]) for t in manifest["trials"]]
            == [(ident, cond, task, arm) for ident, cond, task, _, arm in expected], "Changed ten-slot order/denominator")
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
    require(sha(safe(run, "inputs/extra-origin-manifest.json").read_bytes()) == EXTRA_SOURCE_MANIFEST,
            "Extra development source differs from fixed external anchor")
    extra_origin = read(run, "inputs/extra-origin-manifest.json")
    dataset_origin = read(run, "inputs/origin-dataset-manifest.json")
    require(sha(safe(run, "inputs/origin-dataset-manifest.json").read_bytes()) == origin["origin_manifest_sha256"],
            "Development dataset origin changed")
    results = read(run, "results.json")
    require(len(results) <= len(expected) and [r["trial_id"] for r in results]
            == [e[0] for e in expected[:len(results)]], "Results must preserve the fixed trial prefix")
    summary = read(run, "summary.json")
    require(summary.get("planned_trials") == 10 and summary.get("started_trials") == len(results)
            and summary.get("scored_trials") == sum(r.get("score") is not None for r in results)
            and summary.get("unstarted_trials") == [e[0] for e in expected[len(results):]],
            "Summary changed planned/started/scored/unrun denominator")
    if len(results) < 10:
        require(summary.get("status") == "stopped" and bool(summary.get("reason")), "Unexplained unrun slots")
    verification = {"manifest_sha256": sha(safe(run, "manifest.json").read_bytes()),
        "campaign_seal_sha256": sha(safe(run, "campaign-seal.json").read_bytes()),
        "campaign_files": len(seal), "source_manifest_sha256": SOURCE_MANIFEST,
        "extra_source_manifest_sha256": EXTRA_SOURCE_MANIFEST, "trials": {}}
    for trial, (_, condition, task, scene, arm) in zip(manifest["trials"], expected):
        require(old.canonical(trial.get("runtime")) == old.canonical(RUNTIME)
                and type(trial.get("watchdog_seconds")) is int and trial["watchdog_seconds"] == 515, "Changed trial budget")
        require(trial["task_progress_mode"] == "evidence_harness"
                and (trial.get("input_layout"), trial["harness_revision"]) == ARMS[arm]
                and trial["scheduler"] == "applicability", "Changed arm capabilities")
        fixture = read(run / "inputs", "fixtures/" + task + ".json")
        selected_origin = extra_origin if task == "b1ad4f3e" else origin
        for name in ("fixtures/" + task + ".json", "fixtures/" + task + ".pkl", "fixtures/" + fixture["timeline_file"]):
            require(sha(safe(run / "inputs", name).read_bytes()) == selected_origin["files"][name], "Input differs from anchored development origin: " + name)
        require(fixture["task_id"] == task and fixture["scene_id"] == scene
                and (task == "b1ad4f3e" or fixture["split"] == "development")
                and sha(fixture["command"].encode()) == fixture["command_sha256"], "Fixture identity/command changed")
        if task == "b1ad4f3e":
            require(sha(safe(run, "inputs/dataset/scenes/Target_Assignment_Moderate_1_35ce64d8.json").read_bytes())
                    == fixture["source_sha256"] == "fc67b2f2f809fbaae050b1bd12a5b560fd94e7742ed21bf23c13edc1b83786e0",
                    "Extra scene changed")
        else:
            require(sha(safe(run / "inputs", "dataset/" + fixture["scene_file"]).read_bytes())
                    == dataset_origin["files"][fixture["scene_file"]], "Development scene changed")
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



def _string_assignment(path, name):
    tree = ast.parse(path.read_text())
    matches = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
               and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    require(len(matches) == 1, "Frozen prompt template is not one literal assignment: " + name)
    return matches[0]


def frozen_prompt_templates(run):
    """Read literals from sealed source without importing/executing that source."""
    return (_string_assignment(safe(run, "source/agent4drone/template/system_prompt.py"), "SYSTEM_PROMPT_TEMPLATE"),
            _string_assignment(safe(run, "source/agent4drone/template/parsing_error.py"), "PARSING_ERROR_TEMPLATE"))


def audit_wire_layout(body, layout, prompt_template, parsing_template):
    require(layout in {"full_v1", "compact_tools_v1"}, "Unknown input layout")
    native = body.get("tools")
    require(isinstance(native, list) and bool(native), "Missing native tools")
    names, full_lines = [], []
    for tool in native:
        require(isinstance(tool, dict) and tool.get("type") == "function"
                and isinstance(tool.get("function"), dict), "Invalid native tool schema")
        f = tool["function"]
        name, description = f.get("name"), f.get("description")
        require(isinstance(name, str) and bool(name) and name not in names
                and isinstance(description, str) and bool(description)
                and isinstance(f.get("parameters"), dict), "Incomplete/duplicate native tool definition")
        names.append(name)
        full_lines.append("- " + name + ": " + " ".join(description.split()))
    systems = [m for m in body.get("messages", []) if m.get("role") == "system"]
    require(len(systems) == 1 and isinstance(systems[0].get("content"), str), "Expected one text system message")
    system = systems[0]["content"]
    require(system.count(MARKER) == system.count(TAIL) == 1, "Ambiguous actual tool catalog boundaries")
    prefix, remainder = system.split(MARKER)
    catalog, suffix = remainder.split(TAIL)
    expected_catalog = "\n".join(full_lines if layout == "full_v1" else ["- " + n for n in names])
    require(catalog == expected_catalog, "Actual input layout does not match frozen arm")
    expected_prompt = prompt_template.format(parsing_error_template=parsing_template, tool_lines=expected_catalog)
    require(system.startswith(expected_prompt), "Operating/safety prompt differs from frozen template")
    return {"layout": layout, "native_tool_count": len(names), "catalog_matches_layout": True,
        "native_descriptions_present": True, "native_parameter_schemas_present": True,
        "frozen_operating_rules_match": True, "native_tools_sha256": sha(old.canonical(native)),
        "system_sha256": sha(system.encode()), "system_utf8_bytes": len(system.encode()),
        "catalog_utf8_bytes": len(catalog.encode()), "system_prefix_sha256": sha(prefix.encode()),
        "dynamic_suffix_utf8_bytes": len(suffix.encode()),
        "messages_including_reasoning_exported": False}


def compare_tool_contracts(control, candidate):
    if not control or not candidate:
        return {"verified": None, "reason": "no_dispatched_request_in_one_or_both_arms"}
    for series in (control, candidate):
        require(all(old.canonical(t) == old.canonical(series[0]) for t in series),
                "Native tool contract changed inside a trial")
    before, after = copy.deepcopy(control[0]), copy.deepcopy(candidate[0])
    changed = []
    require(len(before) == len(after), "Paired tools differ in count")
    for left, right in zip(before, after):
        require(left.get("type") == right.get("type") == "function", "Paired tool type changed")
        a, b = left.get("function", {}), right.get("function", {})
        require(a.get("name") == b.get("name"), "Paired tool names/order changed")
        if a.get("description") != b.get("description"):
            require(a.get("name") == "run_harness", "Unexpected native description change")
            changed.append("run_harness")
            a.pop("description", None)
            b.pop("description", None)
    require(old.canonical(before) == old.canonical(after), "Paired native schema/parameters differ")
    return {"verified": True, "native_tool_count": len(before), "tool_names_order_and_parameters_identical": True,
        "description_only_changes": changed, "all_requests_checked": len(control) + len(candidate),
        "normalized_native_tools_sha256": sha(old.canonical(before))}


def search_confirmation(harness, trial):
    if harness is None:
        return {"artifact_present": False, "search_frames": None, "extra_read_count": None, "frames": []}
    frames = []
    for f in harness.get("frames", []):
        if f.get("skill") != "public_search":
            continue
        confirmation = f.get("state", {}).get("search_confirmation")
        extra = [] if confirmation is None else confirmation.get("extra_reads", [])
        require(isinstance(extra, list), "Invalid confirmation read list")
        if confirmation is not None:
            require(trial["arm"] == "candidate" and confirmation.get("revision") == "search_confirm_v9"
                    and confirmation.get("stagnation_handoff_enabled") is False,
                    "Search confirmation differs from frozen candidate")
        for r in extra:
            require(type(r.get("cached_progress_percentage")) in (int, float)
                    and r["cached_progress_percentage"] >= 95
                    and r.get("trigger") == "new_trusted_reach_after_cached_progress_at_least_95"
                    and isinstance(r.get("evidence_ref"), str), "Invalid public completion confirmation trigger")
        frames.append({"plan_id": f.get("plan_id"), "status": f.get("status"), "reason": f.get("reason"),
            "cursor": f.get("cursor"), "api_calls": f.get("api_calls"),
            "final_public_progress": f.get("state", {}).get("progress_percent"),
            "confirmation": old.clean(confirmation), "extra_read_count": len(extra)})
    return {"artifact_present": True, "search_frames": len(frames),
        "extra_read_count": sum(f["extra_read_count"] for f in frames), "frames": frames,
        "scope": "program_public_confirmation_not_a_model_decision_or_automatic_causal_attribution"}


def aggregate_reports(group):
    started = [r for r in group if r["started"]]
    usages = [(r.get("model_io") or {}).get("usage") for r in started]
    usages = [u for u in usages if u is not None]
    result = {"usage_observed_trials": len(usages), "usage_scope": "known_actual_response_usage; incomplete_or_missing_not_zero"}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "known_usage_responses", "delegated_http_attempts"):
        known = [u[key] for u in usages if type(u.get(key)) is int]
        result[key] = sum(known) if known else None
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        known = [u[key] for u in usages if type(u.get(key)) is int]
        reported = sum(u.get(key + "_reported_responses", 0) for u in usages)
        result[key + "_known_sum"] = sum(known) if known else None
        result[key + "_reported_responses"] = reported
        result[key + "_complete"] = reported == result.get("known_usage_responses") if usages else False
    for key in ("action_attempts", "api_requests"):
        values = [(r.get("world") or {}).get(key) for r in started]
        result[key + "_observed_trials"] = sum(type(v) is int for v in values)
        result[key] = sum(v for v in values if type(v) is int) if any(type(v) is int for v in values) else None
    times = [r.get("worker_elapsed_seconds") for r in started]
    result["worker_seconds_observed_trials"] = sum(type(v) in (int, float) for v in times)
    result["worker_seconds"] = sum(v for v in times if type(v) in (int, float)) if result["worker_seconds_observed_trials"] else None
    costs = [r.get("cost", {}).get("settled_conservative_charge") for r in started]
    result["conservative_usd"] = str(sum((Decimal(v) for v in costs if v is not None), Decimal("0"))) if any(v is not None for v in costs) else None
    result["tool_feedback_errors"] = sum(len((r.get("model_io") or {}).get("tool_feedback_errors", [])) for r in started)
    result["unknown_recovery_opportunities"] = sum(r.get("unknown_recovery", {}).get("opportunities", 0) for r in started)
    recovered = sum(r.get("unknown_recovery", {}).get("recovery_rate") == 1 for r in started)
    result["unknown_recovery_completed"] = recovered
    result["unknown_recovery_rate"] = recovered / result["unknown_recovery_opportunities"] if result["unknown_recovery_opportunities"] else None
    result["ready_paused_program_opportunities"] = sum((r.get("harness") or {}).get("opportunity_count", 0) for r in started)
    result["normal_conditions"] = {"scored": sum(type(r.get("checker_passed")) is bool for r in group if r["condition"] != "unknown_takeoff"),
        "passed": sum(r.get("checker_passed") is True for r in group if r["condition"] != "unknown_takeoff")}
    return result


def analyze(run):
    run = Path(run).resolve()
    manifest, results, verification = verify_run(run)
    reports = {}
    wire_pairs = {}
    prompt_template, parsing_template = frozen_prompt_templates(run)
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
        if wire:
            actual_ids = {r["attempt_id"] for r in wire["responses"] if r.get("status_code") is not None}
            layout_audits = [audit_wire_layout(r["body"], trial["input_layout"], prompt_template, parsing_template)
                             for r in requests]
            for audit, request in zip(layout_audits, requests):
                audit.update(sequence=request["sequence"], attempt_id=request["attempt_id"],
                             dispatched=request["attempt_id"] in actual_ids)
            wire["input_layout_audit"] = layout_audits
            actual = [r for r in requests if r["attempt_id"] in actual_ids]
            wire["actual_request_bytes"] = sum(r["request_bytes"] for r in wire["responses"]
                                               if r["attempt_id"] in actual_ids)
            wire_pairs[ident] = [r["body"].get("tools") for r in actual]
            require(all(r["has_execution_projection"] for r in requests),
                    "Integrated execution-state projection missing from actual wire")
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
        harness_raw = read(folder, "policy/harness.json", optional=True)
        report["harness"] = old.harness_analysis(harness_raw, wire, public)
        report["search_confirmation"] = search_confirmation(harness_raw, trial)
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
        b, i = reports[condition + "-control"], reports[condition + "-candidate"]
        complete = type(b["checker_passed"]) is bool and type(i["checker_passed"]) is bool
        pair = {"condition": condition, "task_id": task, "scene_id": scene, "complete_pair": complete,
            "control_passed": b["checker_passed"], "candidate_passed": i["checker_passed"], "differences_candidate_minus_control": None}
        if complete:
            metrics = {"checker_passed": lambda r: int(r["checker_passed"]),
                "known_total_tokens": lambda r: r["model_io"]["usage"]["total_tokens"],
                "delegated_http_attempts": lambda r: r["model_io"]["usage"]["delegated_http_attempts"],
                "action_attempts": lambda r: r["world"]["action_attempts"],
                "api_requests": lambda r: r["world"]["api_requests"],
                "worker_elapsed_seconds": lambda r: r["worker_elapsed_seconds"],
                "tool_feedback_errors": lambda r: len(r["model_io"]["tool_feedback_errors"]),
                "action_non_success_receipts": lambda r: sum(a["status"] != "success" for a in r["world"]["actions"])}
            pair["tool_contracts"] = compare_tool_contracts(wire_pairs.get(b["trial_id"], []),
                                                           wire_pairs.get(i["trial_id"], []))
            metrics.update(prompt_tokens=lambda r: r["model_io"]["usage"]["prompt_tokens"],
                completion_tokens=lambda r: r["model_io"]["usage"]["completion_tokens"],
                actual_request_bytes=lambda r: r["model_io"]["actual_request_bytes"])
            pair["differences_candidate_minus_control"] = {k: f(i) - f(b) if f(i) is not None and f(b) is not None else None for k, f in metrics.items()}
            pair["differences_candidate_minus_control"]["conservative_usd"] = str(
                Decimal(i["cost"]["settled_conservative_charge"]) - Decimal(b["cost"]["settled_conservative_charge"]))
        pairs.append(pair)
    grouped = {}
    for arm in ("control", "candidate"):
        group = [r for r in reports.values() if r["arm"] == arm]
        scored = [r for r in group if type(r["checker_passed"]) is bool]
        grouped[arm] = {"planned": len(group), "started": sum(r["started"] for r in group), "scored": len(scored),
            "checker_passed": sum(r["checker_passed"] for r in scored),
            "unstarted": [r["trial_id"] for r in group if not r["started"]],
            "warning": "five_conditions_include_a_repeated_task_and_selected_development_search; not_independent_generalization"}
        grouped[arm].update(aggregate_reports(group))
    processes = [p for r in results for p in r.get("processes", [])]
    _, _, verification_after = verify_run(run)
    require(verification_after == verification, "Source drifted during read-only analysis")
    return {"schema_version": 1, "valid": True, "run": str(run), "local_smoke": manifest["local_smoke"],
        "analyzer_sources": {str(p): sha(p.read_bytes()) for p in (Path(__file__).resolve(), Path(contract.__file__).resolve(), Path(old.__file__).resolve())},
        "verification": verification, "summary": read(run, "summary.json"), "cost": costs,
        "planned": 10, "started": len(results), "scored": sum(r.get("score") is not None for r in results),
        "unstarted": [r["trial_id"] for r in reports.values() if not r["started"]],
        "tasks": 4, "scenes": 4, "conditions": 5, "repetitions_per_condition_arm": 1,
        "by_arm": grouped, "pairs": pairs, "trials": list(reports.values()),
        "wall_time": {"sum_worker_seconds": sum(r.get("worker_elapsed_seconds") or 0 for r in reports.values()),
            "first_process_start_to_last_exit_seconds": (max(p["ended_ns"] for p in processes) - min(p["started_ns"] for p in processes)) / 1e9 if processes else None},
        "limits": ["Known development tasks; one repetition; no significance or generalization claim.",
            "Both arms are integrated: full_v1/v7 versus compact_tools_v1/v9; joint changes, no individual attribution or raw-tool comparison.",
            "Controlled lost receipt; subsequent decisions are real only for a non-smoke run.",
            "Same-response parallel actions and program-internal reads are not model recovery.",
            "Hover action/event audit does not measure unspecified briefly duration.",
            "Zero opportunity rates are null; unstarted slots are not failures.",
            "Local hashes prove consistency, not an external cryptographic trust root."]}


def markdown(report):
    text = ["# DeepSeek 输入与搜索成本同期对照：证据分析", "", "运行目录：`" + report["run"] + "`", ""]
    if not report["valid"]:
        return "\n".join(text + ["**封存或证据核验失败；不输出可信成绩/行为结论。**", "", cell(report["error"]), ""])
    text += ["**本地脚本 smoke；不能作为真实模型行为。**" if report["local_smoke"] else "真实 DeepSeek API + 本地隔离仿真。",
        "", "4 个旧开发任务、4 个场景、5 条件，每条件每臂一次；两版整合配置对照，不支持显著性、单机制因果或泛化结论。",
        "", f"计划 10 槽；启动 {report['started']}；评分 {report['scored']}；未运行 {len(report['unstarted'])}。", "",
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
    text += ["", "control 为 full_v1 / observed_target_v7；candidate 为 compact_tools_v1 / search_confirm_v9。两臂均启用相同执行状态与恢复机制，未设置同期原工具臂。", "",
             "| 臂 | 成功 / 已评分 | 实际输入 token | 实际输出 token | 实际总 token | HTTP | 环境动作 | 保守 USD |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm, metrics in report["by_arm"].items():
        text.append("| " + " | ".join(map(cell, [arm, str(metrics["checker_passed"]) + "/" + str(metrics["scored"]),
            metrics["prompt_tokens"], metrics["completion_tokens"], metrics["total_tokens"], metrics["delegated_http_attempts"],
            metrics["action_attempts"], metrics["conservative_usd"]])) + " |")
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
    validation = {"valid": report["valid"], "provider_calls": 0, "source_run_modified": False,
        "planned": report.get("planned"), "started": report.get("started"), "scored": report.get("scored"),
        "verification": report.get("verification"), "error": report.get("error")}
    (output / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"valid": report["valid"], "output": str(output), "error": report.get("error")}, ensure_ascii=False))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
