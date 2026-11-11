"""Wall-clock episode adapter; transport is injected, scoring runs separately.

This module has no provider configuration lookup and no campaign launcher.
The companion local validation entry point supplies MockTransport only.
"""
from __future__ import annotations
import asyncio
import copy
from contextlib import redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import pickle
import re
import threading
import time
import uuid

from harness_episode_local import sha, digest, write, setup_paths, current_task
from validate_harness_episode import check_hashes, safe_path, policy_scan

ROOT = Path(__file__).resolve().parents[1]


def task_request_allowed(method, endpoint, admin=False):
    if admin:
        return False
    if method.upper() == "GET":
        return endpoint in {"/drones", "/sessions/current", "/environments/current", "/targets/waypoints"} or any(
            re.fullmatch(pattern, endpoint) for pattern in (
                r"/drones/[^/]+(?:/nearby)?", r"/sessions/[^/]+/(?:data|task-progress)",
                r"/targets/[^/]+", r"/obstacles/[^/]+"))
    commands = "take_off|land|move_to|move_along_path|change_altitude|hover|rotate|move_towards|return_home|set_home|calibrate|charge|take_photo|send_message|broadcast"
    return method.upper() == "POST" and (endpoint in {"/obstacles/collision/check", "/obstacles/collision/path"}
        or re.fullmatch(r"/drones/[^/]+/command/(?:" + commands + ")", endpoint) is not None)


class EpisodeLifecycle:
    """Cancellation requests stop admission immediately; freeze is linearized."""
    def __init__(self, world):
        self.world = world
        self.cancel_event = threading.Event()
        self.requested_at = None
        self.effective_at = None
        self._lock = threading.Lock()

    def request_cancel(self):
        with self._lock:
            if self.requested_at is not None:
                return
            self.requested_at = time.monotonic()
            self.cancel_event.set()
        with self.world.lock:
            self.world.freeze_score(reason="cancellation_effective")
            self.effective_at = time.monotonic()

    def check(self):
        if self.cancel_event.is_set():
            raise RuntimeError("episode_cancelled")
        self.world.check_admission()

    def snapshot(self):
        return {"requested_at": self.requested_at, "effective_at": self.effective_at,
                "cancelled": self.cancel_event.is_set(),
                "semantics": "freeze after acquiring world lock; in-flight actions are not retroactively cancelled"}


