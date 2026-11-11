#!/usr/bin/env python3
"""Stateful deployment using the frozen 600-task protocol.

Agent decisions, tools, prompts and the upstream check-tree evaluator are reused
unchanged. This module owns its server/agent processes; never attach it to a GUI run.
"""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "datasets/original-benchmark/benchmark.zip"
ZIP_SHA = "b5040097d2bfdd44600f3bf486fdb43ee3eb1247fec9c67900b5fcda6feb94a3"
PLAN = "flockcue-600-first-pass"
PLAN_SHA = "6589d8e1b4be6664cb3d651f09fe2cc54e41ffca7a361a6dae675ee455ec8823"
STAGE = ROOT.parent / f"{ROOT.name}-runs" / PLAN
DATA = STAGE / "scenes"
RUNS = STAGE / "runs"
SETTINGS = STAGE / "settings.json"
ENVIRONMENTS = {
    "server": "simulation/server",
    "controller": "runtime",
    "agent": "flockcue",
}
EXPECTED_FREEZE = {
    "server": "63e4bede0f8d49b7f9336f3a446e0102e1833537ebe524b77e90923a57b86396",
    "controller": "7d0330dee2b5e2ebc4781372c2f4840909ba1e724f9da48d2aae61114a7fbd29",
    "agent": "a0325ce0dad9e85ea98784b97205e9bd7d3645b5fe03786cc573e5c67de9f4a1",
}
GATEWAY = "https://api.deepseek.com"
PROVIDER = "deepseek_official"
MODEL = "deepseek-flash"
PROTOCOL_ID = "agent4drone-v3-gui500-timeout-scoring"
TASK_TIMEOUT_SECONDS = 500


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def leaves(node, path="root"):
    if not isinstance(node, dict):
        raise ValueError(f"Invalid check node: {path}")
    if "checks" in node:
        if node.get("logic", "and").lower() not in {"and", "or", "not"}:
            raise ValueError(f"Invalid check operator: {path}")
        return [leaf for i, child in enumerate(node["checks"])
                for leaf in leaves(child, f"{path}.{i}")]
    endpoint = node.get("endpoint") or node.get("path", "")
    if not endpoint.startswith("/check/"):
        raise ValueError(f"Unexpected check endpoint: {endpoint}")
    return [{"path": path, "definition": node}]


def canonical_scenes():
    if digest(ZIP.read_bytes()) != ZIP_SHA:
        raise ValueError("Benchmark ZIP differs from the audited upstream version")
    with zipfile.ZipFile(ZIP) as z:
        scenes = [(n, json.loads(z.read(n))) for n in sorted(z.namelist()) if n.endswith(".json")]
    ids = [s["id"] for _, s in scenes]
    task_ids = [t["id"] for _, s in scenes for t in s["tasks"]]
    checks = sum(len(leaves(t["execution_check_apis"])) for _, s in scenes for t in s["tasks"])
    if (len(ids), len(set(ids)), len(task_ids), len(set(task_ids)), checks) != (75, 75, 1500, 1500, 9396):
        raise ValueError("Unexpected benchmark counts or duplicate IDs")
    return scenes


def select_scenes(scenes):
    selection = read_json(ROOT / "datasets/flockcue-600/candidate-600.json")
    ids = {item["session_id"] for stratum in selection["strata"] for item in stratum["sessions_selected"]}
    return [(name, scene) for name, scene in scenes if scene["id"] in ids]


