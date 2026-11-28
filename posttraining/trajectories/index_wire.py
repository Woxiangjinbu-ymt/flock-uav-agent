"""Rebuild the sealed HTTP and tool evidence indices for the first formal FlockCue A fold."""

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from flockcue.provider import execution_history
from flockcue.recording.compact_receipts import compact_receipts

from .rebuild import AUDIT, ROOT, blob, digest, file_digest, must_match, packed, provider_payload


EXAMPLE = ("04cd6269", "a05a871a")
EVIDENCE_HEADER = "\n\n[M1-R task evidence; derived from actual tool receipts]\n"
PRIVATE_MARKERS = (
    b"/check/",
    b"execution_check_apis",
    b"related_apis",
    b"mark-done",
    b"world.pkl",
    b"checker_sha256",
    b"score-world.pkl",
    b"eligibility.json",
    b"initial-score.json",
    b"boundary.json",
    b"is_passed",
)
OUTPUT_FILES = ("wire-turns.jsonl", "wire-episodes.jsonl", "public-example.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_bytes())


def sealed_bytes(folder, files, path):
    data = path.read_bytes()
    must_match(digest(data), files[path.relative_to(folder).as_posix()], str(path))
    return data


def arguments(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return ["invalid_raw_tool_arguments", value]


def normalized(message):
    return [
        message.get("content") or "",
        message.get("reasoning_content"),
        [
            (call["id"], call["function"]["name"], arguments(call["function"]["arguments"]))
            for call in message.get("tool_calls") or []
        ],
    ]


class WireEvidence:
    """Join per-attempt HTTP bytes, handler returns and tool spans in archive order."""

    def __init__(self, episode, command, annotations, example):
        self.episode = episode
        self.command = command
        self.annotations = annotations
        self.example = example
        self.models = {}
        self.attempts = {}
        self.spans = {}
        self.receipts = {}
        self.history = []
        self.call_ids = set()
        self.context_events = {}

    def model_start(self, logical_id):
        require(logical_id not in self.models, "Duplicate logical model")
        self.models[logical_id] = {
            "scene_id": self.episode["scene_id"],
            "task_id": self.episode["task_id"],
            "turn": len(self.models) + 1,
            "logical_call_id": logical_id,
            "attempts": [],
            "calls": [],
            "issues": [],
            "handler_returned": False,
            "example": None,
        }

    def request(self, event, location):
        attempt_id, logical_id = event["attempt_id"], event["logical_call_id"]
        require(attempt_id not in self.attempts, "Duplicate HTTP attempt")
        must_match(event["body_scope"], "serialized_http_request_content", "request scope")
        body = blob(event["body"])
        request = json.loads(body)
        require(
            request["model"] == "deepseek-flash"
            and request["stream"] is False
            and request["temperature"] == 0.1,
            "Request protocol differs",
        )
        model = self.models[logical_id]
        messages = request["messages"]
        issues = model["issues"]
        if logical_id in self.context_events:
            context = self.context_events[logical_id]
            if context["window"].get("compact_receipts"):
                original = [{**msg, "content": self.receipts.get(msg["tool_call_id"], msg["content"])}
                            if msg["role"] == "tool" else msg for msg in messages]
                if compact_receipts(original) != messages:
                    issues.append("visible_receipt_projection_differs")
                messages = original
            messages = list(messages)
            for index, original in context["omitted"]:
                messages.insert(index, original)
        if any(marker in body for marker in PRIVATE_MARKERS):
            issues.append("private_marker_in_public_request")
        users = [m.get("content") for m in messages if m.get("role") == "user"]
        if users != [self.command + self.annotations.get(None, "")]:
            issues.append("cannot_verify_public_command_annotation")
        if [normalized(m) for m in messages if m.get("role") == "assistant"] != self.history:
            issues.append("visible_assistant_history_differs")
        expected_calls = Counter(call[0] for step in self.history for call in step[2])
        history_tools = [m for m in messages if m.get("role") == "tool"]
        if Counter(m["tool_call_id"] for m in history_tools) != expected_calls:
            issues.append("visible_call_id_set_differs")
        for tool in history_tools:
            if self.receipts.get(tool["tool_call_id"]) != tool["content"]:
                issues.append("visible_tool_receipt_unverified")

        index = {
            "attempt_id": attempt_id,
            "request": location,
            "request_sha256": digest(body),
            "request_bytes": len(body),
            "status_code": None,
            "response": None,
            "response_sha256": None,
        }
        self.attempts[attempt_id] = {"index": index, "logical": logical_id, "header": None, "chunks": []}
        model["attempts"].append(index)
        model["visible_message_count"] = len(request["messages"])
        if self.example:
            model["example"] = {"request": request, "target": None, "receipts": []}

    def response(self, kind, event, location):
        attempt_id, logical_id = event["attempt_id"], event["logical_call_id"]
        attempt = self.attempts[attempt_id]
        must_match(attempt["logical"], logical_id, "HTTP response ownership")
        index = attempt["index"]
        if kind == "http_response_start":
            require(attempt["header"] is None, "Duplicate response start")
            attempt["header"] = event
            attempt["start"] = location[0]
            index["status_code"] = event["status_code"]
            return
        if kind == "http_response_chunk":
            must_match(event["chunk_index"], len(attempt["chunks"]) + 1, "chunk order")
            attempt["chunks"].append(blob(event["body"]))
            return
        must_match(kind, "http_response_end", "HTTP event type")
        if not event["complete"] or not event["response_observed"] or not attempt["header"]:
            index["transport_error"] = event.get("error_type") or event["status"]
            return
        body = b"".join(attempt["chunks"])
        body_hash = digest(body)
        must_match(
            (len(body), body_hash, len(attempt["chunks"])),
            (event["length"], event["sha256"], event["chunk_count"]),
            "response integrity",
        )
        index["response"] = [attempt["start"], sum(location) - attempt["start"]]
        index["response_sha256"] = body_hash
        if 200 <= index["status_code"] < 300:
            replies = provider_payload(body, attempt["header"])
            require(len(replies) == 1, "Non-buffered response")
            self.provider_reply(self.models[logical_id], attempt_id, replies[0])

    def provider_reply(self, model, attempt_id, reply):
        require(
            reply["model"] == "deepseek-flash"
            and len(reply["choices"]) == 1
            and reply["choices"][0]["index"] == 0,
            "Provider identity or choices differ",
        )
        choice = reply["choices"][0]
        target = choice["message"]
        must_match(target["role"], "assistant", "provider target role")
        if "response_attempt_id" in model:
            model["issues"].append("multiple_success_responses")
        executed = execution_history(target)
        model["response_attempt_id"] = attempt_id
        model["finish_reason"] = choice["finish_reason"]
        model["model"] = reply["model"]
        model["fingerprint"] = reply.get("system_fingerprint")
        model["usage"] = reply["usage"]
        if any(marker in packed(target) for marker in PRIVATE_MARKERS):
            model["issues"].append("private_marker_in_teacher_reply")
        calls = target.get("tool_calls") or []
        if target.get("refusal") or choice["finish_reason"] != ("tool_calls" if calls else "stop"):
            model["issues"].append("incomplete_teacher_target")
        model["provider_message_sha256"] = digest(packed(target))
        model["expected_msg"] = normalized(executed)
        if self.example:
            model["example"]["target"] = target
        for raw, action in zip(calls, executed.get("tool_calls") or [], strict=True):
            raw_args = raw["function"]["arguments"]
            if not isinstance(arguments(raw_args), dict):
                model["issues"].append("invalid_raw_tool_arguments")
            call_id = raw["id"]
            require(call_id not in self.call_ids, "Provider reused call ID")
            self.call_ids.add(call_id)
            model["calls"].append(
                {
                    "id": call_id,
                    "name": raw["function"]["name"],
                    "raw_arguments_sha256": digest(raw_args.encode()),
                    "exec_args": arguments(action["function"]["arguments"]),
                    "span": None,
                    "receipt": None,
                    "receipt_sha256": None,
                    "receipt_status": None,
                }
            )

    def model_end(self, event, sequence):
        model = self.models[event["logical_call_id"]]
        model["handler_returned"] = event["status"] == "returned"
        model["model_end_sequence"] = sequence
        if not model["handler_returned"] or not model.get("response_attempt_id"):
            model["issues"].append("no_returned_teacher_decision")
            return
        expected = model["expected_msg"]
        result = event["output"]["result"]
        message = result[0] if result and isinstance(result, list) else {}
        calls = [(c["id"], c["name"], c["args"]) for c in message.get("tool_calls") or []]
        if (
            message.get("type") != "ai"
            or message.get("invalid_tool_calls")
            or (message.get("content") or "") != expected[0]
            or calls != expected[2]
        ):
            model["issues"].append("wire_handler_action_differs")
        self.history.append(expected)

    def tool_start(self, event, location, sequence):
        call_id, origin, span = (
            event["raw_tool_call_id"],
            event.get("source_model_id"),
            event["tool_span_id"],
        )
        require(span not in self.spans, "Duplicate tool span")
        model = self.models.get(origin)
        call = next((c for c in model["calls"] if c["id"] == call_id), None) if model else None
        inputs = event["input"]
        matched = (
            call is not None
            and call["span"] is None
            and 1 <= model.get("model_end_sequence", sequence) < sequence
            and event["source_relation"] == "unique"
            and event["source_candidates"] == [origin]
            and inputs.get("id") == call_id
            and inputs.get("name") == call["name"]
            and inputs.get("args") == call["exec_args"]
        )
        self.spans[span] = (model, call, matched)
        if matched:
            call["span"] = span
            call["start"] = location
        else:
            self.episode["issues"].append("unbound_tool_start")
            if model:
                model["issues"].append("unbound_tool_start")

    def tool_end(self, event, location):
        model, call, matched = self.spans[event["tool_span_id"]]
        if not matched:
            return
        call_id = call["id"]
        output = event.get("output") or {}
        if not (
            event.get("status") == "returned"
            and event.get("tool_call_id_mismatch") is False
            and not event.get("error_type")
            and output.get("type") == "tool"
            and output.get("name") == call["name"]
            and output.get("tool_call_id") == call_id
            and isinstance(output.get("content"), str)
            and output.get("status") in ("error", "success")
        ):
            model["issues"].append("tool_receipt_unverified")
            return
        require(call["receipt"] is None, "Duplicate tool end")
        call["receipt"] = location
        call["receipt_sha256"] = digest(output["content"].encode())
        call["receipt_status"] = output["status"]
        self.receipts[call_id] = output["content"] + self.annotations.get(call_id, "")
        if self.example:
            model["example"]["receipts"].append(
                {
                    "tool_call_id": call_id,
                    "name": call["name"],
                    "status": output["status"],
                    "content": output["content"],
                }
            )

    def finalized(self):
        example_turns = []
        for model in self.models.values():
            if any(c["receipt"] is None for c in model["calls"]):
                model["issues"].append("missing_tool_receipt")
            if not model["handler_returned"]:
                model["issues"].append("no_returned_teacher_decision")
            for attempt in model["attempts"]:
                if attempt["response"] is None:
                    attempt.setdefault("transport_error", "no_complete_response")
            model["issues"] = sorted(set(model["issues"]))
            model["wire_verified"] = not model["issues"]
            model.pop("expected_msg", None)
            model.pop("model_end_sequence", None)
            for call in model["calls"]:
                del call["exec_args"]
            example = model.pop("example")
            if self.example:
                require(example is not None and model["wire_verified"], "Unverified example")
                example_turns.append(example)
        return list(self.models.values()), example_turns


def read_archive(path, episode, command, annotations, example):
    evidence = WireEvidence(episode, command, annotations, example)
    hasher = hashlib.sha256()
    offset = 0
    archive_id = None
    with path.open("rb") as stream:
        for sequence, line in enumerate(stream, 1):
            require(line.endswith(b"\n"), "Incomplete archive line")
            location = [offset, len(line)]
            offset += len(line)
            hasher.update(line)
            row = json.loads(line)
            event, kind = row["data"], row["kind"]
            if sequence == 1:
                must_match(kind, "archive_start", "first archive event")
                archive_id = row["archive_id"]
            must_match(
                (row["command_id"], row["sequence"], row["archive_id"]),
                (episode["command_id"], sequence, archive_id),
                "archive identity or order",
            )
            if kind == "archive_start":
                require(sequence == 1, "Duplicate archive start")
            elif kind == "archive_end":
                episode["archive_complete"] = event["complete"]
                episode["archive_end"] = event["reason"]
            elif kind == "model_start":
                evidence.model_start(event["logical_call_id"])
            elif kind == "student_context":
                logical_id = event["logical_call_id"]
                evidence.context_events[logical_id] = event
                evidence.models[logical_id]["student_context"] = event["window"]
            elif kind == "http_request":
                evidence.request(event, location)
            elif kind.startswith("http_response_"):
                evidence.response(kind, event, location)
            elif kind == "model_end":
                evidence.model_end(event, sequence)
            elif kind == "tool_start":
                evidence.tool_start(event, location, sequence)
            elif kind == "tool_end":
                evidence.tool_end(event, location)
            else:
                raise ValueError(f"Unexpected archive event: {kind}")
    require(archive_id is not None, "Empty archive")
    must_match(hasher.hexdigest(), episode["archive_sha256"], "archive seal")
    models, sample = evidence.finalized()
    episode["archive_id"] = archive_id
    episode["archive_bytes"] = offset
    episode["issues"] = sorted(set(episode["issues"]))
    episode["logical_turns"] = len(models)
    episode["verified_turns"] = sum(model["wire_verified"] for model in models)
    episode["http_attempts"] = len(evidence.attempts)
    return models, sample


def annotations_for(folder, files, command_id, command):
    chains = list((folder / "agent-traces/toolchains").glob(f"*_{command_id}_*.json"))
    require(len(chains) <= 1, "Multiple toolchains")
    annotations = {}
    for path in chains:
        trace = json.loads(sealed_bytes(folder, files, path))
        must_match((trace["command_id"], trace["command"]), (command_id, command), "toolchain task")
        for row in trace["task_progress"]["task_evidence"]["injections"]:
            call_id = row["tool_call_id"]
            require(
                call_id not in annotations and row["text"].startswith(EVIDENCE_HEADER),
                "Unexpected tool evidence annotation",
            )
            annotations[call_id] = row["text"]
    return annotations


def scene_indices(registered_run, folder, files, scene_id, split, task_ids=None):
    commands = defaultdict(set)
    metrics = folder / "agent-traces/reproduction-metrics.jsonl"
    for line in sealed_bytes(folder, files, metrics).splitlines():
        row = json.loads(line)
        if row.get("job_id") and row.get("command_id"):
            commands[row["job_id"]].add(row["command_id"])
    archives = {}
    for path in (folder / "agent-traces/modelio").glob("*.jsonl"):
        with path.open("rb") as stream:
            command_id = json.loads(stream.readline())["command_id"]
        require(command_id not in archives, "Duplicate task archive")
        archives[command_id] = path
    results = json.loads(sealed_bytes(folder, files, folder / "results.json"))
    require(len(results) == 20, f"Expected one 20-task scene: {scene_id}")
    for index, row in enumerate(results, 1):
        if task_ids is not None and row["task_id"] not in task_ids:
            continue
        task_id, job_id = row["task_id"], row["job_id"]
        require(row["session_id"] == scene_id and len(commands[job_id]) == 1, "Task/job binding")
        (command_id,) = commands[job_id]
        job = json.loads(sealed_bytes(folder, files, folder / f"{task_id}.job.json"))
        must_match(job["job_id"], job_id, "job identity")
        annotations = annotations_for(folder, files, command_id, job["command"])
        path = archives[command_id]
        episode = {
            "scene_id": scene_id,
            "task_id": task_id,
            "job_id": job_id,
            "command_id": command_id,
            "archive_path": (registered_run / scene_id / path.relative_to(folder)).as_posix(),
            "archive_sha256": files[path.relative_to(folder).as_posix()],
            "split": split,
            "archive_complete": False,
            "issues": [],
        }
        models, sample = read_archive(
            path, episode, job["command"], annotations, (scene_id, task_id) == EXAMPLE
        )
        episode["task_index"] = index
        episode["tool_calls"] = sum(len(m["calls"]) for m in models)
        episode["id_bound_receipts"] = sum(c["receipt"] is not None for m in models for c in m["calls"])
        yield episode, models, {"task": job["command"], "turns": sample} if sample else None


def build_indices(run, stage):
    registration = read_json(AUDIT / "source-seals.json")
    roles = read_json(AUDIT / "scene-roles.json")
    fold = read_json(ROOT / "datasets/flockcue-600/proposed-folds.json")
    scenes = [group["A"] for group in fold["strata"]]
    require(len(scenes) == 15 and len(set(scenes)) == 15, "Expected the formal A fold")
    role_by_scene = {row["session_id"]: row["role"] for row in roles["scenes"]}
    must_match(set(role_by_scene), set(scenes), "registered scenes")
    must_match(roles["run"], registration["run"], "formal run")
    registered_run = Path(registration["run"])
    manifest = read_json(run / "manifest.json")
    counts = Counter()
    with ExitStack() as streams:
        turns = streams.enter_context((stage / "wire-turns.jsonl").open("xb"))
        episodes = streams.enter_context((stage / "wire-episodes.jsonl").open("xb"))
        for scene_id in scenes:
            role = role_by_scene[scene_id]
            seal_record = registration["scenes"][scene_id]
            must_match(role, seal_record["role"], "role")
            split = {"train": "training", "internal_validation": "validation"}[role]
            folder = run / scene_id
            seal_path = folder / "complete.json"
            seal_hash = file_digest(seal_path)
            must_match(seal_hash, seal_record["scene_seal_sha256"], "registered scene seal")
            must_match(seal_hash, manifest["scenes"][scene_id]["seal_sha256"], "run scene seal")
            files = read_json(seal_path)["files"]
            for episode, models, sample in scene_indices(registered_run, folder, files, scene_id, split):
                episodes.write(packed(episode) + b"\n")
                for model in models:
                    turns.write(packed(model) + b"\n")
                if sample:
                    with (stage / "public-example.json").open("x", encoding="utf-8") as output:
                        output.write(json.dumps(sample, ensure_ascii=False, indent=2) + "\n")
                counts.update(episodes=1, turns=len(models), verified_turns=episode["verified_turns"])
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", type=Path, default=AUDIT.parent / "raw", help="Captured run containing its manifest and scene folders (default: local teacher release raw/)"
    )
    parser.add_argument("--output", type=Path, required=True, help="Fresh directory for verified indices")
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error("--output must be a fresh directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp)
        counts = build_indices(args.run.resolve(), stage)
        manifest = read_json(AUDIT / "manifest.json")
        checks = {Path(entry["path"]).name: entry["sha256"] for entry in manifest["files"]}
        for name in OUTPUT_FILES:
            must_match(file_digest(stage / name), checks[name], name)
        output.mkdir()
        try:
            for name in OUTPUT_FILES:
                (stage / name).rename(output / name)
        except BaseException:
            shutil.rmtree(output)
            raise
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
