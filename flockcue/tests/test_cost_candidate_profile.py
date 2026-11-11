"""Candidate knobs remain local-only and cannot relax execution contracts."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from flockcue.runtime.profile import AgentProfile


def candidate():
    value = json.loads((ROOT / "experiments/agent-execution-v1/profile-main.json").read_text())
    value.update(schema_version=3, profile_id="general-agent-v3-local")
    value["agent"]["input_layout"] = "compact_tools_v1"
    value["harness"]["revision"] = "search_progress_v8"
    return value


def test_candidate_is_explicit_orthogonal_and_preserves_contract_caps():
    value = candidate()
    hashes = set()
    for layout in ("full_v1", "compact_tools_v1"):
        for revision in ("observed_target_v7", "search_progress_v8", "search_confirm_v9"):
            configured = copy.deepcopy(value)
            configured["agent"]["input_layout"] = layout
            configured["harness"]["revision"] = revision
            profile = AgentProfile(configured)
            hashes.add(profile.fingerprint)
            env = profile.environment()
            assert env["UAV_AGENT_INPUT_LAYOUT"] == layout
            assert env["UAV_HARNESS_REVISION"] == revision
            assert env["UAV_TASK_EXECUTION_STATE"] == env["UAV_MODEL_IO_ARCHIVE"] == "1"
            assert profile.execution_contract == "task_execution_state_v1"
            assert profile.implementation_budget() == value["implementation_budget"]
            assert profile.agent_kwargs()["model_io_archive"] is True
            assert profile.agent_kwargs()["input_layout"] == layout
            assert profile.to_dict()["online_authorized_executions"] == 0
    assert len(hashes) == 6


@pytest.mark.parametrize("section,field,replacement", [
    ("agent", "input_layout", "unknown"), ("agent", "input_layout", None),
    ("agent", "input_layout", True), ("harness", "revision", "legacy"),
    ("agent", "execution_contract", "off"), ("agent", "model_io_archive", False),
    ("execution", "transport", "http"), ("execution", "worker", "reused"),
    ("implementation_budget", "search_rounds", 8),
])
def test_candidate_rejects_unsafe_or_ambiguous_combinations(section, field, replacement):
    value = candidate()
    value[section][field] = replacement
    with pytest.raises(ValueError):
        AgentProfile(value)


@pytest.mark.parametrize("version", [1, 2])
def test_older_profile_cannot_enable_candidate_by_inherited_environment(version, monkeypatch):
    path = "experiments/general-agent-v1/profile.json" if version == 1 else "experiments/agent-execution-v1/profile-main.json"
    value = json.loads((ROOT / path).read_text())
    monkeypatch.setenv("UAV_AGENT_INPUT_LAYOUT", "compact_tools_v1")
    assert AgentProfile(value).environment()["UAV_AGENT_INPUT_LAYOUT"] == "full_v1"
    value["agent"]["input_layout"] = "compact_tools_v1"
    with pytest.raises(ValueError):
        AgentProfile(value)


def test_v3_requires_layout_and_has_no_online_authorization():
    value = candidate()
    value["online_authorized_executions"] = 1
    with pytest.raises(ValueError):
        AgentProfile(value)
    value = candidate()
    del value["agent"]["input_layout"]
    with pytest.raises(ValueError):
        AgentProfile(value)
