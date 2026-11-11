"""Negative controls for the archived-request and single-description checks."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinking_events import sense_example_prepare as prep


@pytest.fixture
def pair(monkeypatch):
    before = "Original instructions\n        Example: old"
    after = before.partition("Example:")[0] + prep.EXAMPLE
    monkeypatch.setattr(prep, "DESCRIPTIONS", {"A": prep.digest(before.encode()),
                                              "B": prep.digest(after.encode())})
    body = {"messages": [{"role": "user", "content": "sense"}], "tools": [
        {"type": "function", "function": {"name": prep.SENSE, "description": before}},
        {"type": "function", "function": {"name": "list_drones"}}]}
    return body, prep.variant(body)


def test_variant_preserves_wire_order_and_original(pair):
    body, changed = pair
    assert prep.sense_function(body)["description"].endswith("Example: old")
    assert prep.sense_function(changed)["description"].endswith(prep.EXAMPLE)
    assert list(changed) == list(body)
    prep.verify_variant(body, changed)


@pytest.mark.parametrize("difference", ["second_field", "tool_order", "field_order"])
def test_hidden_intervention_stops_preparation(pair, difference):
    body, changed = pair
    if difference == "second_field":
        changed["messages"][0]["content"] += "!"
    if difference == "tool_order":
        changed["tools"].reverse()
    if difference == "field_order":
        changed["messages"][0] = dict(reversed(changed["messages"][0].items()))
    with pytest.raises(ValueError, match="order changed"):
        prep.verify_variant(body, changed)


def test_sdk_request_must_be_unique_and_bytes_must_match(tmp_path):
    body = b'{"b":1,"a":2}'
    sha = prep.digest(body)
    event = {"kind": "http_request", "data": {"body": {"base64": base64.b64encode(body).decode(),
                                                      "length": len(body), "sha256": sha}}}
    archive = tmp_path / "events.jsonl"
    archive.write_text(json.dumps(event) + "\n")
    assert prep.lookup_body(prep.verified_wire_bodies([archive]), sha) == body
    archive.write_text((json.dumps(event) + "\n") * 2)
    with pytest.raises(ValueError, match="found 2"):
        prep.lookup_body(prep.verified_wire_bodies([archive]), sha)
    damaged = deepcopy(event)
    damaged["data"]["body"]["base64"] = base64.b64encode(b'{"a":2,"b":1}').decode()
    archive.write_text(json.dumps(damaged) + "\n")
    with pytest.raises(ValueError, match="digest/length mismatch"):
        prep.verified_wire_bodies([archive])


def test_changed_index_fails_before_reading_campaign_or_weights(tmp_path):
    index = tmp_path / "index.jsonl"
    index.write_text("{}\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prep.prepare(index, tmp_path / "absent-run", tmp_path / "out", tmp_path / "absent-model")
    assert not (tmp_path / "out").exists()
