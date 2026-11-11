"""Rebuild the released public teacher decisions from the formal run's sealed HTTP and tool traces."""
import argparse
import base64
from contextlib import ExitStack
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from report_flockcue_deployment600 import provider_payload


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "datasets/teacher/2026-11-11-fold-a/audit"
REVIEWED = "reviewed-compatible-public-turns.jsonl.gz"


def rows(path):
    with path.open("rb") as stream:
        for line in stream:
            yield json.loads(line)


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def must_match(actual, expected, description):
    if actual != expected:
        raise ValueError(f"Release mismatch: {description}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def blob(encoded):
    data = base64.b64decode(encoded["base64"], validate=True)
    must_match((len(data), digest(data)), (encoded["length"], encoded["sha256"]), "HTTP body")
    return data


def read_at(stream, location):
    offset, length = location
    if offset < 0 or length <= 0:
        raise ValueError("Invalid wire coordinates")
    stream.seek(offset)
    data = stream.read(length)
    must_match(len(data), length, "wire range")
    return data


def public_turn(stream, turn):
    attempts = [row for row in turn["attempts"] if row["attempt_id"] == turn["response_attempt_id"]]
    must_match(len(attempts), 1, "successful attempt")
    attempt = attempts[0]
    request = json.loads(read_at(stream, attempt["request"]))["data"]
    request_body = blob(request["body"])
    must_match(digest(request_body), attempt["request_sha256"], "request")
    visible = json.loads(request_body)
    events = [json.loads(line) for line in read_at(stream, attempt["response"]).splitlines()]
    for event in events:
        must_match((event["data"]["attempt_id"], event["data"]["logical_call_id"]),
                   (attempt["attempt_id"], turn["logical_call_id"]), "response identity")
    must_match((events[0]["kind"], events[-1]["kind"]), ("http_response_start", "http_response_end"), "response framing")
    chunks = [blob(event["data"]["body"]) for event in events[1:-1]]
    response = b"".join(chunks)
    must_match((len(chunks), digest(response)), (events[-1]["data"]["chunk_count"], attempt["response_sha256"]), "response")
    replies = provider_payload(response, events[0]["data"])
    must_match(len(replies), 1, "number of replies")
    target = replies[0]["choices"][0]["message"]
    must_match(digest(packed(target)), turn["provider_message_sha256"], "model target")
    receipts = []
    for call in sorted(turn["calls"], key=lambda item: item["receipt"][0]):
        event = json.loads(read_at(stream, call["receipt"]))["data"]
        out = event["output"]
        must_match((event["tool_span_id"], out["tool_call_id"], out["name"], digest(out["content"].encode())),
                   (call["span"], call["id"], call["name"], call["receipt_sha256"]), "tool receipt")
        receipts.append({"tool_call_id": out["tool_call_id"], "name": out["name"],
                         "status": out["status"], "content": out["content"]})
    return {"messages": visible["messages"], "tools": visible["tools"], "target": target, "post_action_receipts": receipts}


def write_packages(audit, workspace, stage, selection):
    summary = json.loads((audit / "summary.json").read_bytes())
    roles = json.loads((audit / "scene-roles.json").read_bytes())["scenes"]
    scene_role = {row["session_id"]: row["role"] for row in roles}
    episodes = {(e["scene_id"], e["task_id"]): e for e in rows(audit / "wire-episodes.jsonl")}
    mapped = {(m["scene_id"], m["task_id"], m["logical_call_id"]): m for m in rows(audit / "public-row-map.jsonl")}
    reviewed = {(r["session_id"], r["task_id"], r["logical_call_id"]): r for r in rows(audit / "reviewed-qwen-projection.jsonl")
                if r["status"] == "compatible"}
    reports = {name: (info["file"], info["rows"], info["uncompressed_sha256"], info["gzip_sha256"])
               for name, info in summary["public_packages"].items()}
    info = summary["reviewed_public_package"]
    reports["reviewed"] = (REVIEWED, info["rows"], info["uncompressed_sha256"], info["sha256"])
    counts = {name: 0 for name in selection}
    sums = {name: hashlib.sha256() for name in selection}
    previous = None
    with ExitStack() as stack, ExitStack() as current:
        streams = {}
        for name in selection:
            file = stack.enter_context((stage / reports[name][0]).open("xb"))
            streams[name] = stack.enter_context(gzip.GzipFile(filename="", fileobj=file, mode="wb", mtime=0,
                                                             compresslevel=5 if name == "reviewed" else 2))
        for turn in rows(audit / "wire-turns.jsonl"):
            if not turn["wire_verified"]:
                continue
            episode_id = turn["scene_id"], turn["task_id"]
            key = (*episode_id, turn["logical_call_id"])
            episode = episodes[episode_id]
            split = episode["split"]
            must_match(scene_role[turn["scene_id"]], {"training": "train", "validation": "internal_validation"}[split], "scene role")
            targets = [split] if split in selection else []
            if "reviewed" in selection and key in reviewed:
                must_match(split, "training", "reviewed sample split")
                targets.append("reviewed")
            if not targets:
                continue
            if previous != episode_id:
                current.close()
                path = (workspace / episode["archive_path"]).resolve()
                if not path.is_relative_to(workspace):
                    raise ValueError(f"Archive outside --workspace: {path}")
                source = current.enter_context(path.open("rb"))
                must_match(hashlib.file_digest(source, "sha256").hexdigest(), episode["archive_sha256"], str(path))
                previous = episode_id
            line = packed(public_turn(source, turn)) + b"\n"
            must_match(digest(line), mapped[key]["row_sha256"], str(key))
            for name in targets:
                counts[name] += 1
                expected_line = reviewed[key]["reviewed_file_line"] if name == "reviewed" else mapped[key]["line"]
                must_match(counts[name], expected_line, "release order")
                streams[name].write(line)
                sums[name].update(line)
    for name in selection:
        filename, count, raw_hash, gzip_hash = reports[name]
        must_match((counts[name], sums[name].hexdigest()), (count, raw_hash), filename)
        must_match(file_digest(stage / filename), gzip_hash, filename + " (gzip)")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=AUDIT, help="released scene, wire and review indices")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="root containing the archived paths in wire-episodes.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="fresh output directory, published only after hash verification")
    parser.add_argument("--package", choices=("reviewed", "training", "validation", "all"), default="reviewed")
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error("--output must be a fresh directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    selection = ("training", "validation", "reviewed") if args.package == "all" else (args.package,)
    with TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp)
        counts = write_packages(args.audit.resolve(), args.workspace.resolve(), stage, selection)
        output.mkdir()
        try:
            for file in stage.iterdir():
                file.rename(output / file.name)
        except BaseException:
            shutil.rmtree(output)
            raise
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
