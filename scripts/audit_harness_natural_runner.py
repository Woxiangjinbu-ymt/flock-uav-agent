"""Independent, read-only joins for local natural-runner acceptance artifacts.

Standard library only. Never imports the runner, Agent, simulator, or checker;
never starts a child. Boolean checks supplement, not replace, archive verification.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path


KILLS = {"hung_drain": "transport_not_drained", "watchdog_hang": "watchdog"}
SUCCESS = {"skill_success", "raw_success", "post_kill_B"}
ERRORS = {"http_error", "request_timeout", "task_deadline", "cancel", "http_limit", "usage_missing", "usage_stop"}


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(body, object_pairs_hook=unique)


def _path(root, name):
    value = Path(name)
    path = value if value.is_absolute() else root / value
    if ".." in value.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Unsafe evidence path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence outside expected directory")
    return path


def _rows(path):
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("Unterminated evidence line")
    return [_json(line) for line in raw.splitlines()]


def _decode(value):
    raw = base64.b64decode(value["base64"], validate=True)
    if type(value["length"]) is not int or len(raw) != value["length"] or _sha(raw) != value["sha256"]:
        raise ValueError("Byte evidence mismatch")
    return raw


def _wire(rows):
    """Read exact byte evidence without trusting archive_end.complete."""
    attempts, models, end = {}, {}, None
    identity = None
    for sequence, row in enumerate(rows, 1):
        if row["sequence"] != sequence or end is not None:
            raise ValueError("Invalid archive order")
        key = (row["command_id"], row["episode_id"], row["archive_id"])
        identity = identity or key
        if key != identity:
            raise ValueError("Mixed archive identity")
        kind, d = row["kind"], row["data"]
        if kind == "model_start":
            if d["logical_call_id"] in models:
                raise ValueError("Duplicate logical model")
            models[d["logical_call_id"]] = False
        elif kind == "model_end":
            if d["logical_call_id"] not in models or models[d["logical_call_id"]]:
                raise ValueError("Unmatched model end")
            models[d["logical_call_id"]] = True
        elif kind == "http_request":
            if d["attempt_id"] in attempts or d["logical_call_id"] not in models or models[d["logical_call_id"]]:
                raise ValueError("Unmatched request")
            attempts[d["attempt_id"]] = {"request": _decode(d["body"]), "logical": d["logical_call_id"],
                "chunks": [], "response": None, "end": None}
        elif kind in {"http_response_start", "http_response_chunk", "http_response_end"}:
            item = attempts[d["attempt_id"]]
            if item["logical"] != d["logical_call_id"] or item["end"] is not None:
                raise ValueError("Mixed attempt/response")
            if kind == "http_response_start":
                if item["response"] is not None or models[item["logical"]]:
                    raise ValueError("Late/duplicate response")
                item["response"] = d
            elif kind == "http_response_chunk":
                if item["response"] is None or models[item["logical"]] or d["chunk_index"] != len(item["chunks"]) + 1:
                    raise ValueError("Misordered chunk")
                item["chunks"].append(_decode(d["body"]))
            else:
                body = b"".join(item["chunks"])
                if (d["length"], d["sha256"], d["chunk_count"]) != (len(body), _sha(body), len(item["chunks"])):
                    raise ValueError("Response aggregate mismatch")
                if d["response_observed"] is not (item["response"] is not None):
                    raise ValueError("Response observation mismatch")
                item["end"] = d
        elif kind == "archive_end":
            end = d
    if not rows or rows[0]["kind"] != "archive_start" or not attempts:
        raise ValueError("Missing wire archive")
    return list(attempts.values()), end, identity, models


def audit_trial(run: Path, trial: dict, row: dict) -> dict[str, bool]:
    """Return fail-closed checks on existing local artifacts; no mutation."""
    try:
        return _audit_trial(Path(run), trial, row)
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, AssertionError):
        return {"oracle_evidence_readable_and_consistent": False}


def _audit_trial(run, trial, row):
    ident = trial["trial_id"]
    if Path(ident).name != ident or ident != row["trial_id"] or trial["scenario"] != ident:
        raise ValueError("Wrong trial identity")
    if ident not in SUCCESS | ERRORS | set(KILLS):
        raise ValueError("Unknown fixed local case")
    folder = _path(run, "trials/" + ident)
    policy = _path(folder, "policy")
    archives = list(_path(policy, "modelio").glob("*.jsonl"))
    if len(archives) != 1:
        raise ValueError("Ambiguous archive")
    archive_path = _path(policy, archives[0])
    archived, archive_end, identity, models = _wire(_rows(archive_path))
    oracle = _rows(_path(folder, "private/local-transport.jsonl"))
    requests = [e for e in oracle if e["kind"] == "delegated_request"]
    responses = [e for e in oracle if e["kind"] == "produced_response"]
    if any(e["kind"] not in {"delegated_request", "produced_response"} for e in oracle):
        raise ValueError("Unknown oracle event")
    if [e["index"] for e in requests] != list(range(len(requests))) or len({e["index"] for e in responses}) != len(responses):
        raise ValueError("Duplicated or missing oracle request")
    by_response = {e["index"]: e for e in responses}
    checks = {"oracle_evidence_readable_and_consistent": True,
        "oracle_request_bytes_match_archive": len(archived) >= len(requests) and all(
            (e["body_sha256"], e["body_length"]) == (_sha(a["request"]), len(a["request"]))
            for e, a in zip(requests, archived)),
        "oracle_responses_join_requests": all(0 <= e["index"] < len(requests)
            and requests[e["index"]]["at"] <= e["at"] for e in responses)}
    raw = _rows(_path(policy, "worldlogs/public-api.jsonl"))
    physical = _rows(_path(policy, "worldlogs/physical-events.jsonl"))
    actions = [e for e in raw if e["phase"] == "task" and e["action"]]
    expected_commands = ["take_off", "rotate", "hover"] if ident in SUCCESS else ["take_off"]
    checks["raw_exact_successful_action_sequence"] = ([e["endpoint"].rsplit("/", 1)[-1] for e in actions] == expected_commands
        and all(e["committed"] is True and not e.get("error") and e["response"]["status"] == "success" and not e["admin"] for e in actions))
    fleet = next(e["response"] for e in raw if e.get("origin", {}).get("tool_call_id") == "local-list")
    actors = [e["id"] for e in fleet if e.get("name") == "Drone 3"]
    if len(actors) != 1:
        raise ValueError("Public actor unavailable")
    actor = actors[0]
    takeoff = actions[0]
    histories = [e for e in physical if e["request_id"] == takeoff["request_id"] and e["history"]["event"] == "take_off"]
    checks["actual_public_takeoff_and_physical_effect"] = (takeoff["params"] == {"altitude": 14.0}
        and takeoff["response"]["drone_id"] == actor and len(histories) == 1
        and histories[0]["actor"] == actor and histories[0]["committed"] is True
        and histories[0]["history"]["start_position"]["z"] == 0
        and histories[0]["history"]["position"]["z"] == 14
        and histories[0]["vertical_distance"] == 14)
    checks["all_task_world_requests_public"] = all(e["phase"] == "task" and not e["admin"]
        and not e["endpoint"].endswith("/check") and not e["endpoint"].startswith("/check/") for e in raw)

    killed = ident in KILLS
    if killed:
        processes = row["processes"]
        checks["kill_after_real_third_dispatch"] = (len(requests) == 3 and set(by_response) == {0, 1}
            and len(archived) == 3 and archived[2]["response"] is None and not archived[2]["chunks"])
        checks["kill_joined_and_no_score_or_completed_worker"] = (row["status"] == "incomplete" and row["score"] is None
            and row["worker"] is None and len(processes) == 1 and processes[0]["exited"] is True
            and processes[0]["returncode"] != 0 and processes[0]["kill_reason"] == KILLS[ident]
            and not (folder / "evaluation").exists() and not (folder / "worker-result.json").exists())
        checks["kill_archive_not_complete"] = (archive_end is not None and archive_end["complete"] is False) if ident == "hung_drain" else archive_end is None
        for i, a in enumerate(archived):
            if i < 2:
                checks[f"delivered_response_{i}_bytes_match_oracle"] = _delivered(a, by_response[i])
        if ident == "watchdog_hang":
            return checks
        limits = _json(_path(folder, "private/request-limits.json").read_bytes())
        checks["poison_notice_matches_joined_worker"] = _json(_path(folder, "termination-required.json").read_bytes())["pid"] == processes[0]["pid"]
        checks["poison_is_unresolved_transport"] = limits["needs_process_termination"] is True and bool(limits["threads_still_alive"])
    else:
        worker = row["worker"]
        limits = _json(_path(folder, "private/request-limits.json").read_bytes())
        checks["controller_snapshot_matches_worker"] = limits == worker["limits"]
        checks["archive_path_and_command_owned_by_worker"] = (Path(worker["agent"]["archive_info"]["path"]) == archive_path
            and worker["entry"]["command_id"] == identity[0])
        checks["closed_drained_controller"] = limits["closed"] is True and not limits["threads_still_alive"] and not limits["needs_process_termination"] and not limits["reusable"]
        complete = ident in SUCCESS
        checks["matrix_expected_archive_complete_or_partial"] = (archive_end is not None
            and archive_end["complete"] is complete and worker["agent"]["archive_info"]["complete"] is complete
            and worker["agent"]["archive_verify"]["complete"] is complete and all(models.values()))

    attempts = limits["attempts"]
    checks["controller_attempt_ids_and_counts"] = ([e["attempt_id"] for e in attempts] == list(range(1, len(attempts) + 1))
        and limits["admitted_http_attempts"] == len(attempts)
        and limits["delegated_http_attempts"] == sum(e["delegated"] is True for e in attempts) == len(requests))
    checks["controller_requests_join_archive"] = len(attempts) <= len(archived) and all(
        (e["request_body_sha256"], e["request_body_length"]) == (_sha(a["request"]), len(a["request"]))
        for e, a in zip(attempts, archived))
    # In this fixed matrix all admitted attempts are dispatched; only the final
    # http_limit/usage_stop hook attempt is denied before becoming a controller job.
    extra = 1 if ident in {"http_limit", "usage_stop"} else 0
    checks["denied_hook_attempt_not_counted_as_dispatch"] = len(archived) == len(attempts) + extra and all(e["delegated"] is True for e in attempts)
    known = 0
    for i, a in enumerate(archived):
        attempt = attempts[i] if i < len(attempts) else None
        produced = by_response.get(i)
        delivered = bool(attempt and attempt["delivered_to_sdk"])
        if delivered:
            checks[f"delivered_response_{i}_bytes_match_oracle"] = produced is not None and _delivered(a, produced)
        else:
            # watchdog_hang returns above with an open archive. hung_drain is
            # killed only after the main thread has closed its partial archive.
            checks[f"undelivered_response_{i}_absent_from_archive"] = a["response"] is None and not a["chunks"] and (
                a["end"] is not None and a["end"]["complete"] is False)
        if attempt is None:
            checks[f"denied_attempt_{i}_no_oracle_response"] = produced is None
            continue
        usage = attempt["usage"]
        if usage is not None:
            checks[f"usage_{i}_matches_independent_oracle"] = produced is not None and usage == produced.get("usage") and _valid_usage(usage)
            known += usage["total_tokens"]
        elif produced is not None:
            checks[f"unknown_usage_{i}_not_synthesized"] = produced.get("usage") is None
        if produced is not None and not delivered:
            diagnostic = attempt.get("late_response") or attempt.get("diagnostic_response")
            body = _decode(diagnostic["body"]) if diagnostic else None
            checks[f"undelivered_response_{i}_private_diagnostic_matches"] = (body is not None
                and (_sha(body), len(body), diagnostic["status_code"]) == (produced["body_sha256"], produced["body_length"], produced["status_code"]))
            if body is not None:
                checks[f"undelivered_response_{i}_not_copied_into_policy"] = all(
                    body not in p.read_bytes() and base64.b64encode(body) not in p.read_bytes()
                    for p in policy.rglob("*") if p.is_file())
    checks["known_usage_sum_exact"] = limits["known_total_tokens"] == known
    return checks


def _delivered(attempt, oracle):
    body = b"".join(attempt["chunks"])
    return (attempt["response"] is not None and attempt["end"] is not None and attempt["end"]["complete"] is True
        and (attempt["response"]["status_code"], _sha(body), len(body)) ==
            (oracle["status_code"], oracle["body_sha256"], oracle["body_length"]))


def _valid_usage(usage):
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return all(type(usage.get(k)) is int and usage[k] >= 0 for k in keys) and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
