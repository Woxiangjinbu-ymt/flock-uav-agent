"""Scene scheduling and recovery around the frozen v3 task execution protocol.

Only the coordinator writes aggregate files. Workers own private scene generations.
Completed generations are sealed after their processes and metrics have stopped.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

from .metrics import ResourceMonitor, summarize_performance
from .process import process_identity

EXECUTOR_ID = "scene-parallel-v1"
STARTUP_LOCK = threading.Lock()
REQUIRED_SUFFIXES = ("before", "after", "window", "barrier", "agent-state", "checks", "job")


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def json_digest(r, value):
    return r.digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def scene_plan(plan, selected):
    result = copy.deepcopy(plan)
    result["sessions"] = [copy.deepcopy(selected)]
    result["expected_tasks"] = len(selected["tasks"])
    result["expected_checks"] = sum(len(t["leaves"]) for t in selected["tasks"])
    return result


def validate_workers(workers):
    if type(workers) is not int or not 1 <= workers <= 75:
        raise ValueError("workers must be an integer from 1 to 75")


@contextmanager
def first_pass_lock(stage):
    """Serialize provider probes and execution for this stage's single first pass."""
    with (stage / ".first-pass.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another operation on this first-pass stage is active") from None
        runs = stage / "runs"
        if runs.exists() and any(path.is_dir() for path in runs.iterdir()):
            raise ValueError("This first-pass stage already contains an attempt")
        yield


@contextmanager
def attempt_lock(attempt, exclusive=True):
    with (attempt / ".attempt.lock").open("a" if exclusive else "r") as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("This attempt is active; wait for its coordinator to stop") from None
        yield


def safe_directory(attempt, relative):
    path = attempt / relative
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid scene generation path")
    if not path.resolve().is_relative_to(attempt.resolve()):
        raise ValueError("Scene generation escapes attempt directory")
    return path


def identity(r, attempt, workers):
    # November's published manifest used the upstream component name for this lock.
    roles = ("server", "controller", "agent4drone") if (attempt / "agent4drone-freeze.txt").is_file() else r.ENVIRONMENTS
    files = ["plan.json", "configuration.json", "agent-settings.json", "source_hashes.json", "source.zip",
             "gateway_preflight.json", *(f"{name}-freeze.txt" for name in roles)]
    return {"executor_id": EXECUTOR_ID, "workers": workers,
            "files": {name: r.digest((attempt / name).read_bytes()) for name in files}}


def verify_provenance(r, attempt, plan, settings):
    verify_archive(r, attempt)
    if r.load_plan(plan["name"]) != plan:
        raise ValueError("Plan or benchmark differs from the frozen attempt")
    if r.public_settings(settings) != r.read_json(attempt / "configuration.json"):
        raise ValueError("Model configuration differs from the frozen attempt")
    if r.current_source_hashes() != r.read_json(attempt / "source_hashes.json"):
        raise ValueError("Source code differs from the frozen attempt; use a new run")
    for component in r.ENVIRONMENTS:
        installed = r.subprocess.run(
            ["uv", "--no-cache", "pip", "freeze", "--python", str(r.interpreter(component))],
            check=True, capture_output=True, text=True).stdout
        if installed != (attempt / f"{component}-freeze.txt").read_text():
            raise ValueError(f"Dependencies changed: {component}; use a new run")


def verify_archive(r, attempt):
    """Validate frozen files without requiring today's working tree or environment."""
    manifest = r.read_json(attempt / "manifest.json")
    saved_identity = r.read_json(attempt / "identity.json")
    if manifest.get("identity_sha256") != json_digest(r, saved_identity):
        raise ValueError("Attempt identity was modified")
    if saved_identity != identity(r, attempt, manifest["executor"]["workers"]):
        raise ValueError("Frozen configuration, plan, source archive or dependencies were modified")


