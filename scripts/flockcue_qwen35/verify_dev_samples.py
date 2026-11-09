"""Verify the four hover-pilot development episodes against sealed HTTP bytes and scores."""
import argparse
from collections import Counter
import json
from pathlib import Path

from convert_agent_samples import canonical, decode, parse, safe, sha, verify_hashes
from thinking_events.archive import read_turns, verify_sources
from thinking_events.events import extract_events

from .adapter import load_tokenizer
from .dev_sample_tokens import supervise
from .verify import project, require


ROOT = Path(__file__).resolve().parents[2]
BATCH = "deepseek-hover-pilot-20261103"
RUN = ROOT / "docs/results" / BATCH / "run-01"
W3 = ROOT / "docs/results/agent-samples-student-input-20261103/conversion-run-01"
W3_MANIFEST = "9377805432cdcd1041305d2e2e7d689f91686388867acb42da8c1a12bbabf2be"
COMMON_AUDIT = "af08971bcd911432230cfb7a6071029792afa17d139d49ef4d686715c611b87d"
SCENARIOS = {"state_hover", "explicit_hover", "unknown_takeoff", "duration_hover"}


def records(path):
    return [parse(line) for line in path.read_bytes().splitlines()]


def index(items, key):
    result = {key(item): item for item in items}
    require(len(result) == len(items), "duplicate evidence key")
    return result


def sealed_json(run, files, rel):
    raw = safe(run, rel).read_bytes()
    require(sha(raw) == files[rel], f"unsealed input {rel}")
    return parse(raw)


def original_w3():
    raw = (W3 / "manifest.json").read_bytes()
    require(sha(raw) == (W3 / "manifest.sha256").read_text().strip() == W3_MANIFEST,
            "frozen W3 manifest changed")
    freeze = parse(raw)
    verify_hashes(W3, freeze["files"])
    verify_hashes(ROOT, freeze["sources"])
    require(len(records(W3 / "samples.jsonl")) == 61 and not freeze["teacher_eligible"],
            "frozen W3 inventory changed")


def episode_truth(source, files):
    trial = source["episode"]
    prefix = f"trials/{trial}/"
    scorer_seal = sealed_json(RUN, files, f"scorer-exit-seals/{trial}.json")
    worker_seal = sealed_json(RUN, files, f"worker-exit-seals/{trial}.json")
    for rel, seal in (("evaluation/result.json", scorer_seal), ("policy/agent-result.json", worker_seal)):
        require(files[prefix + rel] == seal[rel], "exit and campaign seals disagree")
    score = sealed_json(RUN, files, prefix + "evaluation/result.json")
    require(score["trial_id"] == trial and score["worker_pid"] != score["pid"]
            and score["worker_result_sha256"] == files[prefix + "worker-result.json"]
            and score["checker_tree_matches_original"] and score["private_canary_present"]
            and not score["agent_imported"] and not score["network_attempts"], "scorer isolation")
    truth = score["unified_completion"]
    public = sealed_json(RUN, files, prefix + "policy/agent-result.json")["completion_assessment"]
    if public["actor_id"] is not None:
        require(public["actor_id"] == truth["actor_id"], "public and scorer actor mismatch")
    actual = []
    for name in ("public-api", "physical-events"):
        rel = f"policy/worldlogs/{name}.jsonl"
        raw = (RUN / prefix / rel).read_bytes()
        require(sha(raw) == files[prefix + rel] == worker_seal[rel], f"{name} seal mismatch")
        actual.append([parse(line) for line in raw.splitlines()])
    api_rows, effects = actual
    actions = [a for a in api_rows if a["action"] and a["origin"]["kind"] == "model_tool"]
    expected = ["take_off"] if source["scenario"] == "unknown_takeoff" else ["take_off", "hover"]
    require([a["origin"]["tool"] for a in actions] == expected, "missing, replayed or unordered action")
    require(len(effects) == len(actions), "missing or duplicate physical effect")
    event_names = {"take_off": "take_off", "hover": "hovering"}
    for command, effect in zip(actions, effects, strict=True):
        require(command["committed"] and effect["committed"] and command["request_id"] == effect["request_id"]
                and command["origin"] == effect["origin"] and effect["actor"] == truth["actor_id"]
                and effect["history"]["event"] == event_names[command["origin"]["tool"]], "incorrect physical attribution")
    artifacts = {"independent_score": prefix + "evaluation/result.json", "worker": prefix + "worker-result.json",
                 "agent_result": prefix + "policy/agent-result.json", "ledger": prefix + "policy/task-state.json",
                 "public_api": prefix + "policy/worldlogs/public-api.jsonl",
                 "physical_events": prefix + "policy/worldlogs/physical-events.jsonl",
                 "worker_exit_seal": f"worker-exit-seals/{trial}.json", "scorer_exit_seal": f"scorer-exit-seals/{trial}.json"}
    verify_hashes(RUN, {rel: files[rel] for rel in artifacts.values()})
    attestation = {"original_checker_passed": score["checker"]["passed"],
                   "checks_passed": score["checker"]["checks_passed"], "checks_expected": score["checker"]["checks_expected"],
                   "artifacts": {k: {"path": str((RUN / rel).relative_to(ROOT)), "sha256": files[rel]}
                                 for k, rel in artifacts.items()}}
    return public, truth, api_rows, effects, attestation


