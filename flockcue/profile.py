"""Frozen FlockCue policy switches selected for the 600-task deployment."""
import hashlib
import json
from pathlib import Path

PROFILE = Path(__file__).with_name("profile.json")
PROFILE_SHA256 = "41ea656939cdd1c0a840936dbab6a302d6d42976d532a4c56766bc7615a3eca5"


def load_profile():
    data = PROFILE.read_bytes()
    if hashlib.sha256(data).hexdigest() != PROFILE_SHA256:
        raise ValueError("Unexpected deployment profile SHA-256")
    return json.loads(data)


def environment(value):
    harness = value["harness"]
    return {
        "UAV_AGENT_INPUT_LAYOUT": value["agent"].get("input_layout", "full_v1"),
        "UAV_COMPLETION_CONTRACT": value["agent"].get("completion_contract", "off"),
        "UAV_TOOL_INPUT_CONTRACT": value["agent"].get("tool_input_contract", "string_v1"),
        "UAV_TASK_EXECUTION_STATE": "1" if value["agent"]["execution_contract"] == "task_execution_state_v1" else "0",
        "UAV_TASK_PROGRESS_MODE": value["agent"]["task_progress_mode"],
        "UAV_HARNESS_SCHEDULER": harness["scheduler"],
        "UAV_HARNESS_REVISION": harness["revision"],
        "UAV_HARNESS_FIXED_K": str(harness["fixed_k"]),
        "UAV_HARNESS_STATIC_OBSTACLES": "1",
        "UAV_MODEL_IO_ARCHIVE": "1",
        "UAV_MODEL_REQUEST_AUDIT": "1",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
    }