def validate_scene_evidence(r, plan, selected, directory):
    """Validate original scoring responses as well as the execution barriers."""
    rows = r.read_json(directory / "results.json")
    state = r.read_json(directory / "scene.json")
    if state.get("status") != "completed" or state.get("session_id") != selected["id"] or state.get("error"):
        raise ValueError("Scene completion state is inconsistent")
    if not r.summarize(scene_plan(plan, selected), rows, "completed")["valid_complete_run"]:
        raise ValueError("Completed scene contains incomplete scoring or unsafe continuation")
    source = r.read_json(r.DATA / selected["file"])
    if r.digest((r.DATA / selected["file"]).read_bytes()) != selected["sha256"]:
        raise ValueError("Original scene data changed")
    task_map = {task["id"]: task for task in source["tasks"]}
    for row in rows:
        prefix = directory / row["task_id"]
        for suffix in REQUIRED_SUFFIXES:
            if not Path(str(prefix) + f".{suffix}.json").is_file():
                raise ValueError("Completed scene is missing mandatory task evidence")
        if row["agent_timed_out"] and not Path(str(prefix) + ".timeout.json").is_file():
            raise ValueError("Completed timeout is missing timeout evidence")
        barrier = r.read_json(str(prefix) + ".barrier.json")
        window = r.read_json(str(prefix) + ".window.json")
        worker = r.read_json(str(prefix) + ".agent-state.json")
        if not (barrier.get("quiescent") is True and barrier.get("inflight") == 0
                and barrier.get("simulation_paused") is True and barrier.get("lease") == window.get("lease")
                and barrier.get("cutoff_at") == window.get("cutoff_at")
                and worker.get("worker_finished") is True and worker.get("busy") is False
                and worker.get("job_id") == row.get("job_id")):
            raise ValueError("Scene has invalid world/worker completion evidence")
        details = r.read_json(str(prefix) + ".checks.json")
        leaves = [d for d in details if d.get("type") == "leaf"]
        remaining = iter(leaves)

        class SavedAPI:
            def api_check_execution(self, endpoint, parameters, show_error=False):
                leaf = next(remaining)
                expected_parameters = {**leaf["parameters"], "session_id": selected["id"]}
                if endpoint != leaf["endpoint"] or parameters != expected_parameters:
                    raise ValueError("Saved check response does not match original check")
                return leaf["response"]

        passed, checked = r.upstream_evaluate(SavedAPI(), task_map[row["task_id"]]["execution_check_apis"], selected["id"])
        actual_leaves = [d for d in checked if d.get("type") == "leaf"]
        if any(d.get("error") for d in checked) or passed != row["passed"] or len(actual_leaves) != len(leaves):
            raise ValueError("Saved scene scores do not match original checker")
        if [(d.get("result"), d.get("actual")) for d in actual_leaves] != [(d.get("result"), d.get("actual")) for d in leaves]:
            raise ValueError("Saved check responses do not match leaf scores")
        if [(d.get("result"), d.get("actual")) for d in leaves] != [(d.get("result"), d.get("actual")) for d in row["leaves"]]:
            raise ValueError("Saved leaf scores do not match result rows")
    return rows


def verify_scene(r, attempt, plan, selected, record):
    directory = safe_directory(attempt, record["directory"])
    marker = directory / "complete.json"
    if not marker.exists():
        raise ValueError(f"Completed scene is missing its completion marker: {selected['id']}")
    seal = r.read_json(marker)
    expected = scene_plan(plan, selected)
    if seal.get("session_id") != selected["id"] or seal.get("plan_digest") != json_digest(r, expected):
        raise ValueError("Scene completion belongs to another plan")
    if seal.get("identity_sha256") != r.read_json(attempt / "manifest.json")["identity_sha256"]:
        raise ValueError("Scene completion belongs to another experiment identity")
    if record.get("seal_sha256") and record["seal_sha256"] != r.digest(marker.read_bytes()):
        raise ValueError("Scene completion marker was modified")
    actual_files = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file() and p != marker}
    if actual_files != set(seal.get("files", {})):
        raise ValueError("Scene evidence files are missing or added after completion")
    for name, checksum in seal["files"].items():
        path = safe_directory(directory, name)
        if path.is_symlink() or r.digest(path.read_bytes()) != checksum:
            raise ValueError(f"Scene evidence was modified: {selected['id']}/{name}")
    return validate_scene_evidence(r, plan, selected, directory)


