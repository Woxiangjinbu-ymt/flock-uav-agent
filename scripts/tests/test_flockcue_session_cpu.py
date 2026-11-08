"""Regression tests for independent session evidence, including deliberately inconsistent receipts."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from flockcue_session_cpu import verify


def leaf(endpoint, expect, actual):
    return {"type": "leaf", "depth": 1, "endpoint": endpoint, "parameters": {}, "expect": expect,
            "actual": actual, "response": {"result": actual}, "result": actual == expect}


def test_recompute_original_or_and_negative_expectation():
    tree = {"logic": "or", "checks": [{"endpoint": "/check/a"}, {"endpoint": "/check/b", "expect": False}]}
    checker = {"passed": True, "errors": [], "checks_expected": 2, "checks_passed": 1,
               "details": [{"type": "group", "count": 2, "logic": "OR", "depth": 0},
                           leaf("/check/a", True, False), leaf("/check/b", False, False)]}
    assert verify.check_responses(tree, checker) == (True, 2)
    other = deepcopy(checker)
    other["passed"] = False
    with pytest.raises(ValueError, match="aggregate"):
        verify.check_responses(tree, other)
    checker["details"][2]["response"]["result"] = True
    with pytest.raises(ValueError, match="endpoint response"):
        verify.check_responses(tree, checker)


def test_prepare_rejects_resets_even_when_landed_and_charged():
    pos = {"x": 123, "y": 456, "z": 0, "status": "idle", "battery_level": 100}
    before = {"positions": {"D": {**pos, "z": 8, "battery_level": 73}},
              "blackboard": {"targets": [{"id": "seen"}]}}
    start = {"positions": {"D": pos}, "blackboard": deepcopy(before["blackboard"])}
    verify.check_prepare(before, start)
    start["blackboard"] = {"targets": []}
    with pytest.raises(ValueError, match="blackboard"):
        verify.check_prepare(before, start)
    start["blackboard"] = deepcopy(before["blackboard"])
    start["positions"]["D"]["x"] = 12
    with pytest.raises(ValueError, match="reset XY"):
        verify.check_prepare(before, start)


def test_order_is_verified_against_frozen_bytes(tmp_path):
    population = verify.read(verify.DATASET / "candidate-600.json")
    entry = population["strata"][0]["sessions_selected"][0]
    with zipfile.ZipFile(verify.DATASET / "sessions-600.zip") as zip_file:
        original = zip_file.read(entry["filename"])
    scene_file = tmp_path / "source-scene.json"
    scene_file.write_bytes(original)
    scene = verify.original_scene(tmp_path, entry["session_id"])
    assert [item["id"] for item in scene["tasks"]] == entry["task_ids_in_original_order"]
    scene["tasks"][0], scene["tasks"][1] = scene["tasks"][1], scene["tasks"][0]
    scene_file.write_text(json.dumps(scene))
    with pytest.raises(ValueError, match="original scene bytes"):
        verify.original_scene(tmp_path, entry["session_id"])


def test_external_connection_and_scorer_policy_import_fail_closed(tmp_path):
    observed = {"role": "scorer", "pid": 5, "agent_imported": False, "network_attempts": []}
    receipt = {"role": "scorer", "pid": 5, "returncode": 0, "started_ns": 1, "finished_ns": 2}
    boundary = tmp_path / "scorer-boundary.json"

    def seal():
        boundary.write_text(json.dumps(observed))
        receipt["files"] = {boundary.name: verify.file_sha(boundary)}
        (tmp_path / "scorer-exit.json").write_text(json.dumps(receipt))

    seal()
    assert verify.process_evidence(tmp_path.resolve(), "scorer") == receipt
    observed["agent_imported"] = True
    seal()
    with pytest.raises(ValueError, match="imported the policy"):
        verify.process_evidence(tmp_path.resolve(), "scorer")
    observed["network_attempts"] = ["socket_connection_blocked"]
    seal()
    with pytest.raises(ValueError, match="external connection"):
        verify.process_evidence(tmp_path.resolve(), "scorer")


def test_wire_private_tree_and_mutated_response_bytes(tmp_path):
    source_task = {"execution_check_apis": {"endpoint": "/check/altitude"}}

    def wrap(value):
        raw = json.dumps(value).encode()
        return {"base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(), "length": len(raw)}

    def row(kind, **kwargs):
        return {"kind": kind, "data": kwargs}

    request = row("http_request", attempt_id="1", body=wrap({"messages": [{"role": "user", "content": "Fly"}]}))
    response = row("http_response_chunk", attempt_id="1", body=wrap({"choices": [
        {"message": {"tool_calls": [{"function": {"name": "take_off"}}]}}]}))
    records = [row("archive_start"), request, row("http_response_start", attempt_id="1", status_code=200),
               response, row("archive_end")]
    path = tmp_path / "wire.jsonl"

    def archive():
        path.write_text("\n".join(map(json.dumps, records)) + "\n")

    archive()
    assert verify.check_wire(path, source_task) == ([200], ["take_off"])
    response["data"]["body"]["length"] = -1
    archive()
    with pytest.raises(ValueError, match="wire digest"):
        verify.check_wire(path, source_task)
    request["data"]["body"] = wrap({"messages": [{"role": "user", "content": "/check/altitude"}]})
    archive()
    with pytest.raises(ValueError, match="Private checker"):
        verify.check_wire(path, source_task)


def test_physical_action_cannot_be_fabricated_from_a_public_read():
    request = {"request_id": 7, "method": "POST", "endpoint": "/drones/1/command/hover",
               "admin": False, "action": True, "origin": {"kind": "agent"}, "committed": True}
    event = {"request_id": 7, "origin": {"kind": "agent"}, "phase": "task", "committed": True}
    actions = {"requests": [request], "events": [event]}
    assert verify.check_actions(actions) == 1
    request.update(method="GET", action=False)
    with pytest.raises(ValueError, match="public command"):
        verify.check_actions(actions)
