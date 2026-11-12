"""Replay the registered terminal HTTP requests once, with the original official DeepSeek teacher."""
import argparse
import base64
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

from runtime import executor


DATA = executor.ROOT / "datasets"
TEACHER = DATA / "teacher/2026-11-11-fold-a"
REVIEW = TEACHER / "audit/closing-repair-review.json"


def lines(path):
    with path.open("rb") as stream:
        yield from map(json.loads, stream)


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def metadata_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def originals():
    rejected = [r for r in json.loads(REVIEW.read_bytes())["tasks"] if r["verdict"] == "recollect"]
    episodes = {(r["scene_id"], r["task_id"]): r for r in lines(TEACHER / "audit/wire-episodes.jsonl")
                if r["split"] == "training"}
    turns = {(r["scene_id"], r["task_id"], r["turn"]): r for r in lines(TEACHER / "audit/wire-turns.jsonl")}
    for review in rejected:
        scene, task, number = review["scene_id"], review["task_id"], review["terminal_turn"]
        episode = episodes[scene, task]
        turn = turns[scene, task, number]
        if not (episode["archive_complete"] and number == episode["logical_turns"] and
                turn["wire_verified"] and turn["finish_reason"] == "stop" and turn["model"] == executor.MODEL):
            raise ValueError(f"Invalid frozen terminal turn for {task}")
        attempt, = [a for a in turn["attempts"] if a["attempt_id"] == turn["response_attempt_id"]]
        root = TEACHER / "raw" / scene
        archive = root / "agent-traces/modelio" / Path(episode["archive_path"]).name
        if sha256(archive.read_bytes()).hexdigest() != review["archive_sha256"]:
            raise ValueError(f"Changed source archive for {task}")
        with archive.open("rb") as stream:
            offset, length = attempt["request"]
            stream.seek(offset)
            event = json.loads(stream.read(length))["data"]
        raw = base64.b64decode(event["body"]["base64"], validate=True)
        if sha256(raw).hexdigest() != attempt["request_sha256"]:
            raise ValueError(f"Changed request for {task}")
        body = json.loads(raw)
        if set(body) != {"model", "stream", "temperature", "tools", "messages"} or (
            body["model"], body["stream"], body["temperature"], len(body["tools"]), body["messages"][-1]["role"]
        ) != (executor.MODEL, False, 0.1, 31, "tool"):
            raise ValueError(f"Unexpected teacher request configuration for {task}")
        dispatch, = (DATA / "results/benchmark-600/raw").glob(f"*/{scene}/{task}.window.json")
        window = json.loads(dispatch.read_bytes())
        starts = [r["timestamp"] for r in lines(root / "agent-traces/reproduction-metrics.jsonl")
                  if r.get("command_id") == episode["command_id"] and r["event"] == "llm_start"]
        if len(starts) != number:
            raise ValueError(f"Cannot determine original remaining time for {task}")
        remaining = window["cutoff_at"] - starts[-1]
        if not 0 < remaining <= executor.TASK_TIMEOUT_SECONDS:
            raise ValueError(f"Invalid remaining time for {task}")
        yield task, raw, {"scene_id": scene, "task_id": task, "turn": number,
                          "original_request_sha256": attempt["request_sha256"],
                          "original_fingerprint": turn["fingerprint"], "remaining_seconds": remaining}


def sample(task, raw, meta, destination, key):
    endpoint = executor.GATEWAY + "/chat/completions"
    write_new(destination / f"{task}.request.json", raw)
    request = urllib.request.Request(endpoint, data=raw, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept-Encoding": "identity"})
    meta = dict(meta, sent_at=datetime.now(timezone.utc).isoformat(), endpoint=endpoint)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=meta["remaining_seconds"]) as response:
            content = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                content = gzip.decompress(content)
            if response.geturl() != endpoint:
                raise ValueError("Unexpected response origin")
            meta["http_status"] = response.status
        write_new(destination / f"{task}.response.json", content)
        value = json.loads(content)
        meta.update(response_sha256=sha256(content).hexdigest(), response_id=value["id"],
                    model=value["model"], fingerprint=value.get("system_fingerprint"),
                    finish=value["choices"][0]["finish_reason"], usage=value.get("usage"))
    except urllib.error.HTTPError as exc:
        meta.update(http_status=exc.code, error="HTTPError")
        raise
    finally:
        meta["elapsed_seconds"] = time.monotonic() - start
        write_new(destination / f"{task}.meta.json", metadata_bytes(meta))
    if meta["model"] != executor.MODEL or meta["fingerprint"] != meta["original_fingerprint"]:
        raise ValueError(f"Different teacher returned for {task}")
    print(task, meta["finish"], meta["fingerprint"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new external archive directory")
    args = parser.parse_args()
    cases = list(originals())
    if len(cases) != 11:
        raise ValueError("Expected eleven registered terminal turns")
    key = executor.provider_key()
    if not key:
        raise ValueError("No official DeepSeek credential in runtime settings")
    args.output.mkdir(parents=True, exist_ok=False)
    for task, raw, meta in cases:
        sample(task, raw, meta, args.output, key)


if __name__ == "__main__":
    main()