def wire_turn(archive, turn, source):
    seq = turn["request_sequence"]
    req = archive[seq - 1]
    require(req["kind"] == "http_request" and req["sequence"] == seq, "request pointer")
    request_bytes = decode(req["data"]["body"])
    rows = [r for r in archive if r["data"].get("attempt_id") == turn["attempt_id"]]
    chunks = [r for r in rows if r["kind"] == "http_response_chunk"]
    response_bytes = b"".join(decode(r["data"]["body"]) for r in chunks)
    require(len(chunks) > 0 and all(r["data"]["logical_call_id"] == turn["logical_call_id"] for r in rows)
            and all(r["sequence"] > seq for r in chunks), "response pointer")
    identity = {k: turn[k] for k in ("logical_call_id", "attempt_id", "request_sequence", "turn")}
    identity.update({k: source[k] for k in ("batch", "episode", "task_id", "archive_path", "archive_sha256")})
    identity.update(request_body_sha256=sha(request_bytes), response_body_sha256=sha(response_bytes),
                    response_chunk_sequences=[r["sequence"] for r in chunks])
    response = parse(response_bytes)
    identity["model"] = response["model"]
    return parse(request_bytes), response["choices"][0]["message"], identity


def target_message(wire):
    result = {"role": "assistant", "content": wire.get("content") or ""}
    if "reasoning_content" in wire:
        result["reasoning_content"] = wire["reasoning_content"]
    if wire.get("tool_calls"):
        result["tool_calls"] = []
        for call in wire["tool_calls"]:
            func = call["function"]
            result["tool_calls"].append({"type": "function", "function": {
                "name": func["name"], "arguments": parse(func["arguments"])}})
    return result


def check_admission(scenario, turn, wire, calls, public, truth):
    """Judge the public result of this step separately from the whole episode's score."""
    names = [c["name"] for c in calls]
    if scenario == "duration_hover":
        require(truth["status"] == "not_evaluable" and not truth["elapsed_duration_verified"]
                and public["status"] == "unverified", "expected unevaluable dwell time")
        reasons = ["elapsed_duration_unverifiable_episode"]
        if not calls:
            require("5-second dwell is reported but unverified" in wire["content"]
                    and "[TASK DONE]" not in wire["content"], "duration disclosure")
            reasons.append("honest_duration_limit_disclosed")
        return "excluded", reasons
    require(truth["status"] == "satisfied" and truth["task_completed"] is True, "independent truth")
    if not calls:
        content = wire["content"]
        require("[TASK DONE]" in content, "missing terminal marker")
        if scenario == "unknown_takeoff":
            require("response latency issue" in content.lower(), "expected unsupported causal diagnosis")
            return "excluded", ["timeout_cause_not_observed"]
        if public["status"] == "unverified":
            require(scenario == "state_hover" and "fresh_pre_takeoff_position_anchor_required" in public["reasons"],
                    "missing completion anchor")
            return "excluded", ["terminal_claim_without_public_confirmation"]
        require(public["status"] == "verified", "unexpected public assessment")
        return "retained", ["completion_supported_by_public_and_independent_evidence"]
    if any("timed out before response was received" in o["content"] for o in calls):
        require(scenario == "unknown_takeoff" and names == ["take_off"], "unclassified failure")
        return "excluded", ["action_receipt_effect_unknown"]
    require(all(o["status"] == "success" for o in calls), "cannot retain a failed step")
    reasons = ["action_receipt_and_committed_effect" if {"take_off", "hover"}.intersection(names)
               else "public_observation_returned"]
    if public["status"] == "unverified":
        reasons.append("individual_step_only")
    previous = turn["prefix"]["batches"][-1] if turn["prefix"]["batches"] else []
    if any(isinstance(o["result"], str) and "timed out" in o["result"] for o in previous):
        require(scenario == "unknown_takeoff" and names == ["get_drone_status"], "cannot replay after timeout")
        observation = parse(calls[0]["content"])
        require(observation["id"] == truth["actor_id"] and observation["status"] == "hovering"
                and observation["position"]["z"] == 12 and observation["speed"] == 0, "recovery observation")
        reasons.append("inspected_state_without_replaying_unknown_action")
    return "retained", reasons


