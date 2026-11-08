"""End-to-end regression against sealed developer evidence, including failed attempts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_replay(output, *, check=True):
    return subprocess.run(
        [sys.executable, "-m", "thinking_events", "replay", "--output", str(output)],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
        capture_output=True, text=True, check=check,
    )


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    parent = tmp_path_factory.mktemp("g3")
    first, second = parent / "first", parent / "second"
    for output in (first, second):
        run_replay(output)
    return first, second


def json_lines(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def test_all_artifacts_reproduce_and_existing_run_is_preserved(outputs):
    first, second = outputs
    for name in ("sources.json", "turns.jsonl", "attempts.jsonl", "g3.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    prior = (first / "g3.json").read_bytes()
    assert run_replay(first, check=False).returncode != 0
    assert (first / "g3.json").read_bytes() == prior
    for name in ("sources.json", "turns.jsonl", "attempts.jsonl"):
        assert hashlib.sha256((first / name).read_bytes()).hexdigest().encode() in prior


def test_denominators_match_independent_usage_audit(outputs):
    first, _ = outputs
    audit = json.loads((ROOT / "docs/audit/teacher-reasoning-usage-20261108/summary.json").read_bytes())
    turns = json_lines(first / "turns.jsonl")
    attempts = json_lines(first / "attempts.jsonl")
    assert len({(r["batch"], r["episode"], r["logical_call_id"]) for r in turns}) == len(turns)
    sources = json.loads((first / "sources.json").read_bytes())
    assert sources["campaign_seals_sha256"] == "112b0e62173bb16b4605d9385b6ef175db3d1d73f4366e8911ad423b911f458b"
    assert len([s for s in sources["slots"] if s["status"] == "unrun"]) == 1
    for batch, expected in audit["batches"].items():
        rows = [r for r in turns if r["batch"] == batch]
        tried = [r for r in attempts if r["batch"] == batch]
        assert len({r["episode"] for r in rows}) == expected["archives"]
        assert len(tried) == expected["requests"]
        assert len(rows) == expected["responses_with_usage"]
        assert sum(r["reasoning_tokens"] for r in rows) == expected["reasoning_tokens"]
        assert sum(r["reasoning_tokens"] == 0 for r in rows) == expected["responses_with_zero_reasoning"]
        assert sum(r["status"] == "no_response" for r in tried) == len(tried) - len(rows)
    gate = json.loads((first / "g3.json").read_bytes())
    assert sum(r["event_state"] == "start" for r in turns) == 31
    assert [gate["counts"][c] for c in ("A", "B", "U")] == [
        sum(r["event_state"] == state for r in turns) for state in ("event", "E0", "indeterminate")]
    assert sum(gate["counts"][c] for c in ("A", "B", "U")) == 183


@pytest.mark.parametrize("batch,episode,turn,event,reasoning", [
    ("deepseek-cost-pilot-20261103", "search-control", 9, "E1", 933),
    ("deepseek-cost-pilot-20261103", "search-control", 10, "E1", 272),
    ("deepseek-hover-pilot-20261103", "unknown_takeoff-candidate", 5, "E2", 24),
    ("deepseek-cost-pilot-20261103", "unknown_takeoff-candidate", 8, "E4", 0),
])
def test_spec_witnesses_from_real_requests(outputs, batch, episode, turn, event, reasoning):
    rows = json_lines(outputs[0] / "turns.jsonl")
    row, = [r for r in rows if (r["batch"], r["episode"], r["turn"]) == (batch, episode, turn)]
    assert event in {e["type"] for e in row["events"]}
    assert row["reasoning_tokens"] == reasoning
    assert row["labels"]["event"]["requested_think"] is True
    assert row["labels"]["teacher"]["requested_think"] == (reasoning > 0)
    assert row["labels"]["logic"]["requested_think"] is None


def test_event_projection_independent_of_current_reasoning_future_and_private_files(tmp_path, monkeypatch):
    import base64
    import builtins
    import io

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from thinking_events.archive import read_turns, verify_sources
    from thinking_events.events import extract_events

    sources, _ = verify_sources(ROOT)
    source, = [s for s in sources if s["batch"] == "deepseek-hover-pilot-20261103"
               and s["episode"] == "unknown_takeoff-candidate"]
    reference = read_turns(source)[0][4]
    records = json_lines(source["path"])

    # Perturb the accepted fifth reply *after* its decision boundary, keeping
    # the transport envelope consistent. A later real receipt changes as well.
    chunk, = [r["data"] for r in records if r["kind"] == "http_response_chunk"
              and r["data"]["logical_call_id"] == reference["logical_call_id"]]
    end, = [r["data"] for r in records if r["kind"] == "http_response_end"
            and r["data"]["logical_call_id"] == reference["logical_call_id"]]
    body = json.loads(base64.b64decode(chunk["body"]["base64"]))
    body["choices"][0]["message"]["reasoning_content"] = "SYNTHETIC_REASONING_SENTINEL"
    body["usage"]["completion_tokens_details"]["reasoning_tokens"] = 317
    raw = json.dumps(body).encode()
    digest = hashlib.sha256(raw).hexdigest()
    chunk["body"].update(base64=base64.b64encode(raw).decode(), length=len(raw), sha256=digest)
    end.update(length=len(raw), sha256=digest)
    later = next(r for r in records if r["kind"] == "tool_end" and r["sequence"] > reference["request_sequence"])
    later["data"]["output"]["content"] = "SYNTHETIC_FUTURE_RECEIPT"
    fixture = tmp_path / "model-io-fixture.jsonl"
    fixture.write_text("".join(json.dumps(record) + "\n" for record in records))
    copy = {**source, "path": fixture, "archive_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}
    (tmp_path / "scorer-result.json").write_text('{"private": "SYNTHETIC_SCORER_SENTINEL"}')

    def archive_only(path, *args, **kwargs):
        assert Path(path) == fixture, f"Unexpected file access while projecting a turn: {path}"
        return original_open(path, *args, **kwargs)

    original_open = io.open
    monkeypatch.setattr(io, "open", archive_only)
    monkeypatch.setattr(builtins, "open", archive_only)
    changed = read_turns(copy)[0][4]
    assert changed["reasoning_tokens"] == 317 and reference["reasoning_tokens"] == 24
    assert changed["prefix"] == reference["prefix"]
    assert extract_events(changed["prefix"]) == extract_events(reference["prefix"])