def make_plan(name, scenes):
    records = []
    with zipfile.ZipFile(ZIP) as z:
        for filename, scene in scenes:
            tasks = scene["tasks"]
            records.append({"id": scene["id"], "name": scene["name"], "file": filename,
                            "sha256": digest(z.read(filename)), "family": scene["task_type"],
                            "tasks": [{"id": t["id"], "name": t["name"], "difficulty": t["difficulty"],
                                       "content_sha256": digest(t["content"].encode()),
                                       "leaves": leaves(t["execution_check_apis"])} for t in tasks]})
    return {"schema": 1, "name": name, "benchmark_sha256": ZIP_SHA,
            "protocol": {"id": PROTOCOL_ID,
                         "session_order": "filename ascending", "task_order": "original JSON array",
                         "reset": "original JSON once per session; set-current, land, charge before each task",
                         "share_blackboard_by_session": True, "temperature": 0.1,
                         "timeout_seconds": TASK_TIMEOUT_SECONDS,
                         "timeout_source": "upstream Checker GUI timeout_var initial value (500), not AGENT_TIMEOUT fallback (300)",
                         "poll_seconds": 5,
                         "land_wait_seconds": 2, "charge_wait_seconds": 2, "between_tasks_seconds": 1,
                         "timeout_policy": "close admission, drain, freeze world, score; continue only after worker exits; no retry",
                         "drain_seconds": 30, "worker_stop_seconds": 120,
                         "inflight_policy": "finish requests admitted before cutoff; refuse later and stale-lease requests",
                         "grading_state": "pause simulation ticks after drain; resume before next preparation",
                         "agent_budget": "upstream max_iterations=150; recursion_limit=450; not a hard tool budget",
                         "aliases": False, "resume": False},
            "expected_tasks": sum(len(s["tasks"]) for s in records),
            "expected_checks": sum(len(t["leaves"]) for s in records for t in s["tasks"]),
            "sessions": records}


def use_stage(path):
    global STAGE, DATA, RUNS, SETTINGS
    STAGE = Path(path).resolve()
    DATA, RUNS, SETTINGS = STAGE / "scenes", STAGE / "runs", STAGE / "settings.json"


def interpreter(component):
    override = os.environ.get(f"FLOCKCUE_{component.upper()}_PYTHON")
    return Path(override).absolute() if override else ROOT / ENVIRONMENTS[component] / ".venv/bin/python"