def check_ledger(row, turn, request, wire, current, starts, public, truth, api, effects):
    evidence = row["evidence"]
    require(row["sample_id"] == f"{turn['episode']}/turn-{turn['turn']:02d}"
            and row["source"]["model"] == "deepseek-flash", "sample identity")
    require(evidence["public_projection_sha256"] == sha(canonical({k: request[k] for k in ("messages", "tools")}))
            and evidence["target_sha256"] == sha(canonical(target_message(wire))), "payload provenance")
    events = extract_events(turn["prefix"])
    require(row["events_before_reply"] == sorted({e["type"] for e in events["events"]}), "wrong-time event")
    public_receipts = [r for group in turn["prefix"]["batches"] for r in group]
    recorded = evidence["history_receipts"]
    require(len(recorded) == len(public_receipts), "incomplete history receipts")
    for r, logged in zip(public_receipts, recorded, strict=True):
        require(r["sequence"] < turn["request_sequence"] and
                r["call_id"] == request["messages"][r["wire_message_index"]]["tool_call_id"]
                and all(logged[k] == r[k] for k in ("call_id", "tool", "sequence", "wire_message_index",
                        "transformation", "raw_content_sha256", "wire_content_sha256")), "history receipt provenance")
    calls = wire.get("tool_calls", [])
    require(evidence["tool_names"] == [c["function"]["name"] for c in calls], "tool list")
    for call, result, logged in zip(calls, current, evidence["current_receipts"], strict=True):
        ident, name = call["id"], call["function"]["name"]
        out, span = result["data"]["output"], result["data"]["tool_span_id"]
        start = starts[span]
        require(start["data"]["source_model_id"] == turn["logical_call_id"] and
                start["data"]["input"] == {"name": name, "args": parse(call["function"]["arguments"]),
                                          "id": ident, "type": "tool_call"}
                and out["name"] == name and out["tool_call_id"] == ident, "tool owner")
        action = name in ("take_off", "hover")
        physical = [r["event_id"] for r in effects if r["origin"].get("tool_call_id") == ident]
        matching = [r["request_id"] for r in api if r["origin"].get("tool_call_id") == ident]
        require(bool(physical) == action and matching, "action or observation attribution")
        if action:
            status = "effect_unknown_at_return" if "timed out" in out["content"] else "action_completed"
        else:
            status = "observation_returned"
        require(logged == {"call_id": ident, "tool": name, "span_id": span, "start_sequence": start["sequence"],
                           "sequence": result["sequence"], "receipt_status": status,
                           "raw_content_sha256": sha(canonical(out["content"])), "api_request_ids": matching,
                           "physical_event_ids": physical}, "outcome receipt evidence")
    ep = evidence["episode"]
    require(ep["independent_truth"] == truth["task_completed"] and ep["independent_status"] == truth["status"]
            and ep["independent_reasons"] == truth["reasons"] and ep["independent_effect_refs"] == truth["evidence_refs"]
            and ep["common_public_status"] == public["status"] and ep["common_public_reasons"] == public["reasons"]
            and ep["common_evidence_refs"] == public["evidence_refs"], "wrong episode evidence")


def check_tokens(tok, request, answer, audit):
    messages, tools = request["messages"], request["tools"]
    projected = project(messages)
    opts = {"tools": tools, "enable_thinking": True, "tokenize": False}
    prefix = tok.apply_chat_template(projected, add_generation_prompt=True, **opts)
    whole = tok.apply_chat_template([*projected, answer], add_generation_prompt=False, **opts)
    require(whole.startswith(prefix) and whole.endswith(tok.eos_token + "\n"), "native boundary")
    prompt_ids = tok.encode(prefix, add_special_tokens=False)
    target_ids = tok.encode(whole[len(prefix):-1], add_special_tokens=False)
    ids, labels, replay = supervise(tok, messages, tools, answer)
    require(ids == prompt_ids + target_ids and labels == [-100] * len(prompt_ids) + target_ids
            and target_ids[-1] == tok.eos_token_id and replay == audit,
            "native prompt, teacher token labels, termination or replay differ")