def seal_scene(r, attempt, plan, selected, directory):
    # Called only after all subprocesses and monitoring writers have stopped.
    files = {str(p.relative_to(directory)): r.digest(p.read_bytes())
             for p in sorted(directory.rglob("*")) if p.is_file()}
    seal = {"schema": 1, "session_id": selected["id"], "plan_digest": json_digest(r, scene_plan(plan, selected)),
            "identity_sha256": r.read_json(attempt / "manifest.json")["identity_sha256"],
            "completed_at": timestamp(), "files": files}
    r.save_json(directory / "complete.json", seal)
    return r.digest((directory / "complete.json").read_bytes())


def run_scene(r, attempt, plan, selected, directory, key, stop_event, events):
    subset = scene_plan(plan, selected)
    rows = r.initial_results(subset)
    state = {"session_id": selected["id"], "status": "running", "started_at": timestamp(), "processes": []}
    children, current_row = [], None
    monitor = ResourceMonitor(directory / "metrics/reproduction-metrics.jsonl", interval=1.0)

    def checkpoint():
        r.save_json(directory / "results.json", rows)
        r.save_json(directory / "scene.json", state)
        events.put({"session_id": selected["id"], "rows": copy.deepcopy(rows), "status": state["status"]})

    def launch(argv, log, env):
        child = r.Child(argv, r.ROOT, directory / log, env)
        children.append(child)
        state["processes"].append({"pid": child.process.pid, "pgid": child.process.pid, "identity": process_identity(child.process.pid)})
        monitor.add_pids(child.process.pid)
        r.save_json(directory / "scene.json", state)
        return child

    try:
        checkpoint()
        monitor.start()
        r.check_stop(stop_event)
        from simulation.server.config.privilege_keys import ADMIN_PRIVILEGE_KEYS
        admin_key = ADMIN_PRIVILEGE_KEYS[0]
        env = os.environ.copy()
        env.update({"UAV_API_KEY": "", "PYTHONUNBUFFERED": "1", "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost", "MPLBACKEND": "Agg"})
        for credential in ("OPENAI_API_KEY", "LLM_API_KEY", "BUPT_API_KEY", "DEEPSEEK_API_KEY"):
            env.pop(credential, None)
        # Reserve/start services as one local critical section; execution remains parallel.
        with STARTUP_LOCK:
            r.check_stop(stop_event)
            server_port, agent_port = r.allocate_ports()
            base = f"http://127.0.0.1:{server_port}"
            token = uuid.uuid4().hex
            api, agent = r.API(base, admin_key), r.API(f"http://127.0.0.1:{agent_port}", token)
            server_id, agent_id = uuid.uuid4().hex, uuid.uuid4().hex
            server = launch([str(r.interpreter("server")), "-m", "runtime.server", "--host", "127.0.0.1", "--port", str(server_port)], "server.log",
                            {**env, "REPRO_ADMIN_KEY": admin_key, "REPRO_INSTANCE_ID": server_id,
                             "REPRO_SERVER_LOG_DIR": str(directory / "server-traces")})
            r.wait_ready(server, api, "/version", stop_event=stop_event, instance_id=server_id)
            scene = r.read_json(r.DATA / selected["file"])
            if r.digest((r.DATA / selected["file"]).read_bytes()) != selected["sha256"]:
                raise ValueError("Scene data changed during execution")
            api.call("POST", f"/sessions/{scene['id']}", {**scene, "status": "inactive"})
            api.call("POST", f"/sessions/{scene['id']}/set-current")
            r.current_snapshot(api, scene)
            process = launch([str(r.interpreter("agent")), "-m", "flockcue.service",
                              "--config", str(attempt / "agent-settings.json"), "--port", str(agent_port),
                              "--logs", str(directory / "agent-traces")], "agent.log",
                             {**env, "UAV_API_URL": base, "OPENAI_API_KEY": key, "LLM_API_KEY": key,
                              "REPRO_CONTROL_TOKEN": token, "REPRO_INSTANCE_ID": agent_id})
            r.wait_ready(process, agent, "/health", agent=True, stop_event=stop_event, instance_id=agent_id)
        state.update(server_port=server_port, agent_port=agent_port)
        tasks = {t["id"]: t for t in scene["tasks"]}
        for current_row in rows:
            r.check_stop(stop_event)
            task = tasks[current_row["task_id"]]
            prefix = directory / task["id"]
            current_row["status"] = "preparing"
            checkpoint()
            api.call("POST", "/_reproduction/prepare", {})
            r.save_json(str(prefix) + ".before.json", r.prepare_task(api, scene, plan["protocol"], stop_event))
            current_row["status"] = "running"
            checkpoint()
            lease = uuid.uuid4().hex
            window = api.call("POST", "/_reproduction/open", {"lease": lease, "timeout_seconds": plan["protocol"]["timeout_seconds"]})
            r.save_json(str(prefix) + ".window.json", window)
            accepted = agent.call("POST", "/agent/command/async", {"command": task["content"]},
                                  extra_headers={"X-Reproduction-Lease": lease, "X-Reproduction-Cutoff": str(window["cutoff_at"])})
            current_row["job_id"] = accepted["job_id"]
            checkpoint()
            print(f"{scene['id']}/{task['id']} submitted", flush=True)
            try:
                job = r.wait_job(agent, accepted["job_id"], max(0, window["cutoff_at"] - time.time()),
                                 plan["protocol"]["poll_seconds"], lambda j: r.save_json(str(prefix) + ".job.json", j),
                                 cutoff_at=window["cutoff_at"], stop_event=stop_event)
                current_row["agent_timed_out"] = False
            except r.AgentDeadlineExceeded as error:
                current_row["agent_timed_out"] = True
                job = r.read_json(str(prefix) + ".job.json") if Path(str(prefix) + ".job.json").exists() else {"status": "unknown"}
                r.save_json(str(prefix) + ".timeout.json", {"reason": str(error), "cutoff_at": window["cutoff_at"],
                                                           "observed_at": time.time(), "last_job_status": job["status"]})
                print(f"{scene['id']}/{task['id']}: agent timed out; closing actions and grading environment", flush=True)
            current_row.update(agent_status="timeout" if current_row["agent_timed_out"] else job["status"],
                               agent_success=(job.get("result") or {}).get("success"),
                               agent_error="Task budget exhausted" if current_row["agent_timed_out"] else job.get("error"))
            checkpoint()
            barrier = api.call("POST", "/_reproduction/close", {"lease": lease, "drain_seconds": plan["protocol"]["drain_seconds"]},
                               timeout=plan["protocol"]["drain_seconds"] + 5)
            r.save_json(str(prefix) + ".barrier.json", barrier)
            if not (barrier.get("quiescent") is True and barrier.get("inflight") == 0 and barrier.get("simulation_paused") is True):
                raise ValueError("World not quiescent; refusing to score")
            current_row["world_quiescent"] = True
            if current_row["agent_timed_out"]:
                try:
                    agent.call("POST", f"/_reproduction/jobs/{accepted['job_id']}/stop", {}, timeout=2)
                except Exception as error:
                    current_row["stop_request_error"] = type(error).__name__
            r.save_json(str(prefix) + ".after.json", r.current_snapshot(api, scene))
            passed, details = r.upstream_evaluate(api, task["execution_check_apis"], scene["id"])
            r.save_json(str(prefix) + ".checks.json", details)
            check_leaves = [d for d in details if d.get("type") == "leaf"]
            if len(check_leaves) != current_row["expected_checks"] or any(d.get("error") for d in details):
                raise ValueError("Incomplete/erroring check tree; refusing to score")
            for leaf, detail in zip(current_row["leaves"], check_leaves):
                leaf.update(status="scored", result=detail["result"], actual=detail["actual"])
            current_row.update(status="scored", passed=bool(passed), task_pass=bool(passed),
                               token_usage=(job.get("result") or {}).get("token_usage"))
            if passed and job["status"] == "completed" and not current_row["agent_timed_out"]:
                api.call("POST", f"/sessions/{scene['id']}/tasks/{task['id']}/mark-done")
            checkpoint()
            print(f"{scene['id']}/{task['id']}: agent_timeout={current_row['agent_timed_out']}, passed={passed}, checks={sum(d['result'] for d in check_leaves)}/{len(check_leaves)}", flush=True)
            worker = r.wait_worker(agent, accepted["job_id"], plan["protocol"]["worker_stop_seconds"], stop_event)
            r.save_json(str(prefix) + ".agent-state.json", worker)
            r.save_json(str(prefix) + ".job.json", worker["job"])
            current_row.update(continuation_safe=True, agent_final_status=worker["job"]["status"])
            # Independent instrumentation includes LLM responses discarded by upstream cancellation.
            performance = summarize_performance(directory)
            usage = performance.get("by_job", {}).get(accepted["job_id"], {}).get("llm", {}).get("usage")
            if usage is not None:
                current_row["observed_token_usage"] = usage
            checkpoint()
            r.interruptible_sleep(plan["protocol"]["between_tasks_seconds"], stop_event)
        current_row = None
        if not r.summarize(subset, rows, "completed")["valid_complete_run"]:
            raise ValueError("Scene finished without complete scoring")
        state["status"] = "completed"
    except BaseException as error:
        state["status"] = "interrupted" if stop_event.is_set() else "aborted"
        state["error"] = f"{type(error).__name__}: {error}".replace(key, "<redacted>")
        if current_row is not None and current_row["status"] != "scored":
            current_row.update(status="invalid", error=state["error"])
        print(f"{selected['id']}: {state['error']}", file=sys.stderr, flush=True)
    finally:
        for child in reversed(children):
            try:
                child.stop()
            except BaseException as error:
                state.update(status="aborted", error=f"Process cleanup failed: {type(error).__name__}")
        monitor.stop()
        state["finished_at"] = timestamp()
        r.save_json(directory / "performance.json", summarize_performance(directory))
        checkpoint()
    seal = None
    if state["status"] == "completed":
        seal = seal_scene(r, attempt, plan, selected, directory)
    return {"session_id": selected["id"], "rows": rows, "status": state["status"], "seal_sha256": seal,
            "error": state.get("error"), "finished_at": state["finished_at"]}


