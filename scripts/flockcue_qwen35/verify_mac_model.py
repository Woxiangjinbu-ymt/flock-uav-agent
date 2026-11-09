"""Audit the single real-weight Mac hover attempt from its sealed SDK and simulator evidence."""
import base64
import hashlib
import json
import math
from pathlib import Path
import sys

from thinking_events.student import SAMPLING, output_budget
from validate_harness_episode import check_hashes, sha
from .adapter import EOS_IDS, FILES, REVISION, load_tokenizer, parse_generation, render
from .verify import project, read, require, rows

# LFS object reported by the official Qwen/Qwen3.5-2B tree at REVISION.
OFFICIAL_WEIGHT_SHA = "aa33250c4fc64891ddfaba3a314fd9542ea371843c387178b425fbcc5ed680b1"
SLOT = "mac-qwen35-off"
TASK = "c74d7020"
ACTOR = "3d229034"


def verify_identity(run, manifest):
    require(sha((run / "manifest.json").read_bytes()) == (run / "manifest.sha256").read_text().strip(),
            "run manifest changed")
    require(manifest["scope"] == "qwen35_mac_real_weights_dev_hover" and not (run / "fixture-replies.json").exists(),
            "expected a real single-task Mac run")
    check_hashes(run / "source", manifest["sources"])
    check_hashes(run / "inputs", manifest["inputs"])
    require({"scripts/flockcue_qwen35/mac_model.py", "scripts/flockcue_qwen35/mac_smoke.py",
             "scripts/flockcue_qwen35/adapter.py", "scripts/thinking_events/worker_child.py"}
            <= manifest["sources"].keys(), "missing frozen inference or worker code")
    trial, = manifest["trials"]
    require((trial["trial_id"], trial["task_id"]) == (SLOT, TASK)
            and {p.name for p in (run / "trials").glob("*")} <= {SLOT}, "wrong trial set")
    expected_limits = {"max_http_attempts": 12, "max_total_tokens": 100000, "max_output_tokens": 2048,
                       "per_request_timeout_seconds": 120}
    require(trial["runtime"]["task_seconds"] == 500 and trial["runtime"]["request_limits"] == expected_limits,
            "changed limits for the hover task")
    return trial


def check_runtime(model, runtime):
    for key, value in {"model": "Qwen3.5-2B", "revision": REVISION, "backend": "mlx",
                       "weight_sha256": OFFICIAL_WEIGHT_SHA}.items():
        require(model[key] == runtime[key] == value, "unexpected model identity: " + key)
    require(runtime["weights_loaded"] is True and model["thinking"] is False
            and set(model["eos_ids"]) == EOS_IDS, "unexpected model mode or EOS")
    require(runtime["config_sha256"] == FILES["config.json"] and runtime["weight_bytes"] == 4548221488
            and not runtime["quantized"] and runtime["precision"] and runtime["sampling"] == model["sampling"] == SAMPLING
            and set(runtime["eos_ids"]) == EOS_IDS and runtime["mlx"] and runtime["mlx_lm"] and runtime["device"],
            "unexpected weights, sampling or runtime")


