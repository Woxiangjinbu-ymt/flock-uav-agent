"""Frozen, explicitly authorized eight-slot DeepSeek execution-contract pilot.

Real provider + isolated local simulator. A separate local-smoke mode exercises
the identical SDK, cost and process boundaries with synthetic responses.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent4drone"))
from validate_harness_episode import check_hashes, safe_path, sha, write, policy_scan

TASKS = ("c74d7020",)
STUDY = "experiments/task-coverage-v1-study"
STUDY_SHA256 = "6386876e9e6172655abb899c836b4199909e438846b83be515a5f75aadb48bae"
UNKNOWN_ACTOR = "3d229034"
SCOPE = "deepseek_hover_pilot_v1"
VARIANT_CONTRACT = "harness_hover_input_pair_v1"
CONTRACT = "task_execution_state_v1"
FAULT_CONTRACT = "take_off_response_loss_after_success_v1"
IMPLEMENTATION_BUDGET = {"max_iterations": 600, "recursion_limit": 1800, "deadline_seconds": 500,
    "api_calls_per_plan": 2000, "repair_budget": 16, "search_rounds": 16}
RUNTIME = {"task_seconds": 500, "drain_seconds": 5.0, "request_limits": {
    "max_http_attempts": 12, "max_total_tokens": 200000, "max_output_tokens": 8192,
    "per_request_timeout_seconds": 60.0}}
PILOT = {"model": "deepseek-flash", "profile_id": "deepseek_official",
    "base_url": "https://api.deepseek.com", "thinking": "enabled", "reasoning_effort": "high",
    "currency": "USD", "input_per_million": "0.3", "output_per_million": "1.2",
    "max_input_tokens": 1048576, "max_output_tokens": 8192, "max_request_bytes": 200000,
    "batch_budget": "1.50", "trial_budget": "0.50", "sdk_retries": 0,
    "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "parameter_source": "https://api-docs.deepseek.com/guides/thinking_mode/",
    "documentation_model_version": "deepseek-flash", "checked_date": "2026-11-03",
    "cost_semantics": "peak_cache_miss_conservative_accounting_not_supplier_invoice",
    "input_bound_semantics": "provider_context_capacity; byte_limit_is_separate_not_a_token_estimator"}
PROTOCOL = "docs/results/deepseek-hover-pilot-20261103/PROTOCOL_CN.md"
EXTRA_SCRIPTS = ("deepseek_hover_pilot.py", "harness_deepseek_adapter.py", "harness_live_cost.py",
    "hover_pilot_evaluation.py", "analyze_deepseek_hover_pilot.py", "analyze_deepseek_cost_pilot.py",
    "analyze_deepseek_contract_pilot.py", "analyze_deepseek_behavior_pilot.py")
COMMANDS = {
    "original": "Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.",
    "explicit_hover": "Command drone Drone 4 to take off to an altitude of 12 meters and then execute a hover command.",
    "duration_hover": "Command drone Drone 4 to take off to an altitude of 12 meters and hover in place for 5 seconds.",
}
CONDITIONS = (("state_hover", "original"), ("explicit_hover", "explicit_hover"),
              ("unknown_takeoff", "original"), ("duration_hover", "duration_hover"))
ARMS = {"control": ("off", "string_v1"),
        "candidate": ("hover_completion_v1", "json_object_or_string_v1")}



def exact(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def private_payload(value):
    if isinstance(value, dict):
        return any((key in {"execution_check_apis", "related_apis"} and child not in (None, [], {}, ""))
                   or private_payload(child) for key, child in value.items())
    if isinstance(value, list):
        return any(private_payload(child) for child in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return False
        return False if isinstance(parsed, str) else private_payload(parsed)
    return False


def trials():
    result = []
    for index, (condition, variant) in enumerate(CONDITIONS):
        order = ("control", "candidate") if index % 2 == 0 else ("candidate", "control")
        for arm in order:
            completion, inputs = ARMS[arm]
            value = {"trial_id": condition + "-" + arm, "condition": condition,
                "task_id": TASKS[0], "fixture_variant": variant, "arm": arm,
                "task_progress_mode": "evidence_harness", "input_layout": "compact_tools_v1",
                "harness_revision": "search_confirm_v9", "completion_contract": completion,
                "tool_input_contract": inputs, "scheduler": "applicability",
                "runtime": copy.deepcopy(RUNTIME), "watchdog_seconds": 515}
            if condition == "unknown_takeoff":
                value["api_fault"] = {"kind": "response_lost_after_success", "method": "POST",
                    "endpoint": "/drones/" + UNKNOWN_ACTOR + "/command/take_off", "occurrence": 1}
            result.append(value)
    return result


def authorization(local):
    return {"source": "user_explicit_deepseek_hover_pilot_authorization_20261103",
        "episodes": 0 if local else 8, "max_http_attempts": 0 if local else 96,
        "batch_cost_limit_usd": "0" if local else PILOT["batch_budget"],
        "trial_cost_limit_usd": "0" if local else PILOT["trial_budget"],
        "budget_source": "new_bounded_implementation_allocation_not_inherited_old_run",
        "old_6_14_360_queues_resumed": False}


def validate_actor_binding(inputs):
    fixture = json.loads(inputs["fixtures/" + TASKS[0] + ".json"])
    scene = json.loads(inputs["dataset/" + fixture["scene_file"]])
    if fixture["command"] != "Command drone Drone 4 to take off to an altitude of 12 meters and hover in place.":
        raise ValueError("Unknown-effect command changed")
    actors = [d["id"] for d in scene["drones"] if d.get("name") == "Drone 4"]
    if actors != [UNKNOWN_ACTOR] or fixture["named_actor_names"] != ["Drone 4"]:
        raise ValueError("Unknown-effect actor binding changed")


def variant_fixture(original, variant):
    fixture = copy.deepcopy(original)
    fixture.update(command=COMMANDS[variant], command_sha256=sha(COMMANDS[variant].encode()),
        public_instruction_variant={"source_manifest_sha256": STUDY_SHA256,
            "source_command": original["command"], "source_command_sha256": original["command_sha256"],
            "changed_fields": ["command", "command_sha256", "public_instruction_variant"],
            "original_task_and_checker_preserved": True, "split": "development", "teacher_example": False})
    return fixture


def fixture_prefix(variant):
    if variant not in COMMANDS:
        raise ValueError("Unknown public instruction variant")
    return "" if variant == "original" else "variants/" + variant + "/"


def validate_inputs(inputs):
    origin_raw = inputs["origin-manifest.json"]
    if sha(origin_raw) != STUDY_SHA256:
        raise ValueError("Fixed development input origin changed")
    origin = json.loads(origin_raw)
    dataset_raw = inputs["origin-dataset-manifest.json"]
    if sha(dataset_raw) != origin["origin_manifest_sha256"]:
        raise ValueError("Dataset origin binding changed")
    dataset = json.loads(dataset_raw)
    name = "fixtures/" + TASKS[0] + ".json"
    fixture = json.loads(inputs[name])
    names = (name, "fixtures/" + TASKS[0] + ".pkl", "fixtures/" + fixture["timeline_file"])
    if (fixture["task_id"] != TASKS[0] or fixture["split"] != "development"
            or fixture["command"] != COMMANDS["original"]
            or sha(fixture["command"].encode()) != fixture["command_sha256"]
            or any(sha(inputs[n]) != origin["files"][n] for n in names)):
        raise ValueError("Fixture no longer matches fixed origin")
    scene = fixture["scene_file"]
    if sha(inputs["dataset/" + scene]) != dataset["files"][scene]:
        raise ValueError("Scene no longer matches fixed dataset origin")
    validate_actor_binding(inputs)
    for variant in ("explicit_hover", "duration_hover"):
        prefix = fixture_prefix(variant)
        if not exact(json.loads(inputs[prefix + name]), variant_fixture(fixture, variant)):
            raise ValueError("Derived public command/provenance changed")
        if any(inputs[prefix + n] != inputs[n] for n in names[1:]):
            raise ValueError("Derived initial snapshot/timeline changed")
    expected = {"origin-manifest.json", "origin-dataset-manifest.json", "dataset/" + scene, *names,
        *(fixture_prefix(v) + n for v in ("explicit_hover", "duration_hover") for n in names)}
    if set(inputs) != expected:
        raise ValueError("Unexpected/missing frozen inputs")


def prepare(output, *, local_smoke=False):
    from general_agent import SCRIPTS
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("No overwrite or automatic continuation")
    if type(local_smoke) is not bool:
        raise ValueError("local_smoke must be boolean")
    study = ROOT / STUDY
    origin = (study / "manifest.json").read_bytes()
    if sha(origin) != STUDY_SHA256:
        raise ValueError("Fixed development study changed")
    files = json.loads(origin)["files"]
    inputs = {"origin-manifest.json": origin}
    origin_dataset = Path(json.loads(origin)["origin_dataset"])
    dataset_raw = (origin_dataset / "manifest.json").read_bytes()
    if sha(dataset_raw) != json.loads(origin)["origin_manifest_sha256"]:
        raise ValueError("Development dataset origin changed")
    inputs["origin-dataset-manifest.json"] = dataset_raw
    for task in TASKS:
        name = "fixtures/" + task + ".json"
        fixture = json.loads(safe_path(study, name).read_bytes())
        names = [name, "fixtures/" + task + ".pkl", "fixtures/" + fixture["timeline_file"]]
        check_hashes(study, {name: files[name] for name in names})
        if fixture["task_id"] != task or sha(fixture["command"].encode()) != fixture["command_sha256"]:
            raise ValueError("Fixture identity changed")
        for name in names:
            inputs[name] = safe_path(study, name).read_bytes()
        scene_name = fixture["scene_file"]
        check_hashes(origin_dataset, {scene_name: json.loads(dataset_raw)["files"][scene_name]})
        inputs["dataset/" + scene_name] = safe_path(origin_dataset, scene_name).read_bytes()
    original_fixture = json.loads(inputs["fixtures/" + TASKS[0] + ".json"])
    names = ("fixtures/" + TASKS[0] + ".json", "fixtures/" + TASKS[0] + ".pkl",
             "fixtures/" + original_fixture["timeline_file"])
    for variant in ("explicit_hover", "duration_hover"):
        prefix = fixture_prefix(variant)
        inputs[prefix + names[0]] = json.dumps(variant_fixture(original_fixture, variant),
            ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
        for name in names[1:]:
            inputs[prefix + name] = inputs[name]
    validate_inputs(inputs)
    paths = [p for directory in ("agent4drone", "server") for p in (ROOT / directory).rglob("*.py")
        if not any(part in {".venv", "tests", "__pycache__"} for part in p.relative_to(ROOT).parts)]
    paths += [ROOT / "scripts" / name for name in set(SCRIPTS) | set(EXTRA_SCRIPTS)]
    paths += [ROOT / "scripts/tests/test_deepseek_hover_pilot.py", ROOT / "scripts/tests/test_deepseek_hover_analysis.py",
              ROOT / "scripts/tests/test_harness_online_hover_variants.py",
              ROOT / "scripts/tests/test_hover_pilot_evaluation.py",
              ROOT / "agent4drone/requirements.txt", ROOT / "server/requirements.txt"]
    sources = {str(p.relative_to(ROOT)): p.read_bytes() for p in paths}
    protocol = (ROOT / PROTOCOL).read_bytes()
    output.mkdir(parents=True)
    for folder, entries in (("source", sources), ("inputs", inputs)):
        for name, content in entries.items():
            target = safe_path(output / folder, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    (output / "PROTOCOL_CN.md").write_bytes(protocol)
    manifest = {"schema_version": 3, "scope": SCOPE,
        "workspace": str(ROOT), "study": str(output / "inputs"), "model": PILOT["model"],
        "online_pilot": PILOT, "local_smoke": local_smoke, "trials": trials(),
        "online_contract": CONTRACT, "online_variant_contract": VARIANT_CONTRACT,
        "online_common_runtime": RUNTIME,
        "online_implementation_budget": IMPLEMENTATION_BUDGET, "online_fault_contract": FAULT_CONTRACT,
        "authorization": authorization(local_smoke),
        "private_canary": "PRIVATE_DEEPSEEK_HOVER_PILOT_20261103",
        "sources": {n: sha(b) for n, b in sources.items()}, "inputs": {n: sha(b) for n, b in inputs.items()},
        "protocol_sha256": sha(protocol), "python": sys.version,
        "evidence_scope": "synthetic local SDK/cost/continuation acceptance" if local_smoke else
            "one known development scene; four fixed conditions including two public-command derivatives; three supported-task pairs and one duration diagnostic pair; v3/v4 package comparison, not unseen generalization"}
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return copy.deepcopy(manifest)


def load_manifest(run):
    run = Path(run).resolve()
    raw = (run / "manifest.json").read_bytes()
    if sha(raw) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    manifest = json.loads(raw)
    if (manifest["scope"] != SCOPE or manifest["schema_version"] != 3 or not exact(manifest["online_pilot"], PILOT)
            or not exact(manifest["trials"], trials()) or manifest["model"] != PILOT["model"]
            or type(manifest["local_smoke"]) is not bool or manifest.get("online_contract") != CONTRACT
            or manifest.get("online_variant_contract") != VARIANT_CONTRACT
            or not exact(manifest.get("online_common_runtime"), RUNTIME)
            or not exact(manifest.get("online_implementation_budget"), IMPLEMENTATION_BUDGET)
            or manifest.get("online_fault_contract") != FAULT_CONTRACT):
        raise ValueError("Outside fixed pilot")
    local = manifest["local_smoke"]
    if not exact(manifest["authorization"], authorization(local)):
        raise ValueError("Authorization differs from prepared scope")
    if Path(manifest["study"]).resolve() != run / "inputs":
        raise ValueError("Inputs outside run")
    for folder, expected in (("source", manifest["sources"]), ("inputs", manifest["inputs"])):
        check_hashes(run / folder, expected)
        inventory = list((run / folder).rglob("*"))
        if any(p.is_symlink() for p in inventory):
            raise ValueError("Symlink in frozen run")
        actual = {str(p.relative_to(run / folder)) for p in inventory if p.is_file() and "__pycache__" not in p.parts}
        if actual != set(expected):
            raise ValueError("Frozen inventory changed")
    if sha((run / "PROTOCOL_CN.md").read_bytes()) != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed")
    inputs = {name: safe_path(run / "inputs", name).read_bytes() for name in manifest["inputs"]}
    validate_inputs(inputs)
    return manifest


def cost_policy():
    from harness_live_cost import LiveCostPolicy
    return LiveCostPolicy(currency=PILOT["currency"], input_per_million=PILOT["input_per_million"],
        output_per_million=PILOT["output_per_million"], max_input_tokens=PILOT["max_input_tokens"],
        max_output_tokens=PILOT["max_output_tokens"], max_request_bytes=PILOT["max_request_bytes"])


def credential(manifest):
    if manifest["local_smoke"]:
        return "synthetic-local-deepseek-candidate-not-a-real-key"
    settings = json.loads((Path(manifest["workspace"]) / "agent4drone/llm_settings.json").read_bytes())
    config = settings["provider_configs"][PILOT["profile_id"]]
    if (config.get("base_url", "").rstrip("/") != PILOT["base_url"]
            or config.get("default_model") != PILOT["model"] or not isinstance(config.get("api_key"), str)
            or not config["api_key"].strip()):
        raise ValueError("Configured official DeepSeek identity/key unavailable")
    return config["api_key"].strip()


def model_factory_for(key):
    from harness_deepseek_adapter import _DeepSeekCandidate
    def factory(**kwargs):
        kwargs.update(api_key=key, base_url=PILOT["base_url"], model=PILOT["model"], temperature=None,
            max_retries=0, streaming=False, use_responses_api=False, thinking_mode="enabled",
            candidate_effort="high", extra_body={"thinking": {"type": "enabled"}})
        return _DeepSeekCandidate(**kwargs)
    return factory


def mock_transport(httpx, trial):
    """Real SDK + fixed local decisions; candidate objects remain native objects."""
    from general_agent_local import resolve_public
    count = 0
    def reply(request):
        nonlocal count
        index, count = count, count + 1
        payload = json.loads(request.content)
        steps = [{"tool": "list_drones", "args": {}}]
        if trial["condition"] == "unknown_takeoff":
            steps.append({"tool": "run_harness", "args": {"input_json": {"action": "start", "skill": "simple_chain"}}})
        else:
            steps.append({"tool": "take_off", "args": {"input_json": {"drone_id": "$drone:Drone 4", "altitude": 12}}})
        if trial["condition"] in {"explicit_hover", "duration_hover"}:
            args = {"drone_id": "$drone:Drone 4"}
            if trial["condition"] == "duration_hover":
                args["duration"] = 5
            steps.append({"tool": "hover", "args": {"input_json": args}})
        steps.append({"tool": "get_drone_status", "args": {"input_json": {"drone_id": "$drone:Drone 4"}}})
        step = steps[index] if index < len(steps) else None
        tool = step["tool"] if step else None
        message = {"role": "assistant", "reasoning_content": "Synthetic local protocol check.",
                   "content": "Local protocol stop. [TASK DONE]"}
        if tool:
            if tool not in {t["function"]["name"] for t in payload.get("tools", [])}:
                raise ValueError("Smoke tool was not advertised")
            message["content"] = ""
            args = resolve_public(step["args"], payload["messages"])
            if trial["arm"] == "control" and isinstance(args.get("input_json"), dict):
                args["input_json"] = json.dumps(args["input_json"])
            message["tool_calls"] = [{"id": "smoke-" + str(index), "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)}}]
        return httpx.Response(200, json={"id": "smoke-response-" + str(index), "object": "chat.completion",
            "model": PILOT["model"], "created": 1, "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}, request=request)
    return httpx.MockTransport(reply)


def finish_worker_cost(ledger, trial_id, result):
    """Close accounting only; parent verification alone permits next slot.

    The ledger cannot remain active across worker close. Pure reconciliation is
    reused without pretending that this worker has passed its parent exit seal.
    A known-charge model error may close its account but still stops the parent.
    """
    from episode_termination import _cost_error, _limits_error
    state = ledger.snapshot()
    error = _cost_error(result, state) or _limits_error(result["limits"])
    if (error is None and result.get("drained") is True and result["limits"].get("closed") is True
            and result["limits"].get("needs_process_termination") is False
            and result["limits"].get("threads_still_alive") == []):
        ledger.finish_trial(trial_id)
    else:
        ledger.block(error or "worker_cost_or_transport_unsettled")
    return ledger.snapshot()


def worker(run, trial_id):
    from harness_live_cost import LiveCostLedger, make_cost_transport
    from harness_natural_runner import run_worker
    from harness_episode_local import block_network
    from episode_termination import should_block_cost_on_request_stop
    manifest = load_manifest(run)
    trial = next(t for t in manifest["trials"] if t["trial_id"] == trial_id)
    key = credential(manifest)
    network_attempts = block_network() if manifest["local_smoke"] else None
    ledger = LiveCostLedger.open(run / "cost-ledger.jsonl", cost_policy(), PILOT["batch_budget"])
    ledger.start_trial(trial_id, PILOT["trial_budget"])
    def factory(httpx, controls):
        raw = mock_transport(httpx, trial) if manifest["local_smoke"] else httpx.HTTPTransport(retries=0, trust_env=False)
        class EndpointTransport(httpx.BaseTransport):
            def handle_request(self, request):
                if request.method != "POST" or str(request.url) != PILOT["base_url"] + "/chat/completions":
                    raise ValueError("Provider endpoint outside frozen scope")
                body = json.loads(request.content)
                if manifest["private_canary"].encode() in request.content or private_payload(body):
                    raise ValueError("Private evaluation material in provider request")
                if (body.get("model") != PILOT["model"] or body.get("thinking") != {"type": "enabled"}
                    or body.get("reasoning_effort") != "high" or body.get("stream") is not False
                    or "temperature" in body):
                    raise ValueError("Provider payload differs from frozen candidate")
                return raw.handle_request(request)
            def close(self):
                raw.close()
        return make_cost_transport(httpx, EndpointTransport(), ledger, trial_id)
    try:
        def request_stop(reason):
            if should_block_cost_on_request_stop(reason):
                ledger.block(reason)
        result = run_worker(run, trial_id, factory, network_attempts=network_attempts,
            model_factory=model_factory_for(key), on_request_stop=request_stop)
        finish_worker_cost(ledger, trial_id, result)
        return result
    except BaseException:
        ledger.block("worker_exception")
        raise
    finally:
        ledger.close()


def launch(run, trial, mode, journal):
    manifest = load_manifest(run)
    packages = [p for c in ("agent4drone", "server") for p in sorted((Path(manifest["workspace"]) / c / ".venv/lib").glob("python*/site-packages"))]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("UAV_", "LANGCHAIN_", "LANGSMITH_"))}
    env.update(PYTHONPATH=os.pathsep.join(map(str, [run / "source/scripts", run / "source/agent4drone", run / "source/server", *packages])),
        PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1", PYGAME_HIDE_SUPPORT_PROMPT="1")
    log = run / "process-logs" / (trial["trial_id"] + "-" + mode + ".log")
    started, started_ns, killed = time.monotonic(), time.time_ns(), None
    with log.open("x") as stream:
        child = subprocess.Popen([sys.executable, str(run / "source/scripts/deepseek_hover_pilot.py"), mode,
            "--run", str(run), "--trial", trial["trial_id"]], cwd=run / "source", env=env,
            stdout=stream, stderr=subprocess.STDOUT)
        journal.write(json.dumps({"event": "start", "mode": mode, "trial_id": trial["trial_id"], "pid": child.pid, "started_ns": started_ns}) + "\n")
        journal.flush(); os.fsync(journal.fileno())
        try:
            while child.poll() is None:
                poison = run / "trials" / trial["trial_id"] / "termination-required.json"
                if mode == "worker" and poison.exists():
                    try:
                        notice = json.loads(poison.read_bytes())
                    except (ValueError, OSError):
                        notice = {}
                    if notice.get("pid") == child.pid and notice.get("trial_id") == trial["trial_id"]:
                        killed = "transport_not_drained"
                if time.monotonic() - started >= (trial["watchdog_seconds"] if mode == "worker" else 30):
                    killed = "watchdog"
                if killed:
                    child.kill(); break
                time.sleep(0.05)
            returncode = child.wait(timeout=5)
        finally:
            if child.poll() is None:
                killed = "parent_interrupted"
                child.kill(); child.wait(timeout=5)
            record = {"event": "exit", "mode": mode, "trial_id": trial["trial_id"], "pid": child.pid,
                "started_ns": started_ns, "ended_ns": time.time_ns(), "elapsed_seconds": time.monotonic() - started,
                "returncode": child.returncode, "exited": child.poll() is not None, "kill_reason": killed,
                "log_sha256": sha(log.read_bytes())}
            journal.write(json.dumps(record) + "\n"); journal.flush(); os.fsync(journal.fileno())
    return record


def result_layers(run, trial, worker=None, score=None):
    """Evaluation only: never feed this combined record back to the Agent."""
    if worker is None:
        return {"run": {"status": "incomplete", "agent_returned_normally": None},
            "public_evidence": {"whole_task": "not_evaluated"},
            "independent_evaluation": {"status": "not_scored", "passed": None}}
    folder = run / "trials" / trial["trial_id"]
    state = json.loads((folder / "policy/task-state.json").read_text())
    agent = json.loads((folder / "policy/agent-result.json").read_text())
    stop = worker["limits"]["stop_reason"]
    status = "returned" if worker["agent"]["success"] else "error"
    if stop:
        status = "cancelled" if stop == "cancelled" else "timed_out" if stop in {"task_deadline", "request_timeout"} else "stopped"
    result = {"run": {"status": status, "worker_sealed": True,
                "agent_returned_normally": worker["agent"]["success"], "stop_reason": stop,
                "archive_complete": worker["agent"]["archive_verify"]["complete"]},
        "public_evidence": {"whole_task": "not_evaluated", "original_command": state["command"],
            "uninterpreted_requirements_policy": "retain full original command; clauses are not exhaustive",
            "agent_claim": agent.get("output"), "state_artifact": "policy/task-state.json",
            "requirements": state.get("task_evidence", {}).get("requirements", []),
            "aggregate_requirements": state.get("task_evidence", {}).get("aggregate_requirements", []),
            "observer_errors": state.get("observer_errors", []), "skill_artifact": "policy/harness.json"},
        "independent_evaluation": {"status": "scored" if score is not None else "not_scored",
            "passed": score["unified_completion"]["task_completed"] if score is not None else None,
            "auxiliary_checker_passed": score["checker"]["passed"] if score is not None else None,
            "unified_completion": score["unified_completion"] if score is not None else None,
            "artifact": "evaluation/result.json" if score is not None else None}}
    execution_state = folder / "policy/execution-state.json"
    if execution_state.exists():
        state = json.loads(execution_state.read_text())
        result["public_evidence"].update(execution_state_artifact="policy/execution-state.json",
            recovery=state["recovery"], mission_completion=state["mission_completion"])
    if "completion_assessment" in agent:
        result["public_evidence"].update(completion_assessment=agent["completion_assessment"],
            task_completed=agent.get("task_completed"), model_output=agent.get("model_output"),
            completion_artifact="policy/agent-result.json")
    return result


def run_prepared(run):
    from harness_live_cost import LiveCostLedger
    from validate_harness_natural_runner import validate_worker, validate_score
    from episode_termination import classify_episode_termination
    run = Path(run).resolve()
    manifest = load_manifest(run)
    if ROOT != run / "source":
        status = subprocess.run([sys.executable, str(run / "source/scripts/deepseek_hover_pilot.py"), "run", "--run", str(run)])
        return status.returncode
    for name in ("process-logs", "worker-exit-seals", "scorer-exit-seals"):
        (run / name).mkdir(exist_ok=True)
    rows, reason = [], None
    with (run / "process-ledger.jsonl").open("x") as journal:
        ledger = LiveCostLedger.create(run / "cost-ledger.jsonl", cost_policy(), PILOT["batch_budget"])
        ledger.close()
        try:
            for trial in manifest["trials"]:
                process = launch(run, trial, "worker", journal)
                row = {"trial_id": trial["trial_id"], "arm": trial["arm"], "task_id": trial["task_id"],
                    "condition": trial["condition"], "processes": [process], "worker": None, "score": None}
                rows.append(row)
                if process["returncode"] == 0 and not process["kill_reason"] and process["exited"]:
                    row["worker"] = validate_worker(run, trial, process, manifest=manifest)
                with LiveCostLedger.open(run / "cost-ledger.jsonl", cost_policy(), PILOT["batch_budget"]) as budget:
                    row["cost_snapshot"] = budget.snapshot()
                row["cost_sequence"] = json.loads((run / "cost-ledger.jsonl.head.json").read_bytes())["sequence"]
                checked = copy.deepcopy(row["worker"])
                if checked is not None:
                    checked["termination_context"] = {"worker_exited": True, "exit_seal_verified": True}
                row["termination"] = classify_episode_termination(checked, row["cost_snapshot"])
                if row["termination"]["score_eligible"]:
                    scored = launch(run, trial, "score", journal)
                    row["processes"].append(scored)
                    row["score"] = validate_score(run, trial, row["worker"], scored)
                    if row["score"]["network_attempts"] != []:
                        raise ValueError("Scorer attempted network")
                row["result"] = result_layers(run, trial, row["worker"], row["score"])
                write(run / "results.json", rows)
                print(json.dumps({"trial": trial["trial_id"], "scored": row["score"] is not None,
                    "termination": row["termination"]["category"],
                    "batch_action": row["termination"]["batch_action"]}), flush=True)
                if row["termination"]["batch_action"] == "stop":
                    reason = row["termination"]["reason"]
                    break
        except BaseException as exc:
            reason = type(exc).__name__ + ": " + str(exc)
            write(run / "incomplete.json", {"reason": reason, "started_trials": len(rows), "automatic_retry": False})
        finally:
            write(run / "results.json", rows)
            with LiveCostLedger.open(run / "cost-ledger.jsonl", cost_policy(), PILOT["batch_budget"]) as budget:
                costs = budget.snapshot()
            write(run / "cost-summary.json", costs)
            write(run / "summary.json", {"status": "stopped" if reason else "completed", "reason": reason,
                "planned_trials": 8, "started_trials": len(rows), "scored_trials": sum(r["score"] is not None for r in rows),
                "unstarted_trials": [t["trial_id"] for t in manifest["trials"][len(rows):]],
                "local_smoke": manifest["local_smoke"], "evidence_scope": manifest["evidence_scope"]})
    write(run / "campaign-seal.json", {"files": {str(p.relative_to(run)): sha(p.read_bytes()) for p in run.rglob("*")
        if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}})
    return 1 if reason else 0


def read_cost_snapshot(run, *, sequence=None):
    """Read/replay only; never open the live ledger or create a lock/marker."""
    from harness_live_cost import LiveCostLedger, _canonical
    path = Path(run) / "cost-ledger.jsonl"
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise ValueError("Incomplete cost journal")
    events = [json.loads(line) for line in raw.splitlines()]
    for index, row in enumerate(events):
        if (set(row) != {"sequence", "previous_sha256", "event", "sha256"}
                or type(row["sequence"]) is not int or row["sequence"] != index + 1
                or row["previous_sha256"] != (events[index - 1]["sha256"] if index else None)
                or row["sha256"] != sha(_canonical({k:v for k,v in row.items() if k != "sha256"}))):
            raise ValueError("Cost journal hash chain changed")
    if json.loads(path.with_name(path.name + ".head.json").read_bytes()) != {
            "sequence": len(events), "sha256": events[-1]["sha256"]}:
        raise ValueError("Cost durable head changed")
    if sequence is None:
        sequence = len(events)
    if type(sequence) is not int or not 1 <= sequence <= len(events):
        raise ValueError("Invalid cost snapshot prefix")
    # Constructor and _replay are pure here: no open(), _load(), snapshot() or close().
    book = LiveCostLedger(path, cost_policy(), PILOT["batch_budget"])
    state = book._replay(events[:sequence])
    if sequence == len(events) and path.with_name(path.name + ".blocked.json").exists() and not state["blocked"]:
        state.update(blocked=True, stop_reason="durable_block_marker", locked_reservation=state["reserved"])
        for attempt in state["attempts"].values():
            if attempt["status"] == "reserved":
                attempt.update(status="locked", stop_reason="durable_block_marker")
        if state["active_trial"] is not None:
            state["trials"][state["active_trial"]]["status"] = "blocked"
    return state


def verify(run):
    """Verify sealed denominator, costs and parent decisions without mutation."""
    from episode_termination import classify_episode_termination
    run = Path(run).resolve()
    manifest = load_manifest(run)
    seal = json.loads((run / "campaign-seal.json").read_bytes())
    check_hashes(run, seal["files"])
    inventory = list(run.rglob("*"))
    if any(p.is_symlink() for p in inventory):
        raise ValueError("Symlink in sealed run")
    actual = {str(p.relative_to(run)) for p in inventory if p.is_file()
        and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    if actual != set(seal["files"]):
        raise ValueError("Run file inventory changed")
    costs = read_cost_snapshot(run)
    if not exact(costs, json.loads((run / "cost-summary.json").read_bytes())):
        raise ValueError("Cost summary differs from ledger replay")
    rows = json.loads((run / "results.json").read_bytes())
    summary = json.loads((run / "summary.json").read_bytes())
    if ([r["trial_id"] for r in rows] != [t["trial_id"] for t in manifest["trials"][:len(rows)]]
            or summary["planned_trials"] != 8 or summary["started_trials"] != len(rows)
            or summary["scored_trials"] != sum(r["score"] is not None for r in rows)
            or summary["unstarted_trials"] != [t["trial_id"] for t in manifest["trials"][len(rows):]]):
        raise ValueError("Planned/executed/unrun denominator changed")
    for index, (trial, row) in enumerate(zip(manifest["trials"], rows)):
        if any(row.get(k) != trial[k] for k in ("task_id", "arm", "condition")):
            raise ValueError("Trial identity changed")
        worker, score = row["worker"], row["score"]
        if worker is not None:
            folder = run / "trials" / trial["trial_id"]
            if not exact(worker, json.loads((folder / "worker-result.json").read_bytes())):
                raise ValueError("Worker result copy changed")
            check_hashes(folder, json.loads((run / "worker-exit-seals" / (trial["trial_id"] + ".json")).read_bytes()))
            process = row["processes"][0]
            if (process["pid"] != worker["pid"] or process["returncode"] != 0
                    or process["exited"] is not True or process["kill_reason"] is not None):
                raise ValueError("Worker exit barrier changed")
        if score is not None:
            folder = run / "trials" / trial["trial_id"]
            if not exact(score, json.loads((folder / "evaluation/result.json").read_bytes())):
                raise ValueError("Score result copy changed")
            check_hashes(folder, json.loads((run / "scorer-exit-seals" / (trial["trial_id"] + ".json")).read_bytes()))
            if score["worker_result_sha256"] != sha((folder / "worker-result.json").read_bytes()) or score["pid"] == worker["pid"]:
                raise ValueError("Score binding/isolation changed")
        if "cost_snapshot" in row:
            if not exact(row["cost_snapshot"], read_cost_snapshot(run, sequence=row["cost_sequence"])):
                raise ValueError("Per-trial cost snapshot changed")
            checked = copy.deepcopy(worker)
            if checked is not None:
                checked["termination_context"] = {"worker_exited": True, "exit_seal_verified": True}
            decision = classify_episode_termination(checked, row["cost_snapshot"])
            if not exact(decision, row["termination"]):
                raise ValueError("Termination classification changed")
            if index < len(rows) - 1 and decision["batch_action"] != "continue":
                raise ValueError("Dispatch after batch stop")
        elif summary["status"] != "stopped" or index != len(rows) - 1:
            raise ValueError("Missing cost/termination record")
        if "result" in row and not exact(row["result"], result_layers(run, trial, worker, score)):
            raise ValueError("Three-layer result changed")
    if len(rows) < 8 and (summary["status"] != "stopped" or not summary["reason"]):
        raise ValueError("Unexplained unrun slots")
    return {"valid": True, "sealed_files": len(seal["files"]), "planned_trials": 8,
        "started_trials": len(rows), "scored_trials": summary["scored_trials"],
        "local_smoke": manifest["local_smoke"], "cost_journal_replayed_without_open": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", required=True, type=Path)
    prep.add_argument("--local-smoke", action="store_true")
    smoke = sub.add_parser("local-smoke")
    smoke.add_argument("--output", required=True, type=Path)
    for action in ("run", "worker", "score", "verify"):
        item = sub.add_parser(action); item.add_argument("--run", required=True, type=Path)
        if action in {"worker", "score"}:
            item.add_argument("--trial", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        m = prepare(args.output, local_smoke=args.local_smoke)
        print(json.dumps({"prepared": str(args.output.resolve()), "trials": len(m["trials"]), "local_smoke": m["local_smoke"]}))
    elif args.action == "local-smoke":
        prepare(args.output, local_smoke=True)
        raise SystemExit(run_prepared(args.output))
    elif args.action == "run":
        raise SystemExit(run_prepared(args.run))
    elif args.action == "worker":
        worker(args.run.resolve(), args.trial)
    elif args.action == "score":
        from harness_episode_local import block_network
        attempts = block_network()
        load_manifest(args.run)
        from harness_natural_runner import run_scorer
        run_scorer(args.run.resolve(), args.trial, network_attempts=attempts)
    else:
        print(json.dumps(verify(args.run)))


if __name__ == "__main__":
    main()
