"""Read existing artifacts only. No simulator, checker, Agent, or child imports."""
from __future__ import annotations
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def main(run, helper):
    run, helper = run.resolve(), helper.resolve()
    spec = importlib.util.spec_from_file_location("read_only_oracle", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest, results, original = (read(run / n) for n in ("manifest.json", "results.json", "summary.json"))
    checks, oracle, details = {}, {}, {}
    seal = read(run / "campaign-seal.json")["files"]
    for name, expected in seal.items():
        p = run / name
        checks["seal:" + name] = p.is_file() and not p.is_symlink() and digest(p) == expected
    actual_files = {str(p.relative_to(run)) for p in run.rglob("*") if p.is_file() and p.name != "campaign-seal.json"}
    checks["seal:exact_file_inventory"] = actual_files == set(seal)
    for name, expected in manifest["sources"].items():
        checks["source:" + name] = digest(run / "source" / name) == expected
    for name, expected in manifest["inputs"].items():
        checks["input:" + name] = digest(Path(manifest["study"]) / name) == expected
    checks["manifest_sha256"] = digest(run / "manifest.json") == (run / "manifest.sha256").read_text().strip()
    checks["zero_online_authorization"] = manifest["scope"] == "local_mock_wallclock_only" and manifest["online_authorized_executions"] == 0
    processes = [p for row in results for p in row["processes"]]
    checks["12_workers_10_scorers_unique_pid"] = (len(processes) == 22 and len({p["pid"] for p in processes}) == 22
        and sum(p["mode"] == "worker" for p in processes) == 12 and sum(p["mode"] == "score" for p in processes) == 10)
    checks["process_journal_exact"] = lines(run / "process-ledger.jsonl") == processes
    checks["all_processes_joined_before_next"] = all(p["exited"] is True for p in processes) and all(
        a["ended_ns"] <= b["started_ns"] for a, b in zip(processes, processes[1:]))
    checks["fixed_trial_order"] = [t["trial_id"] for t in manifest["trials"]] == [r["trial_id"] for r in results]
    commands, initials, first_inputs = {}, {}, {}
    for trial, row in zip(manifest["trials"], results):
        ident = trial["trial_id"]
        folder = run / "trials" / ident
        own = module.audit_trial(run, trial, row)
        oracle.update({ident + ":oracle:" + k: v for k, v in own.items()})
        raw = lines(folder / "policy/worldlogs/public-api.jsonl")
        actions = [e for e in raw if e["action"]]
        archive = lines(next((folder / "policy/modelio").glob("*.jsonl")))
        requests = [base64.b64decode(e["data"]["body"]["base64"], validate=True) for e in archive if e["kind"] == "http_request"]
        canary = manifest["private_canary"].encode()
        checks[ident + ":negative_canary_and_checker_definitions_all_policy_files"] = all(
            not any(word in p.read_bytes() for word in (canary, b"execution_check_apis", b"initialization_check_apis"))
            for p in (folder / "policy").rglob("*") if p.is_file())
        if ident in module.KILLS:
            last = json.loads(requests[2])["messages"][-1]
            receipt = json.loads(last["content"])
            checks[ident + ":third_dispatch_has_actual_prior_takeoff_receipt"] = (
                last["role"] == "tool" and last["tool_call_id"] == "local-takeoff"
                and receipt.get("outcome") == "completed" and all(receipt.get(k) == v for k, v in actions[0]["response"].items()))
            details[ident] = {"status": row["status"], "actions": len(actions), "scorer": False,
                "archive_end_present": any(e["kind"] == "archive_end" for e in archive)}
            continue
        worker, score = row["worker"], row["score"]
        commands[ident] = worker["entry"]["command_id"]
        initials[ident] = worker["initial"]
        first_inputs[ident] = requests[0]
        checks[ident + ":empty_natural_entry"] = worker["entry"]["board"] == {"targets": [], "obstacles": [], "drones": []} and all(
            worker["entry"][k] == 0 for k in ("frames", "ledger_events", "ledger_model_requests"))
        checks[ident + ":worker_result_equals_summary"] = read(folder / "worker-result.json") == worker
        checks[ident + ":score_result_equals_summary"] = read(folder / "evaluation/result.json") == score
        checks[ident + ":no_network"] = worker["network_attempts"] == [] and score["network_attempts"] == []
        checks[ident + ":score_is_separate_pid_after_worker_exit"] = (worker["pid"] == row["processes"][0]["pid"]
            and score["pid"] == row["processes"][1]["pid"] and score["worker_pid"] == worker["pid"])
        checks[ident + ":score_references_exact_closed_worker_and_snapshot"] = (
            score["worker_result_sha256"] == digest(folder / "worker-result.json") and score["snapshot_sha256"] ==
            worker["snapshot"]["sha256"] == digest(folder / worker["snapshot"]["path"]))
        checks[ident + ":checker_private_positive_control"] = (score["private_canary_present"] is True
            and canary in (folder / "evaluation/private-task.json").read_bytes() and score["agent_imported"] is False
            and score["checker_tree_matches_original"] is True)
        checks[ident + ":checker_fixed_expected_outcome"] = (score["checker"]["passed"] is (ident in module.SUCCESS)
            and score["checker"]["checks_expected"] == 2 and score["checker"]["errors"] == [])
        for kind in ("worker", "scorer"):
            for name, expected in read(run / (kind + "-exit-seals") / (ident + ".json")).items():
                checks[ident + ":" + kind + "_exit_hash:" + name] = digest(folder / name) == expected
        details[ident] = {"status": row["status"], "actions": len(actions), "checker_passed": score["checker"]["passed"],
            "archive_complete": worker["agent"]["archive_info"]["complete"], "stop_reason": worker["limits"]["stop_reason"],
            "known_total_tokens": worker["limits"]["known_total_tokens"], "unknown_usage_attempts": worker["limits"]["unknown_usage_attempts"],
            "score_seconds": worker["snapshot"]["score_seconds"], "elapsed_seconds": worker["elapsed"]}
    checks["completed_initial_states_identical"] = all(value == initials["skill_success"] for value in initials.values())
    checks["distinct_completed_command_ids"] = len(set(commands.values())) == 10
    first, b = "skill_success", "post_kill_B"
    a_id, b_id = commands[first].encode(), commands[b].encode()
    checks["post_kill_B_exact_first_request_only_command_id_normalized"] = (first_inputs[first].count(a_id) == first_inputs[b].count(b_id)
        and first_inputs[first].replace(a_id, b"<COMMAND_ID>") == first_inputs[b].replace(b_id, b"<COMMAND_ID>"))
    details["post_kill_B"]["first_request_exact_bytes_equal"] = first_inputs[first] == first_inputs[b]
    details["post_kill_B"]["first_request_command_id_occurrences"] = [first_inputs[first].count(a_id), first_inputs[b].count(b_id)]
    old = Path(manifest["workspace"]) / "docs/results/harness-full-episode-20261025/run-20261025-02"
    checks["prior_full_episode_seal_unchanged"] = digest(old / "campaign-seal.json") == manifest["previous_seal_sha256"]
    for name, expected in read(old / "campaign-seal.json")["files"].items():
        checks["prior_full_episode_file:" + name] = digest(old / name) == expected
    original_oracle = {k: v for k, v in original["checks"].items() if ":oracle:" in k}
    nonoracle = {k: v for k, v in original["checks"].items() if ":oracle:" not in k}
    checks["oracle_check_names_unchanged"] = set(oracle) == set(original_oracle)
    changes = {k: {"before": original_oracle[k], "after": v} for k, v in oracle.items() if original_oracle.get(k) != v}
    corrected = {**nonoracle, **oracle}
    result = {"scope": "read_only_existing_artifacts_no_simulator_checker_model_or_subprocess",
        "run": str(run), "original_seal_sha256": digest(run / "campaign-seal.json"),
        "original_summary_sha256": digest(run / "summary.json"), "helper_sha256": digest(helper),
        "audit_script_sha256": digest(Path(__file__)), "oracle_checks": oracle, "independent_checks": checks,
        "oracle_passed": sum(v is True for v in oracle.values()), "oracle_total": len(oracle),
        "independent_passed": sum(v is True for v in checks.values()), "independent_total": len(checks),
        "changes_from_original_oracle": changes, "trial_details": details,
        "reconstituted_acceptance": {"passed": all(corrected.values()), "passed_count": sum(v is True for v in corrected.values()),
            "total": len(corrected), "note": "Original non-oracle checks retained verbatim, independently checked key boundaries above; no trial rerun."},
        "passed": all(checks.values()) and all(oracle.values())}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
