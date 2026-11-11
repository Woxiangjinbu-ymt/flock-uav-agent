"""Exercise the actual service settings/env path, with no simulator/model I/O."""
import json
import os
from pathlib import Path
import sys
import pytest

# These tests also run in detached, materialized legacy workspaces.
REPOSITORY = next(p for p in Path(__file__).resolve().parents if (p / "scripts").is_dir())
sys.path.insert(0, str(REPOSITORY / "agent4drone"))
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
import agent_api_service as service
import uav_agent
sys.path.insert(0, str(REPOSITORY / "agent4drone/tests"))
from test_task_progress_integration import ScriptedModel, call


@pytest.mark.parametrize("mode,revision", [("evidence", "m1-r1"), ("evidence_r2", "m1-r2"),
                                            ("evidence_r2_hints", "m1-r2")])
def test_service_environment_activates_revision_and_records_it(monkeypatch, tmp_path, mode, revision):
    class LocalClient:
        def __init__(self, *args, **kwargs):
            pass
        def get_current_session(self):
            return {"id": "offline-scene", "created_at": 1, "statistics": {"total_commands_executed": 0}}
    @tool
    def get_drone_status(input_json: str) -> str:
        """Read the offline test drone."""
        return json.dumps({"id": "d1", "name": "Drone 1", "position": {"x": 0, "y": 0, "z": 10}})
    @tool
    def move_to(input_json: str) -> str:
        """Return an offline positive action receipt."""
        return json.dumps({"status": "success", "command": "move_to", "drone_id": "d1",
            "message": "Drone moved - Reached target(s): Fixed Target 10"})
    model = ScriptedModel(replies=[call("get_drone_status", "read", drone_id="d1"),
                                  call("move_to", "move", drone_id="d1", x=20, y=0, z=10),
                                  AIMessage(content="Done [TASK DONE]")])
    settings = {"selected_provider": "deepseek_official", "provider_configs": {"deepseek_official": {
        "type": "openai-compatible", "base_url": "https://api.deepseek.com", "default_model": "deepseek-flash",
        "requires_api_key": True, "api_key": "offline-fixture"}},
        "share_blackboard_by_session": True, "toolchain_json_recording": True}
    monkeypatch.setenv("UAV_TASK_PROGRESS_MODE", mode)
    monkeypatch.setenv("UAV_MODEL_REQUEST_AUDIT", "1")
    monkeypatch.setattr(service, "load_llm_settings", lambda _: settings)
    monkeypatch.setattr(uav_agent, "UAVAPIClient", LocalClient)
    monkeypatch.setattr(uav_agent.UAVControlAgent, "_create_llm", lambda *a, **k: model)
    monkeypatch.setattr(uav_agent.UAVControlAgent, "_create_tools", lambda *a: [get_drone_status, move_to])
    import toolchain_recorder
    captured = []
    monkeypatch.setattr(toolchain_recorder, "write_toolchain_json", lambda rec: captured.append(rec) or tmp_path / "trace.json")
    monkeypatch.setattr(toolchain_recorder, "request_history_jsonl_path", lambda rec: tmp_path / "http.jsonl")
    monkeypatch.setattr(toolchain_recorder, "write_request_history_jsonl", lambda *a: None)
    agent = service.initialize_agent_from_settings()
    result = agent.execute("Drone 1 flies to target Fixed Target 10.")
    assert result["success"] and agent.share_blackboard_by_session and agent._model_request_audit_enabled
    trace = captured[0]
    assert trace["runtime"]["task_progress_mode"] == mode
    assert trace["runtime"]["task_progress_active"] is True
    assert trace["task_progress"]["revision"] == revision
    assert trace["task_progress"]["observer_errors"] == []
    ref = trace["task_progress"]["task_evidence"]["requirements"][0]
    assert ref["knowledge_state"] == "reported_only" and ref["execution_state"] == "evidenced"
    assert len(trace["task_progress"]["task_evidence"]["model_requests"]) == 3
    # This fixture has no HTTP model client; the method's SDK MockTransport
    # tests separately require and verify actual serialized request hashes.
    assert trace["model_requests_wire"] == []
    assert Path(uav_agent.__file__).resolve().parents[1] == REPOSITORY
    if mode == "evidence_harness":
        assert trace["harness"] == result["harness"]
        assert trace["harness"]["schema"] == "minimal-harness-v1"
        assert sum(e["kind"] == "model_request_start" for e in trace["harness"]["events"]) == 3
        assert trace["harness"]["events"][-1]["kind"] == "task_end"
