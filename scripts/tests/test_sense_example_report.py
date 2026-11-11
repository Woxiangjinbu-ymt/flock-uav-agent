from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from flockcue_qwen35 import adapter
import student_input_v2 as v2
from thinking_events import sense_example_report as report


class TextTokens:
    """Fixture with lossless character IDs and the native thinking/EOS boundaries."""

    def __len__(self):
        return 248100

    def convert_tokens_to_ids(self, text):
        assert text == "</think>"
        return 200000

    def encode(self, text):
        parts = text.split("</think>")
        values = list(map(ord, parts[0]))
        for part in parts[1:]:
            values.extend([200000, *map(ord, part)])
        return values

    def decode(self, ids, **kwargs):
        return "".join("</think>" if token == 200000 else chr(token) for token in ids)


TOKENIZER = TextTokens()
TOOLS = [{"type": "function", "function": {"name": name, "parameters": {"type": "object",
    "properties": {"input_json": {"type": "object"}}, "required": ["input_json"]}}}
    for name in (report.SENSE, "move_to")]
PREPARED = v2.prepare_history([{"role": "user", "content": "Check the fleet."}], TOOLS)


def call(name=report.SENSE, body="<parameter=input_json>\n{}\n</parameter>"):
    return f"<tool_call>\n<function={name}>\n{body}\n</function>\n</tool_call>"


@pytest.mark.parametrize(("text", "thinking", "expected"), [
    (call(), False, (1, "sense")),
    (call() + "\n</think>\nNo further action.", True, (0, "skip")),
    (call() + call("move_to", ""), False, (0, "other_call")),
    (call() + call(), False, (1, "sense")),
    (call(body=""), False, (0, "sense_arguments")),
    (call(body="<parameter=detail>summary</parameter>"), False, (0, "sense_arguments")),
    (call() + call("move_to", "").removesuffix("</tool_call>"), False, (0, "other_format")),
    (call(body="") + call("move_to", ""), False, (0, "sense_arguments")),
    (call("move_to", "") + call(body=""), False, (0, "other_call")),
    (call(body="") + " strange suffix", False, (0, "other_format")),
    (call(body="") + "</think>\n" + call(), True, (1, "sense")),
])
def test_entire_group_and_bounded_error_attribution(text, thinking, expected):
    ids = TOKENIZER.encode(text) + [248046]
    score, label, _ = report.score_generation(TOKENIZER, ids, thinking, PREPARED, 2048, adapter.EOS_IDS)
    assert (score, label) == expected


def test_truncated_and_no_output():
    ids = TOKENIZER.encode(call())
    for emitted, limit, kind in [(ids, len(ids), "truncated"), ([], 2048, "no_output")]:
        result = report.score_generation(TOKENIZER, emitted, False, PREPARED, limit, adapter.EOS_IDS)
        assert result[:2] == (0, kind)


def test_partially_observed_pair_does_not_change_complete_pair_count_or_net():
    scores = [(1, 1)] * 2 + [(1, 0)] * 3 + [(0, 1)] * 5 + [(0, 0)] * 38 + [(None, None)] * 2
    def summary(pairs):
        return report.pair_summary([{"A": {"score": a}, "B": {"score": b},
                                     "cell": report._cell(a, b)} for a, b in pairs])

    expected = {"n": 50, "A": 5, "B": 7, "both": 2, "only_a": 3, "only_b": 5, "neither": 38,
                "incomplete": 2, "comparable": 48, "net": 2}
    assert summary(scores) == expected
    scores[-1] = (None, 1)
    assert summary(scores) == dict(expected, B=8)


def test_unknown_usage_and_nearest_rank():
    def row(status, seconds, value):
        return {"status": status, "diagnosis": status, "request_seconds": seconds, "peak_vram_bytes": value,
                **{k: value for k in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")}}
    metrics = report.arm_metrics([row("completed", 1., 0), row("completed", 2., 1),
                                  row("timeout", 120., None), row("missing", None, None)])
    assert metrics["request_seconds"] == {"known": 3, "sum": 123., "p50": 2., "p95": 120.}
    assert metrics["tokens"]["reasoning_tokens"] == {"known": 2, "sum": 1}
    assert metrics["peak_vram_bytes"] == 1 and metrics["peak_vram_known"] == 2
    assert report.arm_metrics([row("timeout", 120., None)])["tokens"]["total_tokens"] == {"sum": None, "known": 0}


def test_report_verifies_claims_missing_receipts_service_and_ledger(tmp_path):
    import json
    from thinking_events.campaign_storage import digest, json_bytes

    for name in ("services", "states", "receipts"):
        (tmp_path / name).mkdir()
    metadata = json_bytes({"eos_ids": [248046, 248044]})
    sha = digest(metadata)
    service = tmp_path / "services" / f"{sha}.json"
    service.write_bytes(metadata)
    receipt = tmp_path / "receipts" / "a.json"
    raw = json_bytes({"slot_key": "a", "service_sha256": sha, "status": "timeout"})
    receipt.write_bytes(raw)
    claim = {"state": "terminal", "receipt_sha256": digest(raw), **json.loads(raw)}
    (tmp_path / "states" / "a.json").write_bytes(json_bytes(claim))
    ledger = {"gpu_seconds": 2, "segments": [{"end_unix": 4, "gpu_seconds": 2}],
              "status_counts": {"timeout": 1, "unstarted": 1}}
    slots = [{"slot_key": "a"}, {"slot_key": "b"}]
    _, receipts = report._verified_evidence(tmp_path, slots, ledger)
    assert set(receipts) == {"a"}
    with pytest.raises(ValueError, match="budget ledger"):
        report._verified_evidence(tmp_path, slots, dict(ledger, status_counts={"unstarted": 2}))
    with pytest.raises(ValueError, match="total GPU"):
        report._verified_evidence(tmp_path, slots, dict(ledger, gpu_seconds=9))
    (tmp_path / "states" / "a.json").write_bytes(json_bytes(dict(claim, receipt_sha256="wrong")))
    with pytest.raises(ValueError, match="final claim"):
        report._verified_evidence(tmp_path, slots, ledger)
    service.write_bytes(b"{}")
    with pytest.raises(ValueError, match="metadata digest"):
        report._verified_evidence(tmp_path, slots, ledger)
    receipt.unlink()
    with pytest.raises(ValueError, match="one final claim and one receipt"):
        report._verified_evidence(tmp_path, slots, ledger)
