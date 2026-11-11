"""Fixed 14-slot local ASGI acceptance for input/search cost candidates.

Scripted public decisions, never provider calls or natural-model/token results.
Uses the maintained frozen general_agent coordinator and independent scorers.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
from pathlib import Path
import sys

from validate_harness_episode import sha, write, check_hashes
from audit_agent_samples import canonical, inspect_archive, wire_calls, MARKER

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/harness-cost-v1"
SOURCE_NAMES = ("scripts/validate_harness_cost_reduction.py", "scripts/audit_agent_samples.py",
    "scripts/validate_harness_partial_recovery.py", "scripts/tests/test_harness_cost_reduction.py",
    "docs/results/harness-cost-reduction-20261103/PROTOCOL_CN.md")
RECOVERY = "[Execution recovery contract: advisory public evidence, not task completion]\n"
ARMS = ("control", "candidate")
GROUPS = ("main", "partial")
REVISIONS = ("search_progress_v8", "search_confirm_v9")
FOLLOWUP_PROTOCOL = "docs/results/harness-cost-reduction-20261103/FOLLOWUP_PROTOCOL_CN.md"
EXPECTED_IDS = ("short-chain", "navigation", "search", "unknown-raw", "unknown-skill",
                "partial-positive", "partial-negative")


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def verify_inventory(root, files, extras=()):
    check_hashes(root, files)
    actual = {str(p.relative_to(root)) for p in root.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    if actual != set(files) | set(extras):
        raise ValueError("Cost acceptance inventory changed")


def preserved_graph_messages(raw_messages, wire_messages, injections):
    """Check every original non-system graph message, without history cropping.

    IDs/parameters are compared exactly within the same episode. Known R2
    suffixes must match the sealed ledger text and original message position.
    """
    if len(raw_messages) != len(wire_messages):
        return False
    for index, (raw, wire) in enumerate(zip(raw_messages, wire_messages)):
        role = {"human": "user", "ai": "assistant", "tool": "tool"}.get(raw.get("type"))
        if role is None or wire.get("role") != role:
            return False
        original, actual = raw.get("content"), wire.get("content")
        # The SDK emits null for an empty assistant that only makes tool calls.
        if role == "assistant" and not original and actual is None:
            actual = ""
        if actual != original:
            if not isinstance(original, str) or not isinstance(actual, str) or not actual.startswith(original):
                return False
            suffix = actual[len(original):]
            known = [item for item in injections if item.get("message_index") == index
                     and item.get("tool_call_id") == raw.get("tool_call_id") and item.get("text") == suffix]
            if not suffix.startswith(MARKER) or len(known) != 1:
                return False
        if role == "assistant":
            calls = [(c.get("id"), c.get("name"), c.get("args")) for c in raw.get("tool_calls", [])]
            if calls != wire_calls(wire):
                return False
        if role == "tool" and wire.get("tool_call_id") != raw.get("tool_call_id"):
            return False
    return True


def wire_audit(archive, ledger):
    raw = archive.read_bytes()
    injections = ledger["task_evidence"].get("injections", [])
    _, decisions = inspect_archive(raw, injections=injections)
    events = rows(archive)
    starts = {row["data"]["logical_call_id"]: row["data"]["input"] for row in events if row["kind"] == "model_start"}
    output = []
    for row in events:
        if row["kind"] != "http_request":
            continue
        data, blob = row["data"], row["data"]["body"]
        content = base64.b64decode(blob["base64"], validate=True)
        if len(content) != blob["length"] or sha(content) != blob["sha256"]:
            raise ValueError("Wire bytes changed")
        request = json.loads(content)
        messages = request["messages"]
        non_system = [m for m in messages if m["role"] != "system"]
        preserved = preserved_graph_messages(starts[data["logical_call_id"]]["messages"], non_system, injections)
        schemas = canonical(request["tools"])
        stable_schemas = copy.deepcopy(request["tools"])
        harness_description = None
        for tool in stable_schemas:
            if tool["function"]["name"] == "run_harness":
                harness_description = tool["function"].pop("description", None)
        output.append({"logical_call_id": data["logical_call_id"], "request_sha256": sha(content),
            "request_bytes": len(content), "tools_canonical_bytes": len(schemas),
            "tools_canonical_sha256": sha(schemas), "tool_names": [t["function"]["name"] for t in request["tools"]],
            "tool_parameters_sha256": sha(canonical({t["function"]["name"]: t["function"]["parameters"] for t in request["tools"]})),
            "schemas_except_harness_description_sha256": sha(canonical(stable_schemas)),
            "harness_description_sha256": sha(canonical(harness_description)),
            "system_content_bytes": sum(len(m["content"].encode()) for m in messages if m["role"] == "system"),
            "original_graph_messages_preserved": preserved, "history_receipt_links_verified": True,
            "recovery_projection_present": any(RECOVERY in m.get("content", "") for m in messages if m["role"] == "system")})
    return {"archive_sha256": sha(raw), "logical_decisions": len(decisions), "requests": output,
            "wire_bytes": sum(r["request_bytes"] for r in output),
            "tools_canonical_bytes": sum(r["tools_canonical_bytes"] for r in output),
            "all_original_graph_messages_preserved": bool(output) and all(r["original_graph_messages_preserved"] for r in output)}, decisions


def unknown_checks(folder, state, api, physical, decisions):
    """The local probe is checked against the real successful API/physical effect.

    Its local schema is intentionally distinct from the online pilot probe.
    """
    probe = read(folder / "private/local-api-fault.json")
    takeoffs = [r for r in api if r["endpoint"].endswith("/command/take_off")]
    hovers = [r for r in api if r["endpoint"].endswith("/command/hover")]
    if len(takeoffs) != 1:
        return {"exactly_one_takeoff": False}
    takeoff = takeoffs[0]
    actor = takeoff["endpoint"].split("/")[2]
    events = [r for r in physical if r["request_id"] == takeoff["request_id"]]
    statuses = [r for r in api if r["request_id"] > takeoff["request_id"] and r["endpoint"] == "/drones/" + actor
                and r.get("origin", {}).get("kind") == "model_tool" and r.get("origin", {}).get("tool") == "get_drone_status"]
    unknown = [r for r in state["actions"] if r["tool_name"] == "take_off" and r["effect_status"] == "unknown"]
    status_decisions = [d for d in decisions if any(c[1] == "get_drone_status" for c in d["calls"])]
    hover_decisions = [d for d in decisions if any(c[1] == "hover" for c in d["calls"])]
    return {"local_probe_matches_api": probe.get("endpoint") == takeoff["endpoint"] and probe.get("after_public_api_return") is True,
        "real_committed_success_before_loss": takeoff["committed"] and takeoff["response"].get("status") == "success"
            and any(e.get("history", {}).get("position", {}).get("z") == 12 for e in events),
        "exactly_one_takeoff": True,
        "new_model_status_then_hover": len(statuses) == len(hovers) == len(status_decisions) == len(hover_decisions) == 1
            and statuses[0]["request_id"] < hovers[0]["request_id"]
            and status_decisions[0]["start_sequence"] < hover_decisions[0]["start_sequence"]
            and hovers[0]["endpoint"] == "/drones/" + actor + "/command/hover"
            and hovers[0]["committed"] and hovers[0]["response"].get("status") == "success",
        "unknown_history_not_rewritten": len(unknown) == 1,
        "mission_not_inferred": state["mission_completion"] == "not_evaluated"}


def profile_for_revision(profile, revision):
    if revision not in REVISIONS or profile.get("harness", {}).get("revision") != REVISIONS[0]:
        raise ValueError("Expected the original v8 profile and an explicitly supported candidate revision")
    selected = copy.deepcopy(profile)
    selected["harness"]["revision"] = revision
    return selected


def search_pair_checks(control, candidate, revision=REVISIONS[0]):
    """A stop is not a mission verdict; improvement requires retained progress."""
    if revision not in REVISIONS:
        raise ValueError("Unsupported candidate revision")
    a, b = control["search"], candidate["search"]
    if revision == "search_confirm_v9":
        # This fixed fdd fixture stays below 95%; v9 must retain v7 exploration.
        return {"search_control_uses_original_round_stop": a["status"] == "needs_replan" and a["reason"] == "round_limit_below_threshold",
            "search_control_below_confirmation_threshold": a["public_progress"] is not None and 0 < a["public_progress"] < 95,
            "search_same_terminal_state_without_stall": a == b,
            "search_same_action_sequence_below_95": control["actions"] == candidate["actions"],
            "search_same_physical_cost_below_95": control["physical_cost"] == candidate["physical_cost"]}
    valid_stop = (b["status"] == "needs_replan" and b["reason"] == "search_progress_stalled") or (
        b["status"] == "completed" and b["public_progress"] is not None and b["public_progress"] > 95)
    return {"search_control_uses_original_round_stop": a["status"] == "needs_replan" and a["reason"] == "round_limit_below_threshold",
        "search_candidate_stop_has_evidence": valid_stop,
        "search_progress_retained": a["public_progress"] is not None and b["public_progress"] is not None
            and b["public_progress"] >= a["public_progress"],
        "search_fewer_actions": candidate["physical_cost"]["action_attempts"] < control["physical_cost"]["action_attempts"],
        "search_fewer_public_requests": candidate["physical_cost"]["api_requests"] < control["physical_cost"]["api_requests"]}


def analyze(output):
    from general_agent import verify
    from validate_harness_partial_recovery import analyze_run as analyze_partial
    output = Path(output)
    plan = read(output / "inputs/plan.json")
    candidate_profile = read(output / "inputs" / plan["arms"]["candidate"])
    revision = candidate_profile["harness"]["revision"]
    if revision not in REVISIONS:
        raise ValueError("Unsupported frozen candidate revision")
    selection = read(output / "inputs/candidate-selection.json") if revision == REVISIONS[1] else None
    checks, reports, processes, partial_reports = [], [], [], {}
    def check(name, value, **details):
        checks.append({"check": name, "passed": bool(value), **details})
    if selection is not None:
        base_raw = (output / "inputs/v8-reference-profile.json").read_bytes()
        effective_raw = (output / "inputs/candidate-profile.json").read_bytes()
        check("followup_changes_only_candidate_revision", candidate_profile == profile_for_revision(json.loads(base_raw), revision)
              and selection["candidate_revision"] == revision and selection["status_at_freeze"] == "followup_candidate_not_yet_recommended"
              and selection["changed_fields"] == ["harness.revision"])
        check("followup_profile_and_protocol_hashes_bound", selection["base_candidate_profile_sha256"] == sha(base_raw)
              and selection["effective_candidate_profile_sha256"] == sha(effective_raw)
              and selection["followup_protocol_sha256"] == sha((output / "source" / FOLLOWUP_PROTOCOL).read_bytes()))
    check("fixed_fourteen_predeclared_slots", plan["planned_episodes"] == 14 and tuple(plan["arms"]) == ARMS
          and set(plan["expected_checker"]) == set(EXPECTED_IDS))
    for arm in ARMS:
        for group in GROUPS:
            run = output / "runs" / f"{arm}-{group}"
            checked = verify(run)
            manifest, results, summary = read(run / "manifest.json"), read(run / "results.json"), read(run / "summary.json")
            expected = read(output / "inputs" / plan["groups"][group])
            if selection is not None:
                check(f"{arm}-{group}:selected_profile_bound", manifest["agent_profile"] == read(output / "inputs" / plan["arms"][arm]))
            check(f"{arm}-{group}:full_denominator", [r["trial_id"] for r in results] == [r["trial_id"] for r in expected]
                  and summary["unstarted_trials"] == [] and checked["valid"])
            check(f"{arm}-{group}:completed_without_hidden_stop", summary["status"] == "completed")
            if group == "partial":
                partial_reports[arm] = analyze_partial(run)
                check(f"{arm}:real_partial_acceptance", partial_reports[arm]["passed"])
            for result in results:
                ident = result["trial_id"]
                folder = run / "trials" / ident
                worker, score = result["worker"], result["score"]
                processes.extend(result["processes"])
                if worker is None or score is None:
                    check(f"{arm}:{ident}:worker_and_score", False)
                    continue
                ledger, state = read(folder / "policy/task-state.json"), read(folder / "policy/execution-state.json")
                archive = next((folder / "policy/modelio").glob("*.jsonl"))
                wire, decisions = wire_audit(archive, ledger)
                api, physical = rows(folder / "policy/worldlogs/public-api.jsonl"), rows(folder / "policy/worldlogs/physical-events.jsonl")
                tag = f"{arm}:{ident}:"
                check(tag + "isolated_drained_sealed", worker["network_attempts"] == [] and score["network_attempts"] == []
                      and worker["drained"] and worker["agent"]["archive_verify"]["complete"]
                      and all(worker["isolation"].values()) and score["pid"] != worker["pid"] and not score["agent_imported"])
                check(tag + "normal_end_keeps_scoring_distinct", worker["agent"]["success"] and worker["limits"]["stop_reason"] is None
                      and result["termination"]["score_eligible"] and result["termination"]["batch_action"] == "continue"
                      and state["mission_completion"] == "not_evaluated")
                check(tag + "expected_independent_score", score["checker"]["passed"] is plan["expected_checker"][ident])
                check(tag + "whole_wire_history_retained", wire["all_original_graph_messages_preserved"]
                      and all(r["recovery_projection_present"] for r in wire["requests"]))
                check(tag + "api_action_counts_recomputed", worker["physical_cost"]["api_requests"] == len(api)
                      and worker["physical_cost"]["action_attempts"] == sum(bool(r["action"]) for r in api))
                actions = [{k: a[k] for k in ("tool", "endpoint", "params", "body", "committed", "status")} for a in worker["actions"]]
                if ident in {"short-chain", "navigation"}:
                    commands = [a["endpoint"].rsplit("/", 1)[-1] for a in actions]
                    expected_commands = ["take_off", "hover"] if ident == "short-chain" else ["take_off", "move_to", "hover"]
                    check(tag + "complete_requested_action_chain", commands == expected_commands
                          and len({a["endpoint"].split("/")[2] for a in actions}) == 1
                          and all(a["committed"] and a["status"] == "success" for a in actions))
                    if ident == "navigation":
                        check(tag + "actual_navigation_coordinates", len(actions) == 3
                              and actions[1]["params"] == {"x": 454, "y": 380, "z": 14})
                report = {"arm": arm, "case": ident, "task_id": worker["task_id"],
                    "initial_state_sha256": worker["initial"]["original_state_sha256"],
                    "worker_pid": worker["pid"], "scorer_pid": score["pid"], "wire": wire,
                    "checker": score["checker"], "termination": result["termination"],
                    "physical_cost": worker["physical_cost"], "actions": actions,
                    "model_calls": [[list(c) for c in d["calls"]] for d in decisions],
                    "source_manifest_sha256": sha((run / "manifest.json").read_bytes())}
                if ident.startswith("unknown"):
                    report["unknown"] = unknown_checks(folder, state, api, physical, decisions)
                    for name, passed in report["unknown"].items():
                        check(tag + name, passed)
                if ident == "search":
                    frame = read(folder / "policy/harness.json")["frames"]
                    frame = next(iter(frame.values())) if isinstance(frame, dict) else frame[0]
                    responses = [r["response"] for r in api if r.get("origin", {}).get("tool") == "get_mission_progress"]
                    latest = responses[-1]["statistics"]["task_progress"]["progress_percentage"] if responses else None
                    report["search"] = {"status": frame["status"], "reason": frame["reason"], "cursor": frame["cursor"],
                        "public_progress": latest, "script_has_no_post_handoff_search": True}
                reports.append(report)
    check("all_fourteen_workers_and_fourteen_scorers", len(reports) == 14 and len(processes) == 28
          and len({p["pid"] for p in processes}) == 28 and all(p["exited"] and p["returncode"] == 0 for p in processes))
    ordered = sorted(processes, key=lambda p: p["started_ns"])
    check("serial_join_before_next_process", all(a["ended_ns"] <= b["started_ns"] for a, b in zip(ordered, ordered[1:])))
    pairs = []
    for ident in EXPECTED_IDS:
        pair = {r["arm"]: r for r in reports if r["case"] == ident}
        if set(pair) != set(ARMS):
            check(ident + ":pair_present", False)
            continue
        a, b = pair["control"], pair["candidate"]
        check(ident + ":paired_initial_state", a["initial_state_sha256"] == b["initial_state_sha256"])
        if ident == "search":
            for name, value in search_pair_checks(a, b, revision).items():
                check(name, value)
        else:
            check(ident + ":same_action_sequence", a["actions"] == b["actions"])
            check(ident + ":same_physical_effect_cost", a["physical_cost"] == b["physical_cost"])
        check(ident + ":same_scripted_request_count", len(a["wire"]["requests"]) == len(b["wire"]["requests"]))
        check(ident + ":same_public_script_decisions", a["model_calls"] == b["model_calls"])
        check(ident + ":same_advertised_tool_names", [r["tool_names"] for r in a["wire"]["requests"]]
              == [r["tool_names"] for r in b["wire"]["requests"]])
        check(ident + ":same_parameter_schemas", [r["tool_parameters_sha256"] for r in a["wire"]["requests"]]
              == [r["tool_parameters_sha256"] for r in b["wire"]["requests"]])
        check(ident + ":only_harness_revision_description_may_change", [r["schemas_except_harness_description_sha256"] for r in a["wire"]["requests"]]
              == [r["schemas_except_harness_description_sha256"] for r in b["wire"]["requests"]])
        check(ident + ":wire_bytes_decrease", b["wire"]["wire_bytes"] < a["wire"]["wire_bytes"])
        pairs.append({"case": ident, "control_wire_bytes": a["wire"]["wire_bytes"], "candidate_wire_bytes": b["wire"]["wire_bytes"],
            "wire_byte_delta": b["wire"]["wire_bytes"] - a["wire"]["wire_bytes"],
            "control_actions": len(a["actions"]), "candidate_actions": len(b["actions"]),
            "control_public_api": a["physical_cost"]["api_requests"], "candidate_public_api": b["physical_cost"]["api_requests"]})
    report = {"schema": "harness-cost-local-acceptance-v1", "passed": all(c["passed"] for c in checks),
        "provider_calls": 0, "teacher_examples": 0, "planned_episodes": 14, "reported_episodes": len(reports),
        "checks": checks, "episodes": reports, "pairs": pairs, "partial": partial_reports,
        "limits": ["scripted_development_not_model_success", "bytes_not_tokens", "no_paid_calls_or_training",
            "joint_search_revision_and_input_layout_comparison_not_single_component_causality",
            "search_has_no_model_decision_after_handoff", "no_fee_ledger_in_local_fourteen_slots",
            "cost_stop_continuation_covered_separately_by_existing_mock_tests"]}
    if selection is not None:
        report["candidate_selection"] = selection
    return report


def run_validation(output, candidate_revision=REVISIONS[0]):
    from harness_episode_local import block_network
    from general_agent import prepare, run_prepared
    attempts = block_network()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Existing cost validation cannot be overwritten or resumed")
    original_configs = {str(p.relative_to(CONFIG)): p.read_bytes() for p in CONFIG.rglob("*") if p.is_file()}
    configs = dict(original_configs)
    selected = profile_for_revision(json.loads(configs["candidate-profile.json"]), candidate_revision)
    source_names = SOURCE_NAMES + ((FOLLOWUP_PROTOCOL,) if candidate_revision == REVISIONS[1] else ())
    sources = {name: (ROOT / name).read_bytes() for name in source_names}
    selection = None
    if candidate_revision == REVISIONS[1]:
        base = configs["candidate-profile.json"]
        configs["v8-reference-profile.json"] = base
        effective = (json.dumps(selected, ensure_ascii=False, indent=2) + "\n").encode()
        if json.loads(configs["recommended-profile.json"]) != selected:
            raise ValueError("Followup candidate profile must differ only by revision")
        configs["candidate-profile.json"] = effective
        selection = {"candidate_revision": candidate_revision, "status_at_freeze": "followup_candidate_not_yet_recommended",
            "base_candidate_profile_sha256": sha(base), "effective_candidate_profile_sha256": sha(effective),
            "followup_protocol_sha256": sha(sources[FOLLOWUP_PROTOCOL]), "changed_fields": ["harness.revision"]}
        configs["candidate-selection.json"] = (json.dumps(selection, indent=2) + "\n").encode()
    plan = json.loads(configs["plan.json"])
    for study in plan["studies"].values():
        if sha((ROOT / study["path"] / "manifest.json").read_bytes()) != study["manifest_sha256"]:
            raise ValueError("Frozen study anchor changed")
    output.mkdir(parents=True)
    for prefix, blobs in (("inputs", configs), ("source", sources)):
        for name, raw in blobs.items():
            path = output / prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    prepared = []
    for arm in ARMS:
        for group in GROUPS:
            run = output / "runs" / f"{arm}-{group}"
            prepare(output / "inputs" / plan["arms"][arm], ROOT / plan["studies"][group]["path"],
                    output / "inputs" / plan["groups"][group], run)
            prepared.append(run)
    for run in prepared:
        run_prepared(run)
    report = analyze(output)
    report["preparation_network_attempts"] = attempts
    report["passed"] &= attempts == []
    write(output / "validation.json", report)
    for name, raw in sources.items():
        if (ROOT / name).read_bytes() != raw:
            raise ValueError("Acceptance source drift")
    for name, raw in original_configs.items():
        if (CONFIG / name).read_bytes() != raw:
            raise ValueError("Acceptance configuration drift")
    frozen_sources = read(prepared[0] / "manifest.json")["sources"]
    for run in prepared:
        if read(run / "manifest.json")["sources"] != frozen_sources:
            raise ValueError("Runtime sources changed between preparation steps")
    check_hashes(ROOT, frozen_sources)
    files = {str(p.relative_to(output)): sha(p.read_bytes()) for p in sorted(output.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts}
    manifest = {"schema": "harness-cost-local-package-v1", "files": files,
        "sources": {name: sha(raw) for name, raw in sources.items()}, "runtime_sources": frozen_sources,
        "provider_calls": 0, "planned_episodes": 14, "passed": report["passed"], "python_executable": sys.executable}
    if selection is not None:
        manifest["candidate_selection"] = selection
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha((output / "manifest.json").read_bytes()) + "\n")
    return report


def verify_package(output):
    output = Path(output).resolve()
    raw = (output / "manifest.json").read_bytes()
    if sha(raw) != (output / "manifest.sha256").read_text().strip():
        raise ValueError("Acceptance manifest changed")
    manifest = json.loads(raw)
    verify_inventory(output, manifest["files"], {"manifest.json", "manifest.sha256"})
    report, saved = analyze(output), read(output / "validation.json")
    if manifest.get("candidate_selection") != report.get("candidate_selection"):
        raise ValueError("Candidate selection metadata changed")
    if {k: v for k, v in saved.items() if k != "preparation_network_attempts"} != report or saved["preparation_network_attempts"]:
        raise ValueError("Recomputed acceptance differs from sealed result")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--candidate-revision", choices=REVISIONS, default=REVISIONS[0],
                        help="run only: explicit followup candidate; verify always uses the frozen profile")
    args = parser.parse_args()
    report = verify_package(args.output) if args.verify else run_validation(args.output, args.candidate_revision)
    print(json.dumps({k: report[k] for k in ("passed", "planned_episodes", "reported_episodes", "provider_calls")}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