def score_evidence(run, folder, worker, summary):
    score = read(folder / "evaluation/result.json")
    boundary = read(folder / "private/score-boundary.json")
    files = set()
    for kind in ("worker", "scorer"):
        seal_path = run / f"{kind}-exit-seals/{SLOT}.json"
        require(sha(seal_path.read_bytes()) == summary[kind + "_seal_sha256"], "exit seal mismatch")
        seal = read(seal_path)
        check_hashes(folder, seal)
        files.update(seal)
    require(files == {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()},
            "incomplete evidence seals")
    snapshot = sha((folder / "private/score-world.pkl").read_bytes())
    require(snapshot == worker["snapshot"]["sha256"] == score["snapshot_sha256"] == boundary["snapshot_sha256"],
            "scoring snapshot differs from the worker's final state")
    require((worker["trial_id"], worker["task_id"]) == (SLOT, TASK) and score["trial_id"] == SLOT
            and score["worker_result_sha256"] == sha((folder / "worker-result.json").read_bytes())
            and score["worker_pid"] == worker["pid"] == summary["worker_pid"] != score["pid"] == summary["scorer_pid"]
            and score["checker_tree_matches_original"] and score["private_canary_present"]
            and not score["agent_imported"] and not score["network_attempts"], "scoring isolation or identity changed")
    require(worker["drained"] and all(worker["isolation"].values()) and not worker["network_attempts"]
            and worker["agent"]["harness_closed"] and worker["agent"]["archive_verify"]["valid"],
            "worker isolation or archive lifecycle failed")
    require(worker["initial"]["original_state_sha256"] == "8e068a9b0ce6a7f5646f7bcb402ccc350469cb00e42d588f7e916eb10eefb4b3"
            and worker["initial"]["original_checker_tree_sha256"] == "4d4fa723a9a704b74bae94a9ca1865a3194d8aa319eb88a6d061cc11d0f18df2",
            "original world or checker differs from the accepted v5 hover fixture")
    check = score["checker"]
    altitude, ground = check["details"]["checks"]
    require(altitude["endpoint"] == "/check/drone_altitude" and ground["endpoint"] == "/check/drone_on_ground"
            and altitude["parameters"]["expected_altitude"] == 12.0 and ground["expected"] is False
            and altitude["parameters"]["drone_id"] == ground["parameters"]["drone_id"] == ACTOR,
            "not the intended original hover checker")
    require(not check["errors"] and check["checks_expected"] == 2
            and check["checks_passed"] == sum(c["result"] for c in (altitude, ground))
            and check["passed"] is (check["checks_passed"] == 2)
            and check["passed"] is summary["passed"], "inconsistent checker decision")
    return {"passed": check["passed"], "checks_passed": check["checks_passed"], "checks_expected": 2,
            "target_drone": ACTOR, "altitude_m": altitude["check_response"]["value"],
            "flight_state": ground["check_response"].get("status"), "snapshot_sha256": snapshot}