def read_inputs(run, trial_id):
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if sha(manifest_path.read_bytes()) != (run / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    if ROOT != (run / "source").resolve():
        raise ValueError("Use the frozen runner source")
    check_hashes(ROOT, manifest["sources"])
    study = Path(manifest["study"])
    check_hashes(study, manifest["inputs"])
    trial = next(t for t in manifest["trials"] if t["trial_id"] == trial_id)
    if Path(trial_id).name != trial_id:
        raise ValueError("Invalid trial identifier")
    fixture = json.loads((study / "fixtures" / (trial["task_id"] + ".json")).read_text())
    blob = (study / "fixtures" / (trial["task_id"] + ".pkl")).read_bytes()
    timeline = (study / "fixtures" / fixture["timeline_file"]).read_bytes()
    if sha(blob) != fixture["snapshot_sha256"] or sha(timeline) != fixture["timeline_sha256"]:
        raise ValueError("Fixture hashes disagree")
    if sha(fixture["command"].encode()) != fixture["command_sha256"]:
        raise ValueError("Task command changed")
    return manifest, trial, fixture, blob, timeline


def run_worker(run, trial_id, transport_factory, *, network_attempts=None):
    manifest, trial, fixture, blob, timeline = read_inputs(run, trial_id)
    folder = run / "trials" / trial_id
    folder.mkdir(parents=True, exist_ok=False)
    policy, private = folder / "policy", folder / "private"
    policy.mkdir(); private.mkdir()
    with (private / "worker-console.log").open("x") as console, redirect_stdout(console), redirect_stderr(console):
        setup_paths(policy)
        from harness_full100_world import FullWorld, TaskDeadlineReached
        from harness_full100_online import agent_class
        from harness_v2_online import client_class, BUDGET
        from validate_harness_observed_target import validate_static_timeline, obstacle_geometry_audit
        from harness_request_limits import RequestLimits, RequestLimitController, make_guarded_transport
        from model_io_archive import verify_archive
        from langchain_openai import ChatOpenAI
        from langchain_core.callbacks import BaseCallbackHandler
        import openai
        from openai import _base_client
        import uav_agent
        import toolchain_recorder
        httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
        toolchain_recorder.TOOLCHAIN_LOG_DIR = policy / "toolchain"
        def no_settings(*args, **kwargs):
            raise RuntimeError("Provider configuration forbidden in local validation")
        uav_agent.load_llm_settings = no_settings
        (policy / "worldlogs").mkdir()

        class LoggedWorld(FullWorld):
            def request(self, method, endpoint, *, admin=False, **kwargs):
                if self.phase == "task" and not task_request_allowed(method, endpoint, admin):
                    raise PermissionError("Task outside public static-writer contract")
                return super().request(method, endpoint, admin=admin, **kwargs)

            def _append(self, name, value):
                destination = policy / "worldlogs" if self.phase == "task" and not value.get("admin", False) else private
                name = name if destination != private else "preparation-" + name
                with (destination / name).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")

        runtime = trial["runtime"]
        world = LoggedWorld(output=private, deadline_seconds=runtime["task_seconds"])
        world.restore(blob, fixture["snapshot_sha256"])
        world.timeline = pickle.loads(timeline)
        timeline_contract = validate_static_timeline(world.timeline)
        transport = getattr(world.client, "_transport", None)
        if (type(transport).__module__ != "starlette.testclient" or type(transport).__name__ != "_TestClientTransport"
                or getattr(transport, "app", None) is not world.server.app or world.client.portal is not None):
            raise ValueError("Requires exclusive local ASGI transport without lifespan")
        geometry_before = obstacle_geometry_audit(world, blob)
        initial = {"original_state_sha256": world.state_digest(), "public_startup_sha256": digest(world.public_seed())}
        if initial != {"original_state_sha256": fixture["state_sha256"], "public_startup_sha256": fixture["public_startup_sha256"]}:
            raise ValueError("Initial state mismatch")
        world.restore(blob, fixture["snapshot_sha256"])
        tree = current_task(world, fixture)["execution_check_apis"]
        initial["original_checker_tree_sha256"] = digest(tree)
        tree["_episode_private_canary"] = manifest["private_canary"]
        initial["derived_state_sha256"] = world.state_digest()
        life = EpisodeLifecycle(world)
        # No virtual clock, precommitted plan or public-seed board injection.
        world.start(timer=True)
        controller = RequestLimitController(RequestLimits(**runtime["request_limits"]),
            task_deadline=world.started + world.deadline_seconds, cancel_check=life.cancel_event.is_set)
        underlying = transport_factory(httpx, {"request_cancel": life.request_cancel,
                                               "deadline": controller.task_deadline})
        guarded = make_guarded_transport(httpx, underlying, controller)
        sync = openai.DefaultHttpxClient(transport=guarded, trust_env=False)

        class RejectAsync(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                raise RuntimeError("async_unsupported_in_this_runner")
        asynchronous = openai.DefaultAsyncHttpxClient(transport=RejectAsync(), trust_env=False)
        uav_agent.ChatOpenAI = lambda **kwargs: ChatOpenAI(http_client=sync, http_async_client=asynchronous,
            max_retries=0, max_tokens=runtime["request_limits"]["max_output_tokens"], streaming=False, **kwargs)
        os.environ.update(UAV_HARNESS_SCHEDULER=trial["scheduler"], UAV_HARNESS_REVISION="observed_target_v7",
                          UAV_HARNESS_STATIC_OBSTACLES="1", UAV_TASK_PROGRESS_MODE="evidence_harness")
        BaseAgent = agent_class(world, policy)
        BaseClient = client_class(world)
        class AdmittedClient(BaseClient):
            def _request(self, *args, **kwargs):
                life.check()
                return super()._request(*args, **kwargs)
        uav_agent.UAVAPIClient = AdmittedClient
        entry = {}
        class NaturalAgent(BaseAgent):
            def _initialize_task_progress(self, command, command_id):
                super()._initialize_task_progress(command, command_id)
                entry.update(command_id=command_id, board=self.blackboard.full(),
                    frames=len(self._harness.frames), ledger_events=len(self._task_progress._events),
                    ledger_model_requests=len(self._task_progress.model_requests))
                if self._harness.frames:
                    raise AssertionError("Natural entry already has a committed plan")
        class Cancellation(BaseCallbackHandler):
            def _check_cancellation(self):
                if life.cancel_event.is_set():
                    raise RuntimeError("episode_cancelled")
        agent, result, error = None, None, None
        try:
            agent = NaturalAgent(base_url="http://testserver", llm_provider="openai-compatible",
                llm_model=manifest["local_model"], llm_api_key="synthetic-local-key",
                llm_base_url="https://offline.invalid/v1", temperature=0.1, verbose=False,
                share_blackboard_by_session=False, task_progress_mode="evidence_harness",
                toolchain_json_recording=True, model_io_archive=True, model_io_archive_dir=policy / "modelio")
            result = agent.execute(fixture["command"], callbacks=[Cancellation()], step_callback=lambda steps: None)
        except BaseException as exc:
            error = type(exc).__name__
        finally:
            world.freeze_score(reason="execution_returned")
        if agent is None or agent._model_io_archive is None:
            raise RuntimeError("Agent failed before auditable initialization: " + str(error))
        archive = agent._model_io_archive
        archive_path = Path(archive.info()["path"])
        archive_at_close = sha(archive_path.read_bytes())
        world_at_close = (sha(world.score_blob), world.state_digest(), len(world.requests), len(world.physical_events))
        drained = controller.drain(runtime["drain_seconds"])
        limits = controller.snapshot()
        write(policy / "harness.json", agent._harness.snapshot())
        write(policy / "agent-result.json", result or {"success": False, "error_type": error})
        write(private / "lifecycle.json", life.snapshot())
        if not drained or limits["needs_process_termination"]:
            write(private / "request-limits.json", limits)
            # No completed manifest. Parent must kill and join; a score snapshot
            # existing on disk is insufficient to authorize scoring this worker.
            write(folder / "termination-required.json", {"trial_id": trial_id, "pid": os.getpid(),
                "reason": "transport_not_drained", "archive_closed": archive.info()["closed"],
                "score_available": world.score_blob is not None})
            console.flush()
            threading.Event().wait()
            raise AssertionError("Unreachable after parent termination")
        controller.close()
        sync.close()
        asyncio.run(asynchronous.aclose())
        limits = controller.snapshot()
        write(private / "request-limits.json", limits)
        geometry_after = obstacle_geometry_audit(world, world.score_blob)
        if geometry_before != geometry_after:
            raise ValueError("Obstacle geometry changed during static episode")
        write(private / "static-obstacles.json", {"before": geometry_before, "after": geometry_after,
              "timeline": timeline_contract, "task_writer_guard": True, "exclusive_local_asgi": True})
        late_rejected = False
        try:
            world.request("POST", "/drones/not-dispatched/command/hover")
        except TaskDeadlineReached:
            late_rejected = True
        unchanged = world_at_close == (sha(world.score_blob), world.state_digest(), len(world.requests), len(world.physical_events))
        public_rows = [r for r in world.requests if r["phase"] == "task" and not r["admin"]]
        actions = [{"request_id": r["request_id"], "tool": r.get("origin", {}).get("tool"),
                    "endpoint": r["endpoint"], "params": r["params"], "body": r["body"],
                    "committed": r["committed"], "status": (r.get("response") or {}).get("status")}
                   for r in public_rows if r["action"]]
        scan = policy_scan([p for p in policy.rglob("*") if p.is_file()], manifest["private_canary"])
        boundary = json.loads((private / "score-boundary.json").read_text())
        report = {"schema_version": 1, "status": "completed", "trial_id": trial_id,
            "task_id": trial["task_id"], "pid": os.getpid(), "instance_nonce": uuid.uuid4().hex,
            "initial": initial, "entry": entry, "runtime": runtime, "budget_reference": BUDGET,
            "elapsed": world.elapsed(), "clock": "time.monotonic", "timer_enabled": True,
            "lifecycle": life.snapshot(), "limits": limits, "drained": drained,
            "agent": {"success": bool(result and result.get("success")), "error_type": error,
                      "archive_info": archive.info(), "archive_verify": verify_archive(archive_path),
                      "harness_closed": agent._harness._closed},
            "isolation": {"archive_unchanged_after_drain": archive_at_close == sha(archive_path.read_bytes()),
                          "world_unchanged_after_drain": unchanged, "late_action_rejected": late_rejected, **scan},
            "network_attempts": list(network_attempts) if network_attempts is not None else None,
            "physical_cost": world.cost(), "actions": actions,
            "snapshot": {"path": "private/score-world.pkl", "sha256": sha(world.score_blob),
                         "state_sha256": boundary["state_sha256"], "score_seconds": world.score_seconds}}
        check_hashes(ROOT, manifest["sources"])
        report["files"] = {str(p.relative_to(folder)): sha(p.read_bytes()) for d in (private, policy)
                           for p in d.rglob("*") if p.is_file() and p.name != "worker-console.log"}
        console.flush()
        write(folder / "worker-result.json", report)
        return report


def run_scorer(run, trial_id, *, network_attempts=None):
    manifest, trial, fixture, _, _ = read_inputs(run, trial_id)
    folder = run / "trials" / trial_id
    result_path = folder / "worker-result.json"
    result = json.loads(result_path.read_text())
    if (result["status"] != "completed" or result["trial_id"] != trial_id or result["task_id"] != trial["task_id"]
            or result["snapshot"]["path"] != "private/score-world.pkl"
            or not result["drained"] or result["limits"]["needs_process_termination"]):
        raise ValueError("Only a drained worker may be scored")
    check_hashes(folder, result["files"])
    exit_seal = json.loads((run / "worker-exit-seals" / (trial_id + ".json")).read_text())
    check_hashes(folder, exit_seal)
    snapshot = safe_path(folder, result["snapshot"]["path"])
    blob = snapshot.read_bytes()
    if sha(blob) != result["snapshot"]["sha256"]:
        raise ValueError("Snapshot changed")
    evaluation = folder / "evaluation"
    evaluation.mkdir()
    with (evaluation / "scorer-console.log").open("x") as console, redirect_stdout(console), redirect_stderr(console):
        setup_paths(evaluation)
        from harness_full100_world import FullWorld
        class ScoringWorld(FullWorld):
            def _append(self, name, value):
                with (evaluation / "private-api.jsonl").open("a") as stream:
                    stream.write(json.dumps(value, default=str) + "\n")
        world = ScoringWorld()
        world.restore(blob, result["snapshot"]["sha256"])
        world.phase = "scoring"
        task = world.request("GET", "/sessions/current/tasks/" + trial["task_id"], admin=True)
        write(evaluation / "private-task.json", task)
        tree = task["execution_check_apis"]
        original = {k: v for k, v in tree.items() if k != "_episode_private_canary"}
        checker = world.evaluate(trial["task_id"], blob)
        import sys
        report = {"trial_id": trial_id, "pid": os.getpid(), "worker_pid": result["pid"], "checker": checker,
            "worker_result_sha256": sha(result_path.read_bytes()), "snapshot_sha256": sha(blob),
            "private_canary_present": tree.get("_episode_private_canary") == manifest["private_canary"],
            "checker_tree_matches_original": digest(original) == result["initial"]["original_checker_tree_sha256"],
            "agent_imported": "uav_agent" in sys.modules,
            "network_attempts": list(network_attempts) if network_attempts is not None else None}
        write(evaluation / "result.json", report)
        return report
