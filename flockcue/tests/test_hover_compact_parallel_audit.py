"""Pure adversarial linkage checks for the supplemental parallel audit."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import audit_hover_compact_parallel as audit


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Parallel audit unit tests cannot contact a provider")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def wire(calls, attempt="attempt-1", logical="model-1"):
    raw = json.dumps({"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": calls}}]}).encode()
    return [{"kind": "http_response_chunk", "data": {"attempt_id": attempt, "logical_call_id": logical,
        "chunk_index": 1, "body": {"base64": base64.b64encode(raw).decode(), "length": len(raw), "sha256": audit.sha(raw)}}},
        {"kind": "http_response_end", "data": {"attempt_id": attempt, "logical_call_id": logical,
        "complete": True, "status": "complete", "error_type": None, "chunk_count": 1,
        "length": len(raw), "sha256": audit.sha(raw)}}]


def fixture(second="get_weather", encoding="object", unknown=False):
    events, api, calls, entries = [], [], [], []
    for i, (ident, tool, evidence, peer) in enumerate((("a", "list_drones", "e1", "e2"), ("b", second, "e2", "e1"))):
        params = {"drone_id": "3d229034", "altitude": 12} if tool == "take_off" else {}
        args = {"input_json": json.dumps(params) if encoding == "string" else params} if params else {}
        calls.append({"id": ident, "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}})
        events.append({"tool_call_id": ident, "tool_name": tool, "evidence_id": evidence,
            "start_sequence": i + 1, "end_sequence": i + 3, "ordering": "uncertain", "overlaps": [peer],
            "args": params, "scope": "action_receipt" if tool == "take_off" else "observation",
            "status": ("unknown" if unknown else "completed") if tool == "take_off" else "observed"})
        method, endpoint = audit.ENDPOINTS[tool]
        api.append({"request_id": 10 + i, "phase": "task", "admin": False, "committed": True,
            "method": method, "endpoint": endpoint, "action": tool == "take_off",
            "params": {"altitude": 12} if params else None,
            "origin": {"kind": "model_tool", "tool": tool, "tool_call_id": ident}})
        entries.append({"event": "entered", "tool_call_id": ident, "method": method, "endpoint": endpoint})
    barriers = entries + [{"event": "released", "tool_call_ids": ["a", "b"]}]
    return {"events": events}, barriers, wire(calls), api, ["list_drones", second], calls


@pytest.mark.parametrize("second,encoding,unknown", [("get_weather", "object", False),
    ("take_off", "object", False), ("take_off", "object", True), ("take_off", "string", False)])
def test_two_distinct_original_calls_bind_once_to_ledger_and_backend(second, encoding, unknown):
    state, barriers, archive, api, tools, _ = fixture(second, encoding, unknown)
    original = deepcopy((state, barriers, archive, api))
    result = audit.audit_parallel_records(state, barriers, archive, api, tools)
    assert result["passed"] and result["actual_mutual_overlap"] and result["unique_call_links"] == 2
    assert {link["tool_call_id"] for link in result["links"]} == {"a", "b"}
    assert (state, barriers, archive, api) == original


@pytest.mark.parametrize("damage", ["duplicate_ledger_id", "missing_ledger_event", "duplicate_barrier_id",
    "duplicate_release_id", "missing_api", "duplicate_api", "api_endpoint", "barrier_endpoint",
    "api_method", "uncommitted", "origin_tool", "origin_kind", "api_actor", "api_altitude",
    "request_id_collision", "raw_duplicate_id", "raw_missing_id", "raw_tool", "raw_arguments",
    "two_model_responses", "interval_not_overlapping", "duplicate_boundary", "asymmetric_overlap",
    "broken_barrier", "incomplete_http", "corrupt_http"])
def test_forged_ambiguous_or_missing_links_never_pass(damage):
    state, barriers, archive, api, tools, calls = fixture("take_off")
    if damage == "duplicate_ledger_id": state["events"][1]["tool_call_id"] = "a"
    elif damage == "missing_ledger_event": state["events"].pop()
    elif damage == "duplicate_barrier_id": barriers[1]["tool_call_id"] = "a"
    elif damage == "duplicate_release_id": barriers[-1]["tool_call_ids"] = ["a", "a"]
    elif damage == "missing_api": api.pop()
    elif damage == "duplicate_api": api.append(deepcopy(api[1]))
    elif damage == "api_endpoint": api[1]["endpoint"] = "/drones/3d229034/command/hover"
    elif damage == "barrier_endpoint": barriers[1]["endpoint"] = "/wrong"
    elif damage == "api_method": api[1]["method"] = "GET"
    elif damage == "uncommitted": api[1]["committed"] = False
    elif damage == "origin_tool": api[1]["origin"]["tool"] = "hover"
    elif damage == "origin_kind": api[1]["origin"]["kind"] = "skill"
    elif damage == "api_actor": api[1]["endpoint"] = "/drones/other/command/take_off"
    elif damage == "api_altitude": api[1]["params"]["altitude"] = 18
    elif damage == "request_id_collision": api[1]["request_id"] = api[0]["request_id"]
    elif damage == "raw_duplicate_id": calls[1]["id"] = "a"; archive = wire(calls)
    elif damage == "raw_missing_id": archive = wire(calls[:1])
    elif damage == "raw_tool": calls[1]["function"]["name"] = "hover"; archive = wire(calls)
    elif damage == "raw_arguments":
        calls[1]["function"]["arguments"] = json.dumps({"input_json": {"drone_id": "3d229034", "altitude": 18}})
        archive = wire(calls)
    elif damage == "two_model_responses": archive = wire(calls[:1]) + wire(calls[1:], "attempt-2", "model-2")
    elif damage == "interval_not_overlapping": state["events"][1].update(start_sequence=4, end_sequence=5)
    elif damage == "duplicate_boundary": state["events"][1]["start_sequence"] = 1
    elif damage == "asymmetric_overlap": state["events"][1]["overlaps"] = []
    elif damage == "broken_barrier": barriers.append({"event": "broken"})
    elif damage == "incomplete_http": archive[-1]["data"]["complete"] = False
    else: archive[0]["data"]["body"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        audit.audit_parallel_records(state, barriers, archive, api, tools)


def test_audit_scope_is_exactly_four_parallel_slots_from_twenty_local_slots():
    assert [(group, ident) for group, ident, _ in audit.CASES] == [
        ("main", "parallel-readonly"), ("main", "parallel-action"), ("main", "parallel-unknown"),
        ("v4_reference", "v4-parallel-readonly")]
    assert set(audit.ENDPOINTS) == {"list_drones", "get_weather", "take_off"}
