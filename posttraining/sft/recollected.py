"""Merge audited terminal-only DeepSeek resamples into the frozen second-round SFT pack."""
import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from ..inference import HISTORICAL_EPISODE_TOKEN_LIMIT, output_budget
from . import reviewed_decisions as parent
from .export import checked_bytes, json_line
from .history import parse, sha
from .supervision import supervise


MAIN_SHA = "c34aa351d85da6e125ab348945e564abf8ca77e2b5ed29397a323a01cd0799a6"
REPAIR_SHA = "da659cad47218ac36b69c715218aae6a1a47284546b07f31b86fcd86e9bef144"


def copy_parent(source, stage, manifest_sha):
    manifest_bytes = checked_bytes(source / "manifest.json", manifest_sha)
    manifest = parse(manifest_bytes)
    stage.mkdir()
    for name, entry in manifest["files"].items():
        (stage / name).write_bytes(checked_bytes(source / name, entry["sha256"]))
    (stage / "manifest.json").write_bytes(manifest_bytes)
    return manifest


def verify_capture(meta, key, req_sha, resp_sha, reply):
    scene, task, call = key
    expected = {"scene_id": scene, "task_id": task, "turn": int(call.removeprefix("model-")),
                "http_status": 200, "original_request_sha256": req_sha, "response_sha256": resp_sha,
                "response_id": reply["id"], "model": reply["model"], "fingerprint": reply["system_fingerprint"]}
    if any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError(f"Capture metadata does not bind the response to {task}")
    elapsed, remaining = meta["elapsed_seconds"], meta["remaining_seconds"]
    if not (math.isfinite(remaining) and 0 <= elapsed <= remaining):
        raise ValueError(f"Recollected response exceeded the original time window for {task}")


def teacher_choice(response, wire):
    if (not wire["fingerprint"] or response.get("model") != wire["model"]
            or response.get("system_fingerprint") != wire["fingerprint"]):
        raise ValueError("Resample provider model or fingerprint differs from the original")
    if not response.get("id") or len(response.get("choices", [])) != 1:
        raise ValueError("Expected one identifiable provider completion")
    return response["choices"][0]


def encode_closing(request, response, wire, original, budget, tokenizer, *, continued_spent=None):
    choice = teacher_choice(response, wire)
    target = choice["message"]
    if choice["finish_reason"] != "stop" or target.get("tool_calls") or not (target.get("content") or "").strip():
        raise ValueError("Expected a completed natural-language terminal decision")
    ids, labels, audit = supervise(tokenizer, request["messages"], request["tools"], target)
    if continued_spent is None and audit["prompt_ids_sha256"] != original["prompt_ids_sha256"]:
        raise ValueError("Rendered student prompt differs from the sealed original")
    spent = budget - original["prompt_tokens"] - original["target_tokens"] if continued_spent is None else continued_spent
    capacity, reason = output_budget(audit["masked_prompt_tokens"], spent, HISTORICAL_EPISODE_TOKEN_LIMIT)
    if reason or audit["supervised_tokens"] > capacity:
        raise ValueError("Original episode budget cannot admit the replacement decision")
    data = json_line({"input_ids": ids, "labels": labels})
    index = {k: audit[k] for k in ("masked_prompt_tokens", "supervised_tokens", "supervised_reasoning_tokens",
                                    "prompt_ids_sha256", "target_ids_sha256")}
    index.update(kind="closing", source_kind="resampled_terminal", first_pass_row=None,
                 session_id=wire["scene_id"], task_id=wire["task_id"], logical_call_id=wire["logical_call_id"],
                 original_prefix_training_line=original["source_training_line"],
                 original_public_row_sha256=original["source_row_sha256"],
                 provider_response_id=response["id"], provider_model=response["model"],
                 provider_fingerprint=response["system_fingerprint"], train_row_sha256=sha(data),
                 prefix_spent_tokens=spent, response_capacity_tokens=capacity)
    return data, index


def bound_artifact(storage, branch, name):
    raw_path = Path(branch[f"{name}_path"])
    if raw_path.is_absolute() or not (storage / raw_path).resolve().is_relative_to(storage.resolve()):
        raise ValueError("Branch artifact is outside the collection")
    return parse(checked_bytes(storage / raw_path, branch[f"{name}_sha256"]))


