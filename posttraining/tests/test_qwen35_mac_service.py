"""A late Metal result must never reach the Mac simulator, even if the command is valid."""
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from flockcue_qwen35 import mac_smoke


@pytest.mark.parametrize("complete", [True, False])
def test_request_deadline_never_dispatches_a_late_command(monkeypatch, complete):
    clock = iter((10.0, 20.1))
    monkeypatch.setattr(mac_smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mac_smoke, "render", lambda *args: (
        {"input_ids": [20], "history_reasoning_tokens": 0, "template_sha256": "test"}, {}))
    monkeypatch.setattr(mac_smoke, "parse_generation", lambda *args: {
        "message": {"role": "assistant", "content": "done"}, "reasoning_tokens": 0, "anomalies": [], "terminated": True})
    model = Mock()
    if complete:
        model.generate.return_value = {"first_token_monotonic": 19.1, "timing": {}, "generated_ids": [248046]}
    else:
        model.generate.side_effect = TimeoutError
    service = mac_smoke.LiveService(model, tokenizer=None)
    result = service.request(slot=mac_smoke.SLOT, turn=1, messages=[{"role": "user", "content": "hover"}],
                             tools=[], thinking=False, seed=6, spent_tokens=0, episode_deadline=99.0, request_timeout=10.0)
    model.generate.assert_called_once_with([20], 2048, 6, 20.0)
    model.cancel.assert_called_once_with()
    assert (result["status"], result["stop_reason"], result["message"]) == ("stopped", "request_timeout", None)
    assert result["input_ids"] == [20]
    if complete:
        assert result["late_generation"] and result["usage"]["total_tokens"] == 2
    else:
        assert result["generated_ids"] is None and result["usage"] is None


def test_thinking_run_uses_its_declared_slot_and_mode(monkeypatch):
    model = Mock()
    service = mac_smoke.LiveService(model, tokenizer=None, thinking=True)
    request = dict(slot=mac_smoke.ON_SLOT, turn=1, messages=[{"role": "user", "content": "hover"}],
                   tools=[], thinking=True, seed=6, spent_tokens=0, episode_deadline=float("inf"), request_timeout=10.0)
    for invalid in ({"thinking": False}, {"slot": mac_smoke.SLOT}):
        assert service.request(**{**request, **invalid})["stop_reason"] == "input_rejected"
    model.generate.assert_not_called()
    render = Mock(return_value=({"input_ids": [20], "history_reasoning_tokens": 0, "template_sha256": "test"}, {}))
    parse = Mock(return_value={"message": {"role": "assistant", "content": "done", "reasoning_content": "a"},
                               "reasoning_tokens": 1, "anomalies": [], "terminated": True})
    monkeypatch.setattr(mac_smoke, "render", render)
    monkeypatch.setattr(mac_smoke, "parse_generation", parse)
    model.generate.return_value = {"first_token_monotonic": None, "timing": {}, "generated_ids": [248046]}
    result = service.request(**request)
    render.assert_called_once_with(None, request["messages"], [], True)
    parse.assert_called_once_with(None, [248046], mac_smoke.EOS_IDS, True, {}, 2048)
    assert result["status"] == "ok" and result["thinking"] is True and result["reasoning_tokens"] == 1
