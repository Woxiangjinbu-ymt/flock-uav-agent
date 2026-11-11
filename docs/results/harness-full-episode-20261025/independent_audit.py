"""Read-only independent evidence audit; no project imports, unpickling or execution."""
import base64
import copy
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = HERE / "run-20261025-02"
CHECKS, DETAILS = [], {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def state_digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode())


def check(name, condition, detail=None):
    item = {"name": name, "passed": bool(condition)}
    if detail is not None:
        item["detail"] = detail
    CHECKS.append(item)


def hashes(label, root, mapping):
    mismatches = []
    for name, expected in mapping.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != expected:
            mismatches.append(name)
    check(label, not mismatches, {"files_checked": len(mapping), "mismatches": mismatches})


def decoded(blob):
    raw = base64.b64decode(blob["base64"], validate=True)
    if sha(raw) != blob["sha256"] or len(raw) != blob["length"]:
        raise ValueError("Invalid byte blob")
    return raw


def strip_canary(value):
    if isinstance(value, dict):
        return {k: strip_canary(v) for k, v in value.items() if k != "_episode_private_canary"}
    if isinstance(value, list):
        return [strip_canary(v) for v in value]
    return value


def policy_scan(paths, canary):
    problems = []
    body_count = 0
    def visit(value, where, depth=0):
        nonlocal body_count
        if depth > 24:
            problems.append([where, "excessive_nested_encoding"])
            return
        if isinstance(value, bytes):
            if canary.encode() in value:
                problems.append([where, "private_canary"])
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                return
            try:
                obj = json.loads(text)
            except ValueError:
                return
            visit(obj, where, depth + 1)
        elif isinstance(value, str):
            visit(value.encode(), where, depth + 1)
        elif isinstance(value, dict):
            for key in ("execution_check_apis", "related_apis"):
                if key in value and value[key] not in (None, [], {}):
                    problems.append([where, "nonempty_" + key])
            if {"base64", "sha256", "length"} <= set(value):
                body_count += 1
                visit(decoded(value), where + ":decoded", depth + 1)
            for key, item in value.items():
                if key != "base64":
                    visit(item, where, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, where, depth + 1)
    for path in paths:
        raw = path.read_bytes()
        visit(raw, str(path.relative_to(RUN)))
        if path.suffix == ".jsonl":
            for row in lines(path):
                visit(row, str(path.relative_to(RUN)))
    return {"files_scanned": len(paths), "decoded_byte_blobs": body_count, "violations": problems}


