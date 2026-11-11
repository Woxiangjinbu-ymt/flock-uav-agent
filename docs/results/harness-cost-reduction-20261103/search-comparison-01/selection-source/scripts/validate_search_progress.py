"""Fixed 16-slot local SDK/ASGI comparison; synthetic decisions, no provider.

Delegates worker, scoring, immutable source capture and seals to general_agent.
No result selects a task, changes a threshold or retries an episode.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from general_agent import prepare, run_prepared, verify, load_manifest
from validate_harness_episode import sha, write, check_hashes

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "experiments/search-progress-v8"
PROTOCOL = "docs/results/harness-cost-reduction-20261103/PROTOCOL_CN.md"
TASKS = ("7eba7390", "24ac1838", "b1ad4f3e", "b1eb0270", "898cfdca", "784d71f8", "1905e0ad", "fdd21809")
BUNDLES = (
    ("original-v7", "observed_target_v7", "minimal-harness-full100-v1", "cases-original.json", "profile-v7.json"),
    ("original-v8", "search_progress_v8", "minimal-harness-full100-v1", "cases-original.json", "profile-v8.json"),
    ("current-v7", "observed_target_v7", "task-coverage-v1-study", "cases-current.json", "profile-v7.json"),
    ("current-v8", "search_progress_v8", "task-coverage-v1-study", "cases-current.json", "profile-v8.json"),
)


def prepare_campaign(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Existing attempts cannot be overwritten")
    output.mkdir(parents=True)
    # The campaign driver/protocol/selection are frozen before any worker starts.
    paths = [Path(__file__).resolve(), ROOT / PROTOCOL, *sorted(FIXTURES.rglob("*.json"))]
    sources = {}
    for path in paths:
        name = str(path.relative_to(ROOT))
        data = path.read_bytes()
        target = output / "selection-source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        sources[name] = sha(data)
    bundles = []
    for name, revision, study, cases, profile in BUNDLES:
        manifest = prepare(FIXTURES / profile, ROOT / "experiments" / study,
                           FIXTURES / cases, output / name)
        bundles.append({"name": name, "revision": revision,
            "manifest_sha256": sha((output / name / "manifest.json").read_bytes()),
            "trial_ids": [t["trial_id"] for t in manifest["trials"]],
            "task_ids": [t["task_id"] for t in manifest["trials"]]})
    manifests = [load_manifest(output / b["name"]) for b in bundles]
    if any(m["sources"] != manifests[0]["sources"] for m in manifests[1:]):
        raise ValueError("Shared source drift during preparation; do not run this attempt")
    result = {"schema_version": 1, "scope": "development_scripted_sdk_asgi_search_comparison",
        "planned_slots": 16, "tasks": list(TASKS), "provider_calls_authorized": 0,
        "decision_source": "fixed explicit scripted tool choices, not natural model behavior",
        "input_layout": "full_v1", "bundles": bundles, "selection_sources": sources,
        "source_inventory_sha256": sha(json.dumps(manifests[0]["sources"], sort_keys=True).encode())}
    write(output / "comparison-manifest.json", result)
    (output / "comparison-manifest.sha256").write_text(sha((output / "comparison-manifest.json").read_bytes()) + "\n")
    return result


def load_campaign(output):
    output = Path(output).resolve()
    raw = (output / "comparison-manifest.json").read_bytes()
    if sha(raw) != (output / "comparison-manifest.sha256").read_text().strip():
        raise ValueError("Comparison manifest changed")
    manifest = json.loads(raw)
    check_hashes(output / "selection-source", manifest["selection_sources"])
    for bundle in manifest["bundles"]:
        if sha((output / bundle["name"] / "manifest.json").read_bytes()) != bundle["manifest_sha256"]:
            raise ValueError("Frozen subrun manifest changed")
        load_manifest(output / bundle["name"])
    return manifest


def compare_rows(rows):
    expected = {(t, r) for t in TASKS for r in ("observed_target_v7", "search_progress_v8")}
    if len(rows) != 16 or {(r["task_id"], r["revision"]) for r in rows} != expected:
        raise ValueError("Full fixed 16-slot denominator is required")
    pairs = []
    for task in TASKS:
        old, new = (next(r for r in rows if r["task_id"] == task and r["revision"] == rev)
                    for rev in ("observed_target_v7", "search_progress_v8"))
        if old["initial"] != new["initial"] or old["runtime"] != new["runtime"] or old["trace_sha256"] != new["trace_sha256"]:
            raise ValueError("Pair differs in initial state, runtime or scripted decisions")
        pairs.append({"task_id": task, "v7_passed": old["passed"], "v8_passed": new["passed"],
            "success_lost": old["passed"] and not new["passed"],
            "action_attempt_delta": new["physical_cost"]["action_attempts"] - old["physical_cost"]["action_attempts"],
            "api_request_delta": new["physical_cost"]["api_requests"] - old["physical_cost"]["api_requests"]})
    return {"pairs": pairs, "candidate_preserves_measured_successes": not any(p["success_lost"] for p in pairs),
        "v7_passed": sum(p["v7_passed"] for p in pairs), "v8_passed": sum(p["v8_passed"] for p in pairs),
        "denominator_per_arm": 8, "general_preservation_proven": False, "natural_model_or_token_gain_measured": False}


def collect(output):
    output = Path(output).resolve()
    manifest = load_campaign(output)
    rows, verification = [], []
    for bundle in manifest["bundles"]:
        run = output / bundle["name"]
        verification.append({"name": bundle["name"], **verify(run)})
        sub = load_manifest(run)
        results = json.loads((run / "results.json").read_text())
        if len(results) != len(sub["trials"]):
            raise ValueError("Incomplete run retained; no complete-comparison claim")
        for trial, result in zip(sub["trials"], results):
            worker, score = result["worker"], result["score"]
            if worker is None or score is None or not worker["agent"]["success"] or worker["limits"]["stop_reason"]:
                raise ValueError("Unexpected worker termination; retain attempt and inspect")
            if worker["pid"] == score["pid"] or worker["network_attempts"] or score["network_attempts"]:
                raise ValueError("Isolation failed")
            harness = json.loads((run / "trials" / trial["trial_id"] / "policy/harness.json").read_text())
            frames = [{k: f.get(k) for k in ("skill", "status", "reason", "cursor", "api_calls", "state")}
                      for f in harness.get("frames", [])]
            rows.append({"task_id": trial["task_id"], "trial_id": trial["trial_id"], "bundle": bundle["name"],
                "revision": bundle["revision"], "initial": worker["initial"], "runtime": worker["runtime"],
                "trace_sha256": sub["inputs"][trial["trace_file"]], "passed": score["checker"]["passed"],
                "checks_passed": score["checker"]["checks_passed"], "checks_expected": score["checker"]["checks_expected"],
                "physical_cost": worker["physical_cost"], "frames": frames,
                "worker_pid": worker["pid"], "scorer_pid": score["pid"],
                "archive_complete": worker["agent"]["archive_verify"]["complete"]})
    return {"schema_version": 1, "planned": 16, "started": len(rows), "scored": len(rows), "provider_calls": 0,
            "scope": manifest["scope"], "verification": verification, "rows": rows, **compare_rows(rows)}


def execute(output):
    output = Path(output).resolve()
    manifest = load_campaign(output)
    # Exclusive marker rejects re-running any completed or interrupted campaign.
    with (output / "comparison-started.json").open("x") as stream:
        json.dump({"planned": 16, "automatic_retry": False}, stream)
    try:
        for bundle in manifest["bundles"]:
            run_prepared(output / bundle["name"])
        report = collect(output)
        write(output / "validation.json", report)
        write(output / "comparison-seal.json", {"files": {str(p.relative_to(output)): sha(p.read_bytes())
            for p in sorted(output.rglob("*")) if p.is_file() and p.name != "comparison-seal.json" and "__pycache__" not in p.parts}})
        return report
    except BaseException as exc:
        write(output / "comparison-incomplete.json", {"error": type(exc).__name__, "reason": str(exc), "automatic_retry": False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "verify"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_campaign(args.output)
    elif args.action == "run":
        result = execute(args.output)
    else:
        seal = json.loads((args.output / "comparison-seal.json").read_text())
        check_hashes(args.output, seal["files"])
        actual = {str(p.relative_to(args.output)) for p in args.output.rglob("*") if p.is_file()
            and p.name != "comparison-seal.json" and "__pycache__" not in p.parts}
        if actual != set(seal["files"]):
            raise ValueError("Comparison file inventory changed")
        result = collect(args.output)
        if result != json.loads((args.output / "validation.json").read_text()):
            raise ValueError("Comparison summary changed")
    print(json.dumps({k: v for k, v in result.items() if k not in {"rows", "selection_sources"}}, indent=2))


if __name__ == "__main__":
    main()