def turns_evidence(folder, worker, tok):
    archive, = (folder / "policy/modelio").glob("*.jsonl")
    events = rows(archive)
    requests = [e["data"] for e in events if e["kind"] == "http_request"]
    attempts = worker["limits"]["attempts"]
    path = folder / "private/student-requests.jsonl"
    records = rows(path) if path.is_file() else []
    require(len({r["turn"] for r in records}) == len(records), "duplicate recorded generation")
    records = {r["turn"]: r for r in records}
    require(len(requests) == len(attempts) and set(records) <= set(range(1, 1 + len(requests))),
            "worker and SDK request counts mismatch")
    issued = {}
    results = []
    spent = 0
    for turn, (request, attempt) in enumerate(zip(requests, attempts, strict=True), 1):
        body = base64.b64decode(request["body"]["base64"], validate=True)
        require(sha(body) == attempt["request_body_sha256"] and turn == attempt["attempt_id"],
                "SDK request not in worker budget log")
        wire = json.loads(body)
        ids = tok.apply_chat_template(project(wire["messages"]), tools=wire["tools"], tokenize=True,
                                      enable_thinking=False, add_generation_prompt=True)
        parts = [e["data"] for e in events if e["kind"] == "http_response_chunk"
                 and e["data"]["attempt_id"] == request["attempt_id"]]
        response = b"".join(base64.b64decode(r["body"]["base64"], validate=True) for r in parts)
        row = records.get(turn)
        item = {"turn": turn, "sdk_prompt_tokens": len(ids), "completion_tokens": None, "tool_calls": []}
        results.append(item)
        if row is None:
            require(not response and not attempt["delivered_to_sdk"], "missing successful generation record")
            item["stop_reason"] = attempt["outcome"]
            continue
        require(row["request_body_sha256"] == sha(body) and row["slot"] == SLOT
                and row["spent_before"] == spent and math.isfinite(row["round_trip_seconds"]),
                "student request identity or token accounting failed")
        seed = int.from_bytes(hashlib.sha256(f"{TASK}/{turn}".encode()).digest()[:4], "big")
        require(row["seed"] == seed, "generation seed differs from frozen rule")
        item["round_trip_seconds"] = row["round_trip_seconds"]
        item["stop_reason"] = row.get("stop_reason")
        if row.get("input_ids") is not None:
            require(ids == row["input_ids"], "model input differs from reconstructed SDK token IDs")
        generated = row.get("generated_ids")
        if generated is None:
            require(row["status"] != "ok" and row["message"] is None and not response and not row.get("usage"),
                    "failed generation produced an SDK response or claimed known usage")
            continue
        require(row.get("thinking") is False and row["input_ids"] == ids, "wrong generation mode or prompt")
        cap, reason = output_budget(len(ids), spent)
        require(reason is None and row["max_new_tokens"] == cap, "generation cap differs")
        _, prepared = render(tok, wire["messages"], wire["tools"], False)
        parsed = parse_generation(tok, generated, EOS_IDS, False, prepared, cap)
        usage = row["usage"]
        require((usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]) ==
                (len(ids), len(generated), len(ids) + len(generated)), "wrong usage")
        item["completion_tokens"] = len(generated)
        item["reasoning_tokens"] = parsed["reasoning_tokens"]
        item["peak_mlx_allocated_bytes"] = row["peak_allocated_bytes"]
        timing = row["timing"]
        require(timing["timing_source"] == "mlx_metal_token_stream" and item["peak_mlx_allocated_bytes"] > 0
                and 0 <= timing["generation_seconds"] <= row["round_trip_seconds"]
                and row["sampling"] == SAMPLING, "missing real engine timing, memory or settings")
        item["timing"] = {k: timing[k] for k in ("ttft_seconds", "generation_seconds", "decode_tokens_per_second")}
        if row.get("late_generation"):
            require(row["stop_reason"] in {"episode_deadline", "request_timeout"} and row["message"] is None
                    and row["status"] == "stopped" and not response, "late generation reached the SDK")
            continue
        require(all(row[k] == v for k, v in parsed.items()), "raw completion parses differently")
        if row["status"] != "ok":
            require(parsed["message"] is None and parsed["anomalies"] and not response,
                    "rejected completion escaped into the SDK")
            continue
        require(not parsed["anomalies"] and parsed["terminated"] and parsed["message"] is not None,
                "invalid completion admitted")
        spent += usage["total_tokens"]
        # A late result can be archived internally although the SDK deadline closed.
        if not response:
            require(not attempt["delivered_to_sdk"], "completed response not in SDK archive")
            continue
        sdk = json.loads(response)
        require(sdk["model"] == "Qwen3.5-2B" and sdk["choices"][0]["message"] == parsed["message"]
                and sdk["usage"] == usage, "SDK response differs from parsed completion")
        for call in parsed["message"].get("tool_calls", []):
            func = call["function"]
            require(call["id"] not in issued, "duplicate generated call ID")
            issued[call["id"]] = (func["name"], json.loads(func["arguments"]), request["logical_call_id"])
            item["tool_calls"].append(func["name"])
    spans = [e["data"] for e in events if e["kind"] == "tool_start"]
    dispatched = {}
    for span in spans:
        call = span["input"]
        require(call["id"] not in dispatched and issued.get(call["id"]) ==
                (call["name"], call["args"], span["source_model_id"]), "dispatch changed model decision")
        dispatched[call["id"]] = call["name"]
    return results, dispatched


def action_evidence(folder, worker, dispatched):
    public = rows(folder / "policy/worldlogs/public-api.jsonl")
    for call in public:
        origin = call["origin"]
        if origin["kind"] == "model_tool":
            require(dispatched.get(origin["tool_call_id"]) == origin["tool"], "API call lacks model tool span")
    api_actions = [r for r in public if r["action"] and r["phase"] == "task"]
    physical = worker["actions"]
    require(len(api_actions) == len(physical) == worker["physical_cost"]["action_attempts"],
            "number of actions differs between worker and simulator")
    result = []
    for api, action in zip(api_actions, physical, strict=True):
        require(api["request_id"] == action["request_id"] and api["committed"] == action["committed"]
                and api["endpoint"] == action["endpoint"], "simulator action mismatch")
        result.append({k: action[k] for k in ("tool", "endpoint", "params", "committed", "status")})
    return result


