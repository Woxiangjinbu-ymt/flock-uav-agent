#!/usr/bin/env python3
"""Read-only scoring/telemetry/skill aggregation for the frozen online study.

No policy feedback, service requests, retries, or modification of attempt evidence.
Selection and handoff indicators describe observable behavior, not semantic proof
that the model reasoned correctly. Formal comparisons require complete verified arms.
"""
from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import statistics
import time


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def records(path):
    values, malformed = [], 0
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
    return values, malformed


def skill_audit(trace, eligibility):
    if trace is None:
        return {"trace_missing": True}
    harness = trace.get("harness")
    if harness is None:
        return {"trace_missing": False, "harness_present": False}
    events = harness["events"]
    controls = [e["args"] for e in events if e["kind"] == "model_control_tool"]
    starts = [e for e in controls if e.get("action") == "start"]
    pauses, resumed = set(), set()
    handoffs = []
    for index, event in enumerate(events):
        if event["kind"] == "skill_exit" and event.get("status") == "paused":
            pauses.add(event["plan_id"])
        if event["kind"] == "skill_enter" and event["plan_id"] in pauses:
            resumed.add(event["plan_id"])
        if ((event["kind"] == "skill_exit" and event.get("status") == "needs_replan")
                or event["kind"] == "plan_invalidated"):
            later = events[index + 1:]
            raw = [e for e in later if e["kind"] == "external_tool_start"]
            handoffs.append({"plan_id": event["plan_id"], "reason": event["reason"],
                             "model_requests_after": sum(e["kind"] == "model_request_start" for e in later),
                             "raw_reads_after": sum(not e["action"] for e in raw),
                             "raw_actions_after": sum(e["action"] for e in raw),
                             "first_raw_tools_after": [e["tool"] for e in raw[:5]]})
    return {"trace_missing": False, "harness_present": True, "start_attempts": len(starts),
            "matching_grammar_start_attempts": sum(eligibility != "unsupported" and e.get("skill") == eligibility for e in starts),
            "mismatched_or_unsupported_start_attempts": sum(eligibility == "unsupported" or e.get("skill") != eligibility for e in starts),
            "plan_commits": sum(e["kind"] == "plan_commit" for e in events),
            "resume_attempts": sum(e.get("action") == "resume" for e in controls),
            "cancel_attempts": sum(e.get("action") == "cancel" for e in controls),
            "plans_paused": len(pauses), "paused_plans_reentered": len(resumed),
            "control_rejections": sum(e["kind"] == "control_rejected" for e in events),
            "plan_rejections": sum(e["kind"] == "plan_rejected" for e in events),
            "final_plan_statuses": dict(Counter(f["status"] for f in harness["frames"])),
            "final_plan_reasons": [f["reason"] for f in harness["frames"]],
            "handoffs": handoffs,
            "locally_handled_failure_continuations": sum(e["kind"] == "gate" and e.get("decision") == "CONTINUE"
                and e.get("reason") in {"blocked_candidate_handled", "actor_takeoff_failed_disabled"} for e in events)}


def task_metrics(events, window, barrier):
    starts = [e for e in events if e["event"] == "llm_start"]
    ends = [e for e in events if e["event"] in {"llm_end", "llm_error"}]
    http = [e for e in events if e["event"] == "http_start"]
    within = lambda e: window is not None and window["opened_at"] <= e["timestamp"] <= window["cutoff_at"]
    return {"telemetry_present": bool(events),
            "logical_model_requests_full_trace": len({e["run_id"] for e in starts}) if events else None,
            "logical_model_requests_in_window": len({e["run_id"] for e in starts if within(e)}) if events and window else None,
            "http_attempts_full_trace": len(http) if events else None,
            "http_attempts_in_window": sum(within(e) for e in http) if events and window else None,
            "logical_model_handler_seconds_full_trace": sum(e.get("duration_seconds") or 0 for e in ends) if events else None,
            "scoring_ready_seconds": barrier["frozen_at"] - window["opened_at"] if window and barrier else None,
            "admission_window_seconds": min(500, barrier["closed_at"] - window["opened_at"]) if window and barrier else None}


