"""Audit the CPU session from the original scene, retained task boundaries and checker responses."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "experiments/flockcue-benchmark-600-v1"


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def read(path):
    return json.loads(path.read_bytes())


def canonical_sha(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def owned(run, name):
    candidate = run / name
    resolved = candidate.resolve()
    require(resolved.is_relative_to(run) and not Path(name).is_absolute() and resolved == candidate,
            f"Artifact escapes the run or uses an alias: {name}")
    return resolved


def original_scene(run, session_id):
    manifest = read(DATASET / "manifest.json")
    archive = DATASET / manifest["sessions_archive"]["path"]
    selected = DATASET / manifest["selection"]["path"]
    require(file_sha(archive) == manifest["sessions_archive"]["sha256"], "Changed 600-task scene archive")
    require(file_sha(selected) == manifest["selection"]["sha256"], "Changed confirmed 600-task population")
    entry, = (s for g in read(selected)["strata"] for s in g["sessions_selected"] if s["session_id"] == session_id)
    with zipfile.ZipFile(archive) as scenes:
        raw = scenes.read(entry["filename"])
    require(hashlib.sha256(raw).hexdigest() == entry["source_sha256"], "Original scene differs from its seal")
    require((run / "source-scene.json").read_bytes() == raw, "Executed scene differs from original scene bytes")
    scene = json.loads(raw)
    tasks = [t["id"] for t in scene["tasks"]]
    require(len(tasks) == len(set(tasks)) == 20 and tasks == entry["task_ids_in_original_order"],
            "Original twenty task identities or order differ")
    return scene


def process_evidence(run, role):
    exit_record = read(run / f"{role}-exit.json")
    observed = read(run / f"{role}-boundary.json")
    require(exit_record["role"] == observed["role"] == role and
            type(exit_record["pid"]) is int and exit_record["pid"] == observed["pid"], f"{role} identity")
    require(exit_record["returncode"] == 0 and not exit_record.get("watchdog_expired") and 0 < exit_record["started_ns"] <= exit_record["finished_ns"],
            f"{role} process did not finish cleanly")
    require(observed["network_attempts"] == [], f"{role} attempted an external connection")
    if role == "scorer":
        require(observed["agent_imported"] is False, "Scorer imported the policy")
    for filename, expected in exit_record["files"].items():
        require(file_sha(owned(run, filename)) == expected, f"{role} artifact digest: {filename}")
    require(f"{role}-boundary.json" in exit_record["files"], f"Unsealed {role} process boundary")
    return exit_record


def check_responses(tree, checker):
    """Recompute AND/OR/NOT and negative expectations using archived response values."""
    iterator = iter(checker["details"])
    counts = [0, 0]

    def evaluate(node, depth):
        record = next(iterator, None)
        require(record is not None and record["depth"] == depth, "Missing or reordered checker details")
        if "checks" in node:
            children = node["checks"]
            logic = node.get("logic", "and").lower()
            require(record["type"] == "group" and record["count"] == len(children) and
                    record["logic"].lower() == logic, "Original checker group differs")
            values = [evaluate(child, depth + 1) for child in children]
            if logic == "and":
                return all(values)
            if logic == "or":
                return any(values)
            require(logic == "not", "Unsupported checker logic")
            return not all(values) if values else True
        expected = node.get("expect", True)
        if isinstance(expected, dict):
            expected = expected["result"]
        require(record["type"] == "leaf" and not record.get("error") and
                record["endpoint"] == node["endpoint"] and record["parameters"] == node.get("parameters", {})
                and record["expect"] is expected, "Original checker leaf differs")
        actual = record["response"]["result"]
        require(type(actual) is bool and record["actual"] is actual, "Invalid checker endpoint response")
        passed = actual == expected
        require(record["result"] is passed, "Checker result differs from actual response and expectation")
        counts[0] += 1
        counts[1] += int(passed)
        return passed

    passed = evaluate(tree, 0)
    require(next(iterator, None) is None and not checker["errors"], "Additional or failed checker details")
    require(checker["passed"] is passed and counts == [checker["checks_expected"], checker["checks_passed"]],
            "Checker aggregate differs from recorded responses")
    return passed, counts[0]


def check_projection(state):
    require(canonical_sha(state["blackboard"]) == state["blackboard_sha256"], "Blackboard digest mismatch")
    require(set(state["blackboard"]) == {"drones", "targets", "obstacles"} and
            all(isinstance(entries, list) for entries in state["blackboard"].values()), "Blackboard format")
    require(isinstance(state["world_state_sha256"], str) and len(state["world_state_sha256"]) == 64,
            "Missing simulation digest")


def check_prepare(before, start):
    """Check grounding and charging while preserving XY and the cross-task board."""
    require(before["positions"].keys() == start["positions"].keys(), "Drone identities changed in prepare")
    for drone, old in before["positions"].items():
        current = start["positions"][drone]
        require((old["x"], old["y"]) == (current["x"], current["y"]), "Preparation reset XY")
        require(abs(current["z"]) <= 1e-9 and current["battery_level"] == 100 and current["status"] == "idle",
                f"{drone} has not landed and recharged")
    require(before["blackboard"] == start["blackboard"], "Preparation reset the session blackboard")


def check_initial(scene, session, initial):
    expected_drones = {d["id"]: d for d in scene["drones"]}
    require(set(initial["positions"]) == set(expected_drones), "Initial drone fleet differs from the source")
    for drone_id, actual in initial["positions"].items():
        source = expected_drones[drone_id]
        for axis in ("x", "y", "z"):
            require(actual[axis] == source["position"][axis], f"Initial {axis} differs for {drone_id}")
        require(actual["battery_level"] == source["battery_level"], f"Initial battery differs for {drone_id}")
    require(not any(initial["blackboard"].values()) and initial["clock_seconds"] == 0, "Dirty session start")
    require(initial["world_state_sha256"] == session["initial_world_sha256"] and
            initial["blackboard_sha256"] == session["initial_board_sha256"], "Initial session digests")


def check_wire(path, task):
    """Read the final serialized SDK inputs and responses, including a real mock HTTP error."""
    responses = []
    chunks = {}
    tools_returned = []
    request_ids = []
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    require(records[0]["kind"] == "archive_start" and records[-1]["kind"] == "archive_end", "Incomplete model archive")
    private_tokens = {b"/check/", b"execution_check_apis", b"related_apis", b"mark-done", b"is_passed",
                      b"world.pkl", b"eligibility.json", b"boundary.json"}

    def private_endpoints(node):
        if "checks" in node:
            for child in node["checks"]:
                private_endpoints(child)
        else:
            private_tokens.add(node["endpoint"].encode())
    def decode(body):
        raw = base64.b64decode(body["base64"], validate=True)
        require(len(raw) == body["length"] and hashlib.sha256(raw).hexdigest() == body["sha256"], "SDK wire digest")
        return raw

    private_endpoints(task["execution_check_apis"])
    for record in records:
        data = record["data"]
        if record["kind"] == "http_request":
            raw = decode(data["body"])
            require(not any(token in raw for token in private_tokens), "Private checker or admin data in policy input")
            require(isinstance(json.loads(raw)["messages"], list), "Missing model messages")
            request_ids.append(data["attempt_id"])
        elif record["kind"] == "http_response_start":
            responses.append((data["attempt_id"], data["status_code"]))
            chunks[data["attempt_id"]] = bytearray()
        elif record["kind"] == "http_response_chunk":
            chunks[data["attempt_id"]].extend(decode(data["body"]))
    for key, status in responses:
        if status == 200:
            reply = json.loads(chunks[key])["choices"][0]["message"]
            tools_returned.extend(call["function"]["name"] for call in reply.get("tool_calls", []))
    require(request_ids == [key for key, _ in responses] and len(request_ids) == len(set(request_ids)),
            "Missing or reordered SDK responses")
    return [status for _, status in responses], tools_returned


def check_actions(actions):
    requests = {r["request_id"]: r for r in actions["requests"]}
    require(len(requests) == len(actions["requests"]), "Duplicate simulator request")
    for row in requests.values():
        require(row["admin"] is False and not row["endpoint"].startswith("/check/") and
                "/tasks/" not in row["endpoint"], "Private endpoint used by policy")
    committed = 0
    for event in actions["events"]:
        request = requests[event["request_id"]]
        require(event["phase"] == "task" and event["committed"] is request["committed"] and
                event["origin"] == request["origin"] and request["action"] and request["method"] == "POST",
                "Physical event not bound to a public command")
        committed += int(event["committed"])
    return committed


def verify_run(directory, fault):
    run = directory.resolve(strict=True)
    inputs = read(run / "run-input.json")
    require(inputs["scope"] == "stateful_session_cpu_regression" and inputs["fault"] == fault, "Wrong run scope")
    session_id = inputs["session_id"]
    scene = original_scene(run, session_id)
    require(file_sha(run / "source-scene.json") == inputs["scene_sha256"] and
            file_sha(DATASET / "manifest.json") == inputs["dataset_manifest_sha256"], "Input selection changed")
    worker, scorer = (process_evidence(run, role) for role in ("worker", "scorer"))
    require(worker["pid"] != scorer["pid"] and worker["finished_ns"] <= scorer["started_ns"],
            "Scorer did not run independently after worker exit")
    require({"source-scene.json", "run-input.json", "session.json"} <= set(worker["files"]) and
            "session_scores.json" in scorer["files"] and worker["files"].keys().isdisjoint(scorer["files"]),
            "Incomplete or overlapping process inventories")
    session = read(run / "session.json")
    scores = read(run / "session_scores.json")
    tasks = session["tasks"]
    ids = [task["id"] for task in scene["tasks"]]
    require(inputs["expected_task_ids"] == [row["task_id"] for row in tasks] == ids and
            [row["index"] for row in tasks] == list(range(1, 21)), "Session ledger differs from original order")
    require(scene["id"] == session["session_id"] == scores["session_id"] == session_id and
            session["worker_pid"] == scores["worker_pid"] == worker["pid"] and scores["scorer_pid"] == scorer["pid"],
            "Session process identities or session ID disagree")
    first = None
    previous = None
    carried = 0
    moved = 0
    carried_board = 0
    carried_displacement = 0
    total_leaves = 0
    expected_scores = []
    deployment_passed = 0
    rows = []
    for index, (task, row) in enumerate(zip(scene["tasks"], tasks, strict=True), 1):
        task_id = task["id"]
        status, reason = "completed", "agent_finished"
        if fault == "abort-after-1" and index == 2:
            status, reason = "invalid", "provider_503"
        if fault == "abort-after-1" and index > 2:
            status, reason = "unstarted", "upstream_service_failure"
        if fault == "deadline" and index == 2:
            reason = "deadline"
        require((row["status"], row["reason"]) == (status, reason), f"{task_id} unexpected disposition")
        relative = f"tasks/{index:02}-{task_id}"
        folder = owned(run, relative)
        if status == "unstarted":
            require(not folder.exists(), f"Unexpected artifacts for unstarted {task_id}")
            rows.append({"task_id": task_id, "status": status, "reason": reason, "checker_passed": None,
                         "agent_success": None, "deployment_passed": False})
            continue
        require(row["directory"] == relative and
                all(f"{relative}/{name}" in worker["files"] for name in ("before.json", "boundary.json", "world.pkl")),
                f"{task_id} missing sealed input, boundary or snapshot")
        before = read(folder / "before.json")
        boundary = read(folder / "boundary.json")
        prepare, start = before["prepare_before"], before["policy_start"]
        terminal, continuation = boundary["terminal"], boundary["continuation"]
        for sample in (prepare, start, terminal, continuation):
            check_projection(sample)
        if previous is None:
            first = prepare
            check_initial(scene, session, first)
        else:
            require(previous == prepare, f"{task_id} did not inherit the full previous continuation")
            carried += 1
            carried_board += int(any(prepare["blackboard"].values()))
            carried_displacement += int(any((pos["x"], pos["y"]) !=
                (first["positions"][actor]["x"], first["positions"][actor]["y"])
                for actor, pos in prepare["positions"].items()))
        check_prepare(prepare, start)
        require(start["clock_seconds"] - prepare["clock_seconds"] >= 4,
                f"{task_id} landing and charging did not advance the simulator clock")
        previous = continuation
        require(start["clock_seconds"] <= terminal["clock_seconds"] <= continuation["clock_seconds"],
                f"{task_id} simulation clock regressed")
        for drone_id, current in terminal["positions"].items():
            old = start["positions"][drone_id]
            changed = any(old[axis] != current[axis] for axis in ("x", "y", "z"))
            if changed and current["history_count"] > old["history_count"]:
                moved += 1
        digest = canonical_sha(task["execution_check_apis"])
        require(boundary["task_id"] == task_id and boundary["reason"] == reason and
                boundary["entry_board_sha256"] == start["blackboard_sha256"] and
                boundary["world_state_sha256"] == terminal["world_state_sha256"], f"{task_id} policy/simulator boundary")
        require(set(continuation["marked_done"]) == (set(terminal["marked_done"]) |
                ({task_id} if boundary["marked_done"] else set())), f"{task_id} mark-done did not reach the world")
        require(boundary["checker_sha256"] == digest and boundary["world_sha256"] == worker["files"][relative + "/world.pkl"]
                and boundary["worker_pid"] == worker["pid"], f"{task_id} private snapshot or checker binding")
        if reason == "deadline":
            probe = boundary["deadline_probe"]
            require(probe["rejected"] is True and probe["unchanged"] is True and probe["endpoint"] and
                    probe["before_sha256"] == probe["after_sha256"] and probe["before_sha256"] == terminal["world_state_sha256"] and
                    boundary["score_seconds"] == boundary["deadline_seconds"],
                    "Post-deadline mutation was accepted or changed the world")
        passed = None
        leaf_results = None
        if status == "completed":
            require(relative + "/score.json" in scorer["files"], f"{task_id} missing independent result")
            score = read(folder / "score.json")
            require(score["worker_pid"] == worker["pid"] and score["scorer_pid"] == scorer["pid"] and
                    score["task_id"] == task_id and score["world_sha256"] == boundary["world_sha256"] and
                    score["checker_sha256"] == digest, f"{task_id} score not bound to task and frozen world")
            passed, count = check_responses(task["execution_check_apis"], score["checker"])
            leaf_results = [detail["result"] for detail in score["checker"]["details"] if detail["type"] == "leaf"]
            total_leaves += count
            expected_scores.append({"task_id": task_id, "passed": passed})
            require(boundary["marked_done"] is (passed and reason == "agent_finished"), "Invalid mark-done result")
            if reason == "agent_finished":
                require(relative + "/eligibility.json" in worker["files"], f"{task_id} missing eligibility receipt")
                gate = read(folder / "eligibility.json")
                require(gate["pid"] not in (worker["pid"], scorer["pid"]) and gate["passed"] is passed and
                        gate["task_id"] == task_id and gate["world_sha256"] == score["world_sha256"] and
                        gate["agent_imported"] is False and gate["network_attempts"] == [], f"{task_id} eligibility")
            # The original deployment protocol credits a passed frozen-world check at
            # timeout if the worker exited safely. Mark-done uses a separate rule.
            deployment_passed += int(passed)
        else:
            require(not (folder / "score.json").exists(), f"Scored invalid task {task_id}")
        require(boundary["archive"] in worker["files"] and relative + "/actions.json" in worker["files"],
                f"{task_id} unsealed model or simulator log")
        dispositions, tools_returned = check_wire(owned(run, boundary["archive"]), task)
        actions = read(folder / "actions.json")
        events = check_actions(actions)
        committed = [r["endpoint"] for r in actions["requests"] if r["action"] and r["committed"]]
        require(boundary["http_statuses"] == dispositions, f"{task_id} worker disagrees with recorded SDK statuses")
        if reason == "provider_503":
            require(dispositions == [503] and
                    read(folder / "fault.json")["http_status"] == 503 and not tools_returned and
                    not committed and not events and not boundary["agent_success"], "Missing or misaccounted SDK service failure")
        else:
            require(dispositions == [200] * (2 if reason == "deadline" else 5), f"{task_id} unexpected SDK response")
        if reason == "deadline":
            require("take_off" in tools_returned and "take_off" == read(folder / "fault.json")["delayed_tool"] and
                    not committed and not events and boundary["agent_success"] is False,
                    "Delayed model action missing or changed the frozen world")
        if reason == "agent_finished":
            require(events > 0, f"{task_id} no committed physical action")
        rows.append({"task_id": task_id, "status": status, "reason": reason,
                     "checker_passed": passed, "checker_leaf_results": leaf_results,
                     "agent_success": boundary["agent_success"], "deployment_passed": passed is True,
                     "physical_events": events, "model_requests": len(dispositions)})
    require(expected_scores == scores["tasks"], "Session score summary differs from checked terminal snapshots")
    expected_folders = {row["directory"].split("/")[-1] for row in tasks if row["status"] != "unstarted"}
    require({p.name for p in (run / "tasks").iterdir()} == expected_folders, "Unexpected task directories")
    if fault != "abort-after-1":
        require(moved and carried == 19 and carried_board and carried_displacement, "Missing physical action or blackboard carryover evidence")
    reproducibility = {key: inputs[key] for key in ("baseline", "system", "machine", "python_version")}
    reproducibility["source_map_sha256"] = canonical_sha(inputs["source_sha256"])
    return {"session_id": session_id, "scene_sha256": inputs["scene_sha256"],
            "configuration": reproducibility, "worker_pid": worker["pid"],
            "scorer_pid": scorer["pid"], "initial_world_sha256": first["world_state_sha256"],
            "initial_board_sha256": first["blackboard_sha256"], "initial_positions": first["positions"],
            "planned": len(tasks), "scored": len(expected_scores),
            "raw_passed": sum(row["passed"] for row in expected_scores), "deployment_passed": deployment_passed,
            "checker_leaves": total_leaves, "transitions_with_displaced_xy": carried_displacement, "verified_transitions": carried, "with_nonempty_blackboard": carried_board,
            "drone_position_and_history_changes": moved, "tasks": rows}


def verify_suite(run, restart=None, interrupted=None, timeout=None):
    normal = verify_run(run, "none")
    result = {"normal": normal}
    directories = {run.resolve()}
    for name, path, fault in (("restart", restart, "none"), ("interrupted", interrupted, "abort-after-1"),
                              ("timeout", timeout, "deadline")):
        if path is None:
            continue
        require(path.resolve() not in directories, "Each evidence run needs its own directory")
        directories.add(path.resolve())
        other = verify_run(path, fault)
        for key in ("session_id", "scene_sha256", "configuration", "initial_world_sha256", "initial_board_sha256", "initial_positions"):
            require(other[key] == normal[key], f"{name} initialized with a different {key}")
        require(other["worker_pid"] != normal["worker_pid"], f"{name} reused the original worker")
        if name == "restart":
            for original, repeated in zip(normal["tasks"], other["tasks"], strict=True):
                for key in ("task_id", "status", "reason", "checker_passed", "checker_leaf_results", "deployment_passed"):
                    require(repeated[key] == original[key], f"Cold restart changed {key} on {original['task_id']}")
        result[name] = other
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    for mode in ("restart", "interrupted", "timeout"):
        parser.add_argument("--" + mode, type=Path)
    print(json.dumps(verify_suite(**vars(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
