"""One process, Agent, session blackboard and simulator for twenty ordered tasks."""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from harness_full100_world import FullWorld, TaskDeadlineReached
from harness_v2_world import ROOT, sha, write_json
from .score import canonical_sha

PREPARATION = {"land_wait_seconds": 2, "charge_wait_seconds": 2, "between_tasks_seconds": 1}


class SessionWorld(FullWorld):
    """Use the frozen per-task admission barrier with one increasing session clock."""
    offset = 0.0

    def request(self, method, endpoint, *, admin=False, **kwargs):
        from harness_natural_runner import task_request_allowed
        if self.phase == "task" and (not task_request_allowed(method, endpoint, admin)
                                     or (endpoint == "/sessions/current" and (kwargs.get("params") or {}).get("data"))):
            raise PermissionError(f"Public Agent endpoint denied: {method} {endpoint}")
        return super().request(method, endpoint, admin=admin, **kwargs)

    def advance(self, relative):
        target = max(self.clock.relative, self.offset + relative)
        while self.tick < int(target):
            self.tick += 1
            self.clock.relative = float(self.tick)
            self.server.target_controller.update_moving_targets()
            self.server.session_controller.sync_current_session_state()
            self.server.environment_controller.simulate_weather_changes()
        self.clock.relative = target

    def reset_admission(self):
        if self._timer is not None:
            self._timer.join()
        self.started = self._timer = self.score_blob = self.score_seconds = None
        self.closed = False
        self.phase = "preparation"
        self.offset = self.clock.relative


class PreparationAPI:
    def __init__(self, world):
        self.world = world

    def call(self, method, path, **kwargs):
        if self.world.phase == "preparation":
            self.world.advance(time.monotonic() - self.preparation_start)
        return self.world.request(method, path, admin=True, **kwargs)


def projection(world, board):
    session = world.server.session_controller.sessions[world.server.session_controller.current_session_id]
    positions = {d.id: {**d.position, "battery_level": d.battery_level, "status": d.status.value,
                        "history_count": len(d.history_status)}
                 for d in world.server.drone_controller.drones.values()}
    history = session.to_dict()["history"]
    board = copy.deepcopy(board.full())
    return {"world_state_sha256": world.state_digest(), "positions": positions,
            "blackboard": board, "blackboard_sha256": canonical_sha(board), "clock_seconds": world.clock.relative,
            "marked_done": [k for k, t in session.tasks.items() if t.get("is_done")],
            "session_history_sha256": canonical_sha(history),
            "session_history_counts": {k: len(v) for k, v in history.items() if isinstance(v, (dict, list))}}


