"""Bounded offline comparison: current skills, guarded macro, A/F scheduling.

This is a test-only driver, not an alternative production scheduler.  It never
constructs a model.  The macro shares the production skill/guard implementation
on purpose: this tests whether applicability adds different control semantics
once those capabilities are held fixed, not whether the shared skills are novel.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import copy
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
import socket
import subprocess
import time
from types import SimpleNamespace

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "agent4drone"), str(ROOT / "server")]
ARMS = ("guarded_macro", "applicability", "failure")
REVISION = "observed_target_v7"
DEFAULT_CASES = ROOT / "experiments/harness-method-v1/method-cases.json"
SCHEMA = "harness-method-validation-v1"
NETWORK_ATTEMPTS = []


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n")


def semantic_world_state(raw):
    """Ignore only ASGI audit UUID/wall-clock, preserving full raw evidence."""
    result = copy.deepcopy(raw)
    for session in result["controllers"]["session_controller"]["sessions"].values():
        for request in session.get("request_history", []):
            request.pop("request_id", None)
            request.pop("timestamp", None)
    return result


def source_paths():
    explicit = [
        "scripts/validate_harness_method.py", "scripts/tests/test_harness_method_validation.py",
        "scripts/harness_v2_world.py", "scripts/harness_v2_cases.py", "scripts/harness_v2_online.py",
        "experiments/harness-method-v1/method-cases.json",
        "experiments/harness-method-v1/method-protocol.md",
        "agent4drone/requirements.txt", "server/requirements.txt",
    ]
    # Freeze transitive local implementation, not only the two named classes.
    paths = {ROOT / p for p in explicit}
    paths.update((ROOT / "agent4drone").glob("*.py"))
    for name in ("api", "models", "controllers", "path_planning", "utils"):
        paths.update((ROOT / "server" / name).rglob("*.py"))
    paths.update((ROOT / "server").glob("*.py"))
    return sorted(paths)


def build_cases():
    """Produce declared fixtures, with fixed simulator time before construction."""
    from harness_v2_world import World
    from harness_v2_cases import build_case
    World()  # patches model-local clocks; there is no simulation/background loop
    rows = []

    def add(ident, family, number=1, *, category, reason="skill_public_stop_condition",
            checker_passed=False, **extra):
        case = build_case({"case_id": f"{ident}-{number:02}", "family": family})
        case["event"] = {"kind": "none"}
        case.update(category=category, revision=REVISION, static_obstacle_contract=True,
                    api_budget=2000, repair_budget=16,
                    expected={"status": "completed" if reason == "skill_public_stop_condition" else
                              "unsupported" if reason == "unsupported_skill_or_full_task_grammar" else "needs_replan",
                              "reason": reason, "checker_passed": checker_passed,
                              "local_failure_checkpoints": 0}, **extra)
        rows.append(case)
        return case

    add("normal-simple", "valid_successful_checkpoint", category="normal", checker_passed=True)
    add("normal-search", "recoverable_blocked_candidate", category="normal", checker_passed=True)
    blocked = add("static-blocked-candidate", "recoverable_blocked_candidate",
                  category="locally_repairable", checker_passed=True)
    from models.obstacle import Obstacle, ObstacleType
    # This fixed endpoint is > 80 m from every actor initially.  It exists before
    # any public observation; no transient obstacle or forged receipt is used.
    blocked["scene"]["obstacles"].append(Obstacle("Fixed hidden endpoint obstruction", ObstacleType.CIRCLE,
        position={"x": 155, "y": 365, "z": 0}, radius=1, height=100,
        obstacle_id="fixed-endpoint-obstacle").to_dict())
    blocked["expected"]["local_failure_checkpoints"] = 1
    capability = add("takeoff-capability-repair", "recoverable_takeoff_actor",
                     category="locally_repairable", checker_passed=True)
    capability["scene"]["drones"][0]["max_altitude"] = 10
    capability["expected"]["local_failure_checkpoints"] = 1
    for applied in (True, False):
        case = add("unknown-applied" if applied else "unknown-not-applied", "unknown_effect_receipt",
                   category="must_exit", reason="unknown_action_effect_observed_do_not_replay")
        case["event"] = {"kind": "receipt_loss", "applied": applied}
    dependency = add("actor-dependency-change", "invalid_actor_state", category="must_exit",
                     reason="actor_state_changed_between_units")
    dependency["event"] = {"kind": "actor_change", "heading_delta": 15}
    repair = add("repair-budget", "recoverable_takeoff_actor", category="must_exit",
                 reason="local_repair_budget_exhausted")
    repair["repair_budget"] = 1
    for drone in repair["scene"]["drones"]:
        drone["max_altitude"] = 10
    repair["expected"]["local_failure_checkpoints"] = 1
    scope = add("scene-epoch-change", "valid_successful_checkpoint", category="must_exit",
                reason="scene_scope_changed")
    scope["event"] = {"kind": "epoch_after_action"}
    budget = add("api-budget", "valid_successful_checkpoint", category="must_exit",
                 reason="api_budget_exhausted")
    budget["api_budget"] = 3
    grammar = add("extra-obligation", "valid_successful_checkpoint", category="entry_rejected",
                  reason="unsupported_skill_or_full_task_grammar")
    grammar["command"] += " Then take a photo."
    contract = add("missing-static-contract", "recoverable_blocked_candidate", category="entry_rejected",
                   reason="shared_endpoint_requires_static_obstacle_contract")
    contract["static_obstacle_contract"] = False
    return {"schema": SCHEMA, "purpose": "scripted_control_semantics_not_model_performance",
            "arms": list(ARMS), "revision": REVISION, "case_count": len(rows),
            "planned_trials": len(rows) * len(ARMS), "cases": rows}


def macro_execute(context, factory, skill, invoke, ledger, scope):
    """Ordinary guarded macro: run the committed generator until done/guard exit.

    Checkpoints are descriptive records only.  This loop has no scheduler,
    replanning policy, pause/resume path, or access to HarnessRuntime._drive.
    Runtime-common scene/API bounds and unknown receipt handling are retained.
    """
    from harness_core import Checkpoint, PublicCall, SkillExit
    try:
        frame, generator = factory(skill, f"{context.task_id}:p1", context)
    except (ValueError, SkillExit) as exc:
        return None, {"status": "unsupported", "reason": str(exc)}
    frame.status, frame.reason = "running", "skill_enter"
    sent = None
    try:
        while True:
            if scope() != (frame.scene_id, frame.scene_epoch):
                raise SkillExit("scene_scope_changed")
            instruction = generator.send(sent)
            sent = None
            if isinstance(instruction, Checkpoint):
                frame.units += 1
                continue
            if not isinstance(instruction, PublicCall):
                raise SkillExit("invalid_skill_instruction")
            if frame.api_calls >= frame.api_budget:
                raise SkillExit("api_budget_exhausted")
            frame.state["pending_call"] = asdict(instruction)
            ident = ledger.begin_tool(instruction.tool, instruction.args,
                                      f"harness:{frame.plan_id}:{frame.api_calls + 1}")
            frame.api_calls += 1
            try:
                sent = invoke(instruction)
            except Exception as exc:
                ledger.end_tool(ident, error=exc)
                sent = {"harness_transport_error": type(exc).__name__}
            else:
                ledger.end_tool(ident, sent)
            frame.evidence_refs.append(ident)
            frame.state.pop("pending_call", None)
    except StopIteration:
        frame.status, frame.reason = "completed", "skill_public_stop_condition"
    except SkillExit as exc:
        frame.status, frame.reason = exc.status, exc.reason
    finally:
        generator.close()
    return frame, {"status": frame.status, "reason": frame.reason}


def execute_case(case, arm):
    """One fresh worker invokes public ASGI endpoints under the real agent role."""
    if arm not in ARMS:
        raise ValueError("unknown arm")
    from harness_v2_world import World
    from harness_v2_cases import Faults, checker
    from harness_v2_online import client_class
    from blackboard import PerceptionBlackboard
    from task_evidence_r2 import SessionEvidenceIndexR2, TaskEvidenceLedgerR2, public_progress
    from harness_core import Checkpoint, HarnessRuntime
    from harness_skills import make_skill_factory
    from command_results import command_outcome

    world = World()
    # A fresh campaign worker has no commands; also make direct unit invocation
    # start from the same empty command inventory (Session import omits this).
    world.server.drone_controller.commands.clear()
    world.import_session(case["scene"])
    world_api = []
    request = world.request
    def public_request(method, endpoint, **kwargs):
        if kwargs.get("admin") or endpoint.startswith("/check/"):
            raise AssertionError("policy attempted private/admin API")
        value = request(method, endpoint, **kwargs)
        world_api.append({"method": method, "endpoint": endpoint, "params": kwargs.get("params"),
                          "json_body": copy.deepcopy(kwargs.get("json")),
                          "form_body": copy.deepcopy(kwargs.get("data")),
                          "role": "agent", "response_sha256": sha(canonical(value))})
        return value
    world.request = public_request
    initial_state = world.inspect_blob(world.snapshot())
    initial_digest = sha(canonical(initial_state))
    board = PerceptionBlackboard(case["case_id"])
    seed = world.public_seed()
    for actor, nearby in seed["nearby"].items():
        board.ingest_nearby(actor, nearby)
    setup_api_count = len(world_api)
    index = SessionEvidenceIndexR2(case["case_id"])
    ledger = TaskEvidenceLedgerR2(case["command"], case["case_id"], index=index,
                                 aggregate_feedback=True, inherited_blackboard=board.full())
    client = client_class(world)(base_url="http://testserver")
    def invoke(item):
        if item.tool == "get_mission_progress":
            return public_progress(client.get_current_session(), case["case_id"])
        return getattr(client, item.tool)(**item.args)
    faults = Faults(world, case)
    fault_invoke = faults.wrap(invoke)
    epoch = [index.epoch]
    def dispatched(item):
        value = fault_invoke(item)
        if case["event"]["kind"] == "epoch_after_action" and item.action and not faults.events:
            epoch[0] += 1
            faults.events.append({"kind": "epoch_after_action", "at_skill_call": len(faults.calls),
                                  "new_epoch": epoch[0]})
        return value
    scope = lambda: (case["case_id"], epoch[0])
    checkpoints = []
    actual_factory = make_skill_factory(board)
    def factory(skill, plan_id, runtime):
        frame, inner = actual_factory(skill, plan_id, runtime)
        frame.api_budget, frame.repair_budget = case["api_budget"], case["repair_budget"]
        def traced():
            sent = None
            try:
                while True:
                    try:
                        item = inner.send(sent)
                    except StopIteration:
                        return
                    if isinstance(item, Checkpoint):
                        checkpoints.append({"reason": item.reason, "failed": item.failed,
                            "after_public_call": len(faults.calls), "after_action": sum(c["action"] for c in faults.calls),
                            "cursor": frame.cursor, "repairs": frame.repairs})
                    sent = yield item
            finally:
                inner.close()
        return frame, traced()
    controls, snapshot = [], None
    if arm == "guarded_macro":
        context = SimpleNamespace(command=case["command"], task_id=case["case_id"],
            scene_id=case["case_id"], scene_epoch=index.epoch, deadline=500,
            revision=REVISION, static_obstacle_contract=case["static_obstacle_contract"])
        frame, response = macro_execute(context, factory, case["skill"], dispatched, ledger, scope)
        controls.append({"control": "execute_macro", **response, "after_public_call": len(faults.calls),
                         "after_action": sum(c["action"] for c in faults.calls)})
    else:
        runtime = HarnessRuntime(command=case["command"], task_id=case["case_id"],
            scene_id=case["case_id"], scene_epoch=index.epoch, started=0, clock=lambda: 0,
            ledger=ledger, invoke=dispatched, scope=scope, skill_factory=factory,
            scheduler=arm, revision=REVISION,
            static_obstacle_contract=case["static_obstacle_contract"])
        response = runtime.control("start", skill=case["skill"])
        controls.append({"control": "start", **copy.deepcopy(response), "after_public_call": len(faults.calls),
                         "after_action": sum(c["action"] for c in faults.calls)})
        while response["status"] == "paused":
            if len(controls) > 2000:
                raise AssertionError("scripted resume limit")
            response = runtime.control("resume", plan_id=response["plan_id"])
            controls.append({"control": "scripted_resume", **copy.deepcopy(response),
                             "after_public_call": len(faults.calls),
                             "after_action": sum(c["action"] for c in faults.calls)})
        frame = next(iter(runtime.frames.values()), None)
        runtime.close("offline_validation_done")
        snapshot = runtime.snapshot()
    calls = copy.deepcopy(faults.calls)
    actions = [c for c in calls if c["action"]]
    final_state = world.inspect_blob(world.snapshot())
    # Post-execution scoring only.  This callable is never supplied to a skill.
    score = checker(world, case)
    score["scope"] = "fixture_obligations_independent_post_execution_not_policy_input"
    first = controls[0]
    result = {"schema": SCHEMA, "case_id": case["case_id"], "category": case["category"], "arm": arm,
        "worker_pid": os.getpid(), "revision": REVISION, "model_calls": 0, "network_attempts": len(NETWORK_ATTEMPTS),
        "initial_state_sha256": initial_digest, "initial_state": initial_state,
        "public_setup": seed, "setup_api_count": setup_api_count,
        "calls": calls, "actions": actions, "public_api": world_api, "events": faults.events,
        "checkpoints": checkpoints, "controls": controls, "first_return": {
            k: first[k] for k in ("status", "reason", "after_public_call", "after_action")},
        "terminal": {"status": response["status"], "reason": response["reason"]},
        "frame": asdict(frame) if frame else None, "harness": snapshot, "ledger": ledger.snapshot(),
        "final_state": final_state, "final_state_sha256": sha(canonical(final_state)),
        "final_semantic_state": semantic_world_state(final_state), "checker": score,
        "opportunities": {"local_failure_checkpoints": sum(c["failed"] for c in checkpoints),
            "all_checkpoints": len(checkpoints), "scheduling_pause_returns": sum(c["status"] == "paused" for c in controls),
            "scripted_resumes": sum(c["control"] == "scripted_resume" for c in controls),
            "mandatory_exit": int(response["status"] == "needs_replan"),
            "known_failed_action_receipts": sum(command_outcome(a["public_result"]) in {"blocked", "error", "partial"} for a in actions),
            "unknown_action_receipts": sum(command_outcome(a["public_result"]) == "unknown" for a in actions)}}
    return result


def compare_results(case, rows):
    checks = []
    def check(name, passed, detail=None):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
    expected = case["expected"]
    check("all_three_planned_arms_retained", set(rows) == set(ARMS))
    complete = all(arm in rows and "worker_error" not in rows[arm] for arm in ARMS)
    check("all_three_workers_completed", complete)
    if not complete:
        return checks
    reference = rows["guarded_macro"]
    for arm in ARMS:
        row = rows[arm]
        check(f"{arm}:declared_terminal", row["terminal"] == {k: expected[k] for k in ("status", "reason")})
        check(f"{arm}:declared_fixture_score", row["checker"]["passed"] is expected["checker_passed"])
        check(f"{arm}:declared_local_failure_opportunities",
              row["opportunities"]["local_failure_checkpoints"] == expected["local_failure_checkpoints"])
        check(f"{arm}:no_model_or_network", row["model_calls"] == row["network_attempts"] == 0)
        check(f"{arm}:only_public_agent_role", all(p["role"] == "agent" and not p["endpoint"].startswith("/check/") for p in row["public_api"]))
        check(f"{arm}:all_tool_receipts_closed", all(e.get("end_sequence") is not None for e in row["ledger"]["events"]))
        check(f"{arm}:action_sequence_is_public_call_subsequence", row["actions"] == [c for c in row["calls"] if c["action"]])
        check(f"{arm}:checkpoint_counts_are_observed", row["opportunities"]["all_checkpoints"] == len(row["checkpoints"])
              and row["opportunities"]["local_failure_checkpoints"] == sum(c["failed"] for c in row["checkpoints"]))
        if case["category"] == "entry_rejected":
            check(f"{arm}:entry_rejection_has_zero_actions", not row["actions"])
        check(f"{arm}:no_action_after_mandatory_exit", row["controls"][-1]["after_action"] == len(row["actions"]))
        for field in ("initial_state_sha256", "calls", "public_api", "checkpoints", "terminal", "frame", "final_semantic_state", "checker"):
            check(f"{arm}:equals_macro:{field}", row[field] == reference[field])
        check(f"{arm}:raw_final_state_binding", row["final_state_sha256"] == sha(canonical(row["final_state"]))
              and row["final_semantic_state"] == semantic_world_state(row["final_state"]))
        pauses = row["opportunities"]["scheduling_pause_returns"]
        check(f"{arm}:only_failure_scheduling_adds_pauses",
              pauses == (expected["local_failure_checkpoints"] if arm == "failure" else 0))
        failed_boundaries = [(c["after_public_call"], c["after_action"])
                             for c in row["checkpoints"] if c["failed"]]
        actual_pauses = [(c["after_public_call"], c["after_action"])
                         for c in row["controls"] if c["status"] == "paused"]
        check(f"{arm}:pauses_at_exact_failed_checkpoint_boundaries",
              actual_pauses == (failed_boundaries if arm == "failure" else [])
              and all(c["reason"] == "failure_checkpoint" for c in row["controls"] if c["status"] == "paused"))
        check(f"{arm}:first_return_bound_to_first_control", row["first_return"] == {
            k: row["controls"][0][k] for k in ("status", "reason", "after_public_call", "after_action")})
        if arm == "failure" and failed_boundaries:
            first = row["first_return"]
            check(f"{arm}:first_return_is_first_failed_checkpoint", first["status"] == "paused"
                  and (first["after_public_call"], first["after_action"]) == failed_boundaries[0])
        else:
            first = row["first_return"]
            check(f"{arm}:first_return_is_terminal_without_pause",
                  {k: first[k] for k in ("status", "reason")} == row["terminal"]
                  and (first["after_public_call"], first["after_action"]) == (len(row["calls"]), len(row["actions"])))
        check(f"{arm}:obstacle_geometry_remains_static",
              row["initial_state"]["controllers"]["obstacle_controller"]["obstacles"] ==
              row["final_state"]["controllers"]["obstacle_controller"]["obstacles"])
    if case["event"]["kind"] == "receipt_loss":
        for arm, row in rows.items():
            check(f"{arm}:unknown_not_replayed", len(row["actions"]) == 1 and
                  [c["tool"] for c in row["calls"]][-2:] == ["take_off", "get_drone_status"])
            check(f"{arm}:unknown_fault_actually_triggered", len(row["events"]) == 1 and
                  row["events"][0]["applied"] is case["event"]["applied"])
    if case["event"]["kind"] in {"actor_change", "epoch_after_action"}:
        for arm, row in rows.items():
            check(f"{arm}:dependency_event_triggered_and_no_second_action",
                  len(row["events"]) == len(row["actions"]) == 1)
    return checks


def forbid_network():
    def denied(*args, **kwargs):
        NETWORK_ATTEMPTS.append("denied")
        raise RuntimeError("Network forbidden in offline method validation")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


def validate_case_package(package):
    if (package.get("schema") != SCHEMA or package.get("arms") != list(ARMS)
            or package.get("revision") != REVISION or not package.get("cases")):
        raise ValueError("unsupported case package")
    ids = [c["case_id"] for c in package["cases"]]
    if len(ids) != len(set(ids)) or package.get("case_count") != len(ids) or package.get("planned_trials") != len(ids) * len(ARMS):
        raise ValueError("case denominator mismatch")


def run_campaign(cases_path, output):
    cases_path, output = Path(cases_path).resolve(), Path(output).resolve()
    package = json.loads(cases_path.read_bytes())
    validate_case_package(package)
    if output.exists():
        raise ValueError("output already exists; frozen runs are never overwritten")
    if cases_path != DEFAULT_CASES.resolve():
        raise ValueError("campaign uses the fixed method case package")
    sources = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in source_paths()}
    output.mkdir(parents=True)
    for relative, expected_sha in sources.items():
        dest = output / "source" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw = (ROOT / relative).read_bytes()
        if sha(raw) != expected_sha:
            raise ValueError("source changed while freezing")
        dest.write_bytes(raw)
    plan = {"schema": SCHEMA, "case_package_sha256": sha(cases_path.read_bytes()),
        "sources": sources, "arms": list(ARMS), "revision": REVISION,
        "planned_cases": len(package["cases"]), "planned_trials": package["planned_trials"],
        "task_selection": "fixture_precommitted_not_model_selected", "model_calls": 0,
        "process_policy": "one_fresh_process_per_case_arm_serial_join",
        "execution_sources": "workspace_sources_compared_to_frozen_hashes_before_and_after_campaign",
        "semantic_state_ignored_fields": ["controllers.session_controller.sessions.*.request_history.*.request_id",
                                         "controllers.session_controller.sessions.*.request_history.*.timestamp"],
        "environment_removed_prefixes": ["UAV_", "DEEPSEEK_", "OPENAI_", "ANTHROPIC_", "LLM_"],
        "environment": {
            "python": sys.version, **{p: importlib.metadata.version(p) for p in ("fastapi", "httpx", "pydantic", "shapely")}},
        "planned": [{"case_id": c["case_id"], "arm": a} for c in package["cases"] for a in ARMS]}
    write_json(output / "plan.json", plan)
    all_checks, results, pairs = [], [], []
    for case in package["cases"]:
        rows = {}
        for arm in ARMS:
            relative = f"trials/{case['case_id']}/{arm}.json"
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            started = time.monotonic_ns()
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--cases", str(cases_path),
                       "--case-id", case["case_id"], "--arm", arm, "--output", str(target)]
            with target.with_suffix(".log").open("wb") as stream:
                try:
                    child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, cwd=ROOT,
                        env={**{k: v for k,v in os.environ.items() if not k.startswith(tuple(plan["environment_removed_prefixes"]))},
                             "PYTHONDONTWRITEBYTECODE": "1"})
                    code = child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
                    code = -9
            if code == 0 and target.exists():
                row = json.loads(target.read_bytes())
            else:
                row = {"case_id": case["case_id"], "arm": arm, "worker_error": f"exit:{code}"}
                write_json(target, row)
            rows[arm] = row
            results.append({"case_id": case["case_id"], "arm": arm, "result": relative,
                            "pid": child.pid, "returncode": code, "started_ns": started,
                            "joined_ns": time.monotonic_ns()})
        checks = compare_results(case, rows)
        all_checks.extend({"case_id": case["case_id"], **c} for c in checks)
        pairs.append({"case_id": case["case_id"], "category": case["category"],
            "passed": all(c["passed"] for c in checks), "arms": {a: {
                "terminal": r.get("terminal"), "fixture_score": r.get("checker", {}).get("passed"),
                "public_calls": len(r.get("calls", [])), "actions": len(r.get("actions", [])),
                "first_return": r.get("first_return"), "opportunities": r.get("opportunities")}
                for a, r in rows.items()}})
    all_checks.append({"name": "all_sources_unchanged", "passed": all(sha((ROOT/p).read_bytes()) == h for p,h in sources.items())})
    all_checks.append({"name": "independent_worker_pids", "passed": len({r["pid"] for r in results}) == len(results)})
    all_checks.append({"name": "serial_join", "passed": all(a["joined_ns"] <= b["started_ns"] for a,b in zip(results, results[1:]))})
    report = {"schema": SCHEMA, "passed": all(c["passed"] for c in all_checks),
        "planned_cases": len(package["cases"]), "planned_trials": package["planned_trials"],
        "retained_trials": len(results), "completed_workers": sum(r["returncode"] == 0 for r in results),
        "model_calls": 0, "checks": all_checks, "pairs": pairs, "workers": results,
        "limitations": ["Shared production skills/guards are held fixed; this is not a novel-algorithm proof.",
            "All skill choices and resumes are scripted; pause returns are opportunities, not measured model calls or tokens.",
            "Static simulator time and immediate scripted resumes exclude latency and changing-world effects.",
            "Only declared fixtures and guards are tested; no proof for arbitrary generators, concurrency, cancellation or deadlines.",
            "No model behavior, generalization, sample efficiency, Teacher or training claim.",
            "Current v7 static-obstacle contract is respected by physical failure fixtures; actor/epoch mutations are declared exit tests."]}
    write_json(output / "report.json", report)
    files = {str(p.relative_to(output)): sha(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "manifest.json", {"schema": SCHEMA, "files": files, "sources": sources,
                                         "report_sha256": files["report.json"]})
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return report


def verify_run(output):
    """Read-only integrity and behavioral re-computation from sealed JSON."""
    output = Path(output).resolve()
    checks = []
    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
    try:
        manifest_raw = (output / "manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        check("manifest_sha256", sha(manifest_raw) == (output / "manifest.sha256").read_text().strip())
        check("manifest_schema", manifest["schema"] == SCHEMA)
        listed = manifest["files"]
        actual = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()}
        check("exact_file_inventory", actual == set(listed) | {"manifest.json", "manifest.sha256"})
        check("no_symlinks", not any(p.is_symlink() for p in output.rglob("*")))
        for relative, expected in listed.items():
            path = output / relative
            if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(output):
                raise ValueError("unsafe manifest path")
            check("file_hash:" + relative, path.is_file() and sha(path.read_bytes()) == expected)
        plan = json.loads((output / "plan.json").read_bytes())
        report = json.loads((output / "report.json").read_bytes())
        case_path = output / "source/experiments/harness-method-v1/method-cases.json"
        package = json.loads(case_path.read_bytes())
        validate_case_package(package)
        check("case_package_binding", sha(case_path.read_bytes()) == plan["case_package_sha256"])
        check("report_binding", manifest["report_sha256"] == sha((output / "report.json").read_bytes()))
        check("source_inventory_binding", plan["sources"] == manifest["sources"] and all(
            listed.get("source/" + p) == digest for p,digest in manifest["sources"].items()))
        planned = [{"case_id": c["case_id"], "arm": arm} for c in package["cases"] for arm in ARMS]
        check("fixed_full_plan", plan["planned"] == planned and plan["planned_trials"] == len(planned)
              and plan["planned_cases"] == len(package["cases"]))
        workers = report["workers"]
        check("all_planned_worker_rows_retained", [{k:w[k] for k in ("case_id", "arm")} for w in workers] == planned)
        check("unique_worker_pid", len({w["pid"] for w in workers}) == len(workers))
        check("serial_worker_join", all(a["joined_ns"] <= b["started_ns"] for a,b in zip(workers,workers[1:])))
        check("reported_denominators", report["planned_cases"] == len(package["cases"])
              and report["planned_trials"] == report["retained_trials"] == len(planned)
              and report["completed_workers"] == sum(w["returncode"] == 0 for w in workers))
        rechecked = []
        for case in package["cases"]:
            rows = {}
            for arm in ARMS:
                relative = f"trials/{case['case_id']}/{arm}.json"
                row = json.loads((output / relative).read_bytes())
                rows[arm] = row
                worker = next(w for w in workers if w["case_id"] == case["case_id"] and w["arm"] == arm)
                check(f"worker_binding:{case['case_id']}:{arm}", worker["result"] == relative
                      and row["case_id"] == case["case_id"] and row["arm"] == arm
                      and ("worker_error" in row or row["worker_pid"] == worker["pid"]))
            rechecked.extend({"case_id": case["case_id"], **c} for c in compare_results(case, rows))
        checks.extend(rechecked)
        check("saved_case_checks_equal_recomputation", [c for c in report["checks"] if "case_id" in c] == rechecked)
        check("recorded_run_passed", report["passed"] is True and all(c["passed"] for c in report["checks"]))
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
        checks.append({"name": "read_and_recompute", "passed": False,
                       "error": type(exc).__name__ + ": " + str(exc)})
    return {"schema": SCHEMA, "mode": "read_only_verify", "passed": all(c["passed"] for c in checks),
            "checks": checks, "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepare-cases", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--verify", action="store_true", help="read-only verification of --output")
    parser.add_argument("--case-id")
    parser.add_argument("--arm", choices=ARMS)
    args = parser.parse_args()
    if args.prepare_cases:
        if args.cases.exists():
            raise ValueError("case package already exists")
        forbid_network()
        write_json(args.cases, build_cases())
        return 0
    if args.output is None:
        parser.error("--output is required")
    if args.verify:
        if args.worker or args.prepare_cases:
            parser.error("--verify cannot be combined with worker/preparation")
        result = verify_run(args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1
    if args.worker:
        forbid_network()
        package = json.loads(args.cases.read_bytes())
        validate_case_package(package)
        case = next(c for c in package["cases"] if c["case_id"] == args.case_id)
        write_json(args.output, execute_case(case, args.arm))
        return 0
    report = run_campaign(args.cases, args.output)
    print(json.dumps({k: report[k] for k in ("passed", "planned_cases", "planned_trials", "retained_trials", "completed_workers", "model_calls")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