manifest = read(RUN / "manifest.json")
seal = read(RUN / "campaign-seal.json")
seal_hash = sha((RUN / "campaign-seal.json").read_bytes())
hashes("campaign_files", RUN, seal["files"])
actual = {str(p.relative_to(RUN)) for p in RUN.rglob("*") if p.is_file() and p.name != "campaign-seal.json"}
check("campaign_no_unlisted_files", actual == set(seal["files"]))
hashes("frozen_source_files", RUN / "source", manifest["sources"])
hashes("live_source_matches_frozen", ROOT, manifest["sources"])
hashes("frozen_input_references", Path(manifest["study"]), manifest["inputs"])
original_manifest = read(Path(manifest["study"]) / "manifest.json")
hashes("original_full100_410_files", Path(manifest["study"]), original_manifest["files"])
check("manifest_digest", sha((RUN / "manifest.json").read_bytes()) == (RUN / "manifest.sha256").read_text().strip())
check("protocol_digest", sha((RUN / "PROTOCOL_CN.md").read_bytes()) == manifest["protocol_sha256"])
budget = {"max_iterations": 600, "recursion_limit": 1800, "deadline_seconds": 500,
          "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16}
check("unchanged_budget", manifest["budget"] == budget)
expected_trials = ["reference-B", "success-A", "success-B", "failure-A", "failure-B",
                   "timeout-A", "timeout-B", "cancel-A", "cancel-B"]
check("fixed_nine_trials", [t["trial_id"] for t in manifest["trials"]] == expected_trials)
processes = lines(RUN / "process-ledger.jsonl")
check("eighteen_unique_processes", len(processes) == 18 and len({p["pid"] for p in processes}) == 18)
check("fixed_worker_scorer_order", [(p["trial_id"], p["mode"]) for p in processes] ==
      [(name, mode) for name in expected_trials for mode in ("worker", "score")])
check("all_processes_really_exited", all(p["returncode"] == 0 and p["exited"] is True and not p["watchdog_timeout"] for p in processes))
check("joined_before_next_process", all(p["ended_ns"] <= q["started_ns"] for p, q in zip(processes, processes[1:])))
hashes("process_logs", RUN / "process-logs", {p["trial_id"] + "-" + p["mode"] + ".log": p["log_sha256"] for p in processes})
fixture = read(Path(manifest["study"]) / "fixtures/b1eb0270.json")
original_tree_hash = state_digest(fixture["original_task"]["execution_check_apis"])
canary = manifest["private_canary"]
ALL, archives, command_ids = {}, [], []

for index, trial in enumerate(manifest["trials"]):
    name, condition = trial["trial_id"], trial["condition"]
    prefix = name + ":"
    folder = RUN / "trials" / name
    worker, score = read(folder / "worker-result.json"), read(folder / "evaluation/result.json")
    hashes(prefix + "worker_closed_files", folder, worker["files"])
    hashes(prefix + "worker_exit_seal_after_all_B", folder, read(RUN / "worker-exit-seals" / (name + ".json")))
    hashes(prefix + "scorer_exit_seal", folder, read(RUN / "scorer-exit-seals" / (name + ".json")))
    check(prefix + "process_identity", worker["pid"] == processes[2 * index]["pid"] and
          score["pid"] == processes[2 * index + 1]["pid"] and score["worker_pid"] == worker["pid"])
    check(prefix + "score_exact_worker_snapshot", score["worker_result_sha256"] == sha((folder / "worker-result.json").read_bytes()) and
          score["snapshot_sha256"] == worker["snapshot"]["sha256"] == sha((folder / worker["snapshot"]["path"]).read_bytes()))
    initial = read(folder / "private/initial-state.json")
    check(prefix + "initial_original_and_derived_hash", state_digest(initial) == worker["initial"]["derived_state_sha256"] and
          state_digest(strip_canary(initial)) == fixture["state_sha256"] == worker["initial"]["original_state_sha256"])
    check(prefix + "only_private_canary_delta_and_original_tree", worker["initial"]["original_checker_tree_sha256"] ==
          worker["initial"]["derived_checker_tree_without_canary_sha256"] == original_tree_hash and
          worker["initial"]["public_startup_sha256"] == fixture["public_startup_sha256"])
    private_task = read(folder / "evaluation/private-task.json")
    private_tree = private_task["execution_check_apis"]
    private_api = lines(folder / "evaluation/private-api.jsonl")
    check(prefix + "private_canary_positive", private_tree.get("_episode_private_canary") == canary and
          canary in json.dumps(private_api) and state_digest(strip_canary(private_tree)) == original_tree_hash)
    check(prefix + "isolated_original_checker", len(private_api) == 2 and all(r["admin"] and r["phase"] == "scoring" for r in private_api) and
          private_api[-1]["endpoint"] == "/sessions/current/tasks/b1eb0270/check" and
          private_api[-1]["response"]["details"] == score["checker"]["details"] and
          private_api[-1]["response"]["result"] == score["checker"]["passed"] and not score["checker"]["errors"] and
          not score["agent_imported"] and not score["network_attempts"])
    policy_files = [p for p in (folder / "policy").rglob("*") if p.is_file()]
    scan = policy_scan(policy_files, canary)
    check(prefix + "all_policy_canary_and_check_tree_negative", not scan["violations"], scan)
    raw_api = lines(folder / "policy/worldlogs/public-api.jsonl")
    physical = lines(folder / "policy/worldlogs/physical-events.jsonl")
    check(prefix + "public_task_only", all(r["phase"] == "task" and not r["admin"] and
          not r["endpoint"].startswith("/check/") and not r["endpoint"].endswith("/check") for r in raw_api))
    actions = [r for r in raw_api if r["action"]]
    names = [r["endpoint"].rsplit("/", 1)[-1] for r in actions]
    expected_names = ["take_off", "rotate", "hover"] if condition == "success" else ["take_off"]
    check(prefix + "exact_committed_action_sequence", names == expected_names and all(r["committed"] and
          not r.get("error") and r["response"]["status"] == "success" for r in actions))
    public_fleet = next(r["response"] for r in raw_api if r["origin"].get("tool_call_id") == "episode-list")
    actor = next(d["id"] for d in public_fleet if d["name"] == "Drone 3")
    check(prefix + "actor_from_public_fleet", all(r["response"]["drone_id"] == actor and "/" + actor + "/" in r["endpoint"] for r in actions))
    takeoff = physical[0]
    check(prefix + "real_takeoff_before_termination", takeoff["request_id"] == actions[0]["request_id"] and
          takeoff["committed"] and takeoff["actor"] == actor and takeoff["history"]["event"] == "take_off" and
          takeoff["history"]["start_position"]["z"] == 0 and takeoff["history"]["position"]["z"] == 14 and
          takeoff["vertical_distance"] == 14)
    check(prefix + "physical_history_links", len(physical) == len(actions) and
          all(e["request_id"] in {r["request_id"] for r in actions} and e["phase"] == "task" and e["committed"] for e in physical))
    costs = worker["physical_cost"]
    recomputed = {"api_requests": len(raw_api), "action_attempts": len(actions), "physical_history_events": len(physical),
                  "vertical_distance": sum(e["vertical_distance"] for e in physical),
                  "consumed_mah": sum(v["consumed_mah"] for r in actions for v in r["energy"])}
    check(prefix + "costs_from_raw_evidence", all(math.isclose(costs[k], v, abs_tol=1e-10) for k, v in recomputed.items()))
    terminal = worker["terminal"]
    if condition in {"cancel", "timeout"}:
        reason = "cancelled" if condition == "cancel" else "deadline"
        check(prefix + "actual_boundary_and_no_later_actions", terminal["triggered"] is True and
              terminal["trigger_request_id"] == actions[0]["request_id"] and
              terminal["frame_status"] == "interrupted" and terminal["frame_reason"] == terminal["harness_interruption"] == reason and
              not [r for r in raw_api if r["request_id"] > terminal["trigger_request_id"]])
        check(prefix + "original_deadline_or_cancel_signal", terminal["virtual_elapsed"] == (500 if condition == "timeout" else 0) and
              terminal["cancel_flag"] == (condition == "cancel"))
    check(prefix + "budget_and_independent_terminal", worker["budget"] == budget and not worker["network_attempts"] and
          terminal["virtual_elapsed"] <= 500 and worker["agent"]["success"] == score["checker"]["passed"] == (condition == "success"))
    check(prefix + "empty_entry", worker["entry"]["board"] == {"drones": [], "targets": [], "obstacles": []} and
          all(worker["entry"][k] == 0 for k in ("scene_epoch", "harness_frames", "ledger_event_count", "ledger_model_request_count")))
    command = worker["entry"]["command_id"]
    command_ids.append(command)
    archive_path = Path(worker["agent"]["archive_info"]["path"])
    check(prefix + "archive_inside_sealed_policy", archive_path.is_relative_to(folder / "policy") and str(archive_path.relative_to(folder)) in worker["files"])
    rows = lines(archive_path)
    archives.append(rows[0]["archive_id"])
    check(prefix + "archive_identity_and_sequence", [r["sequence"] for r in rows] == list(range(1, len(rows) + 1)) and
          all(r["command_id"] == command and r["episode_id"] == r["archive_id"] == rows[0]["archive_id"] for r in rows) and
          rows[0]["kind"] == "archive_start" and rows[-1]["kind"] == "archive_end")
    models, tools, attempts, sources, requests = {}, {}, {}, {}, []
    joins_ok = True
    for row in rows:
        kind, d = row["kind"], row["data"]
        if kind == "model_start":
            joins_ok &= d["logical_call_id"] not in models
            models[d["logical_call_id"]] = False
        elif kind == "http_request":
            joins_ok &= d["logical_call_id"] in models and not models[d["logical_call_id"]] and d["attempt_id"] not in attempts
            requests.append(decoded(d["body"]))
            attempts[d["attempt_id"]] = {"logical": d["logical_call_id"], "body": bytearray(), "ended": False, "chunks": 0}
        elif kind == "http_response_start":
            a = attempts[d["attempt_id"]]
            joins_ok &= a["logical"] == d["logical_call_id"] and "status_code" not in a
            a["status_code"] = d["status_code"]
        elif kind == "http_response_chunk":
            a = attempts[d["attempt_id"]]
            a["body"].extend(decoded(d["body"])); a["chunks"] += 1
            joins_ok &= d["chunk_index"] == a["chunks"] and "status_code" in a and not a["ended"]
        elif kind == "http_response_end":
            a = attempts[d["attempt_id"]]
            joins_ok &= (d["length"], d["chunk_count"], d["sha256"], d["complete"]) == (len(a["body"]), a["chunks"], sha(a["body"]), True) and not a["ended"]
            a["ended"] = True
        elif kind == "model_end":
            call = d["logical_call_id"]
            joins_ok &= call in models and not models[call]
            models[call] = True
            for message in (d.get("output") or {}).get("result", []):
                for item in message.get("additional_kwargs", {}).get("tool_calls", []) or message.get("tool_calls", []):
                    sources.setdefault(item["id"], []).append(call)
        elif kind == "tool_start":
            joins_ok &= d["tool_span_id"] not in tools and d["input"]["id"] == d["raw_tool_call_id"] and sources[d["raw_tool_call_id"]] == [d["source_model_id"]]
            tools[d["tool_span_id"]] = {"ended": False, "raw_id": d["raw_tool_call_id"]}
        elif kind == "tool_end":
            t = tools[d["tool_span_id"]]
            joins_ok &= not t["ended"] and (d.get("output") is None or d["output"]["tool_call_id"] == t["raw_id"])
            t["ended"] = True
    check(prefix + "raw_archive_lifecycle_and_tool_joins", joins_ok and all(models.values()) and
          all(t["ended"] for t in tools.values()) and all(a["ended"] for a in attempts.values()))
    oracle = read(folder / "policy/transport-oracle.json")
    response_hashes = [{"status_code": a["status_code"], "sha256": sha(a["body"]), "length": len(a["body"])} for a in attempts.values()]
    check(prefix + "actual_transport_byte_oracle", [{"sha256": sha(r), "length": len(r)} for r in requests] == oracle["requests"] and response_hashes == oracle["responses"])
    codes = [a["status_code"] for a in attempts.values()]
    check(prefix + "fixed_model_call_outcomes", codes == ([200, 200, 500] if condition == "failure" else [200] * (3 if condition == "success" else 2)))
    for a in attempts.values():
        check(prefix + "assembled_response_canary_absent:" + a["logical"], canary.encode() not in a["body"])
    check(prefix + "correct_complete_vs_partial", rows[-1]["data"]["complete"] == worker["agent"]["archive_info"]["complete"] == (condition == "success") and
          worker["agent"]["archive_info"]["closed"] and worker["agent"]["harness_closed"])
    check(prefix + "late_callbacks_reported_no_effect", worker["isolation"]["late_archive_unchanged"] is True and worker["isolation"]["late_world_rejected"] is True)
    harness = read(folder / "policy/harness.json")
    check(prefix + "harness_ends_once", harness["task_id"] == command and harness["events"][-1]["kind"] == "task_end" and sum(e["kind"] == "task_end" for e in harness["events"]) == 1)
    replacements = [(f["plan_id"].encode(), ("<PLAN-" + str(i) + ">").encode()) for i, f in enumerate(harness["frames"])]
    replacements.append((command.encode(), b"<COMMAND>"))
    normalized = []
    replacement_counts = []
    for raw in requests:
        counts = []
        for old, new in replacements:
            counts.append(raw.count(old)); raw = raw.replace(old, new)
        normalized.append(raw); replacement_counts.append(counts)
    ALL[name] = {"worker": worker, "score": score, "normalized": normalized, "physical": physical, "raw_actions": actions}
    DETAILS[name] = {"worker_pid": worker["pid"], "scorer_pid": score["pid"], "condition": condition,
        "actual_terminal": terminal["actual_terminal"], "checker_passed": score["checker"]["passed"],
        "archive_complete": worker["agent"]["archive_info"]["complete"], "archive_reasons": worker["agent"]["archive_info"]["reasons"],
        "model_http_attempts": len(requests), "http_statuses": codes, "api_requests": len(raw_api),
        "action_request_ids": [a["request_id"] for a in actions], "actions": names,
        "physical_takeoff_metres": 14, "virtual_elapsed": terminal["virtual_elapsed"],
        "raw_request_sha256": [sha(r) for r in requests], "normalized_request_sha256": [sha(r) for r in normalized],
        "allowed_identity_replacements": [old.decode() for old, _ in replacements], "replacement_counts_per_request": replacement_counts,
        "policy_files_scanned": scan["files_scanned"], "byte_blobs_scanned": scan["decoded_byte_blobs"]}

check("unique_commands_and_archives", len(set(command_ids)) == len(set(archives)) == 9)
reference = ALL["reference-B"]
for name in ("success-B", "failure-B", "timeout-B", "cancel-B"):
    item = ALL[name]
    check(name + ":exact_HTTP_bytes_after_only_ID_mapping", item["normalized"] == reference["normalized"])
    check(name + ":same_full_initial_and_checker", item["worker"]["initial"] == reference["worker"]["initial"] and item["score"]["checker"] == reference["score"]["checker"])
    check(name + ":same_action_signatures_and_costs", item["worker"]["action_signature"] == reference["worker"]["action_signature"] and item["worker"]["physical_cost"] == reference["worker"]["physical_cost"])
    check(name + ":no_other_episode_identity_in_model_inputs", all(other.encode() not in raw for other in command_ids if other != item["worker"]["entry"]["command_id"] for raw in item["normalized"]))

old = HERE / "run-20261025-01"
old_incomplete = read(old / "incomplete.json")
old_processes = lines(old / "process-ledger.jsonl")
check("run01_excluded_and_preserved_as_infrastructure_failure", old_incomplete["workers_completed"] == old_incomplete["scores_completed"] == 0 and len(old_processes) == 1 and old_processes[0]["returncode"] != 0)
check("campaign_seal_unchanged_after_audit", sha((RUN / "campaign-seal.json").read_bytes()) == seal_hash)
result = {"scope": "read_only_independent_artifact_audit_no_simulator_model_test_or_unpickle",
          "run": str(RUN.relative_to(ROOT)), "campaign_seal_sha256": seal_hash,
          "passed": all(c["passed"] for c in CHECKS), "check_count": len(CHECKS),
          "failed_checks": [c for c in CHECKS if not c["passed"]], "checks": CHECKS, "trials": DETAILS,
          "run01": {"status": old_incomplete, "processes": old_processes,
                    "incomplete_sha256": sha((old / "incomplete.json").read_bytes())},
          "limits": ["One original simple_chain task; nine predeclared lifecycle cases, not nine independent benchmark tasks",
                     "Scripted MockTransport and virtual time; no autonomous LLM, provider, training or performance claim",
                     "Closed snapshot byte hashes verified; no pickle deserialization or checker rerun during this audit",
                     "Late callback probes cross-checked against frozen worker code and final seals; no extra callback or trial executed"]}
(HERE / "independent-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"passed": result["passed"], "check_count": len(CHECKS), "failed_checks": result["failed_checks"]}, ensure_ascii=False))
