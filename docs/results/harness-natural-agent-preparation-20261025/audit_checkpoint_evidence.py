"""Read sealed JSON/JSONL/source bytes only; never import Agent or run a task."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = ROOT / "docs/results/harness-observed-target-20261024/run-20261024-01"
TASKS = ("7eba7390", "24ac1838", "b1ad4f3e")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def receipt_sha(value):
    return sha(json.dumps(value, sort_keys=True, default=str).encode())


seal = read(RUN / "campaign-seal.json")
seal_before = sha((RUN / "campaign-seal.json").read_bytes())
sources = {}


def sealed(path):
    relative = str(path.relative_to(RUN))
    actual = sha(path.read_bytes())
    assert seal["files"][relative] == actual, relative
    sources[str(path.relative_to(ROOT))] = actual
    return path


core = sealed(RUN / "source/agent4drone/harness_core.py").read_text()
skills = sealed(RUN / "source/agent4drone/harness_skills.py").read_text()
sealed(RUN / "source/scripts/harness_v2_online.py")
sealed(RUN / "source/agent4drone/command_results.py")
sealed(RUN / "source/agent4drone/uav_api_client.py")
assert 'self.scheduler == "failure" and item.failed' in core
assert 'yield Checkpoint("blocked_candidate_handled" if outcome == "blocked" else "search_attempt_complete",' in skills
assert 'failed=outcome == "blocked"' in skills
assert 'if frame.repairs > frame.repair_budget:' in skills


def code_lines(text, needle):
    return [i for i, line in enumerate(text.splitlines(), 1) if needle in line]


results = []
for tid in TASKS:
    folder = RUN / "trials" / (tid + "-observed_target_v7")
    harness = read(sealed(folder / "harness.json"))
    result = read(sealed(folder / "result.json"))
    raw = [json.loads(line) for line in sealed(folder / "public-api.jsonl").read_text().splitlines()]
    assert harness["scheduler"] == "applicability" and harness["revision"] == "observed_target_v7"
    assert result["model_calls"] == 0
    events = harness["events"]
    starts = {e["call_id"]: e for e in events if e["kind"] == "public_call_start"}
    ends = {e["call_id"]: e for e in events if e["kind"] == "public_call_end"}

    def public_link(start):
        end = ends[start["call_id"]]
        call = start["call"]
        actor = call["args"].get("drone_id")
        matches = []
        for line, row in enumerate(raw, 1):
            receipt = row.get("response")
            # Reproduce only the visible outcome field added by the frozen
            # local adapter, not a model/policy or command classifier import.
            if row.get("action") and isinstance(receipt, dict):
                outcome = ("completed" if receipt.get("status") == "success" else
                           "blocked" if receipt.get("status") == "error" and
                           (receipt.get("message", "").startswith("Target position conflicts with obstacle:") or
                            receipt.get("message", "").startswith("Path to waypoint ")) else None)
                receipt = {**receipt, "outcome": outcome} if outcome else receipt
            if (row["origin"].get("plan_id") == start["plan_id"]
                    and row["origin"].get("tool") == call["tool"]
                    and (not actor or actor in row["endpoint"].split("/"))
                    and receipt_sha(receipt) == end.get("receipt_sha256")):
                # Repeated identical status reads are resolved by request order:
                # call ordinal plus the fixed initial request offset in this batch.
                ordinal = int(start["call_id"].rsplit(":", 1)[1])
                first = next(r for r in raw if r["origin"].get("plan_id") == start["plan_id"])
                if row["request_id"] == first["request_id"] + ordinal - 1:
                    matches.append((line, row))
        assert len(matches) == 1, (tid, start["call_id"], len(matches))
        line, row = matches[0]
        assert row["phase"] == "task" and not row["admin"]
        return {"public_api_line": line, "request_id": row["request_id"],
                "call_id": start["call_id"], "evidence_id": start["evidence_id"],
                "start_event_sequence": start["sequence"], "end_event_sequence": end["sequence"],
                "client_receipt_sha256": end["receipt_sha256"],
                "raw_receipt_sha256": receipt_sha(row.get("response")),
                "adapter_projection": "add derived outcome only" if row["action"] else "identity"}, row

    candidates = []
    for i, gate in enumerate(events):
        if gate["kind"] != "gate" or gate.get("reason") != "blocked_candidate_handled":
            continue
        update = events[i - 1]
        assert update["kind"] == "plan_update"
        frame = update["frame"]
        state = frame["state"]
        assert gate["decision"] == "CONTINUE" and gate["applicability"] == "valid_or_locally_repairable"
        assert gate["plan_id"] == frame["plan_id"] and gate["cursor"] == frame["cursor"]
        assert frame["status"] == "running" and 0 < frame["repairs"] <= frame["repair_budget"]
        assert state["last_move_outcome"] == "blocked" and state["progress_percent"] <= 95
        assert frame["api_calls"] < frame["api_budget"] and gate["remaining_s"] > 0
        action = next(e for e in reversed(events[:i]) if e["kind"] == "public_call_start" and e["call"]["action"])
        assert action["call"]["tool"] == "move_to" and action["plan_id"] == frame["plan_id"]
        assert state["last_action"]["args"] == action["call"]["args"]
        action_link, row = public_link(action)
        assert row["action"] and row["committed"] and row["response"]["status"] == "error"
        assert row["params"] == {k: v for k, v in action["call"]["args"].items() if k != "drone_id"}
        assert not row["history_events"] and not row["energy"]
        actor = action["call"]["args"]["drone_id"]
        status_reads = [e for e in events[:i] if e["kind"] == "public_call_start"
                        and e["call"]["tool"] == "get_drone_status"
                        and e["call"]["args"].get("drone_id") == actor]
        before = next(e for e in reversed(status_reads) if e["sequence"] < action["sequence"])
        after = next(e for e in status_reads if e["sequence"] > action["sequence"])
        before_link, before_row = public_link(before)
        after_link, after_row = public_link(after)
        assert before_row["response"]["position"] == after_row["response"]["position"]
        next_action = next((e for e in events[i + 1:] if e["kind"] == "public_call_start" and e["call"]["action"]), None)
        next_link = public_link(next_action)[0] if next_action else None
        candidates.append({"gate_sequence": gate["sequence"], "plan_update_sequence": update["sequence"],
            "plan_id": frame["plan_id"], "cursor": frame["cursor"], "repairs": frame["repairs"],
            "repair_budget": frame["repair_budget"], "api_calls": frame["api_calls"],
            "api_budget": frame["api_budget"], "remaining_seconds": gate["remaining_s"],
            "cached_public_progress": state["progress_percent"], "action": action_link,
            "actor": actor, "destination": row["params"], "public_error": row["response"]["message"],
            "status_before": before_link, "status_after": after_link,
            "verified_unchanged_position": after_row["response"]["position"],
            "next_actual_action_in_applicability_trace": next_link,
            "observed_decision": "CONTINUE", "counterfactual_same_state_failure_gate": "paused/failure_checkpoint",
            "counterfactual_scope": "source_branch_only_not_executed_failure_arm_or_natural_model"})
    errors = [r for r in raw if r["action"] and r.get("response", {}).get("status") == "error"]
    used = {c["action"]["request_id"] for c in candidates}
    excluded = [{"request_id": r["request_id"], "endpoint": r["endpoint"],
                 "public_error": r["response"]["message"]} for r in errors if r["request_id"] not in used]
    final = harness["frames"][0]
    results.append({"task_id": tid, "trial": str(folder.relative_to(ROOT)),
        "failed_checkpoint_candidate_count": len(candidates), "public_action_error_count": len(errors),
        "actual_paused_exit_count": sum(e["kind"] == "skill_exit" and e.get("status") == "paused" for e in events),
        "actual_model_call_count": result["model_calls"], "final_status": final["status"],
        "final_reason": final["reason"], "final_cursor": final["cursor"], "final_repairs": final["repairs"],
        "final_public_progress": result["public_progress"]["progress_percentage"],
        "candidates": candidates, "action_errors_without_failed_checkpoint": excluded})

assert [r["failed_checkpoint_candidate_count"] for r in results] == [2, 16, 9]
assert [r["public_action_error_count"] for r in results] == [2, 17, 9]
assert all(r["actual_paused_exit_count"] == 0 for r in results)
assert sha((RUN / "campaign-seal.json").read_bytes()) == seal_before
cost_path = ROOT / "docs/results/minimal-harness-full100-calibration-20261016/trial-metrics.json"
cost_rows = read(cost_path)
cost_ids = ("b1eb0270", "898cfdca", "7eba7390", "784d71f8", "1905e0ad")
selected_costs = [row for row in cost_rows if row["task_id"] in cost_ids]
assert len(selected_costs) == 10
cost_by_task = {}
for tid in cost_ids:
    rows = [row for row in selected_costs if row["task_id"] == tid]
    assert {row["arm"] for row in rows} == {"applicability", "failure"} and len(rows) == 2
    assert all(row["repeat"] == 1 for row in rows)
    cost_by_task[tid] = {"total_tokens": sum(row["usage"]["total_tokens"] for row in rows),
        "trials": [{"trial_id": row["trial_id"], "arm": row["arm"],
                    "total_tokens": row["usage"]["total_tokens"]} for row in rows]}
historical_tokens = sum(row["total_tokens"] for row in cost_by_task.values())
assert historical_tokens == 18011979
output = {"scope": "read_only_sealed_public_evidence_no_agent_import_no_task_no_checker",
    "campaign_seal": str((RUN / "campaign-seal.json").relative_to(ROOT)), "campaign_seal_sha256": seal_before,
    "source_sha256": sources, "script_sha256": sha(Path(__file__).read_bytes()),
    "method": "Enumerate blocked_candidate_handled gates; validate prior plan_update, bounded repairs/API/deadline, last blocked move, receipt SHA, exact public request and unchanged before/after position. Resolve public calls using recorded plan/tool/actor and receipt SHA after the frozen adapter's sole derived outcome addition, plus fixed initial request offset and call ordinal; assert unique match. Record both raw and client receipt SHA. This counts the move-blocked subtype only, not every possible pause/recovery mechanism.",
    "source_branch_lines": {
        "harness_core_failure_checkpoint": code_lines(core, 'self.scheduler == "failure" and item.failed'),
        "harness_skills_failed_move_checkpoint": code_lines(skills, 'yield Checkpoint("blocked_candidate_handled"'),
        "harness_skills_repair_limit_before_checkpoint": code_lines(skills, 'if frame.repairs > frame.repair_budget:')},
    "counts": {"tasks": 3, "candidate_events": 27, "public_action_errors": 28,
               "actual_natural_model_opportunities_verified": 0, "actual_paused_exits_in_source_trials": 0},
    "historical_natural_cost_reference": {
        "source": str(cost_path.relative_to(ROOT)), "source_sha256": sha(cost_path.read_bytes()),
        "task_pairs": 5, "executions": 10, "total_tokens": historical_tokens,
        "per_task": cost_by_task, "not_observed_in_that_calibration": ["24ac1838", "b1ad4f3e"],
        "scope": "Historical reported provider usage only; not current v7 prediction, a fee calculation, online authorization or added budget. Cache input is already included; no double counting."},
    "limits": ["All source trials explicitly start the skill via script and use applicability; legacy origin wording is not evidence of model selection.",
               "The 27 events are correlated within three known development tasks, not independent samples or a predicted natural-agent denominator.",
               "failure would pause at the same checkpoint under the frozen branch, but later trajectory changes cannot be inferred after model intervention.",
               "The 17th 24ac failure exhausts the repair budget before yielding a checkpoint and is a terminal raw-tool handoff, not a resumable paused plan.",
               "Only original fixture and public task command may reach a natural worker; this diagnostic is evaluator-only and must not seed its board or select a skill.",
               "No dynamic Faults injection, no archived generator restore, and no online authorization is provided by this evidence."],
    "tasks": results}
(HERE / "checkpoint-evidence.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"counts": output["counts"], "task_counts": [r["failed_checkpoint_candidate_count"] for r in results]}))