def merge_rows(r, plan, scene_rows):
    rows = []
    for selected in plan["sessions"]:
        subset = scene_plan(plan, selected)
        values = scene_rows.get(selected["id"], r.initial_results(subset))
        r.summarize(subset, values, "running")  # structural validation even when incomplete
        rows.extend(values)
    r.summarize(plan, rows, "running")
    return rows


def _performance(r, attempt, manifest):
    selected = [safe_directory(attempt, record["directory"]) for record in manifest["scenes"].values() if record.get("directory")]
    fleet_files = list(attempt.glob("fleet-resources-*.jsonl"))
    return {"selected_generations": summarize_performance(selected), "all_executed_generations": summarize_performance(attempt),
            "fleet_resources": summarize_performance(fleet_files)["resources"],
            "invocations": manifest.get("invocations", []),
            "workers": manifest["executor"]["workers"], "completed_scenes": sum(v["status"] == "completed" for v in manifest["scenes"].values()),
            "note": "All executed work includes abandoned generations; selected generations are used for task scoring. Resource peaks are sampled."}


def coordinate(r, attempt, plan, settings, key, manifest, scene_rows):
    events, stop_event = queue.Queue(), threading.Event()
    active = {}
    workers = manifest["executor"]["workers"]
    pending = [s for s in plan["sessions"] if manifest["scenes"][s["id"]]["status"] != "completed"]
    session = {"started_at": timestamp(), "finished_at": None, "status": "running"}
    manifest.setdefault("invocations", []).append(session)
    manifest.update(status="running", finished_at=None)
    manifest.pop("error", None)
    invocation_started = time.monotonic()
    fleet = ResourceMonitor(attempt / f"fleet-resources-{len(manifest['invocations']):04d}.jsonl", pids=[os.getpid()])

    def checkpoint():
        rows = merge_rows(r, plan, scene_rows)
        r.save_json(attempt / "results.json", rows)
        r.save_json(attempt / "manifest.json", manifest)
        r.save_json(attempt / "summary.json", r.summarize(plan, rows, manifest["status"]))

    def submit(pool, selected):
        record = manifest["scenes"][selected["id"]]
        generation = record.get("generation", 0) + 1
        if record.get("directory"):
            previous = {k: v for k, v in record.items() if k != "history"}
            record.setdefault("history", []).append(previous)
        relative = selected["id"] if generation == 1 else f"retries/{selected['id']}/{generation:04d}"
        directory = safe_directory(attempt, relative)
        record.update(status="running", generation=generation, directory=relative, seal_sha256=None, error=None)
        scene_rows[selected["id"]] = r.initial_results(scene_plan(plan, selected))
        checkpoint()  # Persist assignment before starting processes, including crash recovery.
        directory.mkdir(parents=True, exist_ok=False)
        future = pool.submit(run_scene, r, attempt, plan, selected, directory, key, stop_event, events)
        active[future] = selected

    checkpoint()
    print(f"Attempt: {attempt}\nProtocol: {plan['protocol']['id']}; task timeout: {plan['protocol']['timeout_seconds']} seconds", flush=True)
    print(f"Executor: {EXECUTOR_ID}; workers={workers}; pending scenes={len(pending)}", flush=True)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scene")
    fleet.start()
    last_checkpoint, last_metrics, dirty = time.monotonic(), time.monotonic(), False
    last_progress = time.monotonic()
    try:
        while active or pending:
            try:
                while pending and len(active) < workers and not stop_event.is_set():
                    submit(pool, pending.pop(0))
                try:
                    event = events.get(timeout=.2)
                    scene_rows[event["session_id"]] = event["rows"]
                    dirty = True
                    while True:
                        event = events.get_nowait()
                        scene_rows[event["session_id"]] = event["rows"]
                except queue.Empty:
                    pass
                for future in list(active):
                    if not future.done():
                        continue
                    selected = active.pop(future)
                    record = manifest["scenes"][selected["id"]]
                    try:
                        result = future.result()
                        scene_rows[selected["id"]] = result.pop("rows")
                        record.update({k: v for k, v in result.items() if k != "session_id"})
                        if record["status"] == "completed":
                            scene_rows[selected["id"]] = verify_scene(r, attempt, plan, selected, record)
                    except Exception as error:
                        record.update(status="aborted", error=f"{type(error).__name__}: {error}".replace(key, "<redacted>"))
                    dirty = True
                    checkpoint()
                if dirty and time.monotonic() - last_checkpoint >= 1:
                    checkpoint()
                    last_checkpoint, dirty = time.monotonic(), False
                if time.monotonic() - last_metrics >= 60:
                    r.save_json(attempt / "performance.json", _performance(r, attempt, manifest))
                    last_metrics = time.monotonic()
                if time.monotonic() - last_progress >= 30:
                    scored = sum(row["status"] == "scored" for rows in scene_rows.values() for row in rows)
                    completed = sum(v["status"] == "completed" for v in manifest["scenes"].values())
                    print(f"Progress: scenes={completed}/{len(plan['sessions'])}; tasks={scored}/{plan['expected_tasks']}; active={len(active)}; queued={len(pending)}", flush=True)
                    last_progress = time.monotonic()
                if stop_event.is_set():
                    pending.clear()
            except KeyboardInterrupt:
                stop_event.set()
                pending.clear()
                print("Stopping dispatch; closing all scene processes and preserving records...", flush=True)
    except BaseException as error:
        stop_event.set()
        manifest["error"] = f"{type(error).__name__}: {error}".replace(key, "<redacted>")
    finally:
        # Running workers observe the event during every bounded API polling/wait phase.
        while True:
            try:
                pool.shutdown(wait=True, cancel_futures=True)
                break
            except KeyboardInterrupt:
                stop_event.set()
        for future, selected in active.items():
            try:
                result = future.result()
                scene_rows[selected["id"]] = result.pop("rows")
                manifest["scenes"][selected["id"]].update({k: v for k, v in result.items() if k != "session_id"})
            except BaseException as error:
                manifest["scenes"][selected["id"]].update(status="aborted", error=type(error).__name__)
        # Reread private scene files after all writers have stopped; event timing cannot
        # overwrite a final result with an earlier checkpoint.
        for selected in plan["sessions"]:
            record = manifest["scenes"][selected["id"]]
            if record.get("directory"):
                directory = safe_directory(attempt, record["directory"])
                if (directory / "results.json").exists():
                    scene_rows[selected["id"]] = r.read_json(directory / "results.json")
                if (directory / "complete.json").exists():
                    try:
                        scene_rows[selected["id"]] = verify_scene(r, attempt, plan, selected, record)
                        record.update(status="completed", seal_sha256=r.digest((directory / "complete.json").read_bytes()))
                    except Exception as error:
                        record.update(status="invalid", error=str(error))
        complete = all(v["status"] == "completed" for v in manifest["scenes"].values())
        manifest["status"] = "completed" if complete and not manifest.get("error") else "interrupted" if stop_event.is_set() else "aborted"
        try:
            verify_provenance(r, attempt, plan, settings)
        except Exception as error:
            manifest.update(status="invalid", error=f"Provenance validation failed: {error}")
        manifest["finished_at"] = timestamp()
        session.update(finished_at=manifest["finished_at"], status=manifest["status"], wall_seconds=time.monotonic() - invocation_started)
        fleet.stop()
        checkpoint()
        r.save_json(attempt / "performance.json", _performance(r, attempt, manifest))
    print(json.dumps(r.read_json(attempt / "summary.json"), ensure_ascii=False, indent=2))
    return manifest["status"] == "completed"


