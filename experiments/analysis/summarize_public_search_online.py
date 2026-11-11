#!/usr/bin/env python3
"""Summarize sealed R2 versus opt-in public-search online pilot artifacts."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "experiments/m1r2-public-search-prefix-v2"
CANDIDATES = {"9c0b52a0", "7eba7390", "b1ad4f3e", "5e743d21", "24ac1838", "7cdefb56"}


def read(path: Path):
    return json.loads(path.read_text())


def candidate_contents():
    result = {}
    plan = read(STUDY / "frozen/study.json")["plan"]
    for scene in plan["sessions"]:
        data = read(STUDY / "frozen/data" / scene["file"])
        for task in data["tasks"]:
            if task["id"] in CANDIDATES:
                result[(scene["id"], task["id"])] = task["content"]
    if len(result) != len(CANDIDATES):
        raise ValueError("The six frozen candidate contents were not found")
    return result


def traces_for_scene(attempt: Path, scene: str, needed: set[str]):
    result = []
    directory = attempt / scene / "agent-traces/toolchains"
    for path in directory.glob("*.json"):
        value = read(path)
        command = value.get("command")
        if command in needed and "started_at" in value and "completed_at" in value:
            result.append(value)
    return result


def summarize_arm(arm: str, attempt: Path, contents: dict):
    summary = read(attempt / "summary.json")
    manifest = read(attempt / "manifest.json")
    rows = read(attempt / "results.json")
    if not summary["valid_complete_run"] or manifest["status"] != "completed":
        raise ValueError(f"Arm {arm} is not a complete sealed attempt")
    if (summary["planned_tasks"], summary["planned_checks"], summary["scored_tasks"],
            summary["scored_checks"]) != (52, 220, 52, 220):
        raise ValueError(f"Arm {arm} denominator changed")
    by_id = {(row["session_id"], row["task_id"]): row for row in rows}
    if len(by_id) != 52:
        raise ValueError(f"Arm {arm} has duplicate/missing rows")
    traces = {scene: traces_for_scene(attempt, scene,
              {command for (sid, _), command in contents.items() if sid == scene})
              for scene in {key[0] for key in contents}}
    candidates = {}
    for key, command in contents.items():
        row = by_id[key]
        window = read(attempt / key[0] / f"{key[1]}.window.json")
        matches = [trace for trace in traces[key[0]] if trace["command"] == command]
        if not matches:
            raise ValueError(f"Missing trace for {key}")
        trace = min(matches, key=lambda trace: abs(
            datetime.fromisoformat(trace["started_at"]).timestamp() - window["opened_at"]))
        if abs(datetime.fromisoformat(trace["started_at"]).timestamp() - window["opened_at"]) > 15:
            raise ValueError(f"Trace does not match task window for {key}")
        evidence = trace.get("task_progress", {}).get("task_evidence", {})
        runs = evidence.get("coordinated_search_runs", [])
        candidates[key[1]] = {
            "scene_id": key[0], "task_pass": row["task_pass"],
            "agent_timed_out": row["agent_timed_out"],
            "checks_passed": sum(leaf.get("result") is True for leaf in row["leaves"]),
            "checks_total": len(row["leaves"]),
            "trace_status": trace["status"],
            "trace_elapsed_s": round((datetime.fromisoformat(trace["completed_at"]) -
                                       datetime.fromisoformat(trace["started_at"])).total_seconds(), 3),
            "public_progress_percentages": [x["progress_percentage"] for x in evidence.get("progress_observations", [])],
            "search_runs": runs,
            "token_usage": row.get("token_usage"),
        }
    started = datetime.fromisoformat(manifest["started_at"])
    finished = datetime.fromisoformat(manifest["finished_at"])
    return {
        "attempt": str(attempt), "summary": summary,
        "elapsed_wall_s": round((finished - started).total_seconds(), 3),
        "scored_task_passes": sum(row["task_pass"] is True for row in rows),
        "scored_check_passes": sum(leaf.get("result") is True for row in rows for leaf in row["leaves"]),
        "agent_timed_out_tasks": sum(row["agent_timed_out"] is True for row in rows),
        "tokens": {key: sum((row.get("token_usage") or {}).get(key, 0) or 0 for row in rows)
                   for key in ("prompt_tokens", "completion_tokens", "total_tokens", "llm_calls")},
        "observed_provider_tokens": {key: sum((row.get("observed_token_usage") or {}).get(key, 0) or 0
                                              for row in rows)
                                     for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                                                 "cache_hit_tokens", "cache_miss_tokens", "calls_with_usage")},
        "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = read(STUDY / "runs.json")
    if set(registry) != {"control", "search"}:
        raise ValueError("Both arms must be registered")
    if any(registry[arm]["status"] != "completed_and_verified" for arm in registry):
        raise ValueError("Both arms must be completed and verified")
    contents = candidate_contents()
    arms = {arm: summarize_arm(arm, Path(registry[arm]["attempt"]), contents)
            for arm in ("control", "search")}
    rows = {arm: {(row["session_id"], row["task_id"]): row
                  for row in read(Path(registry[arm]["attempt"]) / "results.json")}
            for arm in ("control", "search")}
    transitions = {name: [] for name in ("both_pass", "control_only", "search_only", "both_fail")}
    for key, baseline in rows["control"].items():
        treatment = rows["search"][key]
        name = ("both_pass" if baseline["task_pass"] and treatment["task_pass"] else
                "control_only" if baseline["task_pass"] else
                "search_only" if treatment["task_pass"] else "both_fail")
        transitions[name].append({"scene_id": key[0], "task_id": key[1],
                                  "candidate": key[1] in CANDIDATES})
    result = {"study": str(STUDY), "arms": arms,
              "task_delta": arms["search"]["scored_task_passes"] - arms["control"]["scored_task_passes"],
              "check_delta": arms["search"]["scored_check_passes"] - arms["control"]["scored_check_passes"],
              "paired_transitions": transitions}
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