def git_head():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def seal_registration(stage):
    """Anchor the fresh run registration in an independent local Git history."""
    commands = (
        ["init", "--quiet"],
        ["add", "--force", "--", "registration.json"],
        ["-c", "user.name=FlockCue", "-c", "user.email=flockcue@localhost",
         "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "commit", "--quiet", "-m", "Freeze first-pass registration"],
    )
    for arguments in commands:
        subprocess.run(["git", "-C", str(stage), *arguments], check=True)


def prepare():
    from tempfile import TemporaryDirectory

    dest = STAGE
    if dest.exists():
        raise ValueError(f"Stage already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    scenes = select_scenes(canonical_scenes())
    plan = make_plan(PLAN, scenes)
    with TemporaryDirectory(dir=dest.parent, prefix=".prepare-") as temp:
        staged = Path(temp) / "new"
        (staged / "scenes").mkdir(parents=True)
        save_json(staged / "plan.json", plan)
        if digest((staged / "plan.json").read_bytes()) != PLAN_SHA:
            raise ValueError("Rebuilt 600-task plan differs from the published deployment")
        with zipfile.ZipFile(ZIP) as archive:
            for name, _ in scenes:
                if Path(name).name != name:
                    raise ValueError(f"Unsafe scene filename: {name}")
                (staged / "scenes" / name).write_bytes(archive.read(name))
        config = {"selected_provider": PROVIDER, "uav_base_url": "http://127.0.0.1:8000",
                  "uav_api_key": "", "share_blackboard_by_session": True, "toolchain_json_recording": True,
                  "provider_configs": {PROVIDER: {
                      "name": "deepseek-flash", "type": "openai-compatible", "base_url": GATEWAY,
                      "requires_api_key": True, "api_key": "", "default_model": MODEL, "default_models": [MODEL]}}}
        save_json(staged / "settings.json", config)
        save_json(staged / "registration.json", {"protocol": plan["protocol"]["id"], "plan_sha256": PLAN_SHA,
                  "sources": current_source_hashes(), "settings": digest((staged / "settings.json").read_bytes()),
                  "workers": 8, "first_attempt_only": True, "git_head": git_head()})
        seal_registration(staged)
        staged.rename(dest)
    print(f"Prepared {dest}: {len(scenes)} scenes, {plan['expected_tasks']} tasks, {plan['expected_checks']} checks")


def provider_key():
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    private = Path(os.environ.get("FLOCKCUE_SETTINGS", ROOT / "runtime/llm_settings.local.json"))
    if not private.is_file():
        return None
    provider = read_json(private)["provider_configs"][PROVIDER]
    if provider.get("base_url", "").rstrip("/") != GATEWAY:
        raise ValueError("Private credential provider must be the official DeepSeek endpoint")
    return provider.get("api_key")


def selected_config():
    settings = read_json(SETTINGS)
    config = settings["provider_configs"][settings["selected_provider"]]
    if config.get("type") != "openai-compatible" or config.get("base_url", "").rstrip("/") != GATEWAY:
        raise ValueError(f"This protocol requires the official DeepSeek API provider ({GATEWAY})")
    if settings.get("share_blackboard_by_session") is not True or settings.get("toolchain_json_recording") is not True:
        raise ValueError("Protocol requires shared session blackboard and toolchain recording")
    if settings.get("uav_api_key"):
        raise ValueError("Agent must use default AGENT privileges: keep uav_api_key empty")
    if config.get("default_model") != MODEL:
        raise ValueError(f"Protocol requires default_model={MODEL}")
    key = provider_key()
    return settings, config, key


def load_plan(name):
    if name != PLAN or digest((STAGE / "plan.json").read_bytes()) != PLAN_SHA:
        raise ValueError("Expected the registered 600-task first-pass plan")
    plan = read_json(STAGE / "plan.json")
    expected = make_plan(name, select_scenes(canonical_scenes()))
    if plan != expected:
        raise ValueError("Plan differs from frozen protocol; use a separately named protocol for extensions")
    for scene in plan["sessions"]:
        if digest((DATA / scene["file"]).read_bytes()) != scene["sha256"]:
            raise ValueError(f"Extracted scene was modified: {scene['file']}")
    return plan


def public_settings(settings):
    settings = copy.deepcopy(settings)
    settings["uav_api_key"] = ""
    for provider in settings.get("provider_configs", {}).values():
        provider["api_key"] = "<redacted>"
    return settings


def preflight(gateway=False):
    report = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "offline_ready": False,
              "gateway_verified": False, "blockers": []}
    # Invalidate any previous pass before attempting fresh checks.
    save_json(STAGE / "preflight.json", report)
    for name in (PLAN,):
        p = load_plan(name)
        report[name] = {"tasks": p["expected_tasks"], "checks": p["expected_checks"]}
    report["environments"] = {}
    for component in ENVIRONMENTS:
        python = interpreter(component)
        result = subprocess.run(["uv", "--no-cache", "pip", "check", "--python", str(python)], capture_output=True, text=True)
        installed_packages(component)
        report["environments"][component] = {"ok": result.returncode == 0, "output": (result.stdout + result.stderr).strip()}
        if result.returncode:
            report["blockers"].append(f"{component}: dependency check failed")
    settings, config, key = selected_config()
    report["configuration"] = public_settings(settings)
    report["offline_ready"] = not report["blockers"]
    if not key:
        report["blockers"].append("Missing DeepSeek API key in local settings or DEEPSEEK_API_KEY environment")
    elif gateway:
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY"] = key
        result = subprocess.run([str(interpreter("agent")), "-m", "runtime.gateway", str(SETTINGS)],
                                env=env, cwd=ROOT, capture_output=True, text=True, timeout=210)
        try:
            report["gateway"] = json.loads(result.stdout)
        except ValueError:
            report["gateway"] = {"ok": False, "error": "Gateway probe did not return JSON; inspect dependencies"}
        report["gateway_verified"] = result.returncode == 0 and report["gateway"].get("ok", False)
        if not report["gateway_verified"]:
            report["blockers"].append("Gateway model/tool roundtrip failed")
    if not report["gateway_verified"]:
        report["blockers"].append("Run preflight --gateway to verify model ID, authentication and tool roundtrip")
    save_json(STAGE / "preflight.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return not report["blockers"]


class API:
    def __init__(self, url, key=None):
        self.url, self.key = url, key
        # Local simulation traffic must bypass global HTTP(S)_PROXY.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, data=None, params=None, timeout=15, extra_headers=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Only relative local API paths are allowed")
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else str(v).lower()
                                                if isinstance(v, bool) else v for k, v in params.items()})
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers or {})
        if self.key:
            headers["X-API-Key"] = self.key
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: HTTP {e.code}") from None

    def api_check_execution(self, endpoint, parameters, show_error=False):
        result = self.call("GET", endpoint, params=parameters)
        if not isinstance(result, dict) or type(result.get("result")) is not bool:
            raise ValueError("Check endpoint must return a boolean result")
        return result


def upstream_evaluate(api, tree, session_id):
    from simulation.checker import ExecutionChecker
    checker = ExecutionChecker(api)
    return checker.evaluate_execution_check_node(tree, session_id)


