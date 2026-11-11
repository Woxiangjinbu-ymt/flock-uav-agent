"""Read-only acceptance oracle for the fixed eight general-agent-v1 local probes.

Standard library only: no runner, Agent, simulator, SDK, pickle, or subprocess
imports. This verifies scripted integration behavior, not natural-model ability.
The three trace hashes and two task IDs below deliberately limit its scope.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import argparse
import base64
import hashlib
import json


ORDER = ("raw", "skill", "second-task", "premature", "http_error", "cancel",
         "request_timeout", "usage_missing")
FAULTS = {"http_error": "http_error", "cancel": "cancelled",
          "request_timeout": "request_timeout", "usage_missing": "unknown_usage"}
SUCCESS = {"raw", "skill", "second-task"}
TRACES = {
    "raw": "7ccfc45bb491e1b0a96dbbeac90cabaeaca47ff9bffdeddd19a2a51505d18540",
    "skill": "daebf78f63cbc3c674ba30eaa523ddb85776c93804cb3d7a01a501ff7fb07378",
    "premature": "277e9e859a4ab388d4740f9ece0eeec9b183f47dd1aa42c7147e6a5df9b1b2eb",
}
COMMANDS = {
    "b1eb0270": "Drone Drone 3 takes off to 14 meters altitude, then rotates to face heading 353 degrees and hovers.",
    "898cfdca": "Drone Drone 12 should take off to 10 meters, then climb to 30 meters altitude and hover.",
}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def parse(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique)


def path(root, name):
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Evidence path is not relative")
    candidate = root / candidate
    if any(p.is_symlink() for p in (candidate, *candidate.parents)):
        raise ValueError("Symlink in evidence path")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence path outside run")
    return candidate


def read(root, name):
    return parse(path(root, name).read_bytes())


def rows(file):
    raw = file.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Unterminated JSONL evidence")
    return [parse(line) for line in raw.splitlines()]


def hashes(root, expected):
    if not expected or not isinstance(expected, dict):
        raise ValueError("Empty or invalid seal")
    for name, digest in expected.items():
        if sha(path(root, name).read_bytes()) != digest:
            raise ValueError("Evidence hash mismatch: " + name)


def decode(value):
    raw = base64.b64decode(value["base64"], validate=True)
    if type(value["length"]) is not int or len(raw) != value["length"] or sha(raw) != value["sha256"]:
        raise ValueError("Archived bytes do not match their hash/length")
    return raw


def wire(archive):
    attempts, models, identity, end = {}, {}, None, None
    for sequence, row in enumerate(archive, 1):
        if row["sequence"] != sequence or end is not None:
            raise ValueError("Archive sequence or closing boundary invalid")
        key = (row["command_id"], row["episode_id"], row["archive_id"])
        identity = identity or key
        if key != identity:
            raise ValueError("Mixed archive ownership")
        kind, item = row["kind"], row["data"]
        if kind == "model_start":
            if item["logical_call_id"] in models:
                raise ValueError("Duplicate model call")
            models[item["logical_call_id"]] = False
        elif kind == "model_end":
            logical = item["logical_call_id"]
            if logical not in models or models[logical]:
                raise ValueError("Unmatched model end")
            models[logical] = True
        elif kind == "http_request":
            ident, logical = item["attempt_id"], item["logical_call_id"]
            if ident in attempts or logical not in models or models[logical]:
                raise ValueError("Unmatched HTTP request")
            attempts[ident] = {"request": decode(item["body"]), "logical": logical,
                               "response": None, "chunks": [], "end": None}
        elif kind in {"http_response_start", "http_response_chunk", "http_response_end"}:
            attempt = attempts[item["attempt_id"]]
            if attempt["logical"] != item["logical_call_id"] or attempt["end"] is not None:
                raise ValueError("Invalid response association")
            if kind == "http_response_start":
                if attempt["response"] is not None or models[attempt["logical"]]:
                    raise ValueError("Late or duplicate response")
                attempt["response"] = item
            elif kind == "http_response_chunk":
                if (attempt["response"] is None or models[attempt["logical"]]
                        or item["chunk_index"] != len(attempt["chunks"]) + 1):
                    raise ValueError("Misordered response chunk")
                attempt["chunks"].append(decode(item["body"]))
            else:
                body = b"".join(attempt["chunks"])
                if (item["length"], item["sha256"], item["chunk_count"]) != (len(body), sha(body), len(attempt["chunks"])):
                    raise ValueError("Response aggregate mismatch")
                if item["response_observed"] is not (attempt["response"] is not None):
                    raise ValueError("Response observation mismatch")
                attempt["end"] = item
        elif kind == "archive_end":
            end = item
    if not archive or archive[0]["kind"] != "archive_start" or not attempts or end is None or not all(models.values()):
        raise ValueError("Missing or unclosed model archive")
    return list(attempts.values()), end, identity


def policy_clean(policy, canary, archived):
    """Inspect real byte bodies and nested JSON strings, not only encoded logs."""
    def clean(value):
        if isinstance(value, bytes):
            if canary.encode() in value:
                return False
            try:
                return clean(parse(value))
            except (ValueError, UnicodeError):
                return True
        if isinstance(value, str):
            return clean(value.encode())
        if isinstance(value, dict):
            if any(value.get(k) not in (None, [], {}) for k in ("execution_check_apis", "related_apis")):
                return False
            if {"base64", "length", "sha256"} <= value.keys() and not clean(decode(value)):
                return False
            return all(clean(v) for k, v in value.items() if k != "base64")
        if isinstance(value, list):
            return all(clean(v) for v in value)
        return True
    for file in policy.rglob("*"):
        if not file.is_file():
            continue
        raw = file.read_bytes()
        if canary.encode() in raw:
            return False
        if file.suffix == ".jsonl":
            if not all(clean(row) for row in rows(file)):
                return False
        elif not clean(raw):
            return False
    return all(clean(a["request"]) and clean(b"".join(a["chunks"])) for a in archived)


def _audit(run):
    checks = {}
    manifest = read(run, "manifest.json")
    checks["manifest_hash"] = sha(path(run, "manifest.json").read_bytes()) == path(run, "manifest.sha256").read_text().strip()
    seal = read(run, "campaign-seal.json")["files"]
    hashes(run, seal)
    checks["exact_campaign_inventory"] = set(seal) == {str(p.relative_to(run)) for p in run.rglob("*")
        if p.is_file() and p.name != "campaign-seal.json" and "__pycache__" not in p.parts}
    hashes(run / "source", manifest["sources"])
    hashes(run / "inputs", manifest["inputs"])
    results = read(run, "results.json")
    summary = read(run, "summary.json")
    trials = manifest["trials"]
    checks["fixed_eight_case_order"] = tuple(t["trial_id"] for t in trials) == tuple(r["trial_id"] for r in results) == ORDER
    checks["local_only_scope"] = (manifest["scope"] == "general_agent_v1_local_integration"
        and type(manifest["online_authorized_executions"]) is int and manifest["online_authorized_executions"] == 0
        and manifest["local_model"] == "local-scripted-sdk" and summary["online_ready"] is False)
    if not checks["fixed_eight_case_order"]:
        raise ValueError("Oracle only accepts the fixed eight-case matrix")
    processes, initials = [], {}
    for trial, row in zip(trials, results):
        ident = trial["trial_id"]
        def check(name, value):
            checks[ident + ":" + name] = bool(value)
        task = "898cfdca" if ident == "second-task" else "b1eb0270"
        check("fixed_task_and_fault", trial["task_id"] == task and trial.get("fault") == (
            {"kind": ident, "at_response": 2} if ident in FAULTS else None))
        trace_type = "skill" if ident in {"skill", "second-task"} else "premature" if ident == "premature" else "raw"
        trace = read(run / "inputs", trial["trace_file"])
        check("fixed_public_trace", sha(json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()) == TRACES[trace_type])
        folder = path(run, "trials/" + ident)
        worker = read(folder, "worker-result.json")
        score = read(folder, "evaluation/result.json")
        check("result_records_match", row["worker"] == worker and row["score"] == score)
        hashes(folder, worker["files"])
        hashes(folder, read(run, "worker-exit-seals/" + ident + ".json"))
        hashes(folder, read(run, "scorer-exit-seals/" + ident + ".json"))
        check("score_bound_to_worker", score["worker_result_sha256"] == sha(path(folder, "worker-result.json").read_bytes())
              and score["snapshot_sha256"] == worker["snapshot"]["sha256"] == sha(path(folder, "private/score-world.pkl").read_bytes()))
        ps = row["processes"]
        check("joined_distinct_worker_scorer", len(ps) == 2 and [p["mode"] for p in ps] == ["worker", "score"]
              and [p["pid"] for p in ps] == [worker["pid"], score["pid"]] and score["worker_pid"] == worker["pid"]
              and ps[0]["pid"] != ps[1]["pid"] and all(p["exited"] is True and p["returncode"] == 0
              and p["kill_reason"] is None and p["trial_id"] == ident for p in ps))
        processes.extend(ps)
        for process in ps:
            check(process["mode"] + "_log_hash", process["log_sha256"] == sha(path(run, "process-logs/" + ident + "-" + process["mode"] + ".log").read_bytes()))
        check("no_network", worker["network_attempts"] == score["network_attempts"] == [])
        check("independent_scoring", score["agent_imported"] is False and score["private_canary_present"] is True
              and score["checker_tree_matches_original"] is True and score["checker"]["errors"] == [])
        check("expected_task_score", score["checker"]["passed"] is (ident in SUCCESS))
        check("empty_entry", worker["entry"]["frames"] == worker["entry"]["ledger_events"] == worker["entry"]["ledger_model_requests"] == 0)
        initials[ident] = worker["initial"]
        state = read(folder, "policy/task-state.json")
        check("original_command_retained", state["command"] == COMMANDS[task] and row["result"]["public_evidence"]["original_command"] == COMMANDS[task])
        check("three_result_layers", row["result"]["public_evidence"]["whole_task"] == "not_evaluated"
              and row["result"]["independent_evaluation"]["passed"] is score["checker"]["passed"]
              and row["result"]["run"]["agent_returned_normally"] is (ident not in FAULTS))
        limits = read(folder, "private/request-limits.json")
        check("limits_match_and_drained", worker["limits"] == limits and worker["drained"] is True
              and limits["closed"] is True and not limits["threads_still_alive"] and not limits["needs_process_termination"]
              and all(worker["isolation"].values()))
        check("expected_stop", limits["stop_reason"] == FAULTS.get(ident)
              and worker["agent"]["success"] is (ident not in FAULTS))
        harness = read(folder, "policy/harness.json")
        frames = harness["frames"]
        check("actual_skill_frames", (len(frames) == 1 and frames[0]["skill"] == "simple_chain"
              and frames[0]["status"] == "completed" and frames[0]["cursor"] == 3)
              if ident in {"skill", "second-task"} else frames == [])
        public = rows(path(folder, "policy/worldlogs/public-api.jsonl"))
        actions = [a for a in public if a["action"]]
        expected_actions = (["take_off", "change_altitude", "hover"] if ident == "second-task" else
            ["take_off", "rotate", "hover"] if ident in SUCCESS else [] if ident == "premature" else ["take_off"])
        check("actual_action_sequence", [a["endpoint"].rsplit("/", 1)[-1] for a in actions] == expected_actions
              and all(a["committed"] is True and a["response"]["status"] == "success" and not a.get("error") for a in actions))
        kind = "skill" if ident in {"skill", "second-task"} else "model_tool"
        check("actual_action_path", all(a["origin"]["kind"] == kind for a in actions))
        check("public_api_only", all(a["phase"] == "task" and not a["admin"] and not a["endpoint"].startswith("/check/") for a in public))
        if actions:
            fleet = next(a["response"] for a in public if a.get("origin", {}).get("tool_call_id") == "script-0")
            name = "Drone 12" if ident == "second-task" else "Drone 3"
            actors = [a["id"] for a in fleet if a.get("name") == name]
            check("actor_from_public_fleet", len(actors) == 1 and all(a["endpoint"].split("/")[2] == actors[0] for a in actions))
            physical = rows(path(folder, "policy/worldlogs/physical-events.jsonl"))
            takeoffs = [e for e in physical if e["request_id"] == actions[0]["request_id"] and e["history"]["event"] == "take_off"]
            altitude = 10.0 if ident == "second-task" else 14.0
            check("physical_takeoff_effect", actions[0]["params"] == {"altitude": altitude}
                  and len(takeoffs) == 1 and takeoffs[0]["committed"] is True
                  and takeoffs[0]["history"]["position"]["z"] == altitude and takeoffs[0]["actor"] == actors[0])
        files = list(path(folder, "policy/modelio").glob("*.jsonl"))
        if len(files) != 1:
            raise ValueError("Ambiguous archive")
        archived, end, identity = wire(rows(files[0]))
        check("archive_ownership_and_complete", identity[0] == worker["entry"]["command_id"]
              and end["complete"] is (ident not in FAULTS) and worker["agent"]["archive_verify"]["valid"] is True
              and worker["agent"]["archive_verify"]["complete"] is end["complete"]
              and worker["agent"]["archive_info"]["complete"] is end["complete"])
        check("no_private_policy_data", policy_clean(folder / "policy", manifest["private_canary"], archived))
        delegated = rows(path(folder, "private/local-transport.jsonl"))
        attempts = limits["attempts"]
        count = 5 if ident == "raw" else 1 if ident == "premature" else 3
        check("actual_trace_consumption", len(delegated) == len(archived) == len(attempts) == count
              and [r["index"] for r in delegated] == list(range(count))
              and limits["delegated_http_attempts"] == limits["admitted_http_attempts"] == count)
        check("dispatch_archive_controller_join", all(a["delegated"] is True
              and a["request_body_sha256"] == d["body_sha256"] == sha(w["request"])
              and a["request_body_length"] == len(w["request"]) for a, d, w in zip(attempts, delegated, archived)))
        check("known_usage_sum", limits["known_total_tokens"] == sum((a.get("usage") or {}).get("total_tokens", 0) for a in attempts))
        for index, (attempt, archived_attempt) in enumerate(zip(attempts, archived)):
            delivered = attempt["delivered_to_sdk"]
            check("response_delivery_" + str(index), archived_attempt["end"] is not None
                  and (archived_attempt["response"] is not None) is delivered
                  and bool(archived_attempt["chunks"]) is delivered
                  and archived_attempt["end"]["complete"] is delivered)
        if ident in FAULTS:
            final = attempts[-1]
            check("fault_reached_after_two_replies", final["attempt_id"] == 3 and final["outcome"] == FAULTS[ident])
            if ident == "http_error":
                check("http_500_observed", final["status_code"] == 500 and parse(b"".join(archived[-1]["chunks"]))["error"]["type"] == "server_error")
            elif ident == "cancel":
                life = read(folder, "private/lifecycle.json")
                check("cancel_linearized", life["cancelled"] is True and life["requested_at"] <= life["effective_at"] and final["delivered_to_sdk"] is False)
            elif ident == "request_timeout":
                check("real_request_timeout", final["wait_wall_seconds"] >= final["admitted_timeout_seconds"] and final["delivered_to_sdk"] is False)
            else:
                diagnostic = parse(decode(final["diagnostic_response"]["body"]))
                check("missing_usage_not_synthesized", "usage" not in diagnostic and final["usage"] is None and final["delivered_to_sdk"] is False)
    checks["same_initial_state_for_same_task"] = all(initials[name] == initials["raw"] for name in ORDER if name != "second-task")
    checks["second_task_distinct_initial_state"] = initials["second-task"] != initials["raw"]
    checks["all_fresh_processes"] = len({p["pid"] for p in processes}) == 16
    checks["serial_join_before_next_start"] = all(a["ended_ns"] <= b["started_ns"] for a, b in zip(processes, processes[1:]))
    checks["process_journal_matches"] = rows(path(run, "process-ledger.jsonl")) == processes
    checks["summary_matches_results"] = summary["trials"] == [{"trial_id": r["trial_id"], **r["result"]} for r in results]
    return checks


def audit(run):
    try:
        checks = _audit(Path(run).resolve())
        error = None
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, RecursionError) as exc:
        checks = {"evidence_readable_and_consistent": False}
        error = type(exc).__name__ + ": " + str(exc)
    return {"schema_version": 1, "passed": bool(checks) and all(checks.values()),
        "check_count": len(checks), "checks": checks, "failed_checks": [k for k, v in checks.items() if not v],
        "error": error, "scope": "fixed eight scripted local integration probes; not natural-model success rate",
        "provider_calls": 0, "online_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if output.is_relative_to(run):
        raise ValueError("Audit output must be outside the immutable run")
    result = audit(run)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
