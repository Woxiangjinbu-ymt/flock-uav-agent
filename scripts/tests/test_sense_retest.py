"""The live retest changes the visible sense description and preserves the simulator request."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_sense_retest import RetestService
from thinking_events import sense_example_prepare as example


class Capture:
    metadata = {"backend": "fixture"}
    process = None

    def __init__(self):
        self.requests = []

    def request(self, **payload):
        self.requests.append(payload)
        return {"slot": payload["slot"], "turn": payload["turn"]}


@pytest.mark.parametrize("offered", (True, False))
def test_retest_wire_boundary(tmp_path, monkeypatch, offered):
    old = "Instructions\n        Example: old"
    new = old[:old.rindex("Example:")] + example.EXAMPLE
    monkeypatch.setattr(example, "DESCRIPTIONS", {k: example.digest(t.encode()) for k,t in (("A",old), ("B",new))})
    client = Capture()
    service = RetestService(client, tmp_path, example, {"trial"})
    tools = [{"type":"function", "function":{"name":"list_drones","description":"List the fleet"}}]
    if offered:
        tools.append({"type":"function","function":{"name":example.SENSE,"description":old}})
    upstream = {"slot":"trial", "turn":1, "messages":[{"role":"user","content":"mission"}], "tools":tools,
                "seed":7, "thinking":True, "spent_tokens":0, "episode_deadline":567, "request_timeout":120}
    before = deepcopy(upstream)
    assert service.request(**upstream) == {"slot":"trial","turn":1}
    expected = deepcopy(before)
    if offered:
        expected["tools"][-1]["function"]["description"] = new
    assert upstream == before
    assert client.requests == [expected]
    audit = json.loads((tmp_path / "intervention/trial/turn-01.json").read_bytes())
    assert audit["effective_tools_sha256"] == example.digest(example.ordered_bytes(expected["tools"]))
    assert audit["upstream_tools_sha256"] == example.digest(example.ordered_bytes(before["tools"]))
    assert audit["sense_offered"] is offered
    with pytest.raises(ValueError, match="Duplicate intervention"):
        service.request(**upstream)
    with pytest.raises(ValueError, match="outside"):
        service.request(**dict(upstream, slot="reference"))
    assert len(client.requests) == 1
