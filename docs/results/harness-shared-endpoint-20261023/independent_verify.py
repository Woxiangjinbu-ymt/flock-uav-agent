"""Independent read-only audit of a sealed v4/v5 campaign; no runner imports."""
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[2]
RUN = BASE / "run-20261023-01"
OLD = WORKSPACE / "docs/results/harness-blocked-candidate-20261023/run-20261023-01"
TASKS = ["7eba7390", "24ac1838", "b1ad4f3e", "b1eb0270", "898cfdca", "784d71f8", "1905e0ad"]
REVISIONS = ["blocked_candidate_v4", "shared_endpoint_v5"]
CHECKS = []


def read(path):
    return json.loads(path.read_text())


def check(name, value, detail=None, category="invariant"):
    CHECKS.append({"name": name, "passed": bool(value), "detail": detail, "category": category})


def verify_files(name, root, mapping):
    errors = [n for n, h in mapping.items() if not (root / n).is_file()
              or hashlib.sha256((root / n).read_bytes()).hexdigest() != h]
    check(name, not errors, {"count": len(mapping), "mismatches": errors})


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def public_analysis(api):
    state = {}
    actions, errors, moves = [], [], []
    endpoint_groups = defaultdict(list)
    signature_groups = defaultdict(list)
    kinds = Counter()
    for row in api:
        if row["phase"] != "task" or row["admin"] or row["origin"].get("kind") != "skill":
            continue
        tool = row["origin"].get("tool")
        response = row["response"]
        if tool == "list_drones":
            state.update({str(d["id"]): {"request_id": row["request_id"], **d} for d in response})
        elif tool == "get_drone_status":
            state[str(response["id"])] = {"request_id": row["request_id"], **response}
        if not row["action"]:
            continue
        actor = row["endpoint"].split("/")[2]
        event = {"request_id": row["request_id"], "actor": actor,
                 "request": {k: row.get(k) for k in ("method", "endpoint", "params", "body")},
                 "public_state_before": state.get(actor), "receipt": response}
        actions.append(event)
        if tool != "move_to":
            continue
        start = tuple(state[actor]["position"][k] for k in ("x", "y", "z"))
        dest = tuple(row["params"][k] for k in ("x", "y", "z"))
        status = response.get("status")
        message = str(response.get("message", ""))
        kind = ("endpoint_conflict" if status == "error" and message.lower().startswith("target position conflicts with obstacle:")
                else "path_conflict" if status == "error" and "flight path intersects with" in message.lower()
                else "other_error" if status == "error" else "success" if status == "success"
                else "partial" if status == "partial_success" else "unknown")
        kinds[kind] += 1
        move = {**event, "start": start, "destination": dest, "kind": kind}
        move["plan_id"] = row["origin"].get("plan_id")
        moves.append(move)
        if kind in {"endpoint_conflict", "path_conflict", "other_error"}:
            signature = (kind, actor, dest)
            if kind != "endpoint_conflict":
                signature += (start,)
            if kind == "other_error":
                signature += (message,)
            signature_groups[signature].append(row["request_id"])
            errors.append(move)
        if kind == "endpoint_conflict":
            endpoint_groups[(actor, dest)].append(move)
    qualified, invalid = [], []
    for move in moves:
        if move["kind"] != "endpoint_conflict":
            continue
        after = None
        for row in api:
            if (row['request_id'] <= move['request_id'] or row['phase'] != 'task'
                    or row['admin'] or row['origin'].get('kind') != 'skill'
                    or row['origin'].get('plan_id') != move['plan_id']):
                continue
            if row['action'] and row['endpoint'].startswith('/drones/' + move['actor'] + '/'):
                break
            if row['origin'].get('tool') == 'get_drone_status' and row['endpoint'] == '/drones/' + move['actor']:
                after = row
                break
        receipt = move['receipt']
        actual = (after or {}).get('response', {})
        values = actual.get('position', {})
        actual_xyz = tuple(values.get(k) for k in ('x', 'y', 'z'))
        finite_xyz = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                         for v in (*move['start'], *move['destination'], *actual_xyz))
        valid = (receipt.get('command') == 'move_to' and receipt.get('drone_id') == move['actor']
                 and receipt.get('status') == 'error' and re.fullmatch(
                     r"Target position conflicts with obstacle: Position conflicts with (?:circle|ellipse|polygon) obstacle '[^\n]+'",
                     receipt.get('message', '')) is not None
                 and all(receipt.get(k) is None or receipt[k] is False for k in ('success', 'partial_success'))
                 and not re.search(r'\bbattery\b|\bcurrent status\b|\b(?:invalid|missing|required)\b|\baltitude\b[^.\n]*\b(?:maximum|negative|valid)\b',
                     ' '.join(receipt[k] for k in ('message', 'error', 'reason', 'reason_code', 'error_code', 'blocked_reason')
                              if isinstance(receipt.get(k), str)), re.I)
                 and actual.get('id') == move['actor'] and finite_xyz
                 and all(abs(a-b) <= 1e-6 for a,b in zip(move['start'], actual_xyz)))
        move['post_state_request_id'] = (after or {}).get('request_id')
        move['qualified_endpoint_evidence'] = bool(valid)
        (qualified if valid else invalid).append(move)
    prior, cross_events = defaultdict(list), []
    for move in qualified:
        key = move['plan_id'], move['destination']
        sources = [e for e in prior[key] if e['actor'] != move['actor'] and e['request_id'] < move['request_id']]
        if sources:
            cross_events.append({'request_id': move['request_id'], 'actor': move['actor'], 'xyz': move['destination'],
                                 'source_request_ids': [e['request_id'] for e in sources],
                                 'same_actor_overlap': any(e['actor'] == move['actor'] for e in prior[key])})
        prior[key].append(move)
    cross_groups = [{'plan_id': key[0], 'xyz': key[1], 'actors': sorted({e['actor'] for e in events}),
                     'request_ids': [e['request_id'] for e in events]}
                    for key,events in prior.items() if len({e['actor'] for e in events}) > 1]
    return {"actions": actions, "moves": moves, "errors": errors, "classification": dict(kinds),
            "cross_actor_events": cross_events, "cross_actor_groups": cross_groups,
            "unqualified_endpoint_conflicts": [e['request_id'] for e in invalid],
            "unique_failures": len(signature_groups), "signatures": signature_groups,
            "endpoint_repeat_count": sum(len(v) - 1 for v in endpoint_groups.values()),
            "endpoint_repeat_groups": [{"actor": k[0], "endpoint": k[1], "request_ids": [e["request_id"] for e in v],
                                         "starts": [e["start"] for e in v]} for k, v in endpoint_groups.items() if len(v) > 1],
            "idle_move_errors": [e["request_id"] for e in moves if "DroneStatus.IDLE" in str(e["receipt"].get("message", ""))]}