def verify_query_history(original, reply, receipt, followup):
    """Only the sealed get_drone_status decision and its own public result may extend the exact prefix."""
    message = reply["choices"][0]["message"]
    call, = message["tool_calls"]
    name, args = call["function"]["name"], parse(call["function"]["arguments"])
    if name != "get_drone_status" or call.get("index", 0) != 0:
        raise ValueError("Expected a single get_drone_status continuation")
    # Native ChatOpenAI replay serializes empty tool-call content as null and omits the streaming index.
    assistant = {**message, "content": message["content"] or None,
                 "tool_calls": [{k: v for k, v in call.items() if k != "index"}]}
    recorded_assistant, result = receipt["messages"]
    if (recorded_assistant != assistant or result.get("role") != "tool"
            or result.get("tool_call_id") != call["id"]
            or parse(result["content"])["id"] != args["input_json"]["drone_id"]):
        raise ValueError("Resumed tool response does not match the previous teacher call and actor")
    expected = {**original, "messages": [*original["messages"], assistant, result]}
    if followup != expected:
        raise ValueError("Resumed request differs from the exact original prefix and public tool exchange")


def verify_resume_capture(meta, branch, first_meta, response, receipt):
    expected = {"task_id": branch["task_id"], "parent_request_sha256": first_meta["original_request_sha256"],
                "parent_response_sha256": first_meta["response_sha256"], "request_sha256": branch["request_sha256"],
                "response_sha256": branch["response_sha256"], "public_tool_receipt_sha256": branch["receipt_sha256"],
                "scene_id": first_meta["scene_id"], "turn": first_meta["turn"] + 1,
                "status": "response_received", "http_status": 200, "endpoint": first_meta["endpoint"],
                "attempt": "independent_checkpoint_recovery_locked_first_draw", "response_id": response["id"],
                "model": response["model"], "fingerprint": response["system_fingerprint"], "finish": "stop"}
    if any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError("Resumed response metadata does not bind the original and continued attempts")
    started, sent, returned, deadline = (datetime.fromisoformat(meta[k]) for k in
                                          ("attempt_started_at", "sent_at", "returned_at", "deadline_at"))
    tool_start, tool_end = (datetime.fromisoformat(receipt[k]) for k in ("started_at", "returned_at"))
    available = first_meta["remaining_seconds"] - first_meta["elapsed_seconds"]
    total_elapsed = meta["original_prefix_elapsed_seconds"] + first_meta["elapsed_seconds"] + meta["total_attempt_elapsed_seconds"]
    if (not started <= tool_start <= tool_end <= sent <= returned <= deadline
            or (deadline - started).total_seconds() > available + 0.001
            or abs(500 - first_meta["remaining_seconds"] - meta["original_prefix_elapsed_seconds"]) > 0.001
            or abs((returned - sent).total_seconds() - meta["elapsed_seconds"]) > 0.01
            or abs((returned - started).total_seconds() - meta["total_attempt_elapsed_seconds"]) > 0.01
            or abs(meta["original_budget_total_elapsed_seconds"] - total_elapsed) > 0.001 or total_elapsed > 500
            or not 0 <= meta["total_attempt_elapsed_seconds"] <= available
            or not 0 <= meta["elapsed_seconds"] <= meta["remaining_seconds_at_send"]
            or abs(meta["locked_model_elapsed_seconds"] - first_meta["elapsed_seconds"]) > 0.001):
        raise ValueError("Resumed attempt exceeds its independent share of the original time budget")
    api, = receipt["actual_api_calls"]
    tool = receipt["messages"][1]
    observation = parse(tool["content"])
    if (not api["success"] or api["method"] != "GET" or api["status_code"] != 200
            or observation["id"] != meta["actor_id"] or api["endpoint"] != f"/drones/{observation['id']}"
            or tool["tool_call_id"] != meta["locked_tool_call_id"]
            or not {"position", "status"}.issubset(api["response"])
            or any(observation[k] != v for k, v in api["response"].items())):
        raise ValueError("Public tool receipt does not match the executed status GET")


def encode_continuation(branch, storage, original, first_reply, first_meta, candidate, wire, ref, tokenizer,
                        query, spent):
    """Count the sampled tool decision before fitting a new supervised answer to the observed tool result."""
    first = teacher_choice(first_reply, wire)
    if first["finish_reason"] != "tool_calls":
        raise ValueError("An independently resumed attempt must start with the sealed tool decision")
    followup = bound_artifact(storage, branch, "request")
    response = bound_artifact(storage, branch, "response")
    receipt = bound_artifact(storage, branch, "receipt")
    meta = bound_artifact(storage, branch, "meta")
    verify_query_history(original, first_reply, receipt, followup)
    verify_resume_capture(meta, branch, first_meta, response, receipt)
    data, index = encode_closing(followup, response, wire, ref, candidate["teacher_path_tokens"], tokenizer,
                                 continued_spent=spent)
    budget = meta["qwen_budget"]
    expected = {"spent_before": spent, "prompt_tokens": index["masked_prompt_tokens"],
                "prompt_ids_sha256": index["prompt_ids_sha256"], "available_generation": index["response_capacity_tokens"],
                "request_sha256": branch["request_sha256"], "locked_tool_decision_target_ids_sha256": query["target_ids_sha256"]}
    if any(budget[k] != v for k, v in expected.items()):
        raise ValueError("Continuation budget differs from the rerendered native decisions")
    check_review_generation(branch, index["masked_prompt_tokens"], index["supervised_tokens"], spent)
    index.update(source_kind="terminal_after_status_query", logical_call_id=f"model-{wire['turn'] + 1}",
                 parent_request_sha256=first_meta["original_request_sha256"],
                 parent_response_sha256=first_meta["response_sha256"], query_target_ids_sha256=query["target_ids_sha256"],
                 request_sha256=branch["request_sha256"], response_sha256=branch["response_sha256"],
                 capture_sha256=branch["meta_sha256"], receipt_sha256=branch["receipt_sha256"])
    return data, index


