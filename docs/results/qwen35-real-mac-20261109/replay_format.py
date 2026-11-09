"""Isolate the argument-envelope error in turn three of the archived Mac run."""
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from flockcue_qwen35.adapter import EOS_IDS, load_tokenizer, parse_generation, render


def replay(run):
    trial = run / "trials/mac-qwen35-off"
    row = json.loads((trial / "private/student-requests.jsonl").read_bytes().splitlines()[2])
    wirelog, = (trial / "policy/modelio").glob("*.jsonl")
    requests = [r["data"] for r in map(json.loads, wirelog.read_bytes().splitlines()) if r["kind"] == "http_request"]
    payload = base64.b64decode(requests[2]["body"]["base64"], validate=True)
    if row["turn"] != 3 or hashlib.sha256(payload).hexdigest() != row["request_body_sha256"]:
        raise ValueError("Expected the third archived SDK request")
    wire = json.loads(payload)
    tool, = [t["function"] for t in wire["tools"] if t["function"]["name"] == "take_off"]
    tok = load_tokenizer(run / "source/training/tokenizers/qwen35-dev")
    prompt, state = render(tok, wire["messages"], wire["tools"], row["thinking"])
    ids = row["generated_ids"]
    original = parse_generation(tok, ids, EOS_IDS, False, state, row["max_new_tokens"])
    if (prompt["input_ids"] != row["input_ids"] or original["anomalies"] != ["tool_arguments"]
            or original["message"] is not None or not original["terminated"]):
        raise ValueError("Could not reproduce the recorded argument error")
    decoded = tok.decode(ids[:-1], skip_special_tokens=False, clean_up_tokenization_spaces=False)
    args = {"drone_id": "3d229034", "altitude": 12.0}
    fields = "\n".join(f"<parameter={k}>\n{v}\n</parameter>" for k, v in args.items())
    if fields not in decoded or decoded.count("<tool_call>") != 1:
        raise ValueError("Recorded output did not contain the expected single flat call")
    wrapped = "<parameter=input_json>\n" + json.dumps(args, separators=(",", ":")) + "\n</parameter>"
    repaired = tok.encode(decoded.replace(fields, wrapped, 1), add_special_tokens=False) + [ids[-1]]
    parsed = parse_generation(tok, repaired, EOS_IDS, False, state, row["max_new_tokens"])
    if not parsed["message"] or parsed["anomalies"]:
        raise ValueError("Changing the argument envelope did not admit this call")
    call, = parsed["message"]["tool_calls"]
    function = call["function"]
    if function["name"] != "take_off" or json.loads(function["arguments"]) != {"input_json": args}:
        raise ValueError("Tool, aircraft or altitude changed")
    return {"task_id": "c74d7020", "turn": row["turn"], "request_body_sha256": row["request_body_sha256"],
            "advertised_tool": tool, "original_generated_ids": ids, "original_terminal_id": ids[-1],
            "original_tool_call": decoded[decoded.index("<tool_call>"):], "original_anomalies": original["anomalies"],
            "parser_only_replay": {"tool": function["name"], "arguments": json.loads(function["arguments"]),
                                   "anomalies": parsed["anomalies"], "dispatched": False}}


if __name__ == "__main__":
    print(json.dumps(replay(Path(sys.argv[1])), ensure_ascii=False, indent=2))
