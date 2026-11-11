"""Same-agent admission/cleanup contracts; scripted model and public fake only.

No provider, simulator, benchmark or sleeps. Events expose exact race boundaries;
the real constructor, execute graph, recording and service handlers stay in use.
This does not certify shared-world isolation between separate agent instances.
"""
import asyncio
import copy
import json
import socket
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import agent_api_service as service
import toolchain_recorder
import uav_agent
from blackboard import PerceptionBlackboard
from flockcue.evidence.contact_evidence import SessionEvidenceIndexR2
from test_minimal_harness import PublicWorld, SIMPLE
from test_task_progress_integration import ScriptedModel, call
from uav_agent import AgentBusyError, UAVControlAgent


@pytest.fixture(autouse=True)
def isolated_network_and_jobs(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in episode-isolation tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(service, "jobs", {})
    monkeypatch.setattr(service, "agent_instance", None)
    monkeypatch.setenv("UAV_HARNESS_REVISION", "observed_target_v7")
    monkeypatch.setenv("UAV_HARNESS_STATIC_OBSTACLES", "1")
    monkeypatch.setenv("UAV_HARNESS_SCHEDULER", "applicability")


class ThreadCall:
    def __init__(self, function):
        self.value = None
        self.error = None
        self.done = threading.Event()
        def run():
            try:
                self.value = function()
            except BaseException as exc:
                self.error = exc
            finally:
                self.done.set()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def join(self):
        self.thread.join(5)
        assert not self.thread.is_alive(), "Offline test thread failed to drain"
        return self


@contextmanager
def held(function, entered, release):
    running = ThreadCall(function)
    try:
        assert entered.wait(5), "Owner never reached the controlled boundary"
        yield running
    finally:
        release.set()
        running.join()


def refused_without_waiting(function):
    contender = ThreadCall(function)
    assert contender.done.wait(1), "Busy operation waited/queued instead of refusing"
    contender.join()
    assert isinstance(contender.error, AgentBusyError)
    assert contender.error.code == "agent_busy"
    assert "agent_busy" in str(contender.error)
    return contender.error


class HeldModel(ScriptedModel):
    entered: object = Field(default_factory=threading.Event, exclude=True)
    release: object = Field(default_factory=threading.Event, exclude=True)
    block_first: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests) - 1
        if self.block_first and index == 0:
            self.entered.set()
            assert self.release.wait(5), "Scripted model was not released"
        reply = self.replies[index]
        if isinstance(reply, BaseException):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=copy.deepcopy(reply))])


class RecordedPublicWorld(PublicWorld):
    def __init__(self):
        super().__init__()
        self.base_url = "offline://episode-fake"
        self.record_starts = 0
        self.record_stops = 0
        self.records = []
        self.recording = False
        self.command_count = 10
        self.reject_takeoff = False

    def start_api_call_recording(self):
        self.record_starts += 1
        self.records = []
        self.recording = True

    def stop_api_call_recording(self):
        self.record_stops += 1
        self.recording = False

    def get_api_call_records(self):
        return copy.deepcopy(self.records)

    def get_task_progress(self):
        return self._call("get_task_progress")

    def _call(self, name, **args):
        if name == "get_task_progress":
            self.calls.append((name, copy.deepcopy(args)))
            result = {"progress_percentage": 0, "status_message": "fixture", "is_completed": False}
            self.after_call(name)
        elif name == "take_off" and self.reject_takeoff:
            self.calls.append((name, copy.deepcopy(args)))
            result = {"command": name, "drone_id": args["drone_id"], "status": "error",
                      "message": "Explicit fixture rejection"}
            self.after_call(name)
        else:
            result = super()._call(name, **args)
        if name == "get_current_session":
            result["created_at"] = 1
            result["statistics"].update(environment_id="fixture", total_commands_executed=self.command_count)
        if self.recording:
            self.records.append({"index": len(self.records) + 1, "method": "GET" if name.startswith("get_") or name == "list_drones" else "POST",
                                 "endpoint": "/fixture/" + name, "params": copy.deepcopy(args),
                                 "response": copy.deepcopy(result), "success": True, "status_code": 200})
        return result