def check_review_generation(review, prompt, tokens, spent):
    capacity, reason = output_budget(prompt, spent, HISTORICAL_EPISODE_TOKEN_LIMIT)
    if reason or tokens > capacity:
        raise ValueError("Recollected decision exceeds the remaining student budget")
    generation = {"spent_before": spent, "prompt_tokens": prompt, "available_generation": capacity,
                  "new_generation_tokens": tokens, "remaining_after": HISTORICAL_EPISODE_TOKEN_LIMIT - spent - prompt - tokens}
    if "generation" in review and any(review["generation"].get(k) != v for k, v in generation.items()):
        raise ValueError("Reviewed token counts differ from the actual provider decision")


def reviewed_additions(reviews, storage, frozen, branches):
    original_reviews = parse(checked_bytes(parent.REVIEW, frozen["sources"]["review_sha256"]))["tasks"]
    projection, seals = parent.read_projected_turns()
    # Reuse the parent checks for train-only scene roles and the exact projected episode cost.
    parent.selection(original_reviews, projection)
    candidates = [r for r in original_reviews if r["verdict"] == "recollect"]
    by_task = {r["task_id"]: r for r in reviews}
    if len(by_task) != len(reviews) or set(by_task) != {r["task_id"] for r in candidates}:
        raise ValueError("Review must cover every original recollection candidate exactly once")
    table = checked_bytes(parent.RELEASE / "audit/wire-turns.jsonl", seals["wire-turns.jsonl"])
    turns = {(r["scene_id"], r["task_id"], r["logical_call_id"]): r for r in map(parse, table.splitlines())}
    tokenizer = parent.load_tokenizer()
    additions = []
    query_tasks = set()
    for candidate in candidates:
        task = candidate["task_id"]
        review = by_task[task]
        if review["verdict"] not in ("accept", "reject") or not review["reason_cn"].strip():
            raise ValueError(f"Missing review decision for {task}")
        key = candidate["scene_id"], task, f"model-{candidate['terminal_turn']}"
        wire = turns[key]
        attempt, = [a for a in wire["attempts"] if a["attempt_id"] == wire["response_attempt_id"]]
        request = parse(checked_bytes(storage / f"{task}.request.json", attempt["request_sha256"]))
        response_bytes = checked_bytes(storage / f"{task}.response.json", review["response_sha256"])
        branch = branches.get(task)
        if not wire["wire_verified"] or wire["finish_reason"] != "stop":
            raise ValueError(f"Incomplete original terminal decision for {task}")
        response = parse(response_bytes)
        capture_bytes = (storage / f"{task}.meta.json").read_bytes()
        capture = parse(capture_bytes)
        verify_capture(capture, key, attempt["request_sha256"], review["response_sha256"], response)
        ref = projection[key]
        if (response.get("model"), response.get("system_fingerprint")) != (wire["model"], wire["fingerprint"]):
            if review["verdict"] == "reject" and not branch:
                continue
            raise ValueError("Accepting or resuming a response requires the original teacher fingerprint")
        choice = teacher_choice(response, wire)
        spent = candidate["teacher_path_tokens"] - ref["prompt_tokens"] - ref["target_tokens"]
        if choice["finish_reason"] == "tool_calls":
            if review["verdict"] != "reject":
                raise ValueError("A tool request cannot be accepted as a closing")
            query_tasks.add(task)
            _, _, query = supervise(tokenizer, request["messages"], request["tools"], choice["message"])
            if query["prompt_ids_sha256"] != ref["prompt_ids_sha256"]:
                raise ValueError("Rerendered query prompt differs from the frozen original")
            prompt, tokens = query["masked_prompt_tokens"], query["supervised_tokens"]
            check_review_generation(review, prompt, tokens, spent)
            if branch:
                row = encode_continuation(branch, storage, request, response, capture, candidate, wire, ref, tokenizer,
                                          query, spent + prompt + tokens)
                if branch["verdict"] == "accept":
                    additions.append(row)
        else:
            if branch:
                raise ValueError("A terminal response cannot be resumed as a tool call")
            data, index = encode_closing(request, response, wire, ref, candidate["teacher_path_tokens"], tokenizer)
            check_review_generation(review, index["masked_prompt_tokens"], index["supervised_tokens"], spent)
            index.update(request_sha256=attempt["request_sha256"], response_sha256=review["response_sha256"],
                         capture_sha256=sha(capture_bytes))
            if review["verdict"] == "accept":
                if index["target_ids_sha256"] == ref["target_ids_sha256"]:
                    raise ValueError("Accepted resample duplicates the previously rejected target")
                additions.append((data, index))
    if branches and set(branches) != query_tasks:
        raise ValueError("Continuation review must cover all the resampled tool requests")
    return additions