def known_total(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    return {"sum": sum(values) if values else None, "observed_tasks": len(values)}


def collect_arm(workspace, arm, record, study, eligible):
    attempt = Path(record["attempt"])
    manifest = read(attempt / "manifest.json", {})
    rows = read(attempt / "results.json", [])
    all_tasks, scene_totals, malformed = [], [], 0
    for scene in study["plan"]["sessions"]:
        directory = attempt / (manifest.get("scenes", {}).get(scene["id"], {}).get("directory") or scene["id"])
        events, bad = records(directory / "agent-traces/reproduction-metrics.jsonl")
        malformed += bad
        by_job = {}
        for event in events:
            by_job.setdefault(event.get("job_id"), []).append(event)
        traces = {}
        for path in (directory / "agent-traces/toolchains").glob("*.json"):
            trace = read(path)
            traces[trace.get("command_id")] = (trace, str(path.relative_to(workspace)))
        for row in [x for x in rows if x["session_id"] == scene["id"]]:
            job_events = by_job.get(row.get("job_id"), [])
            command_ids = {e.get("command_id") for e in job_events if e.get("command_id")}
            matches = [traces[c] for c in command_ids if c in traces]
            trace, trace_path = matches[0] if len(matches) == 1 else (None, None)
            prefix = directory / row["task_id"]
            window, barrier = read(Path(str(prefix) + ".window.json")), read(Path(str(prefix) + ".barrier.json"))
            kind = eligible[row["task_id"]]
            item = {"session_id": row["session_id"], "task_id": row["task_id"], "name": row["name"],
                    "status": row["status"], "task_pass": row.get("task_pass"),
                    "checks_passed": sum(x.get("result") is True for x in row["leaves"]),
                    "checks_expected": row["expected_checks"], "timed_out": row.get("agent_timed_out"),
                    "eligibility": kind, "trace": trace_path,
                    **task_metrics(job_events, window, barrier), "skill": skill_audit(trace, kind)}
            all_tasks.append(item)
        scene_rows = [x for x in all_tasks if x["session_id"] == scene["id"]]
        scene_totals.append({"session_id": scene["id"], "expected": len(scene["tasks"]),
                             "scored": sum(x["status"] == "scored" for x in scene_rows),
                             "passed": sum(x["status"] == "scored" and x["task_pass"] is True for x in scene_rows)})
    valid = record["status"] == "completed_and_verified" and manifest.get("status") == "completed"
    if valid and (len(all_tasks) != 100 or any(x["status"] != "scored" for x in all_tasks) or malformed):
        raise ValueError("Verified arm has incomplete or malformed analysis evidence")
    durations = [x["scoring_ready_seconds"] for x in all_tasks if x["scoring_ready_seconds"] is not None]
    skills = [x for x in all_tasks if x["skill"].get("harness_present")]
    eligible_rows = [x for x in all_tasks if x["eligibility"] != "unsupported"]
    perf = read(attempt / "performance.json", {}).get("selected_generations", {})
    summary = {"arm": arm, "status": record["status"], "complete_verified_comparison_eligible": valid,
               "expected_tasks": 100, "expected_checks": 605,
               "scored": sum(x["status"] == "scored" for x in all_tasks),
               "passed": sum(x["status"] == "scored" and x["task_pass"] is True for x in all_tasks),
               "checks_passed": sum(x["checks_passed"] for x in all_tasks if x["status"] == "scored"),
               "timed_out": sum(x["timed_out"] is True for x in all_tasks),
               "metric_totals": {k: known_total(all_tasks, k) for k in (
                   "logical_model_requests_full_trace", "logical_model_requests_in_window", "http_attempts_full_trace",
                   "http_attempts_in_window", "logical_model_handler_seconds_full_trace", "scoring_ready_seconds")},
               "median_scoring_ready_seconds": statistics.median(durations) if durations else None,
               "malformed_telemetry_lines": malformed, "trace_missing_tasks": sum(x["skill"]["trace_missing"] for x in all_tasks),
               "observed_usage": perf.get("llm", {}).get("usage"),
               "skill_eligibility_count": 13, "eligible_tasks_with_commit": sum(x["skill"].get("plan_commits", 0) > 0 for x in eligible_rows),
               "unsupported_tasks_with_start_attempt": sum(x["eligibility"] == "unsupported" and x["skill"].get("start_attempts", 0) > 0 for x in all_tasks),
               "skill_totals": {k: sum(x["skill"].get(k, 0) for x in skills) for k in (
                   "start_attempts", "plan_commits", "resume_attempts", "plans_paused", "paused_plans_reentered",
                   "locally_handled_failure_continuations", "control_rejections", "plan_rejections")},
               "handoff_tasks": sum(bool(x["skill"].get("handoffs")) for x in skills),
               "handoff_tasks_with_later_raw_action": sum(any(e["raw_actions_after"] > 0 for e in x["skill"].get("handoffs", [])) for x in skills),
               "scenes": scene_totals, "attempt": str(attempt), "tasks": all_tasks}
    summary["task_success_rate"] = summary["passed"] / 100 if valid else None
    return summary


def compare(a, b):
    left = {(r["session_id"], r["task_id"]): r for r in a["tasks"]}
    right = {(r["session_id"], r["task_id"]): r for r in b["tasks"]}
    if len(left) != 100 or left.keys() != right.keys():
        raise ValueError("Comparison is not a complete same-100 pairing")
    groups = {k: [] for k in ("a_only_pass", "b_only_pass", "both_pass", "both_fail")}
    for key in left:
        x, y = left[key], right[key]
        category = "both_pass" if x["task_pass"] and y["task_pass"] else "a_only_pass" if x["task_pass"] else "b_only_pass" if y["task_pass"] else "both_fail"
        groups[category].append({"session_id": key[0], "task_id": key[1], "eligibility": x["eligibility"]})
    deltas = {}
    for metric in ("logical_model_requests_in_window", "logical_model_requests_full_trace", "http_attempts_in_window",
                   "logical_model_handler_seconds_full_trace", "scoring_ready_seconds"):
        x, y = a.get("metric_totals", {}).get(metric, {}), b.get("metric_totals", {}).get(metric, {})
        deltas[metric] = x["sum"] - y["sum"] if x.get("observed_tasks") == y.get("observed_tasks") == 100 else None
    return {"a": a["arm"], "b": b["arm"], "counts": {k: len(v) for k, v in groups.items()},
            "paired_tasks": groups, "a_minus_b_successes": a["passed"] - b["passed"],
            "a_minus_b_metric_totals_complete_100_only": deltas,
            "note": "One development sweep; scene carryover and fixed arm order preclude independent-task significance or a causal efficiency claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    spec = workspace / "experiments/minimal-harness-v1"
    study = read(spec / "frozen/study.json")
    registry = read(spec / "runs.json", {})
    eligible = {e["task_id"]: e["skill"] for e in read(spec / "frozen/eligibility.json")["entries"]}
    arms = [collect_arm(workspace, arm, registry[arm], study, eligible)
            for arm in study["first_exploratory_arm_order"] if arm in registry]
    complete = [a for a in arms if a["complete_verified_comparison_eligible"]]
    result = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": read(spec / "campaign.json", {}).get("status"),
              "complete_arms": len(complete), "expected_arms": 5, "arms": arms,
              "comparisons": [compare(a, b) for a, b in itertools.combinations(complete, 2)],
              "metric_notes": ["Main call counts are unique logical starts within the original 500-second window; full-trace calls and actual HTTP attempts are separate.",
                  "Scoring-ready wall time includes polling and world draining; model handler time is not internal reasoning time.",
                  "A raw action after handoff or a resumed plan does not prove semantically correct recovery; inspect task outcome and linked trace.",
                  "Incomplete arms retain the full 100/605 denominator but have no reported task success rate or formal paired comparison."]}
    output = workspace / "docs/results/minimal-harness-online-20261015"
    save(output / "comparison.json", result)
    lines = ["# 同技能在线对照进度与结果", "", f"更新：{result['updated_at']}。完整且验证通过：{len(complete)}/5 臂。", "",
             "只有完整、封存并验证的臂进入配对比较。未完成臂的通过数只表示当前观测，不作为完整成功率。", "",
             "| 实验臂 | 状态 | 已计分 / 100 | 已通过 | 窗口内逻辑模型调用 | 评分就绪中位耗时（秒） |", "|---|---|---:|---:|---:|---:|"]
    for a in arms:
        calls = a["metric_totals"]["logical_model_requests_in_window"]
        lines.append(f"| {a['arm']} | {a['status']} | {a['scored']} | {a['passed']} | {calls['sum']}（{calls['observed_tasks']} 题有记录） | {a['median_scoring_ready_seconds']} |")
    lines += ["", "技能选择、暂停后恢复、交回模型后的公开动作、退出原因与逐题轨迹见 [comparison.json](comparison.json)。", "",
              "完整任务成功率、配对 W/L 与调用／耗时需要一起解释。当前第一轮开发集探索不提供显著性或非劣性结论；减少调用本身不是成功。", "",
              "自动统计中的‘匹配语法’、‘重新进入计划’与‘交接后有原始动作’是可观察事件，不代表已人工确认语义正确。"]
    (output / "REPORT_CN.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"report": str(output / "REPORT_CN.md"), "complete_arms": len(complete), "model_calls": 0}))


if __name__ == "__main__":
    main()
