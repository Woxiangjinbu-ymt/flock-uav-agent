"""Real-wire fixtures for the sealed development replay's authenticity boundary."""

import base64
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from thinking_events import archive

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inventory():
    return archive.verify_sources(ROOT)


def fixture_source(inventory, batch, episode):
    sources, _ = inventory
    return next(s for s in sources if s["batch"] == archive.BATCHES[batch] and s["episode"] == episode)


def rows_for(source):
    return [json.loads(line) for line in source["path"].read_bytes().splitlines()]


def write_source(tmp_path, source, rows):
    for n, row in enumerate(rows, 1):
        row["sequence"] = n
    raw = b"".join(archive.canonical(r) + b"\n" for r in rows)
    path = tmp_path / "audited-fixture.jsonl"
    path.write_bytes(raw)
    return {**source, "path": path, "archive_sha256": archive.sha(raw)}


def body(value):
    raw = archive.canonical(value)
    return {"base64": base64.b64encode(raw).decode(), "length": len(raw), "sha256": archive.sha(raw)}


def use_request(rows, logical, mutate):
    row = next(r for r in rows if r["kind"] == "http_request" and r["data"]["logical_call_id"] == logical)
    request = json.loads(archive.decode(row["data"]["body"]))
    mutate(request["messages"])
    row["data"]["body"] = body(request)


def use_response(rows, logical, mutate):
    block = {r["kind"]: r["data"] for r in rows if r["data"].get("logical_call_id") == logical}
    response = json.loads(archive.decode(block["http_response_chunk"]["body"]))
    mutate(response)
    packed = body(response)
    block["http_response_chunk"]["body"] = packed
    block["http_response_end"].update(length=packed["length"], sha256=packed["sha256"])


def test_real_source_identity_and_coverage(inventory):
    sources, manifest = inventory
    assert manifest["campaign_seals_sha256"] == archive.SEAL_DIGEST
    assert [sum(s["batch"] == name for s in sources) for name in archive.BATCHES] == [5, 8, 10, 8]
    assert [s["episode"] for s in manifest["slots"] if s["status"] == "unrun"] == ["7eba7390-integrated"]
    assert sum(s["condition"] == "injected" for s in sources) == 6
    rows, attempts = [], []
    for source in sources:
        t, a = archive.read_turns(source)
        rows.extend(t)
        attempts.extend(a)
    assert len(rows) == 214
    assert Counter(r["status"] for r in attempts) == {"response": 214, "no_response": 4}
    assert sum(r["reasoning_tokens"] for r in rows) == 53319
    assert sum(r["reasoning_tokens"] == 0 for r in rows) == 69
    assert all(not r["prefix"]["gaps"] and not r["prefix"]["history_gaps"] for r in rows)


@pytest.mark.parametrize("name,reason", [
    ("campaign-seal.json", "campaign_seals_pin_mismatch"),
    ("worker-exit-seals/c00d8b78-baseline.json", "seal_hash_mismatch"),
])
def test_changed_seal_is_rejected(tmp_path, monkeypatch, name, reason):
    changed = tmp_path / "corrupt.json"
    changed.write_text("{}")
    safe = archive.safe

    def redirect(root, rel):
        if rel == name and root.parent.name == archive.BATCHES[0]:
            return changed
        return safe(root, rel)
    monkeypatch.setattr(archive, "safe", redirect)
    with pytest.raises(ValueError, match=reason):
        archive.verify_sources(ROOT)


def test_swapped_archive_owner_is_rejected(inventory, tmp_path):
    source = inventory[0][0]
    rows = rows_for(source)
    rows[2]["episode_id"] = "different-worker"
    with pytest.raises(ValueError, match="sequence_or_owner"):
        archive.read_turns(write_source(tmp_path, source, rows))


@pytest.mark.parametrize("case", ["chunk_hash", "request_hash", "aggregate", "chunk_index"])
def test_corrupt_wire_stays_in_attempt_ledger(inventory, tmp_path, case):
    source = fixture_source(inventory, 2, "unknown_takeoff-candidate")
    rows = rows_for(source)
    logical = rows[1]["data"]["logical_call_id"]
    data = {r["kind"]: r["data"] for r in rows if r["data"].get("logical_call_id") == logical}
    if case == "aggregate":
        data["http_response_end"]["sha256"] = "0" * 64
    elif case == "chunk_index":
        data["http_response_chunk"]["chunk_index"] = 2
    else:
        part = "http_request" if case == "request_hash" else "http_response_chunk"
        data[part]["body"]["sha256"] = "0" * 64
    turns, attempts = archive.read_turns(write_source(tmp_path, source, rows))
    assert attempts[0]["status"] == "invalid" and attempts[0]["reason"]
    assert not any(r["logical_call_id"] == logical for r in turns)


def test_retry_and_missing_usage_are_not_zero(inventory, tmp_path):
    source = fixture_source(inventory, 2, "unknown_takeoff-candidate")
    rows = rows_for(source)
    req = next(i for i,r in enumerate(rows) if r["kind"] == "http_request")
    earlier = json.loads(json.dumps(rows[req]))
    earlier["data"]["attempt_id"] = "retry-predecessor"
    end = {**earlier, "kind": "http_response_end",
           "data": {"attempt_id": "retry-predecessor", "logical_call_id": earlier["data"]["logical_call_id"],
                    "complete": False, "status": "partial", "response_observed": False, "chunk_count": 0,
                    "length": 0, "sha256": archive.sha(b"")}}
    rows[req:req] = [earlier, end]
    use_response(rows, "model-1", lambda r: r.pop("usage"))
    turns, attempts = archive.read_turns(write_source(tmp_path, source, rows))
    assert turns[0]["reasoning_tokens"] is None and turns[0]["turn"] == 1
    assert attempts[0]["status"] == "no_response" and attempts[1]["status"] == "response"
    assert len([r for r in turns if r["logical_call_id"] == "model-1"]) == 1
    assert turns[7]["reasoning_tokens"] == 0


