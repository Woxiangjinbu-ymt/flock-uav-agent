"""Frozen, explicitly authorized six-episode DeepSeek system behavior pilot.

Real provider + isolated local simulator. A separate local-smoke mode exercises
the identical SDK, cost and process boundaries with synthetic responses.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent4drone"))
from validate_harness_episode import check_hashes, safe_path, sha, write, policy_scan

TASKS = ("c00d8b78", "b4714028", "7eba7390")
RUNTIME = {"task_seconds": 500, "drain_seconds": 5.0, "request_limits": {
    "max_http_attempts": 12, "max_total_tokens": 200000, "max_output_tokens": 8192,
    "per_request_timeout_seconds": 60.0}}
PILOT = {"model": "deepseek-flash", "profile_id": "deepseek_official",
    "base_url": "https://api.deepseek.com", "thinking": "enabled", "reasoning_effort": "high",
    "currency": "USD", "input_per_million": "0.3", "output_per_million": "1.2",
    "max_input_tokens": 1048576, "max_output_tokens": 8192, "max_request_bytes": 200000,
    "batch_budget": "2.00", "trial_budget": "0.50", "sdk_retries": 0,
    "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "parameter_source": "https://api-docs.deepseek.com/guides/thinking_mode/",
    "documentation_model_version": "deepseek-flash", "checked_date": "2026-11-02",
    "cost_semantics": "peak_cache_miss_conservative_accounting_not_supplier_invoice",
    "input_bound_semantics": "provider_context_capacity; byte_limit_is_separate_not_a_token_estimator"}
PROTOCOL = "docs/results/deepseek-behavior-pilot-20261102/PROTOCOL_CN.md"
EXTRA_SCRIPTS = ("deepseek_behavior_pilot.py", "harness_deepseek_adapter.py", "harness_live_cost.py")


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
    for index, task in enumerate(TASKS):
        arms = ("baseline", "integrated") if index != 1 else ("integrated", "baseline")
        for arm in arms:
            result.append({"trial_id": task + "-" + arm, "task_id": task, "arm": arm,
                "task_progress_mode": "off" if arm == "baseline" else "evidence_harness",
                "harness_revision": "none" if arm == "baseline" else "observed_target_v7",
                "scheduler": "applicability", "runtime": json.loads(json.dumps(RUNTIME)),
                "watchdog_seconds": 515})
    return result


def prepare(output, *, local_smoke=False):
    from general_agent import SCRIPTS
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("No overwrite or automatic continuation")
    study = ROOT / "experiments/minimal-harness-full100-v1"
    origin = (study / "manifest.json").read_bytes()
    files = json.loads(origin)["files"]
    inputs = {"origin-manifest.json": origin}
    for task in TASKS:
        name = "fixtures/" + task + ".json"
        fixture = json.loads(safe_path(study, name).read_bytes())
        names = [name, "fixtures/" + task + ".pkl", "fixtures/" + fixture["timeline_file"]]
        check_hashes(study, {name: files[name] for name in names})
        if fixture["task_id"] != task or sha(fixture["command"].encode()) != fixture["command_sha256"]:
            raise ValueError("Fixture identity changed")
        for name in names:
            inputs[name] = safe_path(study, name).read_bytes()
    paths = [p for directory in ("agent4drone", "server") for p in (ROOT / directory).rglob("*.py")
        if not any(part in {".venv", "tests", "__pycache__"} for part in p.relative_to(ROOT).parts)]
    paths += [ROOT / "scripts" / name for name in set(SCRIPTS) | set(EXTRA_SCRIPTS)]
    sources = {str(p.relative_to(ROOT)): p.read_bytes() for p in paths}
    protocol = (ROOT / PROTOCOL).read_bytes()
    output.mkdir(parents=True)
    for folder, entries in (("source", sources), ("inputs", inputs)):
        for name, content in entries.items():
            target = safe_path(output / folder, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    (output / "PROTOCOL_CN.md").write_bytes(protocol)
    manifest = {"schema_version": 1, "scope": "deepseek_behavior_pilot_v1",
        "workspace": str(ROOT), "study": str(output / "inputs"), "model": PILOT["model"],
        "online_pilot": PILOT, "local_smoke": local_smoke, "trials": trials(),
        "authorization": {"source": "user_explicit_small_scale_deepseek_api_authorization_20261102",
            "episodes": 0 if local_smoke else 6, "max_http_attempts": 0 if local_smoke else 72,
            "batch_cost_limit_usd": "0" if local_smoke else PILOT["batch_budget"],
            "old_14_slot_and_360_queues_resumed": False},
        "private_canary": "PRIVATE_DEEPSEEK_BEHAVIOR_PILOT_20261102",
        "sources": {n: sha(b) for n, b in sources.items()}, "inputs": {n: sha(b) for n, b in inputs.items()},
        "protocol_sha256": sha(protocol), "python": sys.version,
        "evidence_scope": "synthetic local acceptance" if local_smoke else "three known tasks, one scene, one repetition; system package comparison"}
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return manifest


def load_manifest(run):
    run = Path(run).resolve()
    raw = (run / "manifest.json").read_bytes()
    if sha(raw) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    manifest = json.loads(raw)
    if (manifest["scope"] != "deepseek_behavior_pilot_v1" or not exact(manifest["online_pilot"], PILOT)
            or not exact(manifest["trials"], trials()) or manifest["model"] != PILOT["model"]
            or type(manifest["local_smoke"]) is not bool):
        raise ValueError("Outside fixed pilot")
    local = manifest["local_smoke"]
    if not exact(manifest["authorization"], {"source": "user_explicit_small_scale_deepseek_api_authorization_20261102",
            "episodes": 0 if local else 6, "max_http_attempts": 0 if local else 72,
            "batch_cost_limit_usd": "0" if local else PILOT["batch_budget"],
            "old_14_slot_and_360_queues_resumed": False}):
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
    """Protocol exercise only: public list -> optional short skill -> final."""
    count = 0
    def reply(request):
        nonlocal count
        index, count = count, count + 1
        tool = "list_drones" if index == 0 else "run_harness" if index == 1 and trial["arm"] == "integrated" and trial["task_id"] == TASKS[0] else None
        message = {"role": "assistant", "reasoning_content": "Synthetic local protocol check.", "content": "Local protocol stop. [TASK DONE]"}
        if tool:
            message["content"] = ""
            args = {} if tool == "list_drones" else {"input_json": json.dumps({"action": "start", "skill": "simple_chain"})}
            message["tool_calls"] = [{"id": "smoke-" + str(index), "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)}}]
        return httpx.Response(200, json={"id": "smoke-response-" + str(index), "object": "chat.completion",
            "model": PILOT["model"], "created": 1, "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}, request=request)
    return httpx.MockTransport(reply)


def worker(run, trial_id):
    from harness_live_cost import LiveCostLedger, make_cost_transport
    from harness_natural_runner import run_worker
    from harness_episode_local import block_network
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
        result = run_worker(run, trial_id, factory, network_attempts=network_attempts,
            model_factory=model_factory_for(key), on_request_stop=ledger.block)
        if result["agent"]["archive_verify"]["complete"] and not result["limits"]["stop_reason"]:
            ledger.finish_trial(trial_id)
        else:
            ledger.block("partial_archive_or_request_stop")
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
        child = subprocess.Popen([sys.executable, str(run / "source/scripts/deepseek_behavior_pilot.py"), mode,
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


def run_prepared(run):
    from harness_live_cost import LiveCostLedger
    from validate_harness_natural_runner import validate_worker, validate_score
    from general_agent import result_layers
    run = Path(run).resolve()
    manifest = load_manifest(run)
    if ROOT != run / "source":
        status = subprocess.run([sys.executable, str(run / "source/scripts/deepseek_behavior_pilot.py"), "run", "--run", str(run)])
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
                    "processes": [process], "worker": None, "score": None}
                rows.append(row)
                if process["returncode"] != 0 or process["kill_reason"] or not process["exited"]:
                    raise RuntimeError("worker_not_cleanly_exited")
                row["worker"] = validate_worker(run, trial, process, manifest=manifest)
                scored = launch(run, trial, "score", journal)
                row["processes"].append(scored)
                row["score"] = validate_score(run, trial, row["worker"], scored)
                row["result"] = result_layers(run, trial, row["worker"], row["score"])
                write(run / "results.json", rows)
                with LiveCostLedger.open(run / "cost-ledger.jsonl", cost_policy(), PILOT["batch_budget"]) as budget:
                    costs = budget.snapshot()
                print(json.dumps({"trial": trial["trial_id"], "passed": row["score"]["checker"]["passed"],
                    "http_calls": row["worker"]["limits"]["delegated_http_attempts"],
                    "stop_reason": row["worker"]["limits"]["stop_reason"]}), flush=True)
                if (costs["blocked"] or row["worker"]["limits"]["stop_reason"] or not row["worker"]["agent"]["archive_verify"]["complete"]):
                    raise RuntimeError("request_stop_or_partial_archive_no_further_dispatch")
        except BaseException as exc:
            reason = type(exc).__name__ + ": " + str(exc)
            write(run / "incomplete.json", {"reason": reason, "started_trials": len(rows), "automatic_retry": False})
        finally:
            write(run / "results.json", rows)
            with LiveCostLedger.open(run / "cost-ledger.jsonl", cost_policy(), PILOT["batch_budget"]) as budget:
                costs = budget.snapshot()
            write(run / "cost-summary.json", costs)
            write(run / "summary.json", {"status": "stopped" if reason else "completed", "reason": reason,
                "planned_trials": 6, "started_trials": len(rows), "scored_trials": sum(r["score"] is not None for r in rows),
                "unstarted_trials": [t["trial_id"] for t in manifest["trials"][len(rows):]],
                "local_smoke": manifest["local_smoke"], "evidence_scope": manifest["evidence_scope"]})
    write(run / "campaign-seal.json", {"files": {str(p.relative_to(run)): sha(p.read_bytes()) for p in run.rglob("*")
        if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}})
    return 1 if reason else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", required=True, type=Path)
    prep.add_argument("--local-smoke", action="store_true")
    for action in ("run", "worker", "score", "verify"):
        item = sub.add_parser(action); item.add_argument("--run", required=True, type=Path)
        if action in {"worker", "score"}:
            item.add_argument("--trial", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        m = prepare(args.output, local_smoke=args.local_smoke)
        print(json.dumps({"prepared": str(args.output.resolve()), "trials": len(m["trials"]), "local_smoke": m["local_smoke"]}))
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
        load_manifest(args.run)
        seal = json.loads((args.run / "campaign-seal.json").read_bytes())
        check_hashes(args.run, seal["files"])
        actual = {str(p.relative_to(args.run)) for p in args.run.rglob("*") if p.is_file()
            and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
        if actual != set(seal["files"]):
            raise ValueError("Run file inventory changed")
        print(json.dumps({"valid": True, "sealed_files": len(seal["files"])}))


if __name__ == "__main__":
    main()
