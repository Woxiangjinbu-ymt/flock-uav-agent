"""Rebuild the reported scores, rerender both tokenizers and verify the sealed Mac fixture run."""
import argparse
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / "outputs/flockcue-g1g2-execution-mac-20261110"


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def verify(run, agent_python, tokenizer):
    run = run.resolve()
    source = run / "source/scripts"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from thinking_events.paired_backend import MAIN, fixture_replies, load_codec

    with TemporaryDirectory() as temp:
        report_dir = Path(temp) / "report"
        subprocess.run([agent_python, "-m", "thinking_events", "report", "--run", run, "--output", report_dir],
                       cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")}, check=True,
                       stdout=subprocess.DEVNULL)
        for name in ("REPORT_CN.md", "gates.json"):
            if (report_dir / name).read_bytes() != (HERE / name).read_bytes():
                raise ValueError(f"Changed report: {name}")
    gates = json.loads((HERE / "gates.json").read_bytes())
    assert gates["scope"] == "local_fixture" and gates["slot_counts"] == {"scored": 116}
    assert gates["formal_G1"] == gates["formal_G2"] == "unmeasured" and not gates["eligible_for_collection"]
    codecs = {}
    arms = Counter()
    verified_ids = linked_receipts = 0
    for slot in rows(run / "slots.jsonl"):
        sid, model, thinking = slot["slot_id"], slot["model"], slot["thinking"]
        arms[model + "/" + slot["arm"]] += 1
        if model not in codecs:
            codecs[model] = load_codec(model, tokenizer if model == MAIN else None)
        tok, codec, _, _ = codecs[model]
        folder = run / "trials" / sid
        requests = rows(folder / "private/student-requests.jsonl")
        wire_file, = (folder / "policy/modelio").glob("*.jsonl")
        archive = rows(wire_file)
        calls = [r["data"]["body"]["base64"] for r in archive if r["kind"] == "http_request"]
        receipt, = [r["data"]["output"] for r in archive if r["kind"] == "tool_end"]
        expected = fixture_replies(model, thinking)
        for turn, (r, encoded, reply) in enumerate(zip(requests, calls, expected, strict=True), 1):
            body = base64.b64decode(encoded, validate=True)
            assert hashlib.sha256(body).hexdigest() == r["request_body_sha256"]
            request = json.loads(body)
            prompt, _ = codec.render(tok, request["messages"], request["tools"], thinking)
            assert prompt["input_ids"] == r["input_ids"] and tok.encode(reply, add_special_tokens=False) == r["generated_ids"]
            assert r["seed"] == slot["turn_seeds"][turn - 1] and r["thinking"] is thinking
            verified_ids += 1
            if turn == 2:
                call, = requests[0]["message"]["tool_calls"]
                tool = request["messages"][-1]
                assert call["function"]["name"] == "list_drones" and tool["role"] == "tool"
                assert tool["tool_call_id"] == call["id"] == receipt["tool_call_id"]
                original = receipt["content"]
                assert receipt["status"] == "success" and json.loads(original)[0]["id"]
                marker = "\n\n[M1-R task evidence; derived from actual tool receipts]\n"
                assert tool["content"] == original or tool["content"].startswith(original + marker)
                linked_receipts += 1
    assert len(arms) == 4 and set(arms.values()) == {29} and verified_ids == 232 and linked_receipts == 116
    return {"campaign_sha256": gates["campaign_sha256"], "plan_sha256": gates["plan_sha256"],
            "report_sha256": hashlib.sha256((HERE / "gates.json").read_bytes()).hexdigest(),
            "scope": gates["scope"], "scored_slots_by_arm": dict(arms), "requests_verified_from_wire": verified_ids,
            "second_turns_with_public_fleet_receipt": linked_receipts, "formal_launches": gates["formal_launches"],
            "formal_G1": gates["formal_G1"], "formal_G2": gates["formal_G2"],
            "eligible_for_collection": gates["eligible_for_collection"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=BASE / "dev-run")
    parser.add_argument("--agent-python", type=Path, default=ROOT / "agent4drone/.venv/bin/python")
    parser.add_argument("--qwen35-tokenizer", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.run, args.agent_python.absolute(), args.qwen35_tokenizer), indent=2))
