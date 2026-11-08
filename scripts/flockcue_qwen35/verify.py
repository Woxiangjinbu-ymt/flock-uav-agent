"""Reconstruct this two-slot Mac fixture from the sealed SDK, simulator and scorer records."""

import base64
from copy import deepcopy
import json
from pathlib import Path
import sys

from validate_harness_episode import check_hashes, sha

from .adapter import load_tokenizer


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def read(path):
    return json.loads(path.read_bytes())


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def project(messages):
    """Independent reference projection. Recover receipt ownership from SDK IDs."""
    history = deepcopy(messages)
    owners = {}
    pending = set()
    for msg in history:
        for call in msg.get("tool_calls", []):
            sdk_id, function = call["id"], call["function"]
            require(sdk_id not in owners, "reused SDK call ID")
            args = function["arguments"]
            if isinstance(args, str):
                function["arguments"] = json.loads(args)
            owners[sdk_id] = {"call": f"call_{len(owners) + 1}", **function}
            pending.add(sdk_id)
        if msg["role"] == "tool":
            sdk_id = msg["tool_call_id"]
            require(sdk_id in pending, "unknown or duplicate receipt")
            pending.remove(sdk_id)
            payload = json.dumps({**owners[sdk_id], "result": msg["content"]},
                                 sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            msg["content"] = payload.replace("<", r"\u003c").replace(">", r"\u003e")
    require(not pending, "missing receipt")
    return history


def verified_score(run, trial, worker, success):
    files = set()
    for kind in ("worker", "scorer"):
        seal = read(run / f"{kind}-exit-seals" / f"{trial.name}.json")
        check_hashes(trial, seal)
        files.update(seal)
    require(files == {p.relative_to(trial).as_posix() for p in trial.rglob("*") if p.is_file()},
            "incomplete evidence seals")
    score = read(trial / "evaluation/result.json")
    boundary = read(trial / "private/score-boundary.json")
    snapshot = sha((trial / "private/score-world.pkl").read_bytes())
    require(snapshot == worker["snapshot"]["sha256"] == score["snapshot_sha256"] == boundary["snapshot_sha256"],
            "wrong scoring snapshot")
    require(score["worker_result_sha256"] == sha((trial / "worker-result.json").read_bytes())
            and score["worker_pid"] == worker["pid"] != score["pid"] and score["trial_id"] == trial.name
            and score["checker_tree_matches_original"] and score["private_canary_present"]
            and not score["agent_imported"] and not score["network_attempts"], "invalid scoring isolation")
    check = score["checker"]
    require(not check["errors"] and check["passed"] is success and
            (check["checks_passed"], check["checks_expected"]) == (2 if success else 0, 2),
            "wrong original hover checker result")
    return {"passed": check["passed"], "checks_passed": check["checks_passed"],
            "checks_expected": check["checks_expected"], "snapshot_sha256": snapshot}


def verify_pair(messages, public):
    calls = messages[-3]["tool_calls"]
    receipts = messages[-2:]
    ids = [c["id"] for c in calls]
    require(len(set(ids)) == len(ids) == 2 and [m["tool_call_id"] for m in receipts] == ids[::-1],
            "two SDK receipts did not arrive in reverse call order")
    result = []
    for call, receipt in zip(reversed(calls), receipts, strict=True):
        func = call["function"]
        actor = json.loads(func["arguments"])["input_json"]["drone_id"]
        observation = json.JSONDecoder().raw_decode(receipt["content"])[0]
        source, = (r for r in public if r["origin"].get("tool_call_id") == call["id"])
        require(func["name"] == source["origin"]["tool"] == "get_drone_status" and
                source["endpoint"] == "/drones/" + actor and source["method"] == "GET" and
                actor == observation["id"] == source["response"]["id"] and
                observation["position"] == source["response"]["position"], "receipt belongs to wrong UAV")
        result.append({"call_id": call["id"], "drone_id": actor})
    require(result[0]["drone_id"] != result[1]["drone_id"], "expected two different UAVs")
    return result


def verify_trial(run, trial, scripts, tok):
    success = trial.name == "cpu-success"
    worker = read(trial / "worker-result.json")
    require((worker["trial_id"], worker["task_id"]) == (trial.name, "c74d7020"), "wrong hover task")
    check = verified_score(run, trial, worker, success)
    require(worker["drained"] and all(worker["isolation"].values()) and not worker["network_attempts"]
            and worker["agent"]["archive_verify"]["valid"] and worker["agent"]["harness_closed"],
            "worker isolation or archive lifecycle failed")
    archive, = (trial / "policy/modelio").glob("*.jsonl")
    events = rows(archive)
    requests = [e["data"] for e in events if e["kind"] == "http_request"]
    spans = [e["data"] for e in events if e["kind"] == "tool_start"]
    audit = rows(trial / "private/student-requests.jsonl")
    public = [r for r in rows(trial / "policy/worldlogs/public-api.jsonl") if r["origin"]["kind"] == "model_tool"]
    require(len(requests) == (6 if success else 2), "unexpected SDK request count")
    pair = []
    for turn, (req, row, fixture) in enumerate(zip(requests, audit, scripts, strict=True), 1):
        body = base64.b64decode(req["body"]["base64"], validate=True)
        require(sha(body) == row["request_body_sha256"] and row["thinking"] is success
                and (row["turn"], row["slot"]) == (turn, trial.name), "student/SDK request mismatch")
        wire = json.loads(body)
        history = project(wire["messages"])
        ids = tok.apply_chat_template(history, tools=wire["tools"], tokenize=True,
                                      enable_thinking=success, add_generation_prompt=True)
        require(ids == row["input_ids"], "student input tokens differ from reconstructed native prompt")
        expected = tok.encode(fixture, add_special_tokens=False)
        generated = row["generated_ids"]
        limit = row["max_new_tokens"]
        require(generated == expected[:limit], "student output tokens differ from the scripted generator")
        response = b"".join(base64.b64decode(e["data"]["body"]["base64"], validate=True)
                            for e in events if e["kind"] == "http_response_chunk" and
                            e["data"]["attempt_id"] == req["attempt_id"])
        if success and turn == 3:
            pair = verify_pair(wire["messages"], public)
        if not success and turn == 2:
            cut = tok.decode(generated, skip_special_tokens=False)
            require(len(generated) == limit < len(expected) and cut.count("</tool_call>") == 1
                    and "<function=take_off>" in cut.split("</tool_call>")[0]
                    and "<tool_call>" in cut.split("</tool_call>")[1]
                    and "<function=hover>" in fixture.split("</tool_call>")[1]
                    and not response and row["message"] is None and not row["terminated"]
                    and row["stop_reason"] == "generation_truncated",
                    "negative fixture did not hit the token limit inside the second call")
        else:
            require(row["message"] == json.loads(response)["choices"][0]["message"]
                    and row["status"] == "ok" and row["terminated"]
                    and len(expected) == len(generated), "complete native continuation rejected")
    api_names = [r["origin"]["tool"] for r in public]
    source_names = [s["input"]["name"] for s in spans]
    expected_tools = (["list_drones", "get_drone_status", "get_drone_status", "take_off", "hover",
                       "get_drone_status"] if success else ["list_drones"])
    require(source_names == api_names == expected_tools, "unexpected SDK or public tool dispatch")
    actions = worker["actions"]
    committed = [r for r in public if r["action"]]
    require(len(actions) == len(committed) == (2 if success else 0)
            and worker["physical_cost"]["physical_history_events"] == len(committed)
            and worker["physical_cost"]["action_attempts"] == len(committed),
            "unexpected number of physical actions")
    for action, api in zip(actions, committed, strict=True):
        actor = pair[-1]["drone_id"]
        require(action["committed"] and action["status"] == "success" and
                action["request_id"] == api["request_id"] and
                api["endpoint"] == f"/drones/{actor}/command/{action['tool']}" and
                all(e["actor"] == actor for e in api["history_events"]) and api["history_events"],
                "physical action mismatch")
    if success:
        require(actions[0]["params"] == {"altitude": 12}, "wrong takeoff altitude")
    reason = worker["limits"]["stop_reason"]
    require(reason == (None if success else "generation_truncated"), "unexpected terminal status")
    return {"reproduced_requests": len(audit), "reversed_receipts": pair,
            "tool_dispatches": api_names, "physical_actions": [a["tool"] for a in actions],
            "stop_reason": reason, "checker": check}


def verify_inputs(run):
    run = Path(run).resolve()
    manifest = read(run / "manifest.json")
    backend = sha((run / "manifest.json").read_bytes())
    require(backend == (run / "manifest.sha256").read_text().strip() ==
            (run / "selected-backend.sha256").read_text().strip(),
            "changed run manifest")
    check_hashes(run / "source", manifest["sources"])
    check_hashes(run / "inputs", manifest["inputs"])
    require({"scripts/flockcue_qwen35/adapter.py", "scripts/flockcue_qwen35/smoke.py",
             "scripts/flockcue_qwen35/verify.py", "scripts/thinking_events/worker.py"} <= manifest["sources"].keys(),
            "unsealed fixture or runtime sources")
    fixture_path = run / "fixture-replies.json"
    require(sha(fixture_path.read_bytes()) == manifest["fixture_sha256"], "changed fixture replies")
    fixtures = read(fixture_path)
    require(set(fixtures) == {t.name for t in (run / "trials").iterdir()} == {"cpu-success", "cpu-truncated"},
            "unexpected slot set")
    require([(t["task_id"], t["trial_id"]) for t in sorted(manifest["trials"], key=lambda t: t["trial_id"])]
            == [("c74d7020", name) for name in sorted(fixtures)], "wrong manifest task or slot")
    initial = [read(run / "trials" / name / "worker-result.json")["initial"] for name in fixtures]
    require(initial[0] == initial[1] and initial[0]["original_state_sha256"] and
            initial[0]["original_checker_tree_sha256"], "starting scenes or original checker differ")
    tok = load_tokenizer(run / "source/training/tokenizers/qwen35-dev")
    return {name: verify_trial(run, run / "trials" / name, fixture, tok) for name, fixture in fixtures.items()}


if __name__ == "__main__":
    print(json.dumps(verify_inputs(sys.argv[1]), ensure_ascii=False, indent=2))
