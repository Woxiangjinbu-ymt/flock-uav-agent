"""Local full-episode worker/scorer; no provider, settings or external network.

Worker uses a frozen real Agent/SDK/Harness/ASGI simulator with scripted provider
responses. Scoring is a separate process and never imports the Agent. This is
contract validation, not autonomous-model or benchmark evidence.
"""
from __future__ import annotations
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / folder) for folder in ("scripts", "compat/scripts", "compat/agent4drone", ".")]

import argparse
import asyncio
import base64
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import json
import os
import pickle
import socket
import time
import uuid

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "agent4drone").is_dir() and (parent / "server").is_dir())


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode())


def write(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def verify(root, expected):
    for relative, expected_hash in expected.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path.read_bytes()) != expected_hash:
            raise ValueError("Frozen input or source hash mismatch")


def block_network():
    attempts = []
    def denied(*args, **kwargs):
        attempts.append("socket_connection_blocked")
        raise RuntimeError("External network forbidden in episode validation")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    return attempts


def inputs(run, trial_id):
    manifest = json.loads((run / "manifest.json").read_text())
    if ROOT.resolve() != (run / "source").resolve():
        raise ValueError("Worker/scorer must execute the frozen source copy")
    verify(ROOT, manifest["sources"])
    study = Path(manifest["study"]).resolve()
    verify(study, manifest["inputs"])
    trial = next(item for item in manifest["trials"] if item["trial_id"] == trial_id)
    if trial_id != Path(trial_id).name or trial["condition"] not in {"success", "failure", "timeout", "cancel"}:
        raise ValueError("Invalid trial identity or condition")
    fixture = json.loads((study / "fixtures" / (manifest["fixture_id"] + ".json")).read_text())
    blob = (study / "fixtures" / (manifest["fixture_id"] + ".pkl")).read_bytes()
    timeline = (study / "fixtures" / fixture["timeline_file"]).read_bytes()
    if sha(blob) != fixture["snapshot_sha256"] or sha(timeline) != fixture["timeline_sha256"]:
        raise ValueError("Fixture hashes disagree")
    if sha(fixture["command"].encode()) != fixture["command_sha256"]:
        raise ValueError("Original command changed")
    return manifest, trial, fixture, blob, timeline


def setup_paths(destination):
    os.chdir(destination)
    sys.path[:0] = [str(ROOT / name) for name in ("scripts", "compat/scripts", "agent4drone", "compat/agent4drone", "server")]
    import logging_config
    logging_config.LOG_DIR = destination / "agent-logs"


def current_task(world, fixture):
    session = world.server.session_controller.sessions[fixture["scene_id"]]
    return session.tasks[fixture["task_id"]]