def allocate_ports():
    # Reserve two different ephemeral ports during selection, then release for children.
    sockets = [socket.socket(), socket.socket()]
    try:
        for s in sockets:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:
            s.close()


class Child:
    def __init__(self, argv, cwd, log, env):
        self.log_path = Path(log)
        self.log = self.log_path.open("ab")
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._known_members = {}
        parent_read, self._parent_write = os.pipe()
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(ROOT / "runtime/process.py"),
                 "--parent-fd", str(parent_read), "--", *argv],
                cwd=cwd, stdout=self.log, stderr=subprocess.STDOUT, env=env,
                start_new_session=True, pass_fds=(parent_read,))
            from .process import process_identity
            original_identity = process_identity(self.process.pid)
            if original_identity is not None:
                self._known_members[self.process.pid] = original_identity
        except BaseException:
            os.close(self._parent_write)
            self._parent_write = None
            self.log.close()
            if hasattr(self, "process"):
                # Closing the lifeline also handles failures during process
                # identity inspection, before this Child can be registered.
                self.process.wait(timeout=6)
            raise
        finally:
            os.close(parent_read)

    def capture_group_members(self):
        from .process import group_members
        with self._stop_lock:
            members = group_members(self.process.pid)
            if not any(members.get(pid) == identity for pid, identity in self._known_members.items()):
                raise RuntimeError("Owned service process identity was lost during startup")
            self._known_members.update(members)

    def stop(self):
        from .process import group_members
        with self._stop_lock:
            if self._stopped:
                return
            try:
                members = group_members(self.process.pid)
                if members and not any(members.get(pid) == identity for pid, identity in self._known_members.items()):
                    raise RuntimeError("Cannot verify remaining service group ownership; refusing to signal it")
                self._known_members.update(members)
                if members:
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 5
                while members and time.monotonic() < deadline:
                    self.process.poll()
                    members = group_members(self.process.pid)
                    if members and not any(members.get(pid) == identity for pid, identity in self._known_members.items()):
                        raise RuntimeError("Remaining service group identity changed; refusing to signal it")
                    self._known_members.update(members)
                    if members:
                        time.sleep(.05)
                if members:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.process.wait(timeout=5)
                reap_deadline = time.monotonic() + 2
                while group_members(self.process.pid):
                    if time.monotonic() >= reap_deadline:
                        raise RuntimeError("Owned service group remained live after cleanup")
                    time.sleep(.05)
            finally:
                # Never reconsider a reaped PID/PGID on a later stop call.
                self._stopped = True
                if self._parent_write is not None:
                    os.close(self._parent_write)
                    self._parent_write = None
                self.log.close()


