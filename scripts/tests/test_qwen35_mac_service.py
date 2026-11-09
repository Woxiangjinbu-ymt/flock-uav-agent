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