def scan_policy(policy, archive_path, canary):
    bad_canary, bad_definitions, request_hashes = [], [], []
    def private_tree(value):
        if isinstance(value, dict):
            return any((key == "execution_check_apis" and child is not None) or private_tree(child)
                       for key, child in value.items())
        if isinstance(value, list):
            return any(private_tree(child) for child in value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return False
            return private_tree(parsed) if not isinstance(parsed, str) else False
        return False
    for path in policy.rglob("*"):
        if path.is_file() and canary.encode() in path.read_bytes():
            bad_canary.append(str(path.relative_to(policy)))
    for row in map(json.loads, archive_path.read_text().splitlines()):
        if row["kind"] not in {"http_request", "http_response_chunk"}:
            continue
        body = row["data"].get("body")
        if body is None:
            continue
        raw = base64.b64decode(body["base64"], validate=True)
        if canary.encode() in raw:
            bad_canary.append("decoded_wire:" + str(row["sequence"]))
        if row["kind"] == "http_request":
            request_hashes.append(sha(raw))
            if private_tree(json.loads(raw)):
                bad_definitions.append(row["sequence"])
    return {"policy_canary_absent": not bad_canary,
            "policy_checker_definition_absent": not bad_definitions,
            "canary_violations": bad_canary, "checker_definition_violations": bad_definitions,
            "request_body_sha256": request_hashes}


def worker(run, trial_id):
    manifest, trial, fixture, blob, timeline = inputs(run, trial_id)
    output = run / "trials" / trial_id
    output.mkdir(parents=True, exist_ok=True)
    if (output / "worker-result.json").exists():
        raise FileExistsError("Worker result already exists")
    policy, private = output / "policy", output / "private"
    policy.mkdir(); private.mkdir()
    with (private / "worker-console.log").open("x") as console, redirect_stdout(console), redirect_stderr(console):
        setup_paths(policy)
        network_attempts = block_network()
        from harness_full100_world import FullWorld, TaskDeadlineReached
        from harness_full100_online import agent_class
        from harness_v2_online import BUDGET, client_class
        from harness_skills import MAX_ROUNDS
        from model_io_archive import archive_response, verify_archive
        from langchain_openai import ChatOpenAI
        from langchain_core.callbacks import BaseCallbackHandler
        import openai
        from openai import _base_client
        import uav_agent
        import toolchain_recorder
        httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
        toolchain_recorder.TOOLCHAIN_LOG_DIR = policy / "toolchain"
        def no_settings(*args, **kwargs):
            raise RuntimeError("Provider settings forbidden in local episode validation")
        uav_agent.load_llm_settings = no_settings
        worldlogs = policy / "worldlogs"
        worldlogs.mkdir()
        class EpisodeWorld(FullWorld):
            def _append(self, name, value):
                # Preparation hashes may require public reads, but these are
                # evaluator preparation, not observations obtained by the Agent.
                folder = worldlogs if self.phase == "task" and not value.get("admin", False) else private
                target_name = name if folder == worldlogs else "preparation-" + name
                with (folder / target_name).open("a", encoding="utf-8") as target:
                    target.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        world = EpisodeWorld(output=private, deadline_seconds=BUDGET["deadline_seconds"])
        world.restore(blob, fixture["snapshot_sha256"])
        world.timeline = pickle.loads(timeline)
        original_state = world.state_digest()
        public_start = digest(world.public_seed())
        if original_state != fixture["state_sha256"] or public_start != fixture["public_startup_sha256"]:
            raise ValueError("Original fixture failed structural/public initial check")
        world.restore(blob, fixture["snapshot_sha256"])
        tree = current_task(world, fixture)["execution_check_apis"]
        original_tree = copy.deepcopy(tree)
        tree["_episode_private_canary"] = manifest["private_canary"]
        derived_state = world.state_digest()
        stripped_tree = {key: value for key, value in tree.items() if key != "_episode_private_canary"}
        assert digest(stripped_tree) == digest(original_tree)
        write(private / "initial-state.json", world.inspect_blob(world.snapshot()))
        try:
            world.request("GET", "/sessions/current/tasks/" + fixture["task_id"] + "/check")
        except PermissionError:
            private_denied = True
        else:
            private_denied = False
        virtual = {"anchor": time.monotonic(), "offset": 0.0}
        clock = lambda: virtual["anchor"] + virtual["offset"]
        world._monotonic = clock
        trigger = {"triggered": False, "trigger_request_id": None}
        class Cancellation(BaseCallbackHandler):
            cancelled = False
            def _check_cancellation(self):
                if self.cancelled:
                    raise RuntimeError("controlled_episode_cancellation")
        cancel = Cancellation()
        requests, transport_responses, http_stages = [], [], []
        def tool_message(name, ident, arguments):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": ident,
                    "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        def respond(request):
            payload = json.loads(request.content)
            index = len(requests)
            requests.append(request)
            if index == 0:
                message = tool_message("list_drones", "episode-list", {})
            elif index == 1 and trial["condition"] == "failure":
                receipt = json.loads(next(row["content"] for row in reversed(payload["messages"]) if row["role"] == "tool"))
                actor = next(drone["id"] for drone in receipt if drone.get("name") == "Drone 3")
                message = tool_message("take_off", "episode-takeoff", {"input_json": json.dumps({"drone_id": actor, "altitude": 14.0})})
            elif index == 1:
                message = tool_message("run_harness", "episode-harness", {"input_json": json.dumps({"action": "start", "skill": "simple_chain"})})
            elif index == 2 and trial["condition"] == "failure":
                body = b'{"error":{"message":"controlled local provider failure","type":"server_error"}}'
                transport_responses.append({"status_code": 500, "sha256": sha(body), "length": len(body)})
                http_stages.append("provider_failure")
                return httpx.Response(500, content=body, headers={"content-type": "application/json"}, request=request)
            elif index == 2 and trial["condition"] == "success":
                message = {"role": "assistant", "content": "Public sequence verified [TASK DONE]"}
            else:
                raise AssertionError("Unexpected model call beyond fixed local script")
            response = {"id": "episode-response-" + str(index), "object": "chat.completion", "created": 1,
                        "model": "local-scripted-sdk", "choices": [{"index": 0, "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                        "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}
            body = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
            transport_responses.append({"status_code": 200, "sha256": sha(body), "length": len(body)})
            http_stages.append("script_response_" + str(index))
            return httpx.Response(200, content=body, headers={"content-type": "application/json"}, request=request)
        async def arespond(request):
            return respond(request)
        sync = openai.DefaultHttpxClient(transport=httpx.MockTransport(respond), trust_env=False)
        async_ = openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(arespond), trust_env=False)
        uav_agent.ChatOpenAI = lambda **kwargs: ChatOpenAI(http_client=sync, http_async_client=async_,
                                                        max_retries=0, max_tokens=256, **kwargs)
        os.environ.update(UAV_HARNESS_SCHEDULER="applicability", UAV_HARNESS_REVISION="observed_target_v7",
                          UAV_HARNESS_STATIC_OBSTACLES="1", UAV_TASK_PROGRESS_MODE="evidence_harness")
        BaseAgent = agent_class(world, policy)
        OriginalClient = client_class(world)
        class VirtualClient(OriginalClient):
            def _request(self, *args, **kwargs):
                world.check_admission()
                return super()._request(*args, **kwargs)
        uav_agent.UAVAPIClient = VirtualClient
        entry = {}
        class EpisodeAgent(BaseAgent):
            def _initialize_task_progress(self, command, command_id):
                super()._initialize_task_progress(command, command_id)
                self._harness.clock = clock
                self._harness.log.clock = clock
                if (self.max_iterations != BUDGET["max_iterations"] or self._recursion_limit_for_steps() != BUDGET["recursion_limit"]
                        or self._harness.deadline != world.started + 500 or MAX_ROUNDS != BUDGET["search_rounds"]):
                    raise AssertionError("Agent/runtime budget differs from frozen protocol")
                entry.update(command_id=command_id, board=self.blackboard.full(),
                             scene_epoch=self._harness.scene_epoch, harness_frames=len(self._harness.frames),
                             ledger_event_count=len(self._task_progress._events),
                             ledger_model_request_count=len(self._task_progress.model_requests),
                             harness_event_kinds=[event["kind"] for event in self._harness.log.snapshot()])
                invoke = self._harness.invoke
                def controlled(call):
                    for frame in self._harness.frames.values():
                        if (frame.api_budget != BUDGET["api_calls_per_plan"] or frame.repair_budget != BUDGET["repair_budget"]
                                or frame.deadline != world.started + 500):
                            raise AssertionError("Skill frame budget differs from frozen protocol")
                    result = invoke(call)
                    if call.tool == "take_off" and trial["condition"] in {"timeout", "cancel"} and not trigger["triggered"]:
                        row = world.requests[-1]
                        if not row["committed"] or (row.get("response") or {}).get("status") != "success":
                            raise AssertionError("Boundary trigger requires successful committed takeoff")
                        trigger.update(triggered=True, trigger_request_id=row["request_id"])
                        if trial["condition"] == "timeout":
                            virtual["offset"] = 500.0
                            world.freeze_score(500.0, "controlled_deadline")
                        else:
                            cancel.cancelled = True
                            world.freeze_score(reason="controlled_cancel")
                    return result
                self._harness.invoke = controlled
        wall_start = time.monotonic()
        world.start(timer=False)
        agent = None
        caught = None
        result = None
        try:
            agent = EpisodeAgent(base_url="http://testserver", llm_provider="openai-compatible",
                llm_model="local-scripted-sdk", llm_api_key="synthetic-local-key", llm_base_url="https://offline.invalid/v1",
                temperature=0.1, verbose=False, share_blackboard_by_session=False, task_progress_mode="evidence_harness",
                toolchain_json_recording=True, model_io_archive=True, model_io_archive_dir=policy / "modelio")
            result = agent.execute(fixture["command"], callbacks=[cancel], step_callback=lambda steps: None)
        except BaseException as exc:
            caught = type(exc).__name__
        finally:
            world.freeze_score(reason="episode_execution_returned")
            sync.close()
            asyncio.run(async_.aclose())
        wall_elapsed = time.monotonic() - wall_start
        if agent is None or result is None:
            raise RuntimeError("Worker failed before auditable Agent result: " + str(caught))
        archive = agent._model_io_archive
        archive_path = Path(archive.info()["path"])
        archive_before, info_before = archive_path.read_bytes(), archive.info()
        archive_response(httpx.Response(200, content=b'{"late":true}', request=requests[0]))
        late_archive_unchanged = archive_before == archive_path.read_bytes() and info_before == archive.info()
        frozen = world.score_blob
        before_late = (sha(frozen), world.state_digest(), len(world.requests), len(world.physical_events))
        try:
            world.request("POST", "/drones/not-dispatched/command/hover")
        except TaskDeadlineReached:
            late_world_rejected = before_late == (sha(world.score_blob), world.state_digest(), len(world.requests), len(world.physical_events))
        else:
            late_world_rejected = False
        write(policy / "harness.json", agent._harness.snapshot())
        write(policy / "agent-result.json", result)
        write(policy / "transport-oracle.json", {"requests": [{"length": len(req.content), "sha256": sha(req.content)} for req in requests],
                                                  "responses": transport_responses, "stages": http_stages, "usage_is_synthetic": True})
        scanned = scan_policy(policy, archive_path, manifest["private_canary"])
        frames = agent._harness.snapshot()["frames"]
        isolation = {"late_world_rejected": late_world_rejected, "late_archive_unchanged": late_archive_unchanged,
                     "private_endpoint_denied": private_denied, **scanned}
        interruption = agent._harness._interrupted
        provider_failed = bool(transport_responses and transport_responses[-1]["status_code"] == 500)
        actual_terminal = ("cancelled" if interruption == "cancelled" else "timeout" if interruption == "deadline"
                           else "provider_failure" if provider_failed and not result["success"]
                           else "completed" if result["success"] else "other_failure")
        terminal = {"condition": trial["condition"], **trigger, "virtual_elapsed": world.elapsed(),
                    "wall_elapsed": wall_elapsed, "frame_status": frames[0]["status"] if frames else None,
                    "frame_reason": frames[0]["reason"] if frames else None,
                    "cancel_flag": bool(cancel.cancelled), "harness_interruption": interruption,
                    "actual_terminal": actual_terminal, "provider_http_failure_observed": provider_failed,
                    "graph_error_type": getattr(agent, "execution_diagnostics", {}).get("graph_error_type")}
        boundary = json.loads((private / "score-boundary.json").read_text())
        from command_results import normalize_command_result
        action_signature = []
        for row in world.requests:
            if row["phase"] != "task" or not row["action"] or not row["committed"]:
                continue
            receipt = normalize_command_result(copy.deepcopy(row.get("response"))) if isinstance(row.get("response"), dict) else row.get("response")
            if isinstance(receipt, dict):
                receipt = {key: value for key, value in receipt.items() if key not in {"command_id", "timestamp"}}
            action_signature.append({"tool": row.get("origin", {}).get("tool"), "endpoint": row["endpoint"],
                                     "method": row["method"], "params": row["params"], "body": row["body"],
                                     "receipt": receipt, "error_type": row.get("error")})
        report = {"schema_version": 1, **trial, "pid": os.getpid(), "instance_nonce": uuid.uuid4().hex,
            "status": "completed", "budget": BUDGET, "clock_scope": "fixed_virtual_anchor_plus_controlled_offset_not_walltime_performance",
            "initial": {"original_state_sha256": original_state, "public_startup_sha256": public_start,
                        "derived_state_sha256": derived_state, "original_checker_tree_sha256": digest(original_tree),
                        "derived_checker_tree_without_canary_sha256": digest(stripped_tree)},
            "entry": entry, "agent": {"success": result["success"], "output": result["output"], "error_type": caught,
                        "harness_closed": agent._harness._closed, "archive_info": archive.info(), "archive_verify": verify_archive(archive_path)},
            "terminal": terminal, "isolation": isolation, "network_attempts": network_attempts,
            "physical_cost": world.cost(), "action_signature": action_signature,
            "state_changed": boundary["state_sha256"] != derived_state,
            "snapshot": {"path": "private/score-world.pkl", "sha256": sha(frozen),
                         "state_sha256": boundary["state_sha256"], "score_seconds": world.score_seconds}}
        verify(ROOT, manifest["sources"]); verify(Path(manifest["study"]), manifest["inputs"])
        console.flush()
        report["files"] = {str(path.relative_to(output)): sha(path.read_bytes()) for folder in (policy, private)
                           for path in folder.rglob("*") if path.is_file() and path.name != "worker-console.log"}
        write(output / "worker-result.json", report)
        return report


def score(run, trial_id):
    manifest, trial, fixture, _, _ = inputs(run, trial_id)
    output = run / "trials" / trial_id
    worker_path = output / "worker-result.json"
    result = json.loads(worker_path.read_text())
    if result["status"] != "completed" or result["trial_id"] != trial_id:
        raise ValueError("No completed worker record to score")
    verify(output, result["files"])
    if result["snapshot"]["path"] != "private/score-world.pkl":
        raise ValueError("Unexpected score snapshot path")
    snapshot = output / result["snapshot"]["path"]
    blob = snapshot.read_bytes()
    if sha(blob) != result["snapshot"]["sha256"]:
        raise ValueError("Untrusted score snapshot")
    evaluation = output / "evaluation"
    evaluation.mkdir()
    with (evaluation / "scorer-console.log").open("x") as console, redirect_stdout(console), redirect_stderr(console):
        setup_paths(evaluation)
        network_attempts = block_network()
        from harness_full100_world import FullWorld
        class ScoringWorld(FullWorld):
            def _append(self, name, value):
                with (evaluation / "private-api.jsonl").open("a", encoding="utf-8") as target:
                    target.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        world = ScoringWorld()
        world.restore(blob, result["snapshot"]["sha256"])
        world.phase = "scoring"
        private_task = world.request("GET", "/sessions/current/tasks/" + fixture["task_id"], admin=True)
        write(evaluation / "private-task.json", private_task)
        tree = private_task["execution_check_apis"]
        canary_present = tree.get("_episode_private_canary") == manifest["private_canary"]
        original_tree = {key: value for key, value in tree.items() if key != "_episode_private_canary"}
        original_matches = digest(original_tree) == result["initial"]["original_checker_tree_sha256"]
        checker = world.evaluate(fixture["task_id"], blob)
        if "uav_agent" in sys.modules:
            raise AssertionError("Scorer imported Agent")
        report = {"schema_version": 1, "trial_id": trial_id, "pid": os.getpid(), "worker_pid": result["pid"],
                  "worker_result_sha256": sha(worker_path.read_bytes()), "snapshot_sha256": sha(blob),
                  "checker": checker, "private_canary_present": canary_present,
                  "checker_tree_matches_original": original_matches, "agent_imported": False,
                  "network_attempts": network_attempts}
        verify(ROOT, manifest["sources"]); verify(Path(manifest["study"]), manifest["inputs"])
        write(evaluation / "result.json", report)
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--mode", choices=("worker", "score"), required=True)
    args = parser.parse_args()
    if not args.run.is_absolute():
        raise ValueError("--run must be absolute")
    (worker if args.mode == "worker" else score)(args.run.resolve(), args.trial)


if __name__ == "__main__":
    main()
