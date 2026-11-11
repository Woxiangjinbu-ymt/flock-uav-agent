"""Configuration boundary tests; no Agent, credentials or network required."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))
from agent_profile import AgentProfile


@pytest.fixture
def profile_dict():
    return json.loads((ROOT / "experiments/general-agent-v1/profile.json").read_text())


def set_path(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def test_profile_snapshot_and_fingerprint_are_independent_of_input_and_output_mutation(profile_dict):
    profile = AgentProfile.from_dict(profile_dict)
    original_hash = profile.fingerprint
    profile_dict["harness"]["revision"] = "legacy"
    exposed = profile.to_dict()
    exposed["runtime"]["request_limits"]["max_http_attempts"] = 1000
    profile.runtime()["task_seconds"] = 1
    assert profile.fingerprint == original_hash
    assert profile.to_dict()["harness"]["revision"] == "observed_target_v7"
    assert profile.runtime()["task_seconds"] == 500


def test_profile_fingerprint_ignores_object_key_order_but_records_budget_changes(profile_dict):
    first = AgentProfile(profile_dict)
    reordered = json.loads(json.dumps(profile_dict, sort_keys=True))
    assert AgentProfile(reordered).fingerprint == first.fingerprint
    reordered["runtime"]["request_limits"]["max_http_attempts"] = 7
    assert AgentProfile(reordered).fingerprint != first.fingerprint


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True),
    (("online_authorized_executions",), False),
    (("online_authorized_executions",), 1),
    (("scope",), "online"),
    (("model", "name"), "deepseek-flash"),
    (("agent", "task_progress_mode"), "evidence_memory_v7_harness"),
    (("agent", "execution_memory"), "execution_memory_v7"),
    (("agent", "share_blackboard_by_session"), True),
    (("agent", "model_io_archive"), False),
    (("harness", "revision"), "legacy"),
    (("harness", "scheduler"), "failure"),
    (("harness", "static_obstacles"), 1),
    (("execution", "worker"), "reused_process"),
    (("execution", "transport"), "http"),
    (("evaluation", "scorer"), "in_process"),
    (("provider_plan", "status"), "online_ready"),
])
def test_profile_rejects_unvalidated_combinations(profile_dict, path, value):
    set_path(profile_dict, path, value)
    with pytest.raises(ValueError):
        AgentProfile(profile_dict)


@pytest.mark.parametrize("section", [None, "agent", "model", "harness", "runtime", "execution", "evaluation", "provider_plan"])
def test_unknown_field_rejected_at_each_level(profile_dict, section):
    destination = profile_dict if section is None else profile_dict[section]
    destination["api_key"] = "synthetic-do-not-log"
    with pytest.raises(ValueError) as exc:
        AgentProfile(profile_dict)
    assert "synthetic-do-not-log" not in str(exc.value)


def test_missing_field_and_non_object_rejected(profile_dict):
    del profile_dict["agent"]["execution_memory"]
    with pytest.raises(ValueError):
        AgentProfile(profile_dict)
    with pytest.raises(ValueError):
        AgentProfile([])


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, float("nan"), float("inf"), "8", 21])
def test_request_attempt_limit_rejects_unsafe_values(profile_dict, value):
    profile_dict["runtime"]["request_limits"]["max_http_attempts"] = value
    with pytest.raises(ValueError):
        AgentProfile(profile_dict)


@pytest.mark.parametrize("path,value", [
    (("runtime", "task_seconds"), float("nan")),
    (("runtime", "task_seconds"), 501),
    (("runtime", "drain_seconds"), False),
    (("runtime", "drain_seconds"), -1),
    (("runtime", "request_limits", "max_total_tokens"), 100001),
    (("runtime", "request_limits", "max_output_tokens"), 8193),
    (("runtime", "request_limits", "per_request_timeout_seconds"), float("inf")),
    (("runtime", "request_limits", "per_request_timeout_seconds"), 61),
    (("execution", "watchdog_seconds"), 0),
    (("execution", "watchdog_seconds"), 516),
])
def test_other_budget_fields_reject_unsafe_values(profile_dict, path, value):
    set_path(profile_dict, path, value)
    with pytest.raises(ValueError):
        AgentProfile(profile_dict)


def test_output_limit_cannot_exceed_episode_threshold(profile_dict):
    profile_dict["runtime"]["request_limits"]["max_total_tokens"] = 255
    with pytest.raises(ValueError):
        AgentProfile(profile_dict)


def test_duplicate_json_keys_rejected_on_load(tmp_path, profile_dict):
    path = tmp_path / "profile.json"
    body = json.dumps(profile_dict).replace('"max_http_attempts": 8', '"max_http_attempts": 20, "max_http_attempts": 8')
    path.write_text(body)
    with pytest.raises(ValueError, match="Duplicate profile field"):
        AgentProfile.load(path)


def test_adapters_pin_runtime_components_without_reading_credentials(monkeypatch, profile_dict):
    monkeypatch.setenv("UAV_TASK_PROGRESS_MODE", "evidence_memory_v7_harness")
    monkeypatch.setenv("UAV_HARNESS_REVISION", "legacy")
    monkeypatch.setenv("UAV_MODEL_IO_ARCHIVE_DIR", "/unexpected")
    monkeypatch.setenv("OPENAI_API_KEY", "not-an-input")
    profile = AgentProfile(profile_dict)
    args, env = profile.agent_kwargs(), profile.environment()
    assert args["task_progress_mode"] == "evidence_harness"
    assert args["llm_model"] == profile.local_model == "local-scripted-sdk"
    assert args["model_io_archive"] and args["toolchain_json_recording"]
    assert args["share_blackboard_by_session"] is False
    assert not {"model_io_archive_dir", "llm_api_key", "uav_api_key"} & args.keys()
    assert env["UAV_HARNESS_REVISION"] == "observed_target_v7"
    assert env["UAV_HARNESS_STATIC_OBSTACLES"] == "1"
    assert env["LANGSMITH_TRACING"] == "false"
    assert "UAV_MODEL_IO_ARCHIVE_DIR" not in env
    assert "not-an-input" not in json.dumps(profile.to_dict())
    assert profile.watchdog_seconds == 30


def test_implementation_caps_cannot_drift_independently(profile_dict):
    profile = AgentProfile(profile_dict)
    budget = profile.implementation_budget()
    profile.assert_implementation_budget(budget, api_calls_per_plan=2000, repair_budget=16, search_rounds=16)
    with pytest.raises(ValueError):
        profile.assert_implementation_budget(budget, api_calls_per_plan=1000, repair_budget=16, search_rounds=16)
    budget["max_iterations"] = 150
    with pytest.raises(ValueError):
        profile.assert_implementation_budget(budget, api_calls_per_plan=2000, repair_budget=16, search_rounds=16)
    profile_dict["implementation_budget"]["api_calls_per_plan"] = 1000
    with pytest.raises(ValueError):
        AgentProfile(profile_dict)