def check_stop(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Experiment interrupted")


def interruptible_sleep(seconds, stop_event=None):
    if stop_event is None:
        time.sleep(seconds)
    else:
        stop_event.wait(max(0, seconds))
        check_stop(stop_event)


def wait_ready(child, api, path, agent=False, stop_event=None, instance_id=None):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        check_stop(stop_event)
        if child.process.poll() is not None:
            raise RuntimeError("Owned service exited during startup; inspect service log")
        if "Uvicorn running on" not in child.log_path.read_text(errors="replace"):
            time.sleep(0.2)
            continue
        try:
            data = api.call("GET", path, timeout=1)
            if instance_id is not None and api.call("GET", "/_reproduction/identity", timeout=1).get("instance_id") != instance_id:
                raise RuntimeError("Service identity mismatch; refusing another worker's service")
            if agent and not data.get("agent_initialized"):
                raise RuntimeError("Agent initialization failed; inspect agent log")
            # A port collision must not let us proceed using somebody else's service.
            time.sleep(0.1)
            if child.process.poll() is not None:
                raise RuntimeError("Owned service exited after startup")
            child.capture_group_members()
            return data
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError("Service startup timed out")


def current_snapshot(api, scene):
    snapshot = api.call("GET", "/sessions/current", params={"data": True})
    if snapshot.get("id") != scene["id"]:
        raise ValueError("Current session ID changed")
    for kind in ("drones", "targets", "obstacles"):
        if {x["id"] for x in snapshot[kind]} != {x["id"] for x in scene[kind]}:
            raise ValueError(f"Entity IDs changed: {kind}")
    return snapshot


def prepare_task(api, scene, protocol, stop_event=None):
    check_stop(stop_event)
    api.call("POST", f"/sessions/{scene['id']}/set-current")
    before = current_snapshot(api, scene)
    landed = api.call("POST", "/drones/land_all")
    if landed.get("drones_blocked") != 0 or landed.get("total_drones") != len(scene["drones"]):
        raise ValueError("Landing preparation blocked or incomplete")
    interruptible_sleep(protocol["land_wait_seconds"], stop_event)
    charged = api.call("POST", "/drones/charge_all")
    if charged.get("total_drones") != len(scene["drones"]):
        raise ValueError("Charging preparation incomplete")
    interruptible_sleep(protocol["charge_wait_seconds"], stop_event)
    after = current_snapshot(api, scene)
    positions = {d["id"]: d["position"] for d in before["drones"]}
    for d in after["drones"]:
        old, pos = positions[d["id"]], d["position"]
        if (pos["x"], pos["y"]) != (old["x"], old["y"]):
            raise ValueError("Preparation changed XY position")
        if abs(pos["z"]) > 1e-9 or d["battery_level"] != 100 or d["status"] != "idle":
            raise ValueError("Drone not grounded, idle and fully charged")
    return after


class AgentDeadlineExceeded(TimeoutError):
    pass


def wait_job(agent, job_id, timeout, poll, on_update, cutoff_at=None, stop_event=None):
    deadline = time.monotonic() + timeout
    while True:
        check_stop(stop_event)
        remaining = deadline - time.monotonic()
        # One bounded final read avoids labelling an on-time completion as a
        # timeout just because the last regular poll was five seconds earlier.
        try:
            job = agent.call("GET", f"/agent/jobs/{job_id}", timeout=min(15, remaining) if remaining > 0 else 2)
        except (TimeoutError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise AgentDeadlineExceeded("Agent task budget exhausted") from None
            raise
        on_update(job)
        if job["status"] in {"completed", "failed"}:
            if not job.get("completed_at"):
                # Upstream publishes terminal status before its finally block
                # assigns completed_at. Do not mistake that brief race for a
                # malformed final result, or extend the original task budget.
                if time.monotonic() >= deadline:
                    raise ValueError("Terminal job missing completion timestamp at deadline")
                interruptible_sleep(min(.01, max(0, deadline - time.monotonic())), stop_event)
                continue
            if cutoff_at is not None and datetime.fromisoformat(job["completed_at"]).timestamp() > cutoff_at:
                raise AgentDeadlineExceeded("Agent finished after the task deadline")
            return job
        if job["status"] not in {"queued", "running"}:
            if job["status"] == "cancelled" and cutoff_at is not None and time.time() >= cutoff_at:
                raise AgentDeadlineExceeded("Agent stopped at the task deadline")
            raise ValueError("Unexpected/cancelled job; cannot assume execution has stopped")
        if time.monotonic() >= deadline:
            raise AgentDeadlineExceeded("Agent task budget exhausted")
        interruptible_sleep(min(poll, max(0, deadline - time.monotonic())), stop_event)


def wait_worker(agent, job_id, seconds, stop_event=None):
    deadline = time.monotonic() + seconds
    while True:
        check_stop(stop_event)
        state = agent.call("GET", f"/_reproduction/jobs/{job_id}/state", timeout=min(5, max(.01, deadline - time.monotonic())))
        if state.get("worker_finished") is True and state.get("busy") is False:
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError("Worker did not exit; scored task saved, refusing to start next task")
        interruptible_sleep(min(.2, max(0, deadline - time.monotonic())), stop_event)


def initial_results(plan):
    return [{"session_id": s["id"], "task_id": t["id"], "name": t["name"], "family": s["family"],
             "difficulty": t["difficulty"], "status": "not_run", "expected_checks": len(t["leaves"]),
             "agent_timed_out": None, "task_pass": None, "continuation_safe": False,
             "leaves": [{**copy.deepcopy(leaf), "status": "not_run", "result": None} for leaf in t["leaves"]]}
            for s in plan["sessions"] for t in s["tasks"]]


def summarize(plan, rows, status):
    expected = [(s["id"], t["id"], len(t["leaves"])) for s in plan["sessions"] for t in s["tasks"]]
    actual = [(r["session_id"], r["task_id"], r["expected_checks"]) for r in rows]
    if actual != expected:
        raise ValueError("Task order, IDs or denominator differs from frozen plan")
    planned_tasks = [t for s in plan["sessions"] for t in s["tasks"]]
    if sum(len(t["leaves"]) for t in planned_tasks) != plan["expected_checks"] or len(expected) != plan["expected_tasks"]:
        raise ValueError("Plan denominator is internally inconsistent")
    for task, row in zip(planned_tasks, rows):
        if [{"path": leaf["path"], "definition": leaf["definition"]} for leaf in row["leaves"]] != task["leaves"]:
            raise ValueError("Missing, reordered or replaced leaf checks")
    scored = [r for r in rows if r["status"] == "scored"]
    complete = status == "completed" and len(scored) == len(expected) and all(
        len(r["leaves"]) == r["expected_checks"] and all(x["status"] == "scored" for x in r["leaves"])
        and all(type(x["result"]) is bool for x in r["leaves"]) and type(r.get("passed")) is bool
        for r in scored)
    if plan["protocol"].get("id") == PROTOCOL_ID:
        complete = complete and all(type(r.get("agent_timed_out")) is bool
                                    and r.get("task_pass") == r.get("passed")
                                    and r.get("world_quiescent") is True
                                    and r.get("continuation_safe") is True for r in scored)
    result = {"valid_complete_run": complete, "planned_tasks": len(expected),
              "planned_checks": plan["expected_checks"], "scored_tasks": len(scored),
              "scored_checks": sum(x["status"] == "scored" for r in rows for x in r["leaves"]),
              "agent_timed_out_tasks": sum(r.get("agent_timed_out") is True for r in rows),
              "timed_out_but_passed_tasks": sum(r.get("agent_timed_out") is True and r.get("passed") is True for r in scored),
              "missing_or_invalid_tasks": len(expected) - len(scored), "metrics": None}
    if complete:
        counts = [sum(x["result"] is True for x in r["leaves"]) for r in rows]
        n, k = len(rows), plan["expected_checks"]
        result["metrics"] = {"passed_tasks": sum(r["passed"] for r in rows),
                             "task_pass": sum(r["passed"] for r in rows) / n,
                             "avg_check": sum(p / r["expected_checks"] for p, r in zip(counts, rows)) / n,
                             "global_check": sum(counts) / k,
                             "total_failed": sum(p == 0 for p in counts) / n}
    return result


def source_files():
    for component in ("flockcue", "simulation", "runtime"):
        for base, directories, files in os.walk(ROOT / component):
            directories[:] = [name for name in directories if not name.startswith(".") and name not in
                              {"__pycache__", "tests", "outputs", "server_logs", "logs"}]
            for name in sorted(files):
                path = Path(base) / name
                if (path.suffix == ".py" or name == "requirements.txt" or path == ROOT / "flockcue/profile.json"
                        or path.parent == ROOT / "simulation/server/config/example" and path.suffix == ".json"):
                    yield path


def current_source_hashes():
    return {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sorted(source_files())}


def verify_registration():
    plan = load_plan(PLAN)
    registration_bytes = (STAGE / "registration.json").read_bytes()
    if not (STAGE / ".git").is_dir():
        raise ValueError("Missing first-pass registration history")
    committed = subprocess.check_output(["git", "-C", str(STAGE), "show", "HEAD:registration.json"])
    if committed != registration_bytes:
        raise ValueError("Staged registration differs from its original Git commit")
    registration = json.loads(registration_bytes)
    if (registration["sources"] != current_source_hashes() or registration["plan_sha256"] != PLAN_SHA
            or registration["settings"] != digest(SETTINGS.read_bytes())
            or registration["workers"] != 8 or registration["first_attempt_only"] is not True):
        raise ValueError("A staged source or registration differs; prepare a new stage")
    return plan


def installed_packages(component):
    raw = subprocess.check_output(["uv", "--no-cache", "pip", "freeze", "--python", str(interpreter(component))])
    if digest(raw) != EXPECTED_FREEZE[component]:
        raise ValueError(f"The {component} environment differs from the registered deployment")
    return raw


def code_snapshot(destination):
    hashes = {}
    with zipfile.ZipFile(destination / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_files()):
            name = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            hashes[name] = digest(data)
            archive.writestr(name, data)
    if hashes != read_json(STAGE / "registration.json")["sources"]:
        raise ValueError("Source changed during preparation")
    save_json(destination / "source_hashes.json", hashes)
    for component in ENVIRONMENTS:
        (destination / f"{component}-freeze.txt").write_bytes(installed_packages(component))
    return hashes
