"""Offline receipt replay on the six rejected inputs and three recovered 48K trajectories."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import subprocess

from flockcue.recording.compact_receipts import TOOLS, compact_receipts
from posttraining.sft.adapter import load_tokenizer, render
from posttraining.sft.history import InputRejected, canonical
from posttraining.sft.supervision import supervise
from runtime import executor, parallel
from .context_window import PROMPT_LIMIT, select_window, visible_messages
from .index_wire import scene_indices
from .rebuild import public_turn


def verify_contents(original, projected):
    """Decode independently and compare typed JSON, list order, call bindings and trailing annotations."""
    owners, previous, stats = {}, {}, Counter()
    snapshot = 0
    for a, b in zip(original, projected, strict=True):
        if a["role"] == "assistant":
            owners.update((c["id"], c["function"]["name"]) for c in a.get("tool_calls", []))
        if a == b:
            continue
        if a["role"] != "tool" or owners[a["tool_call_id"]] not in TOOLS or {**b, "content": a["content"]} != a:
            raise ValueError("Changed a non-target message or its call binding")
        old, i = json.JSONDecoder().raw_decode(a["content"])
        new, j = json.JSONDecoder().raw_decode(b["content"])
        if a["content"][i:] != b["content"][j:]:
            raise ValueError("Public annotation changed")
        if "blackboard_snapshot" in new:
            if new.pop("blackboard_snapshot") != snapshot + 1:
                raise ValueError("Snapshot order changed")
            if "blackboard_delta" in new:
                delta = new.pop("blackboard_delta")
                if not snapshot or delta["from_snapshot"] != snapshot:
                    raise ValueError("Delta has no preceding base in the visible window")
                board = dict(previous)
                for group, change in delta["changes"].items():
                    rows = {row["id"]: row for row in board[group]}
                    for key in change.get("remove_ids", []):
                        del rows[key]
                    for row in change.get("upsert_by_id", []):
                        rows[row["id"]] = row
                    board[group] = [rows[key] for key in change.get("order", rows)]
                new["blackboard"] = board
                stats["delta_snapshots"] += 1
            else:
                stats["full_snapshots"] += 1
            previous = new["blackboard"]
            snapshot += 1
        if canonical(old) != canonical(new):
            raise ValueError(f"Receipt payload changed for {a['tool_call_id']}")
        stats["changed_receipt_instances"] += 1
    return stats


def project_window(tokenizer, messages, tools, fallback):
    window = select_window(tokenizer, messages, tools, fallback_limit=fallback, project=compact_receipts)
    visible = visible_messages(messages, window["start"])
    projected = compact_receipts(visible)
    return window, projected, verify_contents(visible, projected)


def rejected_point(tokenizer, archive, chain, fallback, expected):
    command = [executor.interpreter("agent"), "-m", "posttraining.trajectories.rejected_request", archive, chain]
    request = json.loads(subprocess.check_output(command, cwd=executor.ROOT))
    messages, tools = request["messages"], request["tools"]
    start = max(i for i, m in enumerate(messages) if m["role"] == "assistant")
    required = visible_messages(messages, start)
    original = select_window(tokenizer, required, tools, limit=expected)
    if original["tokens"] != expected or request["window"]["minimum_prompt_tokens"] != expected:
        raise ValueError("Reference minimum differs from the recorded rejection")
    shortest = compact_receipts(required)
    verify_contents(required, shortest)
    minimum = len(render(tokenizer, shortest, tools, thinking=True)[0]["input_ids"])
    try:
        window, _, stats = project_window(tokenizer, messages, tools, fallback)
    except InputRejected as error:
        if error.code != "context_length" or error.details["minimum_prompt_tokens"] != minimum:
            raise
        return {"old_min": expected, "new_min": minimum, "admission": "rejected", "selected_prompt_tokens": None}, Counter()
    return {"old_min": expected, "new_min": minimum, "admission": "48K" if "fallback_prompt_limit" in window else "32K",
            "selected_prompt_tokens": window["tokens"]}, stats


def target_size(tokenizer, messages, tools, target, window, output_limit):
    cap = window.get("fallback_prompt_limit", PROMPT_LIMIT) + output_limit
    try:
        _, _, audit = supervise(tokenizer, messages, tools, target, context_limit=cap)
    except InputRejected as error:
        if error.code != "sft_overlength":
            raise
        n, prefix = error.details["target_tokens"], error.details["prompt_ids_sha256"]
    else:
        n, prefix = audit["supervised_tokens"], audit["prompt_ids_sha256"]
    if prefix != window["ids_sha256"]:
        raise ValueError("Supervision prefix differs from admission")
    return n


def trajectory(tokenizer, archive, turns, fallback, output_limit):
    contexts = {}
    with archive.open() as stream:
        for event in map(json.loads, stream):
            if event["kind"] == "student_context":
                data = event["data"]
                contexts[data["logical_call_id"]] = data
    ledger, stats = [], Counter()
    with archive.open("rb") as stream:
        for turn in turns:
            if not turn["wire_verified"]:
                raise ValueError("Expected a verified teacher decision")
            wire = public_turn(stream, turn)
            context = contexts[turn["logical_call_id"]]
            full = list(wire["messages"])
            for idx, msg in context["omitted"]:
                full.insert(idx, msg)
            tools, target = wire["tools"], wire["target"]
            old = select_window(tokenizer, full, tools, fallback_limit=fallback)
            if old != context["window"] or visible_messages(full, old["start"]) != wire["messages"]:
                raise ValueError("Reference replay differs from the actual provider request")
            window, after, verified = project_window(tokenizer, full, tools, fallback)
            stats.update(verified)
            target_tokens = target_size(tokenizer, after, tools, target, window, output_limit)
            if target_size(tokenizer, wire["messages"], tools, target, old, output_limit) != target_tokens:
                raise ValueError("Teacher target length changed during replay")
            ledger.append({"turn": turn["turn"], "old_prompt": old["tokens"], "new_prompt": window["tokens"],
                           "target": target_tokens, "old_start": old["start"], "new_start": window["start"],
                           "fallback": "fallback_prompt_limit" in window})
    return ledger, stats


def prefix(rows, prompt_key, budget, output_limit):
    spent = 0
    for row in rows:
        prompt, target = row[prompt_key], row["target"]
        available = budget - spent - prompt
        if available < 1 or target > min(available, output_limit):
            reason = "episode_input" if available < 1 else (
                "target_limit" if output_limit <= available else "episode_output")
            return {"rounds": row["turn"] - 1, "spent": spent, "next_prompt": prompt, "next_target": target,
                    "remaining": budget - spent, "stop": reason}
        spent += prompt + target
    return {"rounds": len(rows), "spent": spent, "remaining": budget - spent, "stop": "complete"}


def run(attempt, out):
    registration = executor.read_json(executor.FALLBACK_REGISTRATION)
    reference = executor.read_json(executor.FALLBACK_REGISTRATION.with_name("results.json"))
    if attempt.name != reference["attempt"]:
        raise ValueError("Expected the registered 48K fallback first pass")
    executor.use_stage(attempt.parent.parent)
    parallel.verify_archive(executor, attempt)
    plan, manifest = (executor.read_json(attempt / name) for name in ("plan.json", "manifest.json"))
    blocked = {tid: (n, minimum) for tid, n, minimum in reference["required_prompt_tokens_on_rejection"]
               if tid in registration["blocked_regressions"]}
    recovered = {x["task"]: x for x in reference["recovered"]}
    fallback = registration["fallback_prompt_tokens"]
    budget, cap = registration["student_episode_tokens"], registration["max_generation_tokens"]
    tokenizer, evidence = load_tokenizer(), Counter()
    gates, budgets, ledger = {}, {}, []
    for scene in plan["sessions"]:
        sid = scene["id"]
        if manifest["scenes"][sid]["seal_sha256"] != reference["scenes_sha256"][sid]:
            raise ValueError("Scene seal differs from the reference")
        folder = attempt / sid
        parallel.verify_scene(executor, attempt, plan, scene, manifest["scenes"][sid])
        if not any(t["id"] in blocked or t["id"] in recovered for t in scene["tasks"]):
            continue
        files = executor.read_json(folder / "complete.json")["files"]
        for ep, turns, _ in scene_indices(Path("."), folder, files, sid, "training_expansion"):
            tid = ep["task_id"]
            archive = attempt / ep["archive_path"]
            if tid in blocked:
                chain, = (folder / "agent-traces/toolchains").glob("*_" + ep["command_id"] + "_*.json")
                n, minimum = blocked[tid]
                if turns[-1]["turn"] != n or turns[-1]["attempts"]:
                    raise ValueError("Registered rejection is not the terminal pre-dispatch turn")
                gates[tid], verified = rejected_point(tokenizer, archive, chain, fallback, minimum)
                gates[tid]["turn"] = n
                evidence.update(verified)
            if tid in recovered:
                rows, verified = trajectory(tokenizer, archive, turns, fallback, cap)
                evidence.update(verified)
                old = prefix(rows, "old_prompt", budget, cap)
                expected = recovered[tid]
                if (len(rows), old["rounds"], old["spent"]) != (expected["turns"], expected["full_rounds"],
                                                               expected["prefix_tokens"]):
                    raise ValueError("Baseline budget prefix differs from the registered result")
                budgets[tid] = {"turns": len(rows), "old": old, "new": prefix(rows, "new_prompt", budget, cap)}
                ledger.extend({"task": tid, **row} for row in rows)
    if set(gates) != set(blocked) or set(budgets) != set(recovered):
        raise ValueError("Incomplete replay cohort")
    out.mkdir(parents=True, exist_ok=False)
    executor.save_json(out / "results.json", {"attempt": attempt.name, "ordinary_prompt_cap": PROMPT_LIMIT,
                                              "fallback_prompt_cap": fallback, "output_cap": cap,
                                              "episode_budget": budget, "rejections": gates,
                                              "trajectories": budgets, "evidence": evidence})
    with (out / "turns.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ledger[0]))
        writer.writeheader()
        writer.writerows(ledger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.attempt.resolve(), args.output)
