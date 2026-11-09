"""Look up one accepted or excluded teacher decision in the original local archive."""
import argparse
import json
from pathlib import Path

from convert_agent_samples import decode, parse, require, safe, sha


ROOT = Path(__file__).resolve().parents[2]
DELIVERY = ROOT / "docs/results/deepseek-sft-dev-examples-20261109"


def inspect(bundle, episode, turn, part):
    ledger = [parse(line) for line in (bundle / "decisions.jsonl").read_bytes().splitlines()]
    matching = [row for row in ledger if (row["source"]["episode"], row["source"]["turn"]) == (episode, turn)]
    require(len(matching) == 1, "expected one decision for this episode and turn")
    row = matching[0]
    source = row["source"]
    raw = safe(ROOT, source["archive_path"]).read_bytes()
    require(sha(raw) == source["archive_sha256"], "original archive digest differs from ledger")
    records = {entry["sequence"]: entry for entry in map(parse, raw.splitlines())}
    pointer = source["request_sequence"]
    chunks = source["response_chunk_sequences"]
    result = {"sample_id": row["sample_id"], "status": row["status"], "reasons": row["reasons"],
              "archive": source["archive_path"], "request_sequence": pointer, "response_chunk_sequences": chunks}
    if part in ("request", "response"):
        is_request = part == "request"
        entries = [records[n] for n in ([pointer] if is_request else chunks)]
        kind = "http_request" if is_request else "http_response_chunk"
        require(entries and all(e["kind"] == kind and e["data"]["attempt_id"] == source["attempt_id"]
                                and e["data"]["logical_call_id"] == source["logical_call_id"]
                                for e in entries), "unexpected HTTP entry at the archived location")
        body = b"".join(decode(e["data"]["body"]) for e in entries)
        require(sha(body) == source[f"{part}_body_sha256"], "HTTP body differs from ledger")
        result[part] = parse(body)
    elif part == "receipts":
        receipts = []
        for receipt in row["evidence"]["current_receipts"]:
            entry = records[receipt["sequence"]]
            data = entry["data"]
            require(entry["kind"] == "tool_end" and data["tool_span_id"] == receipt["span_id"]
                    and data["output"]["tool_call_id"] == receipt["call_id"], "receipt pointer mismatch")
            receipts.append({"sequence": entry["sequence"], "output": data["output"]})
        result["receipts"] = receipts
    else:
        result["tool_names"] = row["evidence"]["tool_names"]
        result["receipt_sequences"] = [r["sequence"] for r in row["evidence"]["current_receipts"]]
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("episode", help="archived trial ID, e.g. unknown_takeoff-candidate")
    cli.add_argument("turn", type=int, help="one-based ordinal within the trial")
    cli.add_argument("--part", choices=["summary", "request", "response", "receipts"], default="summary")
    cli.add_argument("--bundle", type=Path, default=DELIVERY)
    args = cli.parse_args()
    print(json.dumps(inspect(args.bundle, args.episode, args.turn, args.part), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