def make_real_agent(monkeypatch, tmp_path, *, replies=None, block=False, mode="evidence_harness"):
    world = RecordedPublicWorld()
    model = HeldModel(replies=replies or [AIMessage(content="A [TASK DONE]"), AIMessage(content="B [TASK DONE]")],
                      block_first=block)
    monkeypatch.setattr(uav_agent, "UAVAPIClient", lambda *args, **kwargs: world)
    monkeypatch.setattr(UAVControlAgent, "_create_llm", lambda *args, **kwargs: model)
    monkeypatch.setattr(toolchain_recorder, "TOOLCHAIN_LOG_DIR", tmp_path / "toolchains")
    # Do not replace _blackboard_for_current_command, _build_system_prompt,
    # _execute_command, _run_agent_once or _record_toolchain_if_enabled.
    agent = UAVControlAgent(base_url="offline://episode-fake", llm_provider="offline", llm_model="scripted",
                            verbose=False, share_blackboard_by_session=True,
                            toolchain_json_recording=True, task_progress_mode=mode)
    agent.max_empty_response_retries = 0
    return agent, world, model


def active_snapshot(agent, world, model):
    harness = getattr(agent, "_harness", None)
    return {"harness_id": id(harness), "ledger_id": id(agent._task_progress),
            "graph_id": id(agent.agent), "board": copy.deepcopy(agent.blackboard.full()),
            "harness": harness.snapshot() if harness else None,
            "model_requests": [str(x) for x in model.requests],
            "wire": copy.deepcopy(agent._model_wire_requests), "calls": copy.deepcopy(world.calls),
            "records": world.get_api_call_records(), "record_starts": world.record_starts,
            "record_stops": world.record_stops, "deadline": getattr(world, "_harness_deadline", None)}


@pytest.mark.parametrize("mode", ["off", "evidence_harness"])
def test_busy_real_execute_refuses_all_public_competitors_before_state_or_uuid(monkeypatch, tmp_path, mode):
    agent, world, model = make_real_agent(monkeypatch, tmp_path, block=True, mode=mode)
    allocated = []
    real_uuid = uav_agent.uuid.uuid4
    monkeypatch.setattr(uav_agent, "uuid", SimpleNamespace(uuid4=lambda: allocated.append(1) or real_uuid()))
    with held(lambda: agent.execute("episode A"), model.entered, model.release) as first:
        before = active_snapshot(agent, world, model)
        count = len(allocated)
        refused_without_waiting(lambda: agent.execute("episode B must not run"))
        refused_without_waiting(lambda: agent.reset_task_evidence("s1"))
        refused_without_waiting(agent.get_session_summary)
        refused_without_waiting(agent.refresh_session_context)
        assert active_snapshot(agent, world, model) == before
        assert len(allocated) == count == 1
    assert first.error is None and first.value["success"]
    old_bytes = Path(first.value["tool_chain_file"]).read_bytes()
    second = agent.execute("episode B")
    assert second["success"] and len(allocated) == 2 and len(model.requests) == 2
    assert "episode A" not in str(model.requests[-1])
    assert first.value["tool_chain_file"] != second["tool_chain_file"]
    assert Path(first.value["tool_chain_file"]).read_bytes() == old_bytes
    assert world.record_starts == world.record_stops == 2
    if mode != "off":
        assert first.value["harness"]["task_id"] != second["harness"]["task_id"]


def test_lock_is_nonreentrant_even_on_owner_thread():
    agent = UAVControlAgent.__new__(UAVControlAgent)
    calls = []
    def body(*args):
        calls.append("owner")
        for operation in (lambda: agent.execute("nested"), agent.reset_task_evidence,
                          agent.get_session_summary, agent.refresh_session_context):
            with pytest.raises(AgentBusyError):
                operation()
        return "only owner"
    agent._execute_command = body
    assert agent.execute("outer") == "only owner" and calls == ["owner"]