def append_rows(stage, manifest, additions):
    train, index = stage / "train.jsonl.gz", stage / "index.jsonl"
    old_index = index.read_bytes()
    prior = list(map(parse, old_index.splitlines()))
    scenes = {r["session_id"] for r in prior}
    tasks = {(r["session_id"], r["task_id"]) for r in prior}
    digests = {r["train_row_sha256"] for r in prior}
    with gzip.open(train, "rb") as stream:
        old_data = stream.read()
    with train.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb",
                                                compresslevel=6, mtime=0) as rows, index.open("ab") as refs:
        rows.write(old_data)
        for number, (line, ref) in enumerate(additions, 1 + len(prior)):
            if ref["train_row_sha256"] in digests:
                raise ValueError("Duplicate resampled training row")
            digests.add(ref["train_row_sha256"])
            rows.write(line)
            refs.write(json_line({"row": number, **ref}))
            scenes.add(ref["session_id"])
            tasks.add((ref["session_id"], ref["task_id"]))
            for name in ("masked_prompt_tokens", "supervised_tokens", "supervised_reasoning_tokens"):
                manifest["counts"][name] += ref[name]
    manifest["counts"].update(decisions=len(prior) + len(additions), scenes=len(scenes), tasks=len(tasks))
    manifest["kinds"]["closing"] += len(additions)
    for path in (train, index):
        manifest["files"][path.name] = {"sha256": sha(path.read_bytes()), "bytes": path.stat().st_size}


def build(reviews, wire, output, base=None, branch_reviews=None):
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    review_bytes = reviews.read_bytes()
    decisions = parse(review_bytes)["tasks"]
    branches = {}
    if branch_reviews is not None:
        branch_bytes = branch_reviews.read_bytes()
        items = parse(branch_bytes)["tasks"]
        branches = {row["task_id"]: row for row in items}
        if (len(items) != len(branches) or any(r["verdict"] not in ("accept", "reject")
                or not r["reason_cn"].strip() for r in items)):
            raise ValueError("Invalid continuation review")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        if base is None:
            base = Path(tmp) / "parent"
            parent.export(base)
        stage = Path(tmp) / "merge"
        manifest = copy_parent(base, stage, MAIN_SHA)
        copy_parent(base / "repair-only", stage / "repair-only", REPAIR_SHA)
        additions = reviewed_additions(decisions, wire, manifest, branches)
        if additions:
            append_rows(stage, manifest, additions)
        manifest["sources"].update(parent_main_manifest_sha256=MAIN_SHA, parent_repair_manifest_sha256=REPAIR_SHA,
                                   resample_review_sha256=sha(review_bytes))
        kinds = Counter(ref["source_kind"] for _, ref in additions)
        if branch_reviews is not None:
            manifest["sources"]["continuation_review_sha256"] = sha(branch_bytes)
        manifest["recollection"] = {"reviewed": len(decisions), "rejected": len(decisions) - len(additions),
                                    "resumed_reviewed": len(branches),
                                    "direct_closing": kinds["resampled_terminal"],
                                    "after_public_query": kinds["terminal_after_status_query"],
                                    "new_closing": len(additions), "new_correction": 0}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
    return manifest


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--reviews", required=True, type=Path)
    cli.add_argument("--wire", required=True, type=Path, help="directory with per-task .request.json, .response.json and .meta.json")
    cli.add_argument("--branch-reviews", type=Path, help="optional report of independent continuation attempts")
    cli.add_argument("--base", type=Path, help="exact frozen pack; reconstructed when omitted")
    cli.add_argument("--output", required=True, type=Path, help="new directory")
    args = cli.parse_args()
    print(json.dumps(build(args.reviews, args.wire, args.output, args.base, args.branch_reviews)["recollection"]))
