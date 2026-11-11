"""The optional envelope applies only to a fully parsed sense call."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
from thinking_events import sense_default, sense_example_prepare as example


def test_public_tool_change_is_limited_to_sense(monkeypatch):
    old = "Query locally.\n        Example: original"
    enabled = old[:old.index("Example:")] + example.EXAMPLE
    monkeypatch.setattr(example, "DESCRIPTIONS", {"A": example.digest(old.encode()),
                                                  "B": example.digest(enabled.encode())})
    contract = {"type": "object", "properties": {"input_json": {"type": "object"}}, "required": ["input_json"]}
    action = {"type": "function", "function": {"name": "take_off", "description": "Take off", "parameters": contract}}
    sense = {"type": "function", "function": {"name": example.SENSE, "description": old, "parameters": contract}}
    # Each boundary sees a fresh deserialization of the actual SDK request.
    original = json.loads(json.dumps([action, sense]))
    before = deepcopy(original)
    assert sense_default.effective_tools([action]) == [action]
    result = sense_default.effective_tools(original)
    desired = deepcopy(before[1])
    del desired["function"]["parameters"]["required"]
    desired["function"]["description"] = "Query locally.\n        " + sense_default.DEFAULT_EXAMPLE
    assert result == [before[0], desired]
    assert original == before


@pytest.mark.parametrize("args", ['{"input_json":{}}', '{"input_json":{"drone_ids":["d1"]}}',
                                  '{"input_json":"{}"}'])
def test_execution_default_preserves_simultaneous_calls_and_nonempty_envelopes(args):
    def call(name, arguments, i):
        return {"type": "function", "id": f"call_{i}", "function": {"name": name, "arguments": arguments}}
    msg = {"role":"assistant", "content":"", "reasoning_content":"Sense the fleet.",
           "tool_calls":[call(example.SENSE, args, 1), call(example.SENSE, "{}", 2), call("list_drones", "{}", 3)]}
    before = deepcopy(msg)
    actual = sense_default.execution_message(msg)
    expected = deepcopy(before)
    expected["tool_calls"][1]["function"]["arguments"] = '{"input_json":{}}'
    assert actual == expected
    assert msg == before
    assert sense_default.execution_message(None) is None