def test_wire_order_missing_duplicate_and_clipped_receipts(inventory, tmp_path):
    source = fixture_source(inventory, 0, "7eba7390-baseline")
    base_turns, _ = archive.read_turns(source)
    target = base_turns[3]
    latest = target["prefix"]["batches"][-1]
    assert len(latest) == 5 and len({r["tool"] for r in latest}) == 1

    def replay(mutate):
        rows = rows_for(source)
        use_request(rows, target["logical_call_id"], mutate)
        turns, _ = archive.read_turns(write_source(tmp_path, source, rows))
        return turns[3]["prefix"]

    flipped = replay(lambda msgs: msgs.__setitem__(slice(-5, None), msgs[-5:][::-1]))
    assert [(r["call_id"], r["arguments"]) for r in flipped["batches"][-1]] == [
        (r["call_id"], r["arguments"]) for r in reversed(latest)]
    missing = replay(lambda msgs: msgs.pop())
    assert missing["gaps"] == ["missing_visible_receipt:" + latest[-1]["call_id"]]
    duplicate = replay(lambda msgs: msgs.append(msgs[-1]))
    assert duplicate["gaps"] and latest[-1]["call_id"] not in [r["call_id"] for r in duplicate["batches"][-1]]
    clipped = replay(lambda msgs: msgs.__delitem__(slice(-6, None)))
    assert clipped["batches"][-1] == [] and clipped["history_gaps"] and clipped["gaps"]


def test_decoration_and_future_data_isolation(inventory, tmp_path):
    source = fixture_source(inventory, 2, "search-control")
    turns, _ = archive.read_turns(source)
    t = next(
        r for r in turns if r["prefix"]["batches"] and any(x["transformation"] != "none" for x in r["prefix"]["batches"][-1]))
    receipt = next(x for x in t["prefix"]["batches"][-1] if x["transformation"] != "none")
    assert receipt["wire_content_sha256"] != receipt["raw_content_sha256"]
    idx, logical = t["turn"] - 1, t["logical_call_id"]
    rows = rows_for(source)
    use_response(rows, logical, lambda r: r["choices"][0]["message"].update(reasoning_content="replacement"))
    next_tool = next(r for r in rows if r["kind"] == "tool_end" and r["sequence"] > t["request_sequence"])
    next_tool["data"]["output"]["content"] = "changed future receipt"
    modified, _ = archive.read_turns(write_source(tmp_path, source, rows))
    assert archive.canonical(modified[idx]["prefix"]) == archive.canonical(t["prefix"])
    use_request(rows, logical, lambda msgs: msgs[receipt["wire_message_index"]].update(content="unknown rewrite"))
    modified, _ = archive.read_turns(write_source(tmp_path, source, rows))
    assert "unknown_receipt_rewrite:" + receipt["call_id"] in modified[idx]["prefix"]["gaps"]


def test_no_terminal_evidence_files_read(monkeypatch):
    opened = []
    original = Path.open

    def inspect(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", inspect)
    sources, _ = archive.verify_sources(ROOT)
    archive.read_turns(sources[0])
    assert opened and all(not {"private", "evaluation"}.intersection(path.relative_to(ROOT).parts) and
                          path.name not in {"task-state.json", "execution-state.json", "results.json"} for path in opened)


def test_unassociated_http_reply_is_audited_without_manufacturing_decisions(inventory, tmp_path):
    source = fixture_source(inventory, 2, "unknown_takeoff-candidate")
    rows = rows_for(source)
    orphan = json.loads(json.dumps(next(r for r in rows if r["kind"] == "http_response_chunk")))
    orphan["data"].update(logical_call_id="unknown-model", attempt_id="unknown-attempt")
    rows.insert(-1, orphan)
    decisions, attempts = archive.read_turns(write_source(tmp_path, source, rows))
    assert len(decisions) + 1 == len(attempts)
    assert attempts[-1]["status"] == "invalid" and attempts[-1]["reason"] == "orphan_http_response"


def test_split_wire_chunk_reconstructs_the_same_reply(inventory, tmp_path):
    source = fixture_source(inventory, 3, "unknown_takeoff-candidate")
    rows = rows_for(source)
    index = next(i for i, r in enumerate(rows) if r["kind"] == "http_response_chunk")
    chunk = rows[index]
    raw = archive.decode(chunk["data"]["body"])
    fragments = []
    for ordinal, part in enumerate((raw[:11], raw[11:]), 1):
        packed = {"base64": base64.b64encode(part).decode(), "length": len(part), "sha256": archive.sha(part)}
        fragments.append({**chunk, "data": {**chunk["data"], "chunk_index": ordinal, "body": packed}})
    rows[index + 1]["data"]["chunk_count"] = 2
    rows[index:index + 1] = fragments
    original, _ = archive.read_turns(source)
    replayed, ledger = archive.read_turns(write_source(tmp_path, source, rows))
    assert [(r["logical_call_id"], r["reasoning_tokens"], r["has_reasoning_text"]) for r in original] == [
        (r["logical_call_id"], r["reasoning_tokens"], r["has_reasoning_text"]) for r in replayed]
    assert all(r["status"] == "response" for r in ledger)
