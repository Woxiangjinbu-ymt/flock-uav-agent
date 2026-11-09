"""Rebuild the four predetermined DeepSeek hover development fixtures offline.

Only samples.jsonl is a model-facing payload. decisions.jsonl and batch.json are
out-of-band provenance. All source paths and line/sequence numbers are repo-relative
and one-based respectively.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from convert_agent_samples import _calls, _episode_gate, canonical, decode, parse, read, require, sha
from student_input import InputRejected
from thinking_events.archive import read_turns, verify_sources
from thinking_events.events import extract_events

from .adapter import load_tokenizer
from .dev_sample_tokens import supervise

ROOT = Path(__file__).resolve().parents[2]
BATCH = "deepseek-hover-pilot-20261103"
CONDITIONS = ("state_hover", "explicit_hover", "unknown_takeoff", "duration_hover")
COMMON_AUDIT_SHA256 = "af08971bcd911432230cfb7a6071029792afa17d139d49ef4d686715c611b87d"
PRIVATE_MARKERS = (b"/check/", b"execution_check_apis", b"related_apis", b"mark-done",
                   b"world.pkl", b"checker_sha256", b"score-world.pkl")
ACTIONS = {"take_off": "take_off", "hover": "hovering"}
QUERIES = {"list_drones", "get_weather", "sense_nearby_entities", "get_drone_status"}


def lines(path):
    return [parse(line) for line in path.read_bytes().splitlines()]


def jsonl(rows):
    # The native template renders schema and argument key order. Preserve wire order.
    return b"".join((json.dumps(row, ensure_ascii=False, separators=(",", ":"),
                                allow_nan=False) + "\n").encode() for row in rows)


def sealed_artifact(run, files, name):
    path = run / name
    digest = sha(path.read_bytes())
    require(digest == files[name], f"changed_sealed_artifact:{name}")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest}


def episode_evidence(run, files, trial, result, common):
    name = trial["trial_id"]
    parent = f"trials/{name}"
    paths = {
        "independent_score": f"{parent}/evaluation/result.json",
        "worker": f"{parent}/worker-result.json",
        "agent_result": f"{parent}/policy/agent-result.json",
        "ledger": f"{parent}/policy/task-state.json",
        "public_api": f"{parent}/policy/worldlogs/public-api.jsonl",
        "physical_events": f"{parent}/policy/worldlogs/physical-events.jsonl",
        "worker_exit_seal": f"worker-exit-seals/{name}.json",
        "scorer_exit_seal": f"scorer-exit-seals/{name}.json",
    }
    artifacts = {key: sealed_artifact(run, files, path) for key, path in paths.items()}
    score = read(run, paths["independent_score"])
    worker = read(run, paths["worker"])
    _episode_gate(run, trial, result, worker, score)
    truth = score["unified_completion"]
    public = read(run, paths["agent_result"])["completion_assessment"]
    require(truth["condition"] == trial["condition"] and
            common["independent_truth"] is truth["task_completed"] and
            common["independent_truth_status"] == truth["status"] and
            common["candidate_online_matches_common"] is True and
            public["status"] == common["common_public_status"] and
            public["task_completed"] is common["common_public_task_completed"] and
            public["reasons"] == common["common_public_reasons"] and
            public["evidence_refs"] == common["common_evidence_refs"], "episode_audits_disagree")
    return {
        "original_checker_passed": score["checker"]["passed"],
        "checks_passed": score["checker"]["checks_passed"],
        "checks_expected": score["checker"]["checks_expected"],
        "independent_truth": truth["task_completed"], "independent_status": truth["status"],
        "independent_reasons": truth["reasons"], "independent_effect_refs": truth["evidence_refs"],
        "common_public_status": public["status"], "common_public_reasons": public["reasons"],
        "common_evidence_refs": public["evidence_refs"], "artifacts": artifacts,
    }


def wire_decision(turn, archive):
    sequence = turn["request_sequence"]
    req = archive[sequence - 1]
    logical, attempt = turn["logical_call_id"], turn["attempt_id"]
    require(req["sequence"] == sequence and req["kind"] == "http_request" and
            req["data"]["logical_call_id"] == logical and req["data"]["attempt_id"] == attempt,
            "wrong_request")
    chunks = [r for r in archive if r["kind"] == "http_response_chunk" and r["data"]["attempt_id"] == attempt]
    require(chunks and all(r["sequence"] > sequence and r["data"]["logical_call_id"] == logical
                           for r in chunks), "wrong_reply")
    request_body = decode(req["data"]["body"])
    response_body = b"".join(decode(r["data"]["body"]) for r in chunks)
    request, response = parse(request_body), parse(response_body)
    choice, = response["choices"]
    reply = choice["message"]
    calls = _calls(reply)
    require(reply["role"] == "assistant" and choice["finish_reason"] == ("tool_calls" if calls else "stop"),
            "incomplete_reply")
    target = {"role": "assistant", "content": reply["content"] or ""}
    if "reasoning_content" in reply:
        target["reasoning_content"] = reply["reasoning_content"]
    messages, tools = request["messages"], request["tools"]
    available = {tool["function"]["name"] for tool in tools}
    public = canonical({"messages": messages, "tools": tools})
    require(all(c["name"] in available and c["id"].encode() not in public for c in calls),
            "illegal_tool_or_current_id_in_input")
    if calls:
        target["tool_calls"] = [{"type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                                for c in calls]
    require(not any(marker in public or marker in canonical(target) for marker in PRIVATE_MARKERS),
            "private_marker_in_sample")
    prefix = turn["prefix"]
    require(not prefix["gaps"] and not prefix["history_gaps"], "incomplete_public_prefix")
    history = [{k: r[k] for k in ("call_id", "tool", "sequence", "wire_message_index", "transformation",
                                  "raw_content_sha256", "wire_content_sha256")}
               for batch in prefix["batches"] for r in batch]
    require(sum(m["role"] == "tool" for m in messages) == len(history) and
            all(r["sequence"] < sequence and messages[r["wire_message_index"]]["tool_call_id"] == r["call_id"]
                for r in history), "wrong_history_receipt")
    source = {k: turn[k] for k in ("batch", "episode", "task_id", "logical_call_id", "attempt_id", "turn",
                                   "archive_path", "archive_sha256", "request_sequence")}
    source.update(model=response["model"], request_body_sha256=sha(request_body),
                  response_chunk_sequences=[r["sequence"] for r in chunks], response_body_sha256=sha(response_body))
    require(response["model"] == "deepseek-flash", "unexpected_teacher")
    sample_id = f"{turn['episode']}/turn-{turn['turn']:02d}"
    sample = {"sample_id": sample_id, "messages": messages, "tools": tools, "target": target}
    return sample, source, history, calls


def current_receipt(call, source, archive, api, physical, actor):
    """Check the post-decision receipts and action effects, for selection/audit only."""
    ident, tool = call["id"], call["name"]
    start, = (r for r in archive if r["kind"] == "tool_start" and r["data"]["raw_tool_call_id"] == ident)
    span = start["data"]
    end, = (r for r in archive if r["kind"] == "tool_end" and r["data"]["tool_span_id"] == span["tool_span_id"])
    result, output = end["data"], end["data"]["output"]
    logical = source["logical_call_id"]
    require(span["source_relation"] == "unique" and span["source_model_id"] == logical and
            span["source_candidates"] == [logical] and span["input"]["args"] == call["args"] and
            span["input"]["name"] == tool and span["input"]["id"] == ident and
            max(source["response_chunk_sequences"]) < start["sequence"] < end["sequence"] and
            result["status"] == "returned" and not result["error_type"] and
            result["tool_call_id_mismatch"] is False and output["type"] == "tool" and
            output["name"] == tool and output["tool_call_id"] == ident, "unattributed_current_receipt")
    origin = {"kind": "model_tool", "tool": tool, "tool_call_id": ident}
    requests = [row for row in api if row["origin"] == origin and row["phase"] == "task"]
    require(requests and all(row["admin"] is False for row in requests), "missing_public_api_proof")
    events = [row for row in physical if row["origin"] == origin and row["phase"] == "task"]
    content = output["content"]
    if tool in ACTIONS:
        require(len(events) == 1 and events[0]["committed"] is True and events[0]["actor"] == actor and
                events[0]["history"]["event"] == ACTIONS[tool] and any(
                    r["request_id"] == events[0]["request_id"] and r["committed"] is True for r in requests),
                "missing_committed_action_effect")
    else:
        require(tool in QUERIES and not events and all(row["action"] is False for row in requests),
                "unexpected_observation_effect")
    if content == "Error during takeoff: Request timed out before response was received":
        require(tool == "take_off", "unrecognized_timeout")
        status = "effect_unknown_at_return"
    else:
        payload = parse(content)
        require(output["status"] == "success" and isinstance(payload, (dict, list)) and bool(payload)
                and (not isinstance(payload, dict) or "error" not in payload), "unusable_tool_result")
        if tool in ACTIONS:
            require(payload["drone_id"] == actor and payload["command"] == tool and
                    payload["status"] == "success" and payload["outcome"] == "completed", "bad_action_receipt")
        status = "action_completed" if tool in ACTIONS else "observation_returned"
    return {"call_id": ident, "tool": tool, "span_id": span["tool_span_id"],
            "start_sequence": start["sequence"], "sequence": end["sequence"], "receipt_status": status,
            "raw_content_sha256": sha(canonical(content)), "api_request_ids": [r["request_id"] for r in requests],
            "physical_event_ids": [r["event_id"] for r in events]}


def assess(episode, current, previous, target):
    """Decide at the unit being taught: command, observation, recovery, or final claim."""
    text = target["content"]
    if episode["independent_truth"] is not True:
        require(episode["independent_status"] == "not_evaluable" and
                "elapsed_hover_duration_not_verified_by_current_public_api" in episode["common_public_reasons"],
                "unreviewed_unassessed_goal")
        reasons = ["elapsed_duration_unverifiable_episode"]
        if not current:
            require("5-second dwell is reported but unverified" in text and "[TASK DONE]" not in text,
                    "unreviewed_duration_final")
            reasons.append("honest_duration_limit_disclosed")
        return "excluded", reasons
    if any(r["receipt_status"] == "effect_unknown_at_return" for r in current):
        return "excluded", ["action_receipt_effect_unknown"]
    if not current:
        if episode["common_public_status"] != "verified":
            require("[TASK DONE]" in text and "fresh_pre_takeoff_position_anchor_required" in
                    episode["common_public_reasons"], "unreviewed_unverified_final")
            return "excluded", ["terminal_claim_without_public_confirmation"]
        # This exact reviewed claim asserts the cause of the timeout. Observations verify
        # the subsequent state but cannot identify the transport failure's root cause.
        if "The timeout on the take_off call was a response latency issue" in text:
            return "excluded", ["timeout_cause_not_observed"]
        require("[TASK DONE]" in text, "unreviewed_final")
        return "retained", ["completion_supported_by_public_and_independent_evidence"]
    reasons = ["action_receipt_and_committed_effect" if any(r["tool"] in ACTIONS for r in current)
               else "public_observation_returned"]
    if episode["common_public_status"] != "verified":
        reasons.append("individual_step_only")
    if any(r["receipt_status"] == "effect_unknown_at_return" for r in previous):
        require([r["tool"] for r in current] == ["get_drone_status"], "unreviewed_recovery")
        reasons.append("inspected_state_without_replaying_unknown_action")
    return "retained", reasons


def build(tokenizer):
    sources, inventory = verify_sources(ROOT)
    run = ROOT / "docs/results" / BATCH / "run-01"
    files = read(run, "campaign-seal.json")["files"]
    results_ref = sealed_artifact(run, files, "results.json")
    audit_path = run.parent / "completion-independent-audit.json"
    audit_bytes = audit_path.read_bytes()
    require(sha(audit_bytes) == COMMON_AUDIT_SHA256, "common_public_audit_changed")
    audits = {r["trial_id"]: r for r in parse(audit_bytes)["trials"]}
    results = {r["trial_id"]: r for r in read(run, "results.json")}
    selected = [t for t in read(run, "manifest.json")["trials"] if t["arm"] == "candidate"]
    require([t["condition"] for t in selected] == list(CONDITIONS), "candidate_inventory_changed")
    samples, decisions, attempts = [], [], []
    for trial in selected:
        name = trial["trial_id"]
        source, = (s for s in sources if s["batch"] == BATCH and s["episode"] == name)
        episode = episode_evidence(run, files, trial, results[name], audits[name])
        actor = read(run, f"trials/{name}/evaluation/result.json")["unified_completion"]["actor_id"]
        archive = lines(source["path"])
        api = lines(ROOT / episode["artifacts"]["public_api"]["path"])
        physical = lines(ROOT / episode["artifacts"]["physical_events"]["path"])
        turns, episode_attempts = read_turns(source)
        attempts.extend([{k: a[k] for k in ("episode", "logical_call_id", "attempt_id", "request_sequence",
                                              "status", "reason")} for a in episode_attempts])
        require(len(turns) == len(episode_attempts) and all(a["status"] == "response" for a in episode_attempts),
                "unreviewed_http_attempt")
        previous = []
        for turn in turns:
            sample, loc, history, calls = wire_decision(turn, archive)
            current = [current_receipt(c, loc, archive, api, physical, actor) for c in calls]
            status, reasons = assess(episode, current, previous, sample["target"])
            tokens = None
            if status == "retained":
                try:
                    _, _, tokens = supervise(tokenizer, sample["messages"], sample["tools"], sample["target"])
                except InputRejected as exc:
                    status, reasons = "excluded", [f"tokenization_{exc.code}"]
                else:
                    samples.append(sample)
            events = extract_events(turn["prefix"])
            evidence = {"episode": episode, "history_receipts": history, "current_receipts": current,
                        "public_projection_sha256": sha(canonical({"messages": sample["messages"], "tools": sample["tools"]})),
                        "target_sha256": sha(canonical(sample["target"])), "tool_names": [c["name"] for c in calls]}
            decisions.append({"sample_id": sample["sample_id"], "status": status, "reasons": reasons,
                              "source": loc, "evidence": evidence,
                              "events_before_reply": sorted({e["type"] for e in events["events"]}),
                              "tokenization": tokens})
            previous = current
    payload = {"samples.jsonl": jsonl(samples), "decisions.jsonl": jsonl(decisions)}
    manifest = {
        "usage": "previously_seen_development_fixtures_only", "eligible_for_training": False,
        "source_batch": BATCH, "run": "run-01", "selection": "all candidate episodes in sealed manifest order",
        "episodes": [t["trial_id"] for t in selected], "http_attempts": attempts,
        "campaign_seals_sha256": inventory["campaign_seals_sha256"], "campaign_sha256": inventory["campaign_seals"][BATCH],
        "common_audit": {"path": audit_path.relative_to(ROOT).as_posix(), "sha256": COMMON_AUDIT_SHA256},
        "results": results_ref, "status_counts": dict(Counter(r["status"] for r in decisions)),
        "sha256": {name: sha(raw) for name, raw in payload.items()},
    }
    payload["batch.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    return payload


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path, help="directory for the three reproducible artifacts")
    args = cli.parse_args()
    payload = build(load_tokenizer())
    args.output.mkdir(parents=True, exist_ok=True)
    # Publish each complete file once, manifest last. A retry may reuse identical files.
    with TemporaryDirectory(dir=args.output) as staging:
        for name, raw in payload.items():
            path = args.output / name
            require(not path.exists() or path.read_bytes() == raw, f"output_collision:{name}")
            (Path(staging) / name).write_bytes(raw)
        for name, raw in payload.items():
            path = args.output / name
            try:
                path.hardlink_to(Path(staging) / name)
            except FileExistsError:
                require(path.read_bytes() == raw, f"output_collision:{name}")
    print(json.dumps(parse(payload["batch.json"])["status_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