def test_lazy_new_adapter_has_one_gate_under_simultaneous_first_use():
    agent = UAVControlAgent.__new__(UAVControlAgent)
    barrier, entered, release, rejected = threading.Barrier(3), threading.Event(), threading.Event(), threading.Event()
    calls = []
    def body(*args):
        calls.append("admitted")
        entered.set()
        assert release.wait(5)
        return True
    agent._execute_command = body
    def contender():
        barrier.wait(5)
        try:
            return agent.execute("same instant")
        except AgentBusyError:
            rejected.set()
            raise
    tasks = [ThreadCall(contender), ThreadCall(contender)]
    try:
        barrier.wait(5)
        assert entered.wait(5) and rejected.wait(1)
        assert calls == ["admitted"]
    finally:
        release.set()
        for task in tasks:
            task.join()
    assert sum(isinstance(task.error, AgentBusyError) for task in tasks) == 1
    assert sum(task.value is True for task in tasks) == 1


def test_different_instances_have_independent_admission_not_world_isolation():
    release = threading.Event()
    entered = [threading.Event(), threading.Event()]
    agents = [UAVControlAgent.__new__(UAVControlAgent) for _ in range(2)]
    for i, agent in enumerate(agents):
        def body(*args, i=i):
            entered[i].set()
            assert release.wait(5)
            return i
        agent._execute_command = body
    tasks = [ThreadCall(lambda a=a: a.execute("independent")) for a in agents]
    try:
        assert all(event.wait(5) for event in entered)
    finally:
        release.set()
        for task in tasks:
            task.join()
    assert [task.value for task in tasks] == [0, 1]


@pytest.mark.parametrize("exception", [RuntimeError("failure"), KeyboardInterrupt("interrupt"), SystemExit("exit")])
def test_wrapper_releases_after_exception_including_baseexception(exception):
    agent = UAVControlAgent.__new__(UAVControlAgent)
    def fail(*args):
        raise exception
    agent._execute_command = fail
    with pytest.raises(type(exception)):
        agent.execute("A")
    agent._execute_command = lambda *args: "B admitted"
    assert agent.execute("B") == "B admitted"