class ScriptedModel:
    """A deliberately partial CPU policy. Its decisions use only public commands and receipts."""
    def begin(self, command, fault, deadline, path):
        self.step = 0
        self.command = command
        self.fault = fault
        self.deadline = deadline
        self.path = path
        self.triggered = False

    def decision(self, messages):
        from general_agent_local import _public_receipt
        if self.step == 0:
            return "list_drones", {}
        # list_drones was the first call in this command; names, IDs and positions
        # all come from its real wire receipt, never from the admin scene.
        receipts = [m for m in messages if m["role"] == "tool"]
        actors = _public_receipt(receipts[0]["content"])
        name = re.search(r"\bDrone\s+\d+\b", self.command, re.I)
        actor = next((d for d in actors if name and d["name"].lower() == name[0].lower()), actors[0])
        drone = {"drone_id": actor["id"]}
        altitude = re.search(r"tak(?:e|ing) off (?:together )?to (?:an altitude of )?(\d+(?:\.\d+)?)", self.command, re.I)
        if self.step == 1:
            return "take_off", {"input_json": {**drone, "altitude": float(altitude[1]) if altitude else 10.0}}
        if self.step == 2:
            if "hover in place" in self.command.lower():
                return "hover", {"input_json": drone}
            return "move_towards", {"input_json": {**drone, "distance": 1.0, "heading": 90.0}}
        if self.step == 3:
            return "sense_nearby_entities", {"input_json": {}}
        if self.step == 4:
            return None, {}
        raise RuntimeError("Unexpected model call after end of scripted public actions")

    def respond(self, request):
        from openai import _base_client
        httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
        payload = json.loads(request.content)
        index = self.step
        if self.fault == "abort-after-1" and index == 0:
            self.triggered = True
            self.step += 1
            write_json(self.path / "fault.json", {"kind": "provider_503", "http_status": 503, "call_index": index})
            return httpx.Response(503, json={"error": {"message": "Local CPU fixture outage"}}, request=request)
        tool, args = self.decision(payload["messages"])
        if self.fault == "deadline" and index == 1:
            time.sleep(max(0, self.deadline - time.monotonic()) + .05)
            self.triggered = True
            write_json(self.path / "fault.json", {"kind": "deadline", "call_index": index, "delayed_tool": tool})
        self.step += 1
        if tool:
            if tool not in {item["function"]["name"] for item in payload["tools"]}:
                raise ValueError(f"Unadvertised scripted tool: {tool}")
            message = {"role": "assistant", "content": "", "tool_calls": [{"id": f"cpu-{index}",
                       "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}
        else:
            message = {"role": "assistant", "content": "The scripted public-action probe has finished."}
        body = {"id": f"cpu-{index}", "object": "chat.completion", "created": 1, "model": "local-scripted-sdk",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}}
        return httpx.Response(200, json=body, request=request)


def build_agent(world, policy, model, profile):
    from harness_full100_online import agent_class
    from langchain_openai import ChatOpenAI
    import openai
    from openai import _base_client
    import toolchain_recorder
    import uav_agent
    toolchain_recorder.TOOLCHAIN_LOG_DIR = policy / "toolchains"
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    sync = openai.DefaultHttpxClient(transport=httpx.MockTransport(model.respond), trust_env=False)

    class ClosedAsync(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise RuntimeError("Session CPU fixture is synchronous")

    async_client = openai.DefaultAsyncHttpxClient(transport=ClosedAsync(), trust_env=False)

    def no_settings(*args, **kwargs):
        raise RuntimeError("Provider settings are disabled in the session CPU regression")

    uav_agent.load_llm_settings = no_settings
    uav_agent.ChatOpenAI = lambda **kwargs: ChatOpenAI(http_client=sync, http_async_client=async_client,
              max_retries=0, max_tokens=profile.runtime()["request_limits"]["max_output_tokens"], streaming=False, **kwargs)
    os.environ.update(profile.environment())
    base = agent_class(world, policy)

    class SessionAgent(base):
        def _initialize_task_progress(self, command, command_id):
            super()._initialize_task_progress(command, command_id)
            if self.blackboard is not self._session_blackboards.get(self.blackboard.session_id):
                raise RuntimeError("Command lost its session blackboard")
            self.entry_board = copy.deepcopy(self.blackboard.full())

    opts = profile.agent_kwargs()
    opts["share_blackboard_by_session"] = True
    return SessionAgent(base_url="http://testserver", llm_api_key="synthetic-local-key",
            llm_base_url="https://offline.invalid/v1", temperature=.1, verbose=False,
            model_io_archive_dir=policy / "modelio", **opts)


def run_session(scene: dict, output: Path, *, fault: str = "none"):
    from harness_episode_local import setup_paths
    from reproduction import prepare_task
    from agent_profile import AgentProfile
    if fault not in {"none", "deadline", "abort-after-1"}:
        raise ValueError("Unknown session fault")
    output = output.resolve()
    policy = output / "policy"
    policy.mkdir()
    setup_paths(policy)
    for name in ("models", "controllers"):
        sys.modules[name + ".__init__"] = importlib.import_module(name)
    world = SessionWorld()
    world.import_session(scene)
    api = PreparationAPI(world)
    profile = AgentProfile.load(ROOT / "experiments/hover-compact-contract-v2/profile.json")
    model = ScriptedModel()
    cold_world = world.snapshot()
    agent = build_agent(world, policy, model, profile)
    # Initialize via the same public session identity path used by execute().
    board = agent._blackboard_for_current_command()
    world.restore(cold_world)
    initial = projection(world, board)
    session = {"session_id": scene["id"], "worker_pid": os.getpid(), "initial": initial,
               "initial_world_sha256": initial["world_state_sha256"], "initial_board_sha256": initial["blackboard_sha256"],
               "fault": fault, "preparation": PREPARATION, "tasks": []}
    aborted = False
    for index, task in enumerate(scene["tasks"], start=1):
        row = {"index": index, "task_id": task["id"], "directory": f"tasks/{index:02}-{task['id']}"}
        session["tasks"].append(row)
        if aborted:
            row.update(status="unstarted", reason="upstream_service_failure")
            continue
        folder = output / row["directory"]
        folder.mkdir(parents=True)
        world.reset_admission()
        before = projection(world, board)
        api.preparation_start = time.monotonic()
        prepare_task(api, scene, PREPARATION)
        world.offset = world.clock.relative
        start = projection(world, board)
        write_json(folder / "before.json", {"prepare_before": before, "policy_start": start})
        injected = fault if index == 2 else "none"
        world.deadline_seconds = 1.5 if injected == "deadline" else profile.runtime()["task_seconds"]
        world.start(timer=True)
        model.begin(task["content"], injected, world.started + world.deadline_seconds, folder)
        request_start, event_start = len(world.requests), len(world.physical_events)
        result = agent.execute(task["content"])
        blob = world.freeze_score()
        world._timer.join()
        if injected != "none" and not model.triggered:
            raise RuntimeError(f"Fixture fault did not fire: {injected}")
        timed_out = world.score_seconds >= world.deadline_seconds
        if timed_out != (injected == "deadline"):
            raise RuntimeError("Unexpected deadline disposition")
        if agent.entry_board != start["blackboard"] or board is not agent.blackboard:
            raise RuntimeError("Agent failed to inherit the previous command's blackboard")
        archive_path = Path(agent._model_io_archive.info()["path"])
        archive_rows = list(map(json.loads, archive_path.read_text().splitlines()))
        statuses = [r["data"]["status_code"] for r in archive_rows if r["kind"] == "http_response_start"]
        provider_failed = statuses == [503] and result.get("success") is False
        if provider_failed != (injected == "abort-after-1"):
            raise RuntimeError(f"Unexpected provider result: {statuses}, {result.get('error')}")
        if injected == "none" and (statuses != [200] * 5 or model.step != 5):
            raise RuntimeError(f"Scripted Agent path incomplete: {result.get('error') or result.get('output')}")
        reason = "provider_503" if provider_failed else "deadline" if timed_out else "agent_finished"
        row.update(status="invalid" if provider_failed else "completed", reason=reason)
        (folder / "world.pkl").write_bytes(blob)
        world_sha = sha(blob)
        terminal = projection(world, board)
        boundary = {"worker_pid": os.getpid(), "task_id": task["id"], "world_sha256": world_sha,
                    "world_state_sha256": world.state_digest(), "checker_sha256": canonical_sha(task["execution_check_apis"]),
                    "terminal": terminal, "reason": reason, "score_seconds": world.score_seconds,
                    "deadline_seconds": world.deadline_seconds, "entry_board_sha256": canonical_sha(agent.entry_board),
                    "archive": str(archive_path.relative_to(output)), "http_statuses": statuses,
                    "agent_success": result.get("success"), "marked_done": False}
        public_requests = [r for r in world.requests[request_start:] if r["phase"] == "task"]
        if (provider_failed or timed_out) and any(r["action"] and r["committed"] for r in public_requests):
            raise RuntimeError("Injected fault allowed a committed public action")
        write_json(folder / "actions.json", {"requests": [{k: r[k] for k in ("request_id", "method", "endpoint", "admin",
                  "action", "committed", "origin")} for r in public_requests], "events": world.physical_events[event_start:]})
        if timed_out:
            endpoint = f"/drones/{next(iter(terminal['positions']))}/command/land"
            try:
                world.request("POST", endpoint)
            except TaskDeadlineReached:
                unchanged = world.state_digest() == terminal["world_state_sha256"]
                boundary["deadline_probe"] = {"endpoint": endpoint, "rejected": True, "unchanged": unchanged,
                    "before_sha256": terminal["world_state_sha256"], "after_sha256": world.state_digest()}
                if not unchanged:
                    raise RuntimeError("Late action changed a closed world")
            else:
                raise RuntimeError("Late action was admitted")
        world.phase = "continuation"
        if reason == "agent_finished":
            # Only the boolean from the isolated checker enters the admin path.
            # It never enters the model transport, tool receipts or blackboard.
            with (folder / "eligibility.log").open("xb") as log:
                subprocess.run([sys.executable, "-m", "flockcue_session_cpu.score", str(output / "source-scene.json"),
                    str(folder), task["id"], world_sha], cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                    timeout=30, check=True)
            eligible = json.loads((folder / "eligibility.json").read_bytes())
            if eligible["passed"]:
                api.call("POST", f"/sessions/{scene['id']}/tasks/{task['id']}/mark-done")
                boundary["marked_done"] = True
        boundary["continuation"] = projection(world, board)
        write_json(folder / "boundary.json", boundary)
        aborted = row["status"] == "invalid"
        if not aborted and index < len(scene["tasks"]):
            time.sleep(PREPARATION["between_tasks_seconds"])
    write_json(output / "session.json", session)
