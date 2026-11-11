"""Read-only audit of frozen v2 evidence; writes only derived files beside this script."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STUDY = ROOT / "experiments/minimal-harness-v2"
OUT = Path(__file__).resolve().parent
ARMS = ("applicability", "failure")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def family(case_id):
    return case_id.rsplit("-", 1)[0]


def segment_distance(point, start, end):
    p = [point[k] for k in ("x", "y")]
    a = [start[k] for k in ("x", "y")]
    b = [end[k] for k in ("x", "y")]
    vector = [b[i] - a[i] for i in range(2)]
    length2 = sum(v * v for v in vector)
    t = max(0, min(1, sum((p[i] - a[i]) * vector[i] for i in range(2)) / length2)) if length2 else 0
    return math.dist(p, [a[i] + t * vector[i] for i in range(2)])


def independent_goal(world, case):
    """Recompute from private final state/history, never from completed strings."""
    cs = world["controllers"]
    drones = cs["drone_controller"]["drones"]
    expected = case["checker"]
    if expected["kind"] == "simple_sequence":
        drone = drones[expected["actor"]]
        hist = drone["history_status"]
        events = [e["event"] for e in hist]
        order = all(k in events for k in ("take_off", "rotate", "hovering"))
        if order:
            order = events.index("take_off") < events.index("rotate") < events.index("hovering")
        passed = (drone["position"]["z"] == expected["altitude"] and drone["heading"] == expected["heading"]
                  and drone["status"] == "hovering" and order)
        return {"passed": passed, "takeoff_history_count": events.count("take_off"),
                "ordered_history": events, "final_altitude": drone["position"]["z"],
                "final_heading": drone["heading"], "final_status": drone["status"]}
    session = cs["session_controller"]["sessions"][case["case_id"]]
    target = cs["target_controller"]["targets"]["goal"]
    visits = [(actor, stamp) for actor, targets in session["target_reaches"].items()
              for stamp in targets.get("goal", [])]
    proofs = []
    for actor, stamp in visits:
        for entry in drones[actor]["history_status"]:
            if entry.get("type") == "waypoint" and entry.get("timestamp") == stamp:
                distance = segment_distance(target["position"], entry["start_position"], entry["position"])
                proofs.append({"actor": actor, "timestamp": stamp, "action": entry["event"],
                               "distance_to_segment": distance, "task_radius": drones[actor]["task_radius"],
                               "target_radius": target["radius"],
                               "within_task_radius": distance <= drones[actor]["task_radius"] + target["radius"] + 1e-6})
    passed = bool(visits) and any(p["within_task_radius"] for p in proofs)
    return {"passed": passed, "target_visits": visits, "matching_physical_segments": proofs}


def aggregate(rows):
    result = {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        result[arm] = {
            "n": len(group), "success": sum(r["checker"]["passed"] for r in group),
            "logical_calls_500": sum(r["logical_calls_within_500"] for r in group),
            "http_attempts": sum(r["http_attempts"] for r in group),
            "usage": {key: sum(r["usage"][key] for r in group) for key in group[0]["usage"]},
            "physical_api_including_common_prefix": {key: sum(r["physical_api"][key] for r in group)
                                                    for key in group[0]["physical_api"]},
            "ready_mean_seconds": statistics.mean(r["ready_seconds"] for r in group),
            "ready_median_seconds": statistics.median(r["ready_seconds"] for r in group),
            "ready_sum_seconds": sum(r["ready_seconds"] for r in group),
            "model_wall_sum_seconds": sum(r["model_wall_seconds"] for r in group),
            "processing_wall_sum_seconds": sum(r["processing_wall_seconds"] for r in group)}
    by_pair = defaultdict(dict)
    for row in rows:
        by_pair[row["block_id"]][row["arm"]] = row
    assert all(set(p) == set(ARMS) for p in by_pair.values())
    paired = []
    for key, pair in sorted(by_pair.items()):
        a, f = (pair[k] for k in ARMS)
        paired.append({"block_id": key,
            "success_delta": int(a["checker"]["passed"]) - int(f["checker"]["passed"]),
            "call_delta": a["logical_calls_within_500"] - f["logical_calls_within_500"],
            "token_delta": a["usage"]["total_tokens"] - f["usage"]["total_tokens"],
            "ready_seconds_delta": a["ready_seconds"] - f["ready_seconds"],
            "model_wall_delta": a["model_wall_seconds"] - f["model_wall_seconds"]})
    result["paired"] = {"n": len(paired),
        "success_W_L_T": [sum(r["success_delta"] > 0 for r in paired),
                          sum(r["success_delta"] < 0 for r in paired), sum(r["success_delta"] == 0 for r in paired)],
        "calls_fewer_more_tied": [sum(r["call_delta"] < 0 for r in paired),
                                 sum(r["call_delta"] > 0 for r in paired), sum(r["call_delta"] == 0 for r in paired)],
        "ready_faster_slower_tied": [sum(r["ready_seconds_delta"] < 0 for r in paired),
                                    sum(r["ready_seconds_delta"] > 0 for r in paired), sum(r["ready_seconds_delta"] == 0 for r in paired)],
        "median_call_delta": statistics.median(r["call_delta"] for r in paired),
        "median_ready_delta": statistics.median(r["ready_seconds_delta"] for r in paired), "rows": paired}
    result["reductions_pct"] = {
        "calls": 100 * (1 - result[ARMS[0]]["logical_calls_500"] / result[ARMS[1]]["logical_calls_500"]),
        "tokens": 100 * (1 - result[ARMS[0]]["usage"]["total_tokens"] / result[ARMS[1]]["usage"]["total_tokens"]),
        "ready_mean": 100 * (1 - result[ARMS[0]]["ready_mean_seconds"] / result[ARMS[1]]["ready_mean_seconds"])}
    return result


def main():
    manifest = read(STUDY / "manifest.json")
    launch = read(STUDY / "launch.json")
    repair = read(STUDY / "execution/queue-repair/manifest.json")
    campaign = read(STUDY / "campaign.json")
    registry = read(STUDY / "runs.json")
    blocks = read(STUDY / "schedule.json")["blocks"]
    assert sha(STUDY / "manifest.json") == launch["manifest_sha256"] == repair["original_manifest_sha256"]
    assert sha(STUDY / "execution/queue-repair/coordinator.py") == repair["source_sha256"]
    assert sha(STUDY / "schedule.json") == repair["schedule_sha256"]
    assert sha(STUDY / "preparation/queue-repair-tests.log") == repair["test_sha256"]
    for name, expected in manifest["files"].items():
        assert sha(STUDY / name) == expected, name
    expected = {f"{b['case_id']}-r{b['repeat']}-{t['arm']}" for b in blocks for t in b["trials"]}
    assert len(expected) == 96 and expected == set(registry) == {p.name for p in (STUDY / "runs").iterdir()}
    records, audits, sealed_files = [], [], 0
    seeds, prefixes, initial_hashes, command_digests = {}, {}, {}, {}
    statuses, retries = Counter(), Counter()
    for ident in sorted(expected):
        folder = STUDY / "runs" / ident
        assert registry[ident]["status"] == "completed"
        seal = read(folder / "seal.json")
        for name, value in seal["files"].items():
            assert sha(folder / name) == value, f"{ident}:{name}"
        assert set(seal["files"]) == {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and p.name != "seal.json"}
        sealed_files += len(seal["files"])
        r, h, b, w = [read(folder / name) for name in ("result.json", "harness.json", "bootstrap.json", "final-world.json")]
        case = read(STUDY / "fixtures" / f"{r['case_id']}.json")
        trace_path = next((folder / "agent-logs/toolchains").glob("*.json"))
        trace = read(trace_path)
        faults = read(folder / "faults.json")
        public_api = [json.loads(line) for line in (folder / "public-api.jsonl").read_text().splitlines()]
        commands = [row for row in public_api if "/command/" in row["endpoint"]]
        command_statuses = Counter(row["response"].get("status") for row in commands)
        command_digests[ident] = digest([(row["endpoint"], row["params"], row["response"].get("status"))
                                        for row in commands])
        assert set(command_statuses) <= {"success", "error"}, (ident, command_statuses)
        r["physical_api"] = {"requests": len(public_api), "action_attempts": len(commands),
            "action_success_receipts": command_statuses["success"],
            "action_error_receipts": command_statuses["error"],
            "actions_after_500_seconds": sum(row["relative_seconds"] > 500 for row in commands)}
        assert not trace["model_request_audit_errors"]
        assert r["initial_state_sha256"] == case["state_sha256"]
        initial_hashes[ident] = r["initial_state_sha256"]
        seeds[ident], prefixes[ident] = digest(b["public_seed"]), digest(b["common_prefix_calls"])
        assert seeds[ident] == case["public_startup_sha256"]
        goal = independent_goal(w, case)
        assert goal["passed"] == r["checker"]["passed"], ident
        metrics = [json.loads(x) for x in (folder / "reproduction-metrics.jsonl").read_text().splitlines()]
        starts = [e for e in metrics if e["event"] == "llm_start"]
        ends = [e for e in metrics if e["event"] == "llm_end"]
        wire = [e for e in metrics if e["event"] == "http_end"]
        assert len(starts) == len(ends) == len(wire) == r["logical_calls"] == r["http_attempts"]
        assert len({e['run_id'] for e in starts}) == len(starts)
        assert not [e for e in metrics if e["event"] in {"llm_error", "http_error"}]
        for key, reported in r["usage"].items():
            assert reported == sum(e["usage"][key] for e in ends) == sum(e["usage"][key] for e in wire)
        assert r["missing_usage_calls"] == 0 and not any(r["usage_missing_fields"].values())
        statuses.update(e["status_code"] for e in wire)
        retries.update(e["retry_index"] for e in wire)
        frames = {f["plan_id"]: f for f in h["frames"]}
        assert len(frames) == 1
        frame = next(iter(frames.values()))
        controls = [e for e in h["events"] if e["kind"] == "model_control_tool"]
        control_results = {e["tool_call_id"]: json.loads(e["content"]) for e in h["events"] if e["kind"] == "control_result"}
        resume_checks = []
        for e in controls:
            assert e["args"]["plan_id"] in frames
            out = control_results[e["tool_call_id"]]
            if e["args"]["action"] == "resume":
                following = next(x for x in h["events"] if x["sequence"] > e["sequence"] and x["kind"] == "skill_enter")
                previous = [x for x in h["events"] if x["sequence"] < e["sequence"] and x["kind"] == "skill_exit"][-1]
                prior_response = b["result"]
                for old in h["events"]:
                    if old["kind"] == "control_result" and old["sequence"] < e["sequence"]:
                        prior_response = json.loads(old["content"])
                assert previous["status"] == "paused" and following["plan_id"] == e["args"]["plan_id"]
                assert following["cursor"] == prior_response["cursor"]
                assert out["status"] in {"paused", "completed"}
                resume_checks.append({"tool_call_id": e["tool_call_id"], "cursor_in": following["cursor"],
                                      "cursor_out": out["cursor"], "status": out["status"]})
        exits = [e for e in h["events"] if e["kind"] == "skill_exit"]
        mandatory = [e for e in exits if e["status"] == "needs_replan"]
        external = [e for e in h["events"] if e["kind"] == "external_tool_start" and e["action"]]
        invalidations = [e for e in h["events"] if e["kind"] == "plan_invalidated"]
        if mandatory:
            assert not [e for e in controls if e["sequence"] > mandatory[0]["sequence"] and e["args"]["action"] == "resume"]
            assert external and all(e["sequence"] > mandatory[0]["sequence"] for e in external)
            assert goal["passed"]
        simple = case["checker"]["kind"] == "simple_sequence"
        if simple:
            assert goal["takeoff_history_count"] == 1
        if case["event"]["kind"] == "receipt_loss":
            assert b["calls"][-1]["tool"] == "get_drone_status"
            assert sum(c["action"] for c in b["calls"]) == 1
            raw_takeoffs = [e for e in external if e["tool"] == "take_off"]
            assert len(raw_takeoffs) == (0 if case["event"]["applied"] else 1)
        action_rows = []
        for step in trace["tool_chain"]:
            if step["tool"] in {e["tool"] for e in external}:
                try:
                    receipt = json.loads(step["observation"])
                except (TypeError, ValueError):
                    receipt = step["observation"]
                action_rows.append({"index": step["index"], "tool": step["tool"], "input": step["tool_input"],
                                    "receipt": receipt})
        a = {"trial_id": ident, "case_id": r["case_id"], "arm": r["arm"], "repeat": r["repeat"],
            "family": family(r["case_id"]), "initial_gate_result": b["result"]["status"],
            "final_plan_status": frame["status"], "final_plan_reason": frame["reason"],
            "faults_expected_if_skill_continued": case["event"].get("count", 0 if case["event"]["kind"] == "none" else 1),
            "faults_exposed": len(faults), "resumes": resume_checks,
            "physical_api_including_common_prefix": r["physical_api"],
            "cancel_count": sum(e["args"]["action"] == "cancel" for e in controls),
            "mandatory_handoffs": [{"reason": e["reason"], "sequence": e["sequence"]} for e in mandatory],
            "external_dependency_invalidations": len(invalidations), "raw_action_tools": [e["tool"] for e in external],
            "actual_raw_receipts": action_rows, "independent_goal": goal,
            "goal_passed": r["checker"]["passed"], "api_calls_skill": frame["api_calls"],
            "repair_count": frame["repairs"], "tool_steps": r["execution_diagnostics"]["tool_steps"],
            "search_rounds": frame["state"].get("round_number", 0),
            "graph_error_type": r["execution_diagnostics"]["graph_error_type"],
            "step_limit": r["execution_diagnostics"]["stopped_due_to_step_limit"],
            "time_limit": r["ready_seconds"] >= 500, "trace": str(trace_path.relative_to(ROOT))}
        if b["result"]["status"] == "completed":
            a["path_class"] = "skill_completed_before_first_model"
        elif mandatory:
            a["path_class"] = "mandatory_handoff_then_verified_raw_repair"
        elif frame["status"] == "completed" and goal["passed"]:
            a["path_class"] = "verified_plan_resume_to_goal"
        elif goal["passed"] and external:
            a["path_class"] = "switched_from_paused_plan_to_verified_raw_goal"
        else:
            a["path_class"] = "no_effective_takeover"
        audits.append(a); records.append(r)
    pairs = []
    for b in blocks:
        ids = [f"{b['case_id']}-r{b['repeat']}-{t['arm']}" for t in b["trials"]]
        left, right = (registry[i] for i in ids)
        assert left["worker"] == right["worker"] == b["worker"]
        assert left["ended_unix"] <= right["started_unix"]
        assert len({initial_hashes[i] for i in ids}) == len({seeds[i] for i in ids}) == len({prefixes[i] for i in ids}) == 1
        pairs.append({"block_id": b["block_id"], "initial_state_matches": True,
                      "public_seed_matches": True, "common_prefix_matches": True, "adjacent_order_matches": True,
                      "ordered_action_endpoints_params_receipt_status_match": len({command_digests[i] for i in ids}) == 1})
    for case in {b["case_id"] for b in blocks}:
        group = sorted([b for b in blocks if b["case_id"] == case], key=lambda b: b["repeat"])
        assert group[0]["worker"] != group[1]["worker"]
        assert [t["arm"] for t in group[0]["trials"]] == [t["arm"] for t in group[1]["trials"]][::-1]
        assert len({initial_hashes[f"{case}-r{r}-{a}"] for r in (1, 2) for a in ARMS}) == 1
    assert max(r["ended_unix"] for r in registry.values() if r["repeat"] == 1) <= min(r["started_unix"] for r in registry.values() if r["repeat"] == 2)
    concurrent, max_concurrent = 0, 0
    for _, delta in sorted([(r[key], delta) for r in registry.values()
                            for key, delta in (("started_unix", 1), ("ended_unix", -1))]):
        concurrent += delta
        max_concurrent = max(max_concurrent, concurrent)
    assert concurrent == 0 and max_concurrent == 2
    for repeat in (1, 2):
        for worker in (0, 1):
            planned = [f"{b['case_id']}-r{repeat}-{t['arm']}" for b in blocks
                       if b["repeat"] == repeat and b["worker"] == worker for t in b["trials"]]
            actual = [r["trial_id"] for r in sorted(registry.values(), key=lambda r:r["started_unix"])
                      if r["repeat"] == repeat and r["worker"] == worker]
            assert planned == actual
    early = set(repair["already_executed_preserved"])
    early_blocks = {r["block_id"] for r in records if r["trial_id"] in early}
    outlier = "mechanism:recoverable_blocked_candidate-06:r2"
    groups = {"all": records, "recoverable": [r for r in records if r["case_id"].startswith("recoverable")],
              "guard_and_normal_controls": [r for r in records if not r["case_id"].startswith("recoverable")],
              "sensitivity_without_initial_serial_pairs": [r for r in records if r["block_id"] not in early_blocks],
              "posthoc_without_long_provider_pair": [r for r in records if r["block_id"] != outlier],
              "posthoc_recoverable_without_long_provider_pair": [r for r in records if r["block_id"] != outlier and r["case_id"].startswith("recoverable")]}
    for f in {family(r["case_id"]) for r in records}:
        groups[f] = [r for r in records if family(r["case_id"]) == f]
    comparison = {name: aggregate(rows) for name, rows in groups.items()}
    comparison["campaign"] = {"wall_seconds": campaign["ended_unix"] - campaign["started_unix"],
        "tokens": sum(r["usage"]["total_tokens"] for r in records), "http_statuses": dict(statuses),
        "retry_indices": dict(retries), "max_tool_steps": max(a["tool_steps"] for a in audits),
        "max_skill_api": max(a["api_calls_skill"] for a in audits), "max_repairs": max(a["repair_count"] for a in audits),
        "max_search_rounds": max(a["search_rounds"] for a in audits),
        "time_budget_hits": [a["trial_id"] for a in audits if a["time_limit"]],
        "graph_errors": [a["trial_id"] for a in audits if a["graph_error_type"]],
        "step_limit_hits": [a["trial_id"] for a in audits if a["step_limit"]]}
    counts = {}
    for arm in ARMS:
        a = [x for x in audits if x["arm"] == arm]
        counts[arm] = {"path_classes": dict(Counter(x["path_class"] for x in a)),
            "resume_calls": sum(len(x["resumes"]) for x in a), "resume_trials": sum(bool(x["resumes"]) for x in a),
            "resume_result_statuses": dict(Counter(r["status"] for x in a for r in x["resumes"])),
            "cancel_calls": sum(x["cancel_count"] for x in a),
            "mandatory_handoff_trials": sum(bool(x["mandatory_handoffs"]) for x in a),
            "external_dependency_invalidations": sum(x["external_dependency_invalidations"] for x in a),
            "fault_exposure_by_family": {f: [x["faults_exposed"] for x in a if x["family"] == f] for f in {x["family"] for x in a}}}
    save("comparison.json", comparison)
    save("trajectory_audit.json", {"counts": counts, "trials": audits})
    save("seal_verification.json", {"passed": True, "executions": 96, "sealed_file_count": sealed_files,
        "frozen_file_count": len(manifest["files"]), "paired_initial_checks": pairs,
        "all_24_initial_states_match_across_four_executions": True,
        "within_worker_order_and_repeat_barrier_verified": True,
        "max_concurrent_executions": max_concurrent,
        "usage_reconciled_between_logical_callback_and_http": True,
        "all_goal_checks_recomputed_from_world_history": True,
        "initial_serial_deviation_trials": sorted(early), "audit_script_sha256": sha(Path(__file__))})
    lines = ["# 96 条真实轨迹核查索引", "", "目标结论来自实际动作回执、世界历史和独立终态复核；不是按 resume 或 completed 文本计分。", "",
             "|执行|首次返回 → 最终计划|故障暴露|实际 resume|路径|独立目标|", "|---|---|---:|---:|---|---|"]
    for a in audits:
        lines.append(f"|{a['trial_id']}|{a['initial_gate_result']} → {a['final_plan_status']}|{a['faults_exposed']}|{len(a['resumes'])}|{a['path_class']}|{'通过' if a['goal_passed'] else '未通过'}|")
    (OUT / "TRAJECTORY_INDEX_CN.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"seals_passed": True, "executions": 96, "files": sealed_files, "counts": counts,
                      "campaign": comparison["campaign"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