def run_attempt(r, name, workers=1):
    validate_workers(workers)
    plan = r.load_plan(name)
    settings, _, key = r.selected_config()
    if not key:
        raise ValueError("Missing key; configure the provider first")
    verification = r.read_json(r.STAGE / "preflight.json")
    if (not verification.get("offline_ready") or not verification.get("gateway_verified")
            or verification.get("blockers") or verification.get("configuration") != r.public_settings(settings)):
        raise ValueError("Run preflight --gateway with this configuration before starting")
    r.RUNS.mkdir(parents=True, exist_ok=True)
    attempt = r.RUNS / (time.strftime("%Y%m%d-%H%M%S") + f"-{name}-" + uuid.uuid4().hex[:8])
    attempt.mkdir(mode=0o700)
    r.save_json(attempt / "plan.json", plan)
    r.save_json(attempt / "configuration.json", r.public_settings(settings))
    runtime_settings = copy.deepcopy(settings)
    for provider in runtime_settings["provider_configs"].values():
        provider["api_key"] = ""
    r.save_json(attempt / "agent-settings.json", runtime_settings)
    r.save_json(attempt / "gateway_preflight.json", verification)
    r.code_snapshot(attempt)
    fingerprint = identity(r, attempt, workers)
    r.save_json(attempt / "identity.json", fingerprint)
    manifest = {"status": "created", "started_at": timestamp(), "git_head": r.git_head(), "protocol": plan["protocol"],
                "plan": name, "benchmark_sha256": r.ZIP_SHA, "python": sys.version,
                "source": "source.zip and source_hashes.json", "identity_sha256": json_digest(r, fingerprint),
                "executor": {"id": EXECUTOR_ID, "workers": workers, "scene_order": "claim in frozen plan order; finish independently",
                             "task_order": "original JSON array", "recovery": "registered first pass only",
                             "automatic_retries": 0},
                "scenes": {s["id"]: {"status": "pending", "generation": 0, "directory": None, "history": []} for s in plan["sessions"]}}
    with attempt_lock(attempt):
        return coordinate(r, attempt, plan, settings, key, manifest, {})


def status(r, attempt):
    manifest = r.read_json(attempt / "manifest.json")
    rows = r.read_json(attempt / "results.json")
    return {"attempt": str(attempt), "status": manifest["status"], "executor": manifest.get("executor"),
            "scored_tasks": sum(row["status"] == "scored" for row in rows), "planned_tasks": len(rows),
            "scenes": manifest.get("scenes", {}),
            "performance_file": str(attempt / "performance.json"),
            "note": "Read-only progress snapshot; metrics refresh every 60 seconds and at shutdown."}