def main():
    manifest, summary, results = (read(RUN / f) for f in ("manifest.json", "summary.json", "results.json"))
    verify_files("campaign_seal", RUN, read(RUN / "campaign-seal.json")["files"])
    verify_files("frozen_source", RUN / "source", manifest["sources"])
    verify_files("live_source_same_as_freeze", WORKSPACE, manifest["sources"])
    study = Path(manifest["study"])
    verify_files("selected_inputs", study, manifest["inputs"])
    verify_files("original_full_manifest", study, read(study / "manifest.json")["files"])
    verify_files("old_campaign_unchanged", OLD, read(OLD / "campaign-seal.json")["files"])
    check("protocol_hash", hashlib.sha256((RUN / "PROTOCOL_CN.md").read_bytes()).hexdigest() == manifest["protocol_sha256"])
    check("fixed_budgets", [manifest[k] for k in ("runtime_seconds", "api_budget", "repair_budget", "search_max_rounds", "worker_watchdog_seconds")] == [500, 2000, 16, 16, 90])
    expected_order = [task + "-" + revision for i, task in enumerate(TASKS) for revision in REVISIONS[::1 if i % 2 == 0 else -1]]
    check("fourteen_fixed_alternating_trials", [r["trial_id"] for r in results] == expected_order)
    check("fourteen_verified", len(results) == 14 and all(r["status"] == "verified" and r["process_returncode"] == 0 for r in results))
    analyses, by_trial, pids, trial_output, pair_output = {}, {}, [], [], []
    for result in results:
        tid, task = result["trial_id"], result["task_id"]
        folder = RUN / "trials" / tid
        start, harness, fixture = read(folder / "start.json"), read(folder / "harness.json"), read(study / "fixtures" / (task + ".json"))
        api = rows(folder / "public-api.jsonl")
        analysis = public_analysis(api)
        analyses[tid], by_trial[tid] = analysis, result
        pids.append(start["pid"])
        check(tid + "/initial_hash", result["initial_state_sha256"] == start["initial_state_sha256"] == fixture["state_sha256"])
        check(tid + "/public_hash", result["public_startup_sha256"] == start["public_startup_sha256"] == fixture["public_startup_sha256"])
        check(tid + "/command", start["command"] == fixture["command"])
        check(tid + "/empty_board", all(not start["board_initial"][k] for k in ("targets", "obstacles", "drones")))
        check(tid + "/no_model_network", result["model_calls"] == 0 and not result["network_attempts"] and result["model"] is None)
        check(tid + "/same_scheduler_budget", harness["scheduler"] == "applicability" and start["deadline_seconds"] == 500
              and all(f["api_budget"] == 2000 and f["repair_budget"] == 16 for f in harness["frames"]))
        check(tid + "/terminal_control", all(c["no_effect"] and c["before"] == c["after"] for c in result["controls"][1:]))
        if result["response"].get("plan_id"):
            check(tid + "/terminal_explicit_reject", all(c["response"]["status"] == "rejected" and c["response"]["resumable"] is False for c in result["controls"][1:]))
        scoring = [r for r in api if r["phase"] == "scoring"]
        check(tid + "/isolated_original_checker", len(scoring) == 1 and scoring[0]["admin"]
              and scoring[0]["response"]["task"]["execution_check_apis"] == fixture["original_task"]["execution_check_apis"]
              and scoring[0]["response"]["details"] == result["checker"]["details"]
              and scoring[0]["response"]["result"] == result["checker"]["passed"]
              and result["checker"]["checks_expected"] == fixture["n_checks"] and not result["checker"]["errors"])
        check(tid + "/no_checker_to_skill", all(not r["admin"] and "/check" not in r["endpoint"] for r in api if r["origin"].get("kind") == "skill"))
        taskrows = [r for r in api if r["phase"] == "task" and not r["admin"]]
        contract = read(folder / 'static-obstacle-contract.json')
        check(tid + '/static_contract', contract['validated_before_start'] and contract['geometry_unchanged']
              and contract['geometry_before'] == contract['geometry_after'] and not contract['denied_task_requests']
              and contract['runtime_parameter'] == (result['revision'] == 'shared_endpoint_v5')
              and contract['timeline']['obstacle_events'] == 0 and contract['lifespan_started'] is False
              and contract['private_geometry_sent_to_policy'] is False)
        check(tid + '/raw_task_allowlist', all(
            not r['admin'] and (r['method'].upper() == 'GET' and (r['endpoint'] in ('/drones', '/sessions/current')
                or re.fullmatch(r'/drones/[^/]+(?:/nearby)?', r['endpoint']) is not None)
                or r['method'].upper() == 'POST' and re.fullmatch(
                    r'/drones/[^/]+/command/(?:take_off|change_altitude|rotate|hover|move_to)', r['endpoint']) is not None)
            for r in api if r['phase'] == 'task'))
        obstacle_state = read(folder / 'score-world.json')['controllers']['obstacle_controller']['obstacles']
        geometry = {oid: {k: o.get(k) for k in contract['geometry_after']['fields']} for oid,o in obstacle_state.items()}
        geometry_sha = hashlib.sha256(json.dumps(geometry, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        check(tid + '/independent_final_geometry_digest', geometry_sha == contract['geometry_after']['sha256']
              and len(geometry) == contract['geometry_after']['obstacle_count'])
        for_frame = harness['frames']
        check(tid + '/single_static_scope', len(for_frame) <= 1 and harness['scene_epoch'] == 0
              and all(f['scene_epoch'] == 0 and f['scene_id'] == start['scene_id'] for f in for_frame))
        raw_actions = [r for r in taskrows if r["action"]]
        action_counts = Counter(r["response"].get("status") for r in raw_actions)
        cost = result["cost"]
        check(tid + "/raw_api_and_action_cost", cost["api_requests"] == len(taskrows) and cost["action_attempts"] == len(raw_actions)
              and all(cost["action_receipts"][s] == action_counts[s] for s in ("success", "partial_success", "error")))
        consumed = sum(e["consumed_mah"] for r in taskrows for e in r.get("energy", []))
        check(tid + "/raw_energy", math.isclose(consumed, cost["consumed_mah"], abs_tol=1e-8))
        deltas = [[e["history"]["position"][k] - e["history"]["start_position"][k] for k in ("x", "y", "z")]
                  for r in taskrows if r["committed"] for e in r.get("history_events", [])]
        check(tid + "/raw_physical_path", len(deltas) == cost["physical_history_events"]
              and math.isclose(sum(math.hypot(d[0], d[1]) for d in deltas), cost["length_xy"], abs_tol=1e-8)
              and math.isclose(sum(math.sqrt(sum(v*v for v in d)) for d in deltas), cost["length_xyz"], abs_tol=1e-8)
              and math.isclose(sum(abs(d[2]) for d in deltas), cost["vertical_distance"], abs_tol=1e-8))
        counts = result["blocked_candidate_metrics"]
        check(tid + "/raw_blocked_metrics", counts["move_attempt_count"] == len(analysis["moves"])
              and counts["all_move_error_count"] == len(analysis["errors"])
              and counts["unique_failure_count"] == analysis["unique_failures"]
              and counts["same_actor_endpoint_conflict_repeat_count"] == analysis["endpoint_repeat_count"]
              and counts["same_actor_endpoint_conflict_repeat_group_count"] == len(analysis["endpoint_repeat_groups"])
              and all(counts["classification"][kind] == analysis["classification"].get(kind, 0) for kind in counts["classification"]))
        check(tid + "/no_idle_move_regression", not analysis["idle_move_errors"])
        shared = result['shared_endpoint_metrics']
        check(tid + '/raw_qualified_shared_metrics', shared['cross_actor_endpoint_repeat_event_count'] == len(analysis['cross_actor_events'])
              and shared['cross_actor_endpoint_repeat_group_count'] == len(analysis['cross_actor_groups'])
              and shared['same_actor_overlap_count'] == sum(e['same_actor_overlap'] for e in analysis['cross_actor_events'])
              and shared['unqualified_endpoint_conflict_count'] == len(analysis['unqualified_endpoint_conflicts']))
        if result["revision"] == "blocked_candidate_v4":
            old = read(OLD / "trials" / tid / "result.json")
            old_analysis = public_analysis(rows(OLD / "trials" / tid / "public-api.jsonl"))
            check(tid + "/historical_reproduction", result["checker"] == old["checker"]
                  and result["public_progress"] == old["public_progress"] and result["response"]["reason"] == old["response"]["reason"]
                  and [a["request"] for a in analysis["actions"]] == [a["request"] for a in old_analysis["actions"]])
            check(tid + "/historical_cost_exact", result["cost"] == old["cost"],
                  {key: {"historical": old["cost"][key], "current": value} for key, value in cost.items() if old["cost"][key] != value},
                  category="reproducibility_diagnostic")
        trial_output.append({"trial_id": tid, "checker_passed": result["checker"]["passed"],
                             "checks": [result["checker"]["checks_passed"], result["checker"]["checks_expected"]],
                             "progress": result["public_progress"], "stop_reason": result["response"]["reason"],
                             "plan_counters": result["plan_counters"], "cost": cost,
                             "all_move_errors": len(analysis["errors"]), "unique_failures": analysis["unique_failures"],
                             "cross_actor_events": analysis['cross_actor_events'],
                             "cross_actor_groups": analysis['cross_actor_groups'],
                             "unqualified_endpoint_conflicts": analysis['unqualified_endpoint_conflicts'],
                             "endpoint_repeats": analysis["endpoint_repeat_groups"]})
    check("fourteen_distinct_processes", len(set(pids)) == 14)
    for task in TASKS:
        left, right = [analyses[task + "-" + v] for v in REVISIONS]
        n = 0
        while n < min(len(left["actions"]), len(right["actions"])) and left["actions"][n]["request"] == right["actions"][n]["request"]:
            n += 1
        identical = n == len(left["actions"]) == len(right["actions"])
        lset, rset = set(left["signatures"]), set(right["signatures"])
        pair = {"task_id": task, "identical_action_sequence": identical, "common_prefix": n,
                "first_different_action_index": None if identical else n + 1,
                "baseline_first_different": None if identical or n == len(left["actions"]) else left["actions"][n],
                "candidate_first_different": None if identical or n == len(right["actions"]) else right["actions"][n],
                "baseline_unique_errors": len(lset), "candidate_unique_errors": len(rset),
                "new_unique_errors": len(rset - lset), "removed_unique_errors": len(lset - rset)}
        pair_output.append(pair)
        saved = next(p for p in summary["pairs"] if p["task_id"] == task)
        contracts = [read(RUN / 'trials' / (task + '-' + revision) / 'static-obstacle-contract.json') for revision in REVISIONS]
        check(task + '/same_initial_obstacle_geometry', contracts[0]['geometry_before'] == contracts[1]['geometry_before'])
        check(task + "/saved_first_divergence", saved["action_comparison"]["common_prefix_actions"] == n
              and saved["action_comparison"]["identical"] == identical)
        if "failure_comparison" in saved:
            check(task + "/saved_unique_failure_delta", saved["failure_comparison"]["new_unique_failure_count"] == len(rset-lset)
                  and saved["failure_comparison"]["removed_unique_failure_count"] == len(lset-rset))
    output = {"method": "Independent static checks and raw public log recomputation; no runner imports, simulation, or model calls.",
              "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "campaign_seal_sha256": hashlib.sha256((RUN / "campaign-seal.json").read_bytes()).hexdigest(),
              "checks_passed": sum(c["passed"] for c in CHECKS), "checks_failed": sum(not c["passed"] for c in CHECKS),
              "invariant_failures": sum(not c["passed"] and c["category"] == "invariant" for c in CHECKS),
              "reproducibility_differences": [c for c in CHECKS if not c["passed"] and c["category"] == "reproducibility_diagnostic"],
              "checks": CHECKS, "trials": trial_output, "pairs": pair_output}
    (BASE / "independent-verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: output[k] for k in ("checks_passed", "checks_failed")}, ensure_ascii=False))
    for c in CHECKS:
        if not c["passed"]:
            print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    main()
