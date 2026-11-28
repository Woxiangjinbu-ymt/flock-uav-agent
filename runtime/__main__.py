"""Prepare or run the paper's frozen stateful 600-task deployment."""
import argparse
import json
import os
from pathlib import Path
import signal

from . import executor, parallel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "preflight", "run", "status", "score"])
    parser.add_argument("--stage", type=Path, default=None)
    parser.add_argument("--attempt", type=Path, help="Attempt directory for status and score")
    parser.add_argument("--settings", type=Path, help="Local file containing the official provider credential")
    plans = parser.add_mutually_exclusive_group()
    plans.add_argument("--expansion", action="store_true", help="Use the registered three training expansion scenes")
    plans.add_argument("--teacher-pilot", action="store_true", help="Use the registered student-context teacher pilot")
    plans.add_argument("--teacher-collection", action="store_true", help="Collect the remaining registered v2 training scenes")
    plans.add_argument("--teacher-supplement", action="store_true", help="Replay the two interrupted training scenes in original order")
    parser.add_argument("--carried-attempt", type=Path, help="Include the accepted pilot when scoring the full training cohort")
    parser.add_argument("--fallback-48k", action="store_true",
                        help="Prepare a teacher pilot with a 48K fallback for otherwise rejected turns")
    args = parser.parse_args()
    if args.fallback_48k and not (args.teacher_pilot and args.command == "prepare"):
        parser.error("--fallback-48k requires prepare --teacher-pilot")
    if args.carried_attempt and not (args.teacher_collection and args.command == "score"):
        parser.error("--carried-attempt requires score --teacher-collection")
    if args.expansion:
        registration = executor.read_json(executor.EXPANSION_REGISTRATION)
        executor.PLAN = executor.EXPANSION_PLAN
        executor.PLAN_SHA = registration["plan_sha256"]
    elif args.teacher_pilot:
        executor.PLAN = executor.PILOT_PLAN
        executor.PLAN_SHA = executor.read_json(executor.PILOT_REGISTRATION)["plan_sha256"]
    elif args.teacher_supplement:
        executor.PLAN = executor.REPAIR_PLAN
        executor.PLAN_SHA = executor.read_json(executor.REPAIR_REGISTRATION)["plan_sha256"]
    elif args.teacher_collection:
        executor.PLAN = executor.COLLECTION_PLAN
        executor.PLAN_SHA = executor.read_json(executor.COLLECTION_REGISTRATION)["plan_sha256"]
    executor.use_stage(args.stage or executor.ROOT.parent / f"{executor.ROOT.name}-runs" / executor.PLAN)
    if args.settings:
        os.environ["FLOCKCUE_SETTINGS"] = str(args.settings.resolve())
    if args.command == "prepare":
        executor.prepare(fallback_48k=args.fallback_48k)
        return 0
    if args.command in {"status", "score"}:
        if not args.attempt:
            parser.error("--attempt is required")
        attempt = args.attempt.resolve()
        if args.command == "status":
            result = parallel.status(executor, attempt)
        else:
            result = score(attempt, args.carried_attempt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("valid_complete_run", True) else 2
    plan = executor.verify_registration()
    if args.command == "verify":
        print(json.dumps({"sessions": len(plan["sessions"]), "tasks": plan["expected_tasks"],
                          "leaf_checks": plan["expected_checks"], "plan_sha256": executor.PLAN_SHA,
                          "sources": len(executor.current_source_hashes())}))
        return 0
    # Child credentials and service configuration are supplied by the coordinator.
    for name in list(os.environ):
        if name.startswith(("UAV_", "LANGCHAIN_", "LANGSMITH_", "REPRO_", "OPENAI_", "LLM_")) or name in {
            "BUPT_API_KEY", "DASHSCOPE_API_KEY", "PYTHONPATH"
        }:
            del os.environ[name]
    os.environ.update(PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", LANGSMITH_TRACING="false",
                      LANGCHAIN_TRACING_V2="false", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    with parallel.first_pass_lock(executor.STAGE):
        if args.command == "preflight":
            return 0 if executor.preflight(gateway=True) else 2

        def interrupt(signum, frame):
            raise KeyboardInterrupt("SIGTERM")

        signal.signal(signal.SIGTERM, interrupt)
        return 0 if parallel.run_attempt(executor, executor.PLAN, workers=8) else 2


def scored_rows(attempt, plan):
    if executor.read_json(attempt / "plan.json") != plan:
        raise ValueError("Attempt differs from the registered plan")
    with parallel.attempt_lock(attempt, exclusive=False):
        parallel.verify_archive(executor, attempt)
        manifest = executor.read_json(attempt / "manifest.json")
        if manifest["status"] != "completed":
            raise ValueError("Attempt is incomplete; use status to inspect progress")
        records = manifest["scenes"]
        rows = []
        passed = strict = 0
        for scene in plan["sessions"]:
            record = records[scene["id"]]
            if record["status"] != "completed":
                raise ValueError(f"Incomplete scene: {scene['id']}")
            scene_rows = parallel.verify_scene(executor, attempt, plan, scene, record)
            rows.extend(scene_rows)
            directory = parallel.safe_directory(attempt, record["directory"])
            terminal_jobs = set()
            with (directory / "agent-traces/reproduction-metrics.jsonl").open() as events:
                for line in events:
                    event = json.loads(line)
                    if (event["event"] == "llm_error" and isinstance(event.get("status_code"), int)
                            and event["status_code"] >= 400):
                        terminal_jobs.add(event["job_id"])
            for row in scene_rows:
                success = row["task_pass"] and row["job_id"] not in terminal_jobs
                passed += success
                strict += success and row["continuation_safe"]

    return rows, passed, strict


def score(attempt, carried_attempt=None):
    """Re-evaluate sealed first passes against the registered scenes and original checks."""
    plan = executor.load_plan(executor.PLAN)
    rows, passed, strict = scored_rows(attempt, plan)
    if carried_attempt:
        carried_attempt = carried_attempt.resolve()
        registration = executor.read_json(executor.COLLECTION_REGISTRATION)
        manifest = executor.read_json(carried_attempt / "manifest.json")
        seals = {sid: record["seal_sha256"] for sid, record in manifest["scenes"].items()}
        if carried_attempt.name != registration["carried_attempt"] or seals != registration["carried_seals"]:
            raise ValueError("Expected the three accepted compact-receipt pilot seals")
        scenes = executor.canonical_scenes()
        carried_plan = executor.make_plan(executor.PILOT_PLAN, [(n, s) for n, s in scenes if s["id"] in seals])
        stage = executor.STAGE
        try:
            executor.use_stage(carried_attempt.parent.parent)
            carried_rows, carried_passed, carried_strict = scored_rows(carried_attempt, carried_plan)
        finally:
            executor.use_stage(stage)
        training = executor.collection_train_roles()
        plan = executor.make_plan(executor.COLLECTION_PLAN, [(n, s) for n, s in scenes if s["id"] in training])
        order = {s["id"]: i for i, s in enumerate(plan["sessions"])}
        rows = sorted(rows + carried_rows, key=lambda r: order[r["session_id"]])
        passed += carried_passed
        strict += carried_strict
    result = executor.summarize(plan, rows, "completed")
    result.update(passed_tasks=passed, pass_rate=passed / len(rows),
                  strict_deployment_passed=strict, strict_deployment_rate=strict / len(rows))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
