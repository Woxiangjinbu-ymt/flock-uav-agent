#!/usr/bin/env python3
"""Freeze the original DeepSeek first-attempt scores for the unchanged pilot60.

Offline only: loads the historical verifier and checker from source.zip, replays
saved check responses, and writes only baseline.json / baseline-audit.json here.
No model, service, GUI, credential file, or historical evidence is modified.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import types
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ATTEMPT = ROOT / "experiments/runs/20261003-143255-full-5c501118"
PILOT = ROOT / "experiments/runs/20261006-115922-harness-pilot-3f71f966/plan.json"
LEDGER = ROOT / "docs/results/baseline-deployment-20261005/tasks.json"
TAGS = ROOT / "experiments/subsets/representative-v1/tasks.json"
SCENE_IDS = ("ad34350a", "ef36e27a", "3973d0d6")
B0 = "1794e45e421fb5de03094f0b63f9ca95f86ab42f"
INPUTS = {}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(path):
    return str(Path(path).relative_to(ROOT))


def read_bytes(path):
    path = Path(path)
    require(not path.is_symlink(), f"Symlink input refused: {relative(path)}")
    raw = path.read_bytes()
    key, checksum = relative(path), sha(raw)
    require(key not in INPUTS or INPUTS[key] == checksum, f"Input changed: {key}")
    INPUTS[key] = checksum
    return raw


def read_json(path):
    return json.loads(read_bytes(path))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def frozen_modules(archive, hashes):
    """Execute only archived module definitions; extract the pure checker method."""
    source = {}
    with zipfile.ZipFile(archive) as z:
        require(set(z.namelist()) == set(hashes), "Source archive inventory changed")
        for name, checksum in hashes.items():
            raw = z.read(name)
            require(sha(raw) == checksum, f"Archived source hash mismatch: {name}")
            source[name] = raw
    modules = {}
    for name in ("reproduction_metrics", "reproduction_process", "reproduction", "reproduction_parallel"):
        member = f"scripts/{name}.py"
        module = types.ModuleType(name)
        module.__file__ = str(ROOT / member)
        sys.modules[name] = module
        exec(compile(source[member], f"{archive}!/{member}", "exec"), module.__dict__)
        modules[name] = module
    checker_member = "controller/check_ui/agent_checker.py"
    tree = ast.parse(source[checker_member])
    checker_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AgentCheckerApp")
    method = next(n for n in checker_class.body if isinstance(n, ast.FunctionDef)
                  and n.name == "evaluate_execution_check_node")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), f"{archive}!/{checker_member}", "exec"), namespace)
    checker_type = type("ArchivedSavedResponseChecker", (),
                        {"evaluate_execution_check_node": namespace[method.name]})

    def saved_evaluate(api, tree, session_id):
        checker = checker_type()
        checker.api_server = api
        return checker.evaluate_execution_check_node(tree, session_id)

    r, rp = modules["reproduction"], modules["reproduction_parallel"]
    r.ROOT, r.DATA = ROOT, ROOT / "controller/UAVSessions"
    r.read_json = read_json
    r.upstream_evaluate = saved_evaluate
    loaded = {f"scripts/{name}.py": hashes[f"scripts/{name}.py"] for name in modules}
    loaded[checker_member] = hashes[checker_member]
    return r, rp, loaded


def build():
    read_bytes(Path(__file__))
    ledger_seal = read_json(LEDGER.parent / "SHA256SUMS.json")
    ledger = read_json(LEDGER)
    require(INPUTS[relative(LEDGER)] == ledger_seal[LEDGER.name], "Ledger checksum mismatch")
    tags = {(x["session_id"], x["task_id"]): x for x in read_json(TAGS)}
    pilot = read_json(PILOT)["plans"]["pilot"]
    require(tuple(s["id"] for s in pilot["sessions"]) == SCENE_IDS, "Pilot scene order changed")
    plan = read_json(ATTEMPT / "plan.json")
    manifest = read_json(ATTEMPT / "manifest.json")
    identity = read_json(ATTEMPT / "identity.json")
    require(manifest["git_head"] == B0, "Historical commit is not B0")
    require(manifest["status"] == "completed", "Historical attempt is not completed")
    require(identity["workers"] == manifest["executor"]["workers"] == 8, "Historical worker count changed")
    # configuration.json is the exported, redacted public configuration. Never
    # load agent-settings.json, llm_settings.json, environment keys, or .env.
    configuration = read_json(ATTEMPT / "configuration.json")
    require(configuration["selected_provider"] == "deepseek_official", "Historical provider changed")
    provider = configuration["provider_configs"][configuration["selected_provider"]]
    require(provider["default_model"] == "deepseek-flash", "Historical model changed")
    source_hashes = read_json(ATTEMPT / "source_hashes.json")
    read_bytes(ATTEMPT / "source.zip")
    for name in ("plan.json", "configuration.json", "source_hashes.json", "source.zip"):
        require(INPUTS[relative(ATTEMPT / name)] == identity["files"][name], f"Identity hash mismatch: {name}")
    r, rp, loaded = frozen_modules(ATTEMPT / "source.zip", source_hashes)
    require(rp.json_digest(r, identity) == manifest["identity_sha256"], "Manifest identity mismatch")
    by_scene = {s["id"]: s for s in plan["sessions"]}
    cohort = [x for x in ledger if x["model"] == "deepseek" and x["cohort"] == "first_attempt_no_replay"
              and x["session_id"] in SCENE_IDS]
    require(len(cohort) == 60, "Historical cohort is not exactly 60 records")
    by_task = {(x["session_id"], x["task_id"]): x for x in cohort}
    require(len(by_task) == 60, "Duplicate historical task IDs")
    rows, scenes, job_dates = [], [], []
    for pilot_scene in pilot["sessions"]:
        scene_id = pilot_scene["id"]
        selected = by_scene[scene_id]
        require(len(selected["tasks"]) == 20 and selected["tasks"] == pilot_scene["tasks"],
                f"Historical/pilot task IDs, order, content hashes or checks differ: {scene_id}")
        require(selected["sha256"] == pilot_scene["sha256"], f"Historical/pilot scene differs: {scene_id}")
        record = manifest["scenes"][scene_id]
        require(record["generation"] == 1 and record["directory"] == scene_id
                and record["status"] == "completed" and not record.get("error"),
                f"Scene is not the completed first generation: {scene_id}")
        # Critical: use the HISTORICAL FULL PLAN, not the new 60-task plan.
        raw_rows = rp.verify_scene(r, ATTEMPT, plan, selected, record)
        directory = ATTEMPT / record["directory"]
        seal = read_json(directory / "complete.json")
        for name, checksum in seal["files"].items():
            require(sha(read_bytes(directory / name)) == checksum, f"Scene seal hash mismatch: {scene_id}/{name}")
        metrics_path = directory / "agent-traces/reproduction-metrics.jsonl"
        errors = defaultdict(list)
        for line in read_bytes(metrics_path).splitlines():
            event = json.loads(line)
            if event.get("event") == "llm_error" and isinstance(event.get("status_code"), int) and event["status_code"] >= 400:
                errors[event.get("job_id")].append({k: event.get(k) for k in ("timestamp", "error_type", "status_code", "run_id")})
        require([x["task_id"] for x in raw_rows] == [x["id"] for x in selected["tasks"]], "Raw task order mismatch")
        for ordinal, raw in enumerate(raw_rows, 1):
            key = (scene_id, raw["task_id"])
            saved, tag = by_task[key], tags[key]
            require(saved["generation"] == 1 and saved["evidence_dir"] == relative(directory), "Cohort provenance mismatch")
            for old, new in (("original_passed", "passed"), ("original_status", "status"),
                             ("agent_timed_out", "agent_timed_out"), ("job_id", "job_id"),
                             ("original_leaves", "leaves"), ("expected_checks", "expected_checks")):
                require(saved[old] == raw[new], f"Ledger/raw result mismatch: {key}/{old}")
            require(saved["provider_errors"] == errors.get(raw["job_id"], []), f"Provider accounting mismatch: {key}")
            require(saved["worker_confirmed"] is True and raw["continuation_safe"] is True, "Worker unconfirmed")
            require(not saved["provider_errors"] and raw["agent_timed_out"] is False, "Unexpected infrastructure/timeout evidence")
            require(saved["task_pass"] == saved["deployment_pass"] == raw["passed"], "Task accounting mismatch")
            passed_checks = sum(x["result"] is True for x in raw["leaves"])
            require(passed_checks == saved["observed_passed_checks"], "Passed check count mismatch")
            require(tag["ordinal"] == saved["task_index"] == ordinal and tag["name"] == raw["name"], "Task category/order mapping mismatch")
            prefix = directory / raw["task_id"]
            evidence = {name: relative(Path(str(prefix) + f".{name}.json")) for name in
                        ("before", "after", "window", "barrier", "agent-state", "checks", "job")}
            evidence.update(scene_results=relative(directory / "results.json"), scene_seal=relative(directory / "complete.json"),
                            agent_metrics=relative(metrics_path))
            before, after = read_json(ROOT / evidence["before"]), read_json(ROOT / evidence["after"])
            job = read_json(ROOT / evidence["job"])
            require(job["job_id"] == raw["job_id"], "Saved job ID mismatch")
            job_dates.append(job["created_at"])
            rows.append({"scene_id": scene_id, "scene_name": selected["name"], "family": selected["family"],
                         "task_index": ordinal, "task_id": raw["task_id"], "task_name": raw["name"],
                         "category": tag["generator_category"], "template": tag["template"], "difficulty": raw["difficulty"],
                         "task_pass": saved["task_pass"], "deployment_pass": saved["deployment_pass"],
                         "timeout": raw["agent_timed_out"], "provider_errors": saved["provider_errors"],
                         "worker_confirmed": saved["worker_confirmed"], "checks_passed": passed_checks,
                         "checks_count": raw["expected_checks"], "job_id": raw["job_id"], "job_created_at": job["created_at"],
                         "before_progress": before.get("statistics", {}).get("task_progress"),
                         "after_progress": after.get("statistics", {}).get("task_progress"), "evidence": evidence})
        state = read_json(directory / "scene.json")
        scenes.append({"scene_id": scene_id, "status": "verified", "generation": 1,
                       "started_at": state["started_at"], "finished_at": state["finished_at"],
                       "manifest_record": record, "historical_scene_sha256": selected["sha256"],
                       "seal_sha256": INPUTS[relative(directory / "complete.json")], "sealed_files": len(seal["files"]),
                       "tasks": len(raw_rows), "task_passes": sum(x["passed"] for x in raw_rows),
                       "original_checker_replay": "passed", "worker_and_lease_evidence": "passed"})
    summary = {"tasks": len(rows), "task_passes": sum(x["task_pass"] for x in rows),
               "deployment_passes": sum(x["deployment_pass"] for x in rows), "checks_passed": sum(x["checks_passed"] for x in rows),
               "checks_count": sum(x["checks_count"] for x in rows), "timeouts": sum(x["timeout"] for x in rows),
               "provider_error_tasks": sum(bool(x["provider_errors"]) for x in rows),
               "worker_confirmed": sum(x["worker_confirmed"] for x in rows)}
    require(summary == {"tasks": 60, "task_passes": 47, "deployment_passes": 47, "checks_passed": 293,
                        "checks_count": 320, "timeouts": 0, "provider_error_tasks": 0, "worker_confirmed": 60},
            "Frozen baseline counts differ from the audited 47/60")
    summary["task_pass_rate"] = 47 / 60
    summary["by_scene"] = [{"scene_id": s["scene_id"], "passed": s["task_passes"], "tasks": s["tasks"]} for s in scenes]
    summary["by_category"] = [{"category": c, "tasks": sum(x["category"] == c for x in rows),
                               "passed": sum(x["category"] == c and x["task_pass"] for x in rows)}
                              for c in sorted({x["category"] for x in rows})]
    baseline = {"schema": 1, "id": "b1-remedies-pilot-v1-historical-b0", "status": "frozen",
                "baseline_source": {"attempt": relative(ATTEMPT), "agent_commit": B0, "provider": "deepseek_official",
                                    "model": "deepseek-flash", "date": min(job_dates)[:10],
                                    "attempt_started_at": manifest["started_at"],
                                    "selected_jobs_first_created_at": min(job_dates), "selected_jobs_last_created_at": max(job_dates),
                                    "workers": 8, "cohort": "first_attempt_no_replay", "generation": 1,
                                    "ledger": relative(LEDGER), "historical_plan": relative(ATTEMPT / "plan.json"),
                                    "scene_order_source": relative(PILOT) + "#/plans/pilot"},
                "scene_order": list(SCENE_IDS), "task_order": "original JSON array; all 20 per scene",
                "scoring": "Original external checker; all 60 remain in denominator. No replay/best-of selection.",
                "protocol": plan["protocol"], "summary": summary, "rows": rows,
                "limitations": ["Historical comparator, not a simultaneous B0 control; model alias, service conditions and sampling may differ.",
                                "World resets once per scene, not once per task. Earlier tasks' world/blackboard state can affect later scores.",
                                "Same task order does not guarantee identical dynamic per-task initial state across methods.",
                                "B0 3973d0d6/9c0b52a0 already has 100% task progress in before.json; keep the original pass, but not as independent from-zero completion.",
                                "Three scenes are not 60 independent identically distributed samples; prior labels/diagnoses informed development."]}
    # Recheck every consumed file before calling this frozen audit ready.
    for path, checksum in INPUTS.items():
        require(sha((ROOT / path).read_bytes()) == checksum, f"Input changed during audit: {path}")
    audit = {"schema": 1, "id": baseline["id"], "audited_at": datetime.now(timezone.utc).isoformat(),
             "readiness": {"ready": True, "scope": "Historical B0 evidence only; does not authorize/start or validate a new model run.",
                           "scenes_verified": 3, "tasks_verified": 60, "saved_check_responses_replayed": 320,
                           "worker_lease_records_verified": 60, "ledger_rows_matched": 60, "provider_telemetry_checked": True},
             "verification": {"call": "archived rp.verify_scene(r, historical_attempt, historical_full_plan, historical_selected_scene, manifest_generation1_record)",
                              "verifier_source": relative(ATTEMPT / "source.zip"), "loaded_archive_members_sha256": loaded,
                              "checker": "Unmodified archived evaluate_execution_check_node AST; only saved API responses are replayed.",
                              "archive_member_hashes_verified": len(source_hashes),
                              "identity_checks": "Manifest identity digest plus plan, public configuration, source_hashes and source.zip hashes match saved identity.",
                              "not_accessed": ["agent-settings.json", "llm_settings.json", ".env", "environment credentials"],
                              "not_performed": ["live service requests", "model calls", "runtime writes", "full verify_archive credential-file read"]},
             "scenes": scenes, "summary": summary, "baseline_sha256": sha(encoded(baseline)),
             "input_hash_algorithm": "sha256", "input_evidence_count": len(INPUTS),
             "input_evidence_sha256": dict(sorted(INPUTS.items()))}
    return baseline, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Reverify and compare frozen outputs without writing")
    args = parser.parse_args()
    baseline, audit = build()
    if args.check:
        saved_baseline = json.loads((OUT / "baseline.json").read_bytes())
        saved_audit = json.loads((OUT / "baseline-audit.json").read_bytes())
        require(saved_baseline == baseline, "Frozen baseline differs from reverified evidence")
        for obj in (saved_audit, audit):
            obj.pop("audited_at", None)
        require(saved_audit == audit, "Frozen audit differs from reverified evidence")
        action = "verified without writes"
    else:
        (OUT / "baseline.json").write_bytes(encoded(baseline))
        (OUT / "baseline-audit.json").write_bytes(encoded(audit))
        action = "written"
    print(json.dumps({"status": action, "ready": True, "tasks": 60, "passed": 47,
                      "scenes": len(audit["scenes"]), "input_evidence_count": len(INPUTS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