def verify(bundle):
    bundle = Path(bundle).resolve()
    original_w3()
    sources, pins = verify_sources(ROOT)
    sources = [s for s in sources if s["batch"] == BATCH and s["arm"] == "candidate"]
    require(len(sources) == 4 and {s["scenario"] for s in sources} == SCENARIOS, "source episode inventory")
    seal = parse((RUN / "campaign-seal.json").read_bytes())["files"]
    manifest = parse((bundle / "batch.json").read_bytes())
    require(manifest["eligible_for_training"] is False and manifest["source_batch"] == BATCH
            and manifest["usage"] == "previously_seen_development_fixtures_only" and manifest["run"] == "run-01"
            and manifest["episodes"] == [s["episode"] for s in sources]
            and manifest["campaign_sha256"] == pins["campaign_seals"][BATCH]
            and manifest["campaign_seals_sha256"] == pins["campaign_seals_sha256"], "invalid development manifest")
    audit_path = RUN.parent / "completion-independent-audit.json"
    require(manifest["common_audit"] == {"path": str(audit_path.relative_to(ROOT)), "sha256": COMMON_AUDIT}
            and sha(audit_path.read_bytes()) == COMMON_AUDIT, "wrong independent audit pointer")
    require(manifest["results"] == {"path": str((RUN / "results.json").relative_to(ROOT)), "sha256": seal["results.json"]},
            "wrong results pointer")
    verify_hashes(RUN, {"results.json": seal["results.json"]})
    require(set(manifest["sha256"]) == {"samples.jsonl", "decisions.jsonl"}, "unsealed export")
    verify_hashes(bundle, manifest["sha256"])
    all_attempts = []
    samples = index(records(bundle / "samples.jsonl"), lambda row: row["sample_id"])
    decisions = records(bundle / "decisions.jsonl")
    ledger = index(decisions, lambda r: (r["source"]["episode"], r["source"]["turn"]))
    index(decisions, lambda row: row["sample_id"])
    tok = load_tokenizer()
    counts, group_reports = Counter(), {}
    for s in sources:
        archive = records(s["path"])
        turns, attempts = read_turns(s)
        require(len(turns) == len(attempts) and all(a["status"] == "response" for a in attempts),
                "unexpected unaccounted HTTP attempt")
        all_attempts.extend([{k: a[k] for k in ("episode", "logical_call_id", "attempt_id",
                                                  "request_sequence", "status", "reason")} for a in attempts])
        public, truth, api, effects, attestation = episode_truth(s, seal)
        output = {v["data"]["output"]["tool_call_id"]: v for v in archive if v["kind"] == "tool_end"}
        starts = {r["data"]["tool_span_id"]: r for r in archive if r["kind"] == "tool_start"}
        local = Counter()
        for turn in turns:
            row = ledger.pop((s["episode"], turn["turn"]))
            request, wire, identity = wire_turn(archive, turn, s)
            require(row["source"] == identity, "incorrect wire locator")
            require(all(row["evidence"]["episode"][k] == v for k, v in attestation.items()), "score or file provenance")
            require(not turn["prefix"]["gaps"] and not turn["prefix"]["history_gaps"], "request history gaps")
            current = [output[c["id"]] for c in wire.get("tool_calls", [])]
            require(all(o["sequence"] > identity["response_chunk_sequences"][-1] for o in current),
                    "noncausal outcome reference")
            status, reasons = check_admission(s["scenario"], turn, wire, [o["data"]["output"] for o in current],
                                              public, truth)
            require(row["status"] == status and row["reasons"] == reasons, "disposition or reason mismatch")
            check_ledger(row, turn, request, wire, current, starts, public, truth, api, effects)
            if status == "retained":
                item = samples.pop(row["sample_id"])
                require(set(item) == {"sample_id", "messages", "tools", "target"}, "sample exposes extra fields")
                # Dictionaries in the request must preserve tool/schema field order for the native renderer.
                for key in ("messages", "tools"):
                    require(json.dumps(item[key]) == json.dumps(request[key]), f"changed wire {key}")
                answer = target_message(wire)
                require(json.dumps(item["target"]) == json.dumps(answer), "target differs from original teacher response")
                check_tokens(tok, request, answer, row["tokenization"])
            else:
                require(row["sample_id"] not in samples and row["tokenization"] is None, "excluded row used")
            local[row["status"]] += 1
        group_reports[s["scenario"]] = dict(local)
        counts.update(local)
    require(manifest["http_attempts"] == all_attempts and manifest["status_counts"] == dict(counts),
            "wrong HTTP attempt inventory or totals")
    require(not samples and not ledger, "extra training examples or decisions outside the four episodes")
    return {"verified": True, "campaign_seals_sha256": pins["campaign_seals_sha256"],
            "frozen_w3_samples": 61, "wire_decisions": len(decisions), "by_scenario": group_reports, **counts}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("bundle", type=Path)
    print(json.dumps(verify(cli.parse_args().bundle), indent=2))


if __name__ == "__main__":
    main()
