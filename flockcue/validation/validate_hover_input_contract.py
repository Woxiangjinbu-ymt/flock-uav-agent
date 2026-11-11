"""Fixed sixteen-slot local completion/input-contract acceptance.

Uses frozen general_agent workers and independent scorers. Every model decision
is a public script; there is no provider or training path in this driver.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import copy
import json

from validate_harness_episode import sha, write, check_hashes

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
CONFIG = ROOT / "experiments/hover-input-contract-v1"
PROTOCOL = "docs/results/hover-input-contract-20261103/PROTOCOL_CN.md"
STUDY = "experiments/task-coverage-v1-study"
STUDY_SHA256 = "6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae"
TASK = "c74d7020"
GROUPS = ("main", "action", "duration", "legacy")
IDS = ("state-string", "state-object", "missing-status", "wrong-actor", "wrong-altitude",
       "stale-after-land", "unknown-raw", "unknown-skill", "skill-object", "array-recover",
       "scalar-reject", "malformed-recover", "action-missing", "action-complete",
       "duration-unverified", "legacy-object-reject")
DERIVED = {
    "action": "Command drone Drone 4 to take off to an altitude of 12 meters and then execute a hover command.",
    "duration": "Command drone Drone 4 to take off to an altitude of 12 meters and hover in place for 5 seconds.",
}
SOURCES = ("scripts/validate_hover_input_contract.py", "scripts/tests/test_hover_input_contract_validation.py",
           "scripts/validate_harness_cost_reduction.py", "scripts/audit_agent_samples.py", PROTOCOL)


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def validate_plan(plan, config=CONFIG):
    if (plan.get("planned_episodes") != 16 or plan.get("provider_calls_authorized") != 0
            or plan.get("source_study") != STUDY or plan.get("source_manifest_sha256") != STUDY_SHA256
            or plan.get("task_id") != TASK or tuple(g["name"] for g in plan["groups"]) != GROUPS
            or tuple(plan.get("expected_claim", {})) != IDS
            or tuple(plan.get("expected_original_checker", {})) != IDS
            or tuple(plan.get("expected_actions", {})) != IDS):
        raise ValueError("Outside fixed sixteen-slot local scope")
    cases = []
    for group in plan["groups"]:
        name = group["name"]
        if group.get("derived_command") != DERIVED.get(name):
            raise ValueError("Derived public instruction changed")
        if group.get("profile") != ("legacy-profile.json" if name == "legacy" else "profile.json"):
            raise ValueError("Wrong profile selection")
        selected = read(config / group["cases"])
        cases.extend(selected)
    if tuple(c["trial_id"] for c in cases) != IDS or any(c["task_id"] != TASK for c in cases):
        raise ValueError("Fixed cases/order changed")
    return cases


def derive_study(study, output, command):
    """Derive public instruction only; old scene/snapshot/checker stay untouched."""
    study, output = Path(study), Path(output)
    origin_raw = (study / "manifest.json").read_bytes()
    if sha(origin_raw) != STUDY_SHA256 or command not in DERIVED.values():
        raise ValueError("Unknown source or derived instruction")
    origin = json.loads(origin_raw)
    name = "fixtures/" + TASK + ".json"
    fixture_raw = (study / name).read_bytes()
    fixture = json.loads(fixture_raw)
    names = (name, "fixtures/" + TASK + ".pkl", "fixtures/" + fixture["timeline_file"])
    check_hashes(study, {n: origin["files"][n] for n in names})
    derived = copy.deepcopy(fixture)
    derived.update(command=command, command_sha256=sha(command.encode()), public_instruction_variant={
        "source_manifest_sha256": STUDY_SHA256, "source_fixture_sha256": sha(fixture_raw),
        "source_command": fixture["command"], "source_command_sha256": fixture["command_sha256"],
        "changed_fields": ["command", "command_sha256", "public_instruction_variant"],
        "original_task_and_checker_preserved": True,
        "checker_scope": "auxiliary original altitude/airborne check, not derived action/duration verification",
        "split": "development", "teacher_example": False})
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for n in names:
        target = output / n
        target.parent.mkdir(parents=True, exist_ok=True)
        if n == name:
            write(target, derived)
        else:
            target.write_bytes((study / n).read_bytes())
        files[n] = sha(target.read_bytes())
    manifest = {"schema_version": 1, "scope": "local_public_instruction_derivative",
        "provider_calls_authorized": 0, "origin_study": STUDY,
        "origin_manifest_sha256": STUDY_SHA256, "files": files}
    write(output / "manifest.json", manifest)
    return manifest


def prepare_campaign(output):
    from general_agent import prepare
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Existing local attempts cannot be overwritten")
    plan = read(CONFIG / "plan.json")
    validate_plan(plan)
    origin_raw = (ROOT / STUDY / "manifest.json").read_bytes()
    if sha(origin_raw) != STUDY_SHA256:
        raise ValueError("Development origin changed")
    output.mkdir(parents=True)
    source_hashes = {}
    paths = [ROOT / n for n in SOURCES] + sorted(CONFIG.rglob("*.json"))
    for path in paths:
        name = str(path.relative_to(ROOT))
        target = output / "selection-source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        source_hashes[name] = sha(target.read_bytes())
    source_manifest = output / "origin-study-manifest.json"
    source_manifest.write_bytes(origin_raw)
    config = output / "selection-source/experiments/hover-input-contract-v1"
    bundles, derivative_hashes = [], {}
    for group in plan["groups"]:
        name = group["name"]
        study = ROOT / STUDY
        if name in DERIVED:
            study = output / "studies" / name
            derive_study(ROOT / STUDY, study, DERIVED[name])
            derivative_hashes.update({str(p.relative_to(output)): sha(p.read_bytes())
                                     for p in study.rglob("*") if p.is_file()})
        run = output / "runs" / name
        m = prepare(config / group["profile"], study, config / group["cases"], run)
        bundles.append({"name": name, "manifest_sha256": sha((run / "manifest.json").read_bytes()),
                        "trial_ids": [t["trial_id"] for t in m["trials"]],
                        "sources": m["sources"]})
    if any(b["sources"] != bundles[0]["sources"] for b in bundles[1:]):
        raise ValueError("Sources changed while preparing all fixed slots; do not execute")
    manifest = {"schema_version": 1, "scope": "hover_input_local_acceptance_v1",
        "planned_episodes": 16, "provider_calls_authorized": 0, "task_id": TASK,
        "decision_source": "fixed public scripted responses, not real-model behavior",
        "bundles": bundles, "selection_sources": source_hashes,
        "derived_inputs": derivative_hashes, "origin_study_manifest_sha256": sha(origin_raw)}
    write(output / "comparison-manifest.json", manifest)
    (output / "comparison-manifest.sha256").write_text(sha((output / "comparison-manifest.json").read_bytes()) + "\n")
    return manifest


def load_campaign(output):
    from general_agent import load_manifest
    output = Path(output).resolve()
    raw = (output / "comparison-manifest.json").read_bytes()
    if sha(raw) != (output / "comparison-manifest.sha256").read_text().strip():
        raise ValueError("Campaign manifest changed")
    m = json.loads(raw)
    if (m.get("scope") != "hover_input_local_acceptance_v1" or m.get("planned_episodes") != 16
            or m.get("provider_calls_authorized") != 0
            or tuple(b["name"] for b in m["bundles"]) != GROUPS):
        raise ValueError("Outside fixed local campaign")
    check_hashes(output / "selection-source", m["selection_sources"])
    check_hashes(output, m["derived_inputs"])
    if sha((output / "origin-study-manifest.json").read_bytes()) != STUDY_SHA256:
        raise ValueError("Origin anchor changed")
    config = output / "selection-source/experiments/hover-input-contract-v1"
    validate_plan(read(config / "plan.json"), config)
    for bundle in m["bundles"]:
        run = output / "runs" / bundle["name"]
        if sha((run / "manifest.json").read_bytes()) != bundle["manifest_sha256"]:
            raise ValueError("Prepared subrun changed")
        child = load_manifest(run)
        if child["sources"] != bundle["sources"] or [t["trial_id"] for t in child["trials"]] != bundle["trial_ids"]:
            raise ValueError("Frozen source/case selection changed")
    return m


def normalized_actions(api):
    return [{k: row.get(k) for k in ("endpoint", "params", "body", "committed")}
            for row in api if row.get("action")]


def input_rejection_facts(archive):
    """Count original object calls and visible validation errors without rewriting."""
    objects, invalids, starts = [], [], {}
    for row in rows(archive):
        if row.get("kind") == "model_end":
            data = row.get("data", {})
            value = data.get("output", {})
            # ModelResponse.result is a list of serialized AI messages.
            messages = value.get("result", []) if isinstance(value, dict) else []
            for message in messages:
                for call in message.get("tool_calls", []):
                    arg = call.get("args", {}).get("input_json")
                    if isinstance(arg, dict):
                        objects.append({"id": call.get("id"), "name": call.get("name")})
        if row.get("kind") == "tool_start":
            data = row.get("data", {})
            starts[data.get("tool_span_id")] = data.get("raw_tool_call_id")
        if row.get("kind") == "tool_end":
            data = row.get("data", {})
            output = data.get("output", {})
            if isinstance(output, dict) and output.get("status") == "error":
                invalids.append(starts.get(data.get("tool_span_id")))
    return {"original_object_call_count": len(objects), "original_object_calls": objects,
            "tool_error_count": len(invalids), "tool_error_call_ids": invalids}


def analyze_campaign(output):
    from general_agent import verify
    from validate_harness_cost_reduction import wire_audit
    output = Path(output).resolve()
    manifest = load_campaign(output)
    config = output / "selection-source/experiments/hover-input-contract-v1"
    plan = read(config / "plan.json")
    checks, reports, process_ids = [], [], []
    def check(name, value):
        checks.append({"check": name, "passed": bool(value)})
    by_id = {}
    for bundle in manifest["bundles"]:
        run = output / "runs" / bundle["name"]
        checked = verify(run)
        results, summary = read(run / "results.json"), read(run / "summary.json")
        check(bundle["name"] + ":all_prepared_slots_scored", checked["valid"] and
              [r["trial_id"] for r in results] == bundle["trial_ids"] and summary["unstarted_trials"] == [])
        check(bundle["name"] + ":complete_campaign", summary["status"] == "completed")
        for result in results:
            ident, worker, score = result["trial_id"], result.get("worker"), result.get("score")
            folder = run / "trials" / ident
            check(ident + ":worker_and_independent_score", worker is not None and score is not None)
            if worker is None or score is None:
                continue
            process_ids.extend([worker["pid"], score["pid"]])
            agent = read(folder / "policy/agent-result.json")
            state = read(folder / "policy/task-state.json")
            execution = read(folder / "policy/execution-state.json")
            api = rows(folder / "policy/worldlogs/public-api.jsonl")
            physical_path = folder / "policy/worldlogs/physical-events.jsonl"
            physical = rows(physical_path) if physical_path.exists() else []
            archive = next((folder / "policy/modelio").glob("*.jsonl"))
            wire, decisions = wire_audit(archive, state)
            assessment = agent.get("completion_assessment")
            expected = plan["expected_claim"][ident]
            check(ident + ":normal_run_is_distinct_from_completion", worker["agent"]["success"]
                  and worker["limits"]["stop_reason"] is None and result["termination"]["batch_action"] == "continue")
            check(ident + ":isolation_archive_and_no_network", worker["drained"]
                  and worker["agent"]["archive_verify"]["complete"] and all(worker["isolation"].values())
                  and worker["network_attempts"] == score["network_attempts"] == []
                  and score["pid"] != worker["pid"] and score["agent_imported"] is False)
            check(ident + ":original_auxiliary_checker", score["checker"]["passed"] is plan["expected_original_checker"][ident])
            if ident == "legacy-object-reject":
                check(ident + ":legacy_contract_not_silently_enabled", assessment is None)
            else:
                check(ident + ":completion_claim_matches_predeclared_expectation", isinstance(assessment, dict)
                      and assessment.get("completion_claim_accepted") is expected)
                check(ident + ":completion_value_not_guessed", isinstance(assessment, dict)
                      and assessment.get("task_completed") is (True if expected else None))
                check(ident + ":result_completion_consistent", isinstance(assessment, dict)
                      and agent.get("task_completed") is assessment.get("task_completed"))
                if expected is False:
                    check(ident + ":rejected_claim_keeps_original_but_not_public_done_marker",
                          "[TASK DONE]" in agent.get("model_output", "") and "[TASK DONE]" not in agent.get("output", ""))
            check(ident + ":all_original_graph_messages_and_receipts_preserved", wire["all_original_graph_messages_preserved"]
                  and all(r["recovery_projection_present"] for r in wire["requests"]))
            actions = normalized_actions(api)
            check(ident + ":expected_physical_action_count", len(actions) == plan["expected_actions"][ident])
            input_audit = input_rejection_facts(archive)
            if ident in {"array-recover", "scalar-reject", "malformed-recover", "legacy-object-reject"}:
                expected_errors = 2 if ident == "legacy-object-reject" else 1
                expected_takeoffs = 1 if ident in {"array-recover", "malformed-recover"} else 0
                check(ident + ":invalid_input_receipt_and_no_extra_backend_call",
                      input_audit["tool_error_count"] == expected_errors
                      and input_audit["tool_error_call_ids"][0] == "script-1"
                      and sum(r["endpoint"].endswith("/command/take_off") for r in api) == expected_takeoffs)
            if ident in {"state-object", "skill-object"}:
                check(ident + ":original_object_arguments_retained", input_audit["original_object_call_count"] == 2)
            if ident == "state-string":
                check(ident + ":legacy_string_representation_retained", input_audit["original_object_call_count"] == 0)
            if ident in {"unknown-raw", "unknown-skill"}:
                takeoffs = [r for r in api if r["endpoint"].endswith("/command/take_off")]
                unknown = [e for e in execution["actions"] if e["tool_name"] == "take_off" and e["effect_status"] == "unknown"]
                check(ident + ":unknown_not_rewritten_or_replayed", len(takeoffs) == len(unknown) == 1
                      and takeoffs[0]["committed"] and takeoffs[0]["response"].get("status") == "success"
                      and not any(r["endpoint"].endswith("/command/hover") for r in api))
                check(ident + ":status_confirmation_after_real_takeoff_effect", any(
                    r["request_id"] > takeoffs[0]["request_id"] and r.get("origin", {}).get("kind") == "model_tool"
                    and r.get("origin", {}).get("tool") == "get_drone_status" and r["response"].get("status") == "hovering"
                    for r in api) and any(e.get("history", {}).get("event") == "take_off" for e in physical))
            report = {"trial_id": ident, "group": bundle["name"], "original_checker_passed": score["checker"]["passed"],
                "completion_assessment": assessment, "run_returned_normally": worker["agent"]["success"],
                "physical_cost": worker["physical_cost"], "actions": actions,
                "wire": wire, "input_audit": input_audit,
                "unknown_action_count": sum(e["effect_status"] == "unknown" for e in execution["actions"]),
                "initial": worker["initial"]}
            reports.append(report)
            by_id[ident] = report
    check("fixed_sixteen_full_denominator", tuple(r["trial_id"] for r in reports) == IDS)
    check("sixteen_fresh_workers_and_scorers", len(process_ids) == len(set(process_ids)) == 32)
    if {"state-string", "state-object"} <= set(by_id):
        a, b = by_id["state-string"], by_id["state-object"]
        check("string_and_object_have_same_actions_initial_and_goal", a["actions"] == b["actions"]
              and a["initial"] == b["initial"] and a["completion_assessment"]["task_completed"]
              is b["completion_assessment"]["task_completed"] is True)
    return {"schema_version": 1, "passed": all(c["passed"] for c in checks),
        "planned_episodes": 16, "reported_episodes": len(reports), "provider_calls": 0,
        "fresh_workers": len(reports), "independent_scorers": len(reports),
        "evidence_scope": "fixed local scripts and ASGI; no real-model/token/generalization result",
        "checker_scope": plan["checker_scope"], "checks": checks, "trials": reports,
        "known_development_only": True, "teacher_examples": 0}


def run_campaign(output):
    from general_agent import run_prepared
    output = Path(output).resolve()
    manifest = load_campaign(output)
    if (output / "campaign-seal.json").exists() or any((output / "runs" / b["name"] / "process-ledger.jsonl").exists()
                                                        for b in manifest["bundles"]):
        raise FileExistsError("No automatic rerun or continuation")
    for bundle in manifest["bundles"]:
        run_prepared(output / "runs" / bundle["name"])
        summary = read(output / "runs" / bundle["name"] / "summary.json")
        if summary["status"] != "completed":
            raise RuntimeError("Incomplete local subrun retained; no later subrun dispatched")
    result = analyze_campaign(output)
    write(output / "validation.json", result)
    write(output / "campaign-seal.json", {"files": {str(p.relative_to(output)): sha(p.read_bytes())
        for p in sorted(output.rglob("*")) if p.is_file() and p != output / "campaign-seal.json"
        and "__pycache__" not in p.parts}})
    return result


def verify_campaign(output):
    output = Path(output).resolve()
    seal = read(output / "campaign-seal.json")["files"]
    check_hashes(output, seal)
    inventory = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()
                 and p != output / "campaign-seal.json" and "__pycache__" not in p.parts}
    if inventory != set(seal):
        raise ValueError("Campaign inventory changed")
    result = analyze_campaign(output)
    if result != read(output / "validation.json"):
        raise ValueError("Stored acceptance differs from read-only recomputation")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_campaign(args.output)
        print(json.dumps({"planned_episodes": result["planned_episodes"], "provider_calls_authorized": 0}))
    else:
        result = run_campaign(args.output) if args.action == "run" else verify_campaign(args.output)
        print(json.dumps({k: result[k] for k in ("passed", "planned_episodes", "reported_episodes", "provider_calls")}))
        if not result["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