@pytest.mark.parametrize("owner", ["reset", "summary", "refresh"])
def test_memory_reset_and_session_summary_hold_the_same_gate(monkeypatch, tmp_path, owner):
    agent, world, model = make_real_agent(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    if owner == "reset":
        original = agent.blackboard.clear
        def blocked():
            entered.set()
            assert release.wait(5)
            return original()
        monkeypatch.setattr(agent.blackboard, "clear", blocked)
        operation = agent.reset_task_evidence
    else:
        def block(name):
            if name == "get_current_session":
                entered.set()
                assert release.wait(5)
        world.after_call = block
        operation = agent.get_session_summary if owner == "summary" else agent.refresh_session_context
    with held(operation, entered, release) as first:
        calls = copy.deepcopy(world.calls)
        refused_without_waiting(lambda: agent.execute("busy"))
        refused_without_waiting(agent.reset_task_evidence)
        refused_without_waiting(agent.get_session_summary)
        refused_without_waiting(agent.refresh_session_context)
        assert world.calls == calls and not model.requests and world.record_starts == 0
    assert first.error is None
    world.after_call = lambda name: None
    assert agent.execute("now idle")["success"]


def test_internal_visible_reset_while_execute_owns_gate_does_not_reenter(monkeypatch, tmp_path):
    agent, world, model = make_real_agent(monkeypatch, tmp_path)
    assert agent.execute("A")["success"]
    old_board, old_index = agent.blackboard, agent.blackboard.task_evidence_index
    old_board.ingest_nearby("d1", {"targets": [{"id": "old", "name": "OLD_EPISODE_SENTINEL"}]})
    old_epoch = old_index.epoch
    world.command_count = 0  # Same ID/created_at/environment; visible reset counter decreases.
    before = len(world.calls)
    result = agent.execute("B")
    assert result["success"]
    assert old_index.epoch > old_epoch and not old_board.targets
    assert agent.blackboard is not old_board and agent.blackboard.task_evidence_index is not old_index
    assert "OLD_EPISODE_SENTINEL" not in str(model.requests[-1])
    assert [name for name, _ in world.calls[before:]] == ["get_current_session"]


@pytest.mark.parametrize("session", [None, "s1", "other"])
def test_idle_explicit_reset_preserves_session_scope_and_does_no_world_io(session):
    agent = UAVControlAgent.__new__(UAVControlAgent)
    boards = {sid: PerceptionBlackboard(sid) for sid in ("s1", "s2")}
    for board in boards.values():
        board.task_evidence_index = SessionEvidenceIndexR2(board.session_id)
        board.ingest_nearby("d1", {"targets": [{"id": "t1", "name": "marker"}]})
    agent._session_blackboards = dict(boards)
    agent.blackboard = boards["s1"]
    agent.reset_task_evidence(session)
    for sid, board in boards.items():
        cleared = session is None or session == sid
        assert bool(board.targets) is not cleared
        assert (sid in agent._session_blackboards) is not cleared


def test_lease_includes_actual_toolchain_write_and_preserves_first_archive(monkeypatch, tmp_path):
    agent, world, model = make_real_agent(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = toolchain_recorder.write_toolchain_json
    captured = []
    def blocked_write(record, *args, **kwargs):
        captured.append(copy.deepcopy(record))
        if len(captured) == 1:
            entered.set()
            assert release.wait(5)
        return original(record, *args, **kwargs)
    monkeypatch.setattr(toolchain_recorder, "write_toolchain_json", blocked_write)
    with held(lambda: agent.execute("A"), entered, release) as first:
        assert captured[0]["status"] == "completed" and world.recording
        before = active_snapshot(agent, world, model)
        refused_without_waiting(lambda: agent.execute("B too early"))
        refused_without_waiting(agent.reset_task_evidence)
        refused_without_waiting(agent.get_session_summary)
        refused_without_waiting(agent.refresh_session_context)
        assert active_snapshot(agent, world, model) == before
    assert first.error is None and not world.recording
    path = Path(first.value["tool_chain_file"])
    saved = path.read_bytes()
    second = agent.execute("B")
    assert second["success"] and path.read_bytes() == saved
    assert captured[0]["command_id"] != captured[1]["command_id"]
    assert len(captured[0]["api_calls"]) == len(captured[1]["api_calls"]) == 1


@pytest.mark.parametrize("error", [OSError("disk unavailable"), KeyboardInterrupt("stop exporting")])
def test_failed_archive_drains_recorder_and_releases_lease(monkeypatch, tmp_path, error):
    agent, world, model = make_real_agent(monkeypatch, tmp_path)
    original = toolchain_recorder.write_toolchain_json
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(toolchain_recorder, "write_toolchain_json", broken)
    if isinstance(error, Exception):
        assert agent.execute("A")["success"]  # Existing exporter reports failure without changing task output.
    else:
        with pytest.raises(type(error)):
            agent.execute("A")
    assert not world.recording and world.record_stops >= 1
    stopped = world.record_stops
    monkeypatch.setattr(toolchain_recorder, "write_toolchain_json", original)
    assert agent.execute("B")["success"] and world.record_starts == 2 and world.record_stops == stopped + 1


class Cancellation(BaseCallbackHandler):
    def __init__(self):
        self.cancelled = False
    def _check_cancellation(self):
        if self.cancelled:
            raise service.JobCancelledException("controlled cancel")


@pytest.mark.parametrize("ending", ["success", "failure", "cancel", "timeout"])
def test_real_skill_ending_then_clean_adjacent_command(monkeypatch, tmp_path, ending):
    replies = [call("run_harness", "a-start", action="start", skill="simple_chain"),
               AIMessage(content="A [TASK DONE]" if ending == "success" else "A incomplete")]
    agent, world, model = make_real_agent(monkeypatch, tmp_path, replies=replies)
    initial = copy.deepcopy(world.drones)
    cancel = Cancellation()
    if ending == "failure":
        world.reject_takeoff = True
    if ending in {"cancel", "timeout"}:
        def stop(name):
            if name == "take_off":
                if ending == "cancel":
                    cancel.cancelled = True
                else:
                    agent._harness.clock = lambda: agent._harness.deadline
        world.after_call = stop
    first = agent.execute(SIMPLE, callbacks=[cancel])
    assert first["success"] is (ending == "success")
    frame = first["harness"]["frames"][0]
    assert frame["status"] == {"success": "completed", "failure": "needs_replan",
                               "cancel": "interrupted", "timeout": "interrupted"}[ending]
    assert sum(name == "take_off" for name, _ in world.calls) == 1
    old = copy.deepcopy(first["harness"])
    old_path = Path(first["tool_chain_file"])
    old_bytes = old_path.read_bytes()
    world.after_call = lambda name: None
    world.reject_takeoff = False
    world.drones = initial  # Explicit test-fixture reset after A returned, not reset_task_evidence doing I/O.
    agent.reset_task_evidence("s1")
    model.replies = model.replies[:len(model.requests)] + [call("run_harness", "b-start", action="start", skill="simple_chain"),
                                                        AIMessage(content="B [TASK DONE]")]
    second = agent.execute(SIMPLE)
    assert second["success"] and second["harness"]["frames"][0]["status"] == "completed"
    assert second["harness"]["task_id"] != first["harness"]["task_id"]
    assert second["harness"]["frames"][0]["api_calls"] == 10
    assert first["harness"] == old and old_path.read_bytes() == old_bytes
    assert world.record_starts == world.record_stops == 2


@pytest.mark.parametrize("endpoint", ["command", "session"])
def test_service_sync_busy_is_409_and_does_not_enter_active_agent(monkeypatch, tmp_path, endpoint):
    agent, world, model = make_real_agent(monkeypatch, tmp_path, block=True)
    monkeypatch.setattr(service, "agent_instance", agent)
    with held(lambda: agent.execute("A"), model.entered, model.release):
        before = active_snapshot(agent, world, model)
        client = TestClient(service.app)  # No lifespan/settings/provider initialization.
        try:
            response = (client.post("/agent/command", json={"command": "B"}) if endpoint == "command"
                        else client.get("/agent/session"))
        finally:
            client.close()
        assert response.status_code == 409 and response.json()["detail"]["code"] == "agent_busy"
        assert active_snapshot(agent, world, model) == before


def job(ident):
    value = service.JobInfo(job_id=ident, status=service.JobStatus.QUEUED, created_at=datetime.now(), command=ident)
    service.jobs[ident] = value
    return value


def test_cancel_flag_keeps_lease_until_late_model_reply_and_cleanup_finish(monkeypatch, tmp_path):
    agent, world, model = make_real_agent(monkeypatch, tmp_path, block=True, replies=[
        call("run_harness", "late-a-start", action="start", skill="simple_chain"),
        AIMessage(content="B [TASK DONE]")])
    monkeypatch.setattr(service, "agent_instance", agent)
    first, contender = job("A"), job("B")
    with held(lambda: service.process_agent_command("A", SIMPLE), model.entered, model.release) as running:
        asyncio.run(service.cancel_job("A"))
        assert first.status == service.JobStatus.CANCELLED and first.completed_at is None
        service.process_agent_command("B", "B")
        assert contender.status == service.JobStatus.FAILED and "agent_busy" in contender.error
        assert contender.completed_at is not None and contender.result is None
        refused_without_waiting(agent.reset_task_evidence)
        refused_without_waiting(agent.get_session_summary)
        refused_without_waiting(agent.refresh_session_context)
        assert len(model.requests) == 1 and world.record_starts == 1
    assert running.error is None and first.status == service.JobStatus.CANCELLED
    assert first.completed_at is not None and first.result is None
    assert not any(name in {"take_off", "rotate", "hover"} for name, _ in world.calls)
    assert not world.recording
    third = job("C")
    service.process_agent_command("C", "C after actual drain")
    assert third.status == service.JobStatus.COMPLETED and third.result.success
    assert contender.status == service.JobStatus.FAILED and len(model.requests) == 2


def test_async_endpoint_busy_job_fails_once_and_is_not_automatically_retried(monkeypatch, tmp_path):
    agent, world, model = make_real_agent(monkeypatch, tmp_path, block=True)
    monkeypatch.setattr(service, "agent_instance", agent)
    with held(lambda: agent.execute("A"), model.entered, model.release):
        client = TestClient(service.app)
        try:
            response = client.post("/agent/command/async", json={"command": "B"})
        finally:
            client.close()
        assert response.status_code == 200
        rejected = service.jobs[response.json()["job_id"]]
        assert rejected.status == service.JobStatus.FAILED and "agent_busy" in rejected.error
        assert rejected.result is None and world.record_starts == 1 and len(model.requests) == 1
    assert rejected.status == service.JobStatus.FAILED and len(model.requests) == 1


def test_cancelled_contender_is_not_overwritten_as_failed_when_busy_races(monkeypatch, tmp_path):
    agent, world, model = make_real_agent(monkeypatch, tmp_path, block=True)
    monkeypatch.setattr(service, "agent_instance", agent)
    contender = job("B")
    original = agent.execute
    with held(lambda: original("A"), model.entered, model.release):
        def cancelled_then_admit(command, callbacks=None):
            contender.status = service.JobStatus.CANCELLED
            return original(command, callbacks=callbacks)
        monkeypatch.setattr(agent, "execute", cancelled_then_admit)
        service.process_agent_command("B", "B")
        assert contender.status == service.JobStatus.CANCELLED and contender.error is None
        assert contender.completed_at is not None and world.record_starts == 1


def test_escaped_baseexception_holds_gate_until_harness_and_recorder_cleanup(monkeypatch, tmp_path):
    failure = KeyboardInterrupt("original model interruption")
    agent, world, model = make_real_agent(monkeypatch, tmp_path, replies=[failure, AIMessage(content="B [TASK DONE]")])
    entered, release = threading.Event(), threading.Event()
    original = world.stop_api_call_recording
    def held_cleanup():
        entered.set()
        assert release.wait(5)
        original()
    monkeypatch.setattr(world, "stop_api_call_recording", held_cleanup)
    with held(lambda: agent.execute("A"), entered, release) as first:
        assert agent._harness._closed and world.recording
        refused_without_waiting(lambda: agent.execute("B too early"))
        refused_without_waiting(agent.reset_task_evidence)
        assert len(model.requests) == 1
    assert first.error is failure and not world.recording
    assert not list((tmp_path / "toolchains").glob("*.json"))  # No invented successful archive.
    assert agent.execute("B")["success"]
    assert not getattr(agent, "_episode_cleanup_failed", False)


@pytest.mark.parametrize("broken", ["harness", "recorder", "both"])
def test_cleanup_failure_preserves_original_exception_and_fails_closed(monkeypatch, tmp_path, broken):
    failure = KeyboardInterrupt("original interruption must survive cleanup")
    agent, world, model = make_real_agent(monkeypatch, tmp_path, replies=[failure])
    initialized = agent._initialize_task_progress
    attempted = []
    def initialize(*args):
        initialized(*args)
        close = agent._harness.close
        def close_or_fail(reason):
            attempted.append("harness")
            if broken in {"harness", "both"}:
                raise RuntimeError("harness cleanup failed")
            return close(reason)
        agent._harness.close = close_or_fail
    monkeypatch.setattr(agent, "_initialize_task_progress", initialize)
    stop = world.stop_api_call_recording
    def stop_or_fail():
        attempted.append("recorder")
        if broken in {"recorder", "both"}:
            raise SystemExit("recorder cleanup failed")
        return stop()
    monkeypatch.setattr(world, "stop_api_call_recording", stop_or_fail)
    with pytest.raises(KeyboardInterrupt) as observed:
        agent.execute("A")
    assert observed.value is failure and attempted == ["harness", "recorder"]
    assert agent._episode_cleanup_failed and not agent._episode_lock.locked()
    before = active_snapshot(agent, world, model)
    for operation in (lambda: agent.execute("B"), agent.reset_task_evidence,
                      agent.get_session_summary, agent.refresh_session_context):
        with pytest.raises(AgentBusyError, match="fresh agent"):
            operation()
        assert not agent._episode_lock.locked()
    assert active_snapshot(agent, world, model) == before and attempted == ["harness", "recorder"]
    replacement = UAVControlAgent.__new__(UAVControlAgent)
    replacement._execute_command = lambda *args: "new instance admitted"
    assert replacement.execute("replacement") == "new instance admitted"


def test_cancelled_before_worker_start_never_enters_agent_and_has_drain_timestamp(monkeypatch):
    invoked = []
    monkeypatch.setattr(service, "agent_instance", SimpleNamespace(execute=lambda *args, **kwargs: invoked.append(1)))
    pending = job("cancel-before-start")
    pending.status = service.JobStatus.CANCELLED
    assert pending.completed_at is None
    service.process_agent_command(pending.job_id, pending.command)
    assert pending.status == service.JobStatus.CANCELLED and pending.completed_at is not None
    assert pending.result is None and pending.error is None and invoked == []
