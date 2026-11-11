#!/usr/bin/env python3
"""Read sealed experiments without contacting services or changing live results.

Run with controller/.venv/bin/python. Output is a new, immutable analysis folder.
Only completed scene generations at snapshot time are included. Saved checker
responses are replayed locally; each consumed evidence file is seal-verified.
"""
import argparse
import collections
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import reproduction as r
import reproduction_parallel as rp

RUNS = {
    "deepseek": "20261003-143255-full-5c501118",
    "qwen": "20261004-142512-full-aca112a7",
}


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def summarize(rows):
    n = len(rows)
    passed = sum(x["passed"] for x in rows)
    return {
        "tasks": n, "passed": passed, "failed": n-passed,
        "pass_rate": passed/n if n else None,
        "checks": sum(x["expected_checks"] for x in rows),
        "passed_checks": sum(x["passed_checks"] for x in rows),
        "near_miss_partial": sum(x["bucket"] == "near_miss" for x in rows),
        "zero_pass": sum(x["bucket"] == "zero_pass" for x in rows),
        "zero_pass_single_check": sum(x["bucket"] == "zero_pass" and x["expected_checks"] == 1 for x in rows),
        "other_partial": sum(x["bucket"] == "other_partial" for x in rows),
        "timeouts": sum(x["agent_timed_out"] for x in rows),
        "timeouts_passed": sum(x["agent_timed_out"] and x["passed"] for x in rows),
        "agent_success_but_failed": sum(x["agent_success"] is True and not x["passed"] for x in rows),
        "by_bucket_timeout": dict(collections.Counter(x["bucket"] for x in rows if x["agent_timed_out"])),
        "failed_endpoints": dict(collections.Counter(e for x in rows for e in x["failed_endpoints"])),
        "near_miss_endpoints": dict(collections.Counter(e for x in rows if x["bucket"] == "near_miss" for e in x["failed_endpoints"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    captured = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    evidence, all_rows, manifests = {}, {}, {}
    for model, run_name in RUNS.items():
        attempt = r.RUNS / run_name
        manifest = r.read_json(attempt / "manifest.json")
        plan = r.read_json(attempt / "plan.json")
        manifests[model] = manifest
        save(out / f"{model}-manifest.json", manifest)
        records = []
        for scene in plan["sessions"]:
            rec = manifest["scenes"][scene["id"]]
            if rec["status"] != "completed":
                continue
            directory = rp.safe_directory(attempt, rec["directory"])
            marker = directory / "complete.json"
            assert r.digest(marker.read_bytes()) == rec["seal_sha256"]
            seal = r.read_json(marker)
            assert seal["identity_sha256"] == manifest["identity_sha256"]
            assert seal["plan_digest"] == rp.json_digest(r, rp.scene_plan(plan, scene))
            evidence[str(marker.relative_to(r.ROOT))] = r.digest(marker.read_bytes())

            def verified(path):
                data = path.read_bytes()
                checksum = r.digest(data)
                assert checksum == seal["files"][str(path.relative_to(directory))], path
                evidence[str(path.relative_to(r.ROOT))] = checksum
                return json.loads(data)

            # Verify the exact files consumed by the offline original evaluator.
            verified(directory / "scene.json")
            rows = verified(directory / "results.json")
            for row in rows:
                for suffix in ("barrier", "window", "agent-state", "checks"):
                    verified(directory / f"{row['task_id']}.{suffix}.json")
            assert rows == rp.validate_scene_evidence(r, plan, scene, directory)
            source_path = r.DATA / scene["file"]
            evidence[str(source_path.relative_to(r.ROOT))] = r.digest(source_path.read_bytes())
            source = r.read_json(source_path)
            tasks = {x["id"]: x for x in source["tasks"]}
            for ordinal, row in enumerate(rows, 1):
                task = tasks[row["task_id"]]
                passed_checks = sum(x["result"] is True for x in row["leaves"])
                failed = [x for x in row["leaves"] if x["result"] is False]
                assert len(row["leaves"]) == row["expected_checks"]
                bucket = ("pass" if row["passed"] else "zero_pass" if passed_checks == 0
                          else "near_miss" if len(failed) == 1 else "other_partial")
                job = verified(directory / f"{row['task_id']}.job.json")
                result = job.get("result") or {}
                steps = result.get("intermediate_steps") or []
                records.append({**row, "model": model, "scene_name": scene["name"],
                    "scene_difficulty": re.search(r" (Easy|Intermediate|Moderate|Hard|Extreme) \d+$", scene["name"]).group(1),
                    "ordinal": ordinal, "generation": rec["generation"], "bucket": bucket,
                    "passed_checks": passed_checks, "failed_checks": len(failed),
                    "failed_endpoints": [x["definition"]["endpoint"] for x in failed],
                    "command": task["content"], "check_tree": task["execution_check_apis"],
                    "template": task.get("originated_from"),
                    "evidence_dir": str(directory.relative_to(r.ROOT)),
                    "job_path": str((directory / f"{row['task_id']}.job.json").relative_to(r.ROOT)),
                    "output": result.get("output"), "recorded_steps": len(steps),
                    "tool_counts": dict(collections.Counter(s[0].get("tool") for s in steps)),
                    "created_at": job.get("created_at"), "completed_at": job.get("completed_at")})
        all_rows[model] = records
        save(out / f"{model}-tasks.json", records)
    q_ids = {(x["session_id"], x["task_id"]) for x in all_rows["qwen"]}
    paired_d = [x for x in all_rows["deepseek"] if (x["session_id"], x["task_id"]) in q_ids]
    dmap = {(x["session_id"], x["task_id"]): x for x in paired_d}
    pair_counts = collections.Counter()
    for q in all_rows["qwen"]:
        d = dmap[(q["session_id"], q["task_id"])]
        assert d["command"] == q["command"] and d["check_tree"] == q["check_tree"]
        pair_counts["both_pass" if d["passed"] and q["passed"] else "deepseek_only" if d["passed"] else "qwen_only" if q["passed"] else "both_fail"] += 1
    cohorts = {"deepseek_full": all_rows["deepseek"], "deepseek_paired": paired_d, "qwen_sealed": all_rows["qwen"]}
    summary = {"captured_at": captured, "runs": RUNS,
        "scope": "Completed selected generations only; original checker replayed locally; consumed files verified against seals, not a full archive verification.",
        "scene_states": {m: dict(collections.Counter(s["status"] for s in v["scenes"].values())) for m,v in manifests.items()},
        "cohorts": {name: summarize(rows) for name, rows in cohorts.items()},
        "paired_outcomes": dict(pair_counts), "by_group": {}}
    for name, rows in cohorts.items():
        summary["by_group"][name] = {}
        for key in ("family", "scene_difficulty", "template", "ordinal"):
            groups = collections.defaultdict(list)
            for row in rows:
                groups[row[key]].append(row)
            summary["by_group"][name][key] = {k: summarize(v) for k,v in groups.items()}
    save(out / "summary.json", summary)
    save(out / "evidence-sha256.json", evidence)
    failures = [x for rows in all_rows.values() for x in rows if not x["passed"]]
    fields = ["model", "session_id", "scene_name", "scene_difficulty", "family", "generation", "task_id", "ordinal", "name", "bucket", "passed_checks", "expected_checks", "agent_timed_out", "agent_success", "failed_endpoints", "recorded_steps", "command", "output", "job_path"]
    with (out / "failures.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in failures:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], (list,dict)) else row[k] for k in fields})
    save(out / "SHA256SUMS.json", {f.name: r.digest(f.read_bytes()) for f in out.iterdir() if f.is_file()})
    print(json.dumps({"output": str(out), "captured_at": captured, "cohorts": summary["cohorts"], "paired_outcomes": dict(pair_counts)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