def verify(run):
    run = Path(run).resolve()
    manifest = read(run / "manifest.json")
    trial = verify_identity(run, manifest)
    report = read(run.parent / "smoke.json")
    row = report["row"]
    require(report["scope"] == manifest["scope"] and (row["trial_id"], row["task_id"]) == (SLOT, TASK)
            and report["formal_slots_started"] == report["teacher_calls"] == report["rental_cost_cny"] == 0,
            "wrong scope or outcome")
    ledger, = rows(run / "slots.jsonl")
    selected = (run / "selected-backend.sha256").read_text().strip()
    require(ledger["result"] == row and ledger["slot_id"] == SLOT
            and ledger["status"] == row["status"] and ledger["backend_sha256"] == selected
            and selected == (run / "manifest.sha256").read_text().strip(), "slot journal or backend differs")
    output = {"scope": manifest["scope"], "task_id": TASK, "thinking": False,
              "manifest_sha256": sha((run / "manifest.json").read_bytes()), "status": row["status"]}
    runtime_path = run / "model-runtime.json"
    if runtime_path.is_file():
        runtime = read(runtime_path)
        check_runtime(manifest["model"], runtime)
        output["model"] = {k: runtime[k] for k in ("revision", "model", "backend", "weight_sha256", "quantized",
                                                   "checkpoint_dtype", "precision", "mlx", "mlx_lm", "device")}
        output["model_runtime_sha256"] = sha(runtime_path.read_bytes())
        require(output["model_runtime_sha256"] == row["model_runtime_sha256"], "unbound model runtime")
    if row["status"] == "unscored":
        require(row["passed"] is None and row.get("unscored_reason"), "unscored attempt needs a reason")
        return {**output, "outcome": "unscored", "unscored_reason": row["unscored_reason"], "checker": None}
    require(row["status"] == "scored" and runtime_path.is_file(), "unknown status or real runtime missing")
    folder = run / "trials" / SLOT
    worker = read(folder / "worker-result.json")
    require(runtime["pid"] not in {row["worker_pid"], row["scorer_pid"]} and worker["runtime"] == trial["runtime"],
            "model not isolated or runtime budgets changed")
    checker = score_evidence(run, folder, worker, row)
    tok = load_tokenizer(run / "source/training/tokenizers/qwen35-dev")
    turns, dispatched = turns_evidence(folder, worker, tok)
    actions = action_evidence(folder, worker, dispatched)
    reason = worker["limits"]["stop_reason"]
    require(reason == row["stop_reason"], "worker and terminal stop reasons differ")
    outcome = "checker_passed" if checker["passed"] else "checker_failed"
    if not checker["passed"] and reason:
        outcome = "stopped_before_task_success"
    known = [t for t in turns if t["completion_tokens"] is not None]
    latency = sorted(t["round_trip_seconds"] for t in turns if "round_trip_seconds" in t)
    return {**output, "outcome": outcome, "stop_reason": reason, "checker": checker,
            "steps": turns, "dispatched_tools": list(dispatched.values()), "flight_commands": actions,
            "known_prompt_tokens": sum(t["sdk_prompt_tokens"] for t in known),
            "known_completion_tokens": sum(t["completion_tokens"] for t in known),
            "unknown_generation_turns": [t["turn"] for t in turns if t["completion_tokens"] is None],
            "known_reasoning_tokens": sum(t["reasoning_tokens"] or 0 for t in known),
            "unknown_reasoning_turns": [t["turn"] for t in known if t["reasoning_tokens"] is None],
            "elapsed_seconds": worker["elapsed"], "request_latency_samples": len(latency),
            "request_latency_percentile_method": "nearest rank",
            "request_p50_seconds": latency[math.ceil(0.5 * len(latency)) - 1] if latency else None,
            "request_p95_seconds": latency[math.ceil(0.95 * len(latency)) - 1] if latency else None}


if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, indent=2))
