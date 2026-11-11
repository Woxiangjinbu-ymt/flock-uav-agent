"""Count actual response roles in the sealed compact/v9 development pilot.

Run with python3 classify.py to print the JSON report. Each response gets one
role, regardless of parallel calls. A role describes a proposed action, not
whether a cheaper policy could have chosen it. No model is called.
"""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
BATCH = REPO / "docs/results/deepseek-cost-pilot-20261103"
SOURCE = BATCH / "analysis-01/analysis.json"
QUERIES = {
    "get_drone_status", "get_mission_progress", "get_target_info", "get_weather",
    "list_drones",
}
MOVEMENT = {
    "hover", "move_along_path_and_sense", "move_to_and_sense",
    "navigate_to_and_sense", "take_off",
}


def role_of(decisions, is_final):
    names = {decision["name"] for decision in decisions}
    unknown = names - QUERIES - MOVEMENT - {"run_harness", "sense_nearby_entities"}
    if unknown:
        raise ValueError(f"Unclassified tools: {unknown}")
    if is_final:
        assert not names
        return "terminal_text"
    assert names
    if "run_harness" in names:
        assert all(d["arguments"]["action"] == "start"
                   for d in decisions if d["name"] == "run_harness")
        return "program_start"
    if names & MOVEMENT:
        return "movement_requested"
    if "sense_nearby_entities" in names:
        return "sensing_and_optional_queries"
    return "queries_only"


def main():
    source_bytes = SOURCE.read_bytes()
    analysis = json.loads(source_bytes)
    roles = Counter()
    roles_by_group = defaultdict(Counter)
    tokens_by_role = Counter()
    with_tool_error_by_role = Counter()
    terminal_scores = {}
    excluded = []

    for trial in analysis["trials"]:
        trial_id = trial["trial_id"]
        group = "injected_receipt_loss" if trial["condition"] == "unknown_takeoff" else "normal"
        model_io = trial["model_io"]
        archive = BATCH / "run-01/trials" / trial_id / "policy/modelio" / model_io["file"]
        assert sha256(archive.read_bytes()).hexdigest() == model_io["sha256"]
        decisions = defaultdict(list)
        for decision in model_io["decisions"]:
            decisions[decision["attempt_id"]].append(decision)
        finals = {f["attempt_id"] for f in model_io["final_messages"]}
        errors = {e["tool_call_id"] for e in model_io["tool_feedback_errors"]}
        seen = set()
        for response in model_io["responses"]:
            attempt = response["attempt_id"]
            assert attempt not in seen
            seen.add(attempt)
            if response["status_code"] != 200:
                assert response["response_body_sha256"] is None
                assert trial["termination"]["reason"] == "token_limit"
                assert attempt not in decisions and attempt not in finals
                excluded.append([trial_id, attempt])
                continue
            assert response["response_closed"] and response["response_body_sha256"]
            calls = decisions.get(attempt, [])
            role = role_of(calls, attempt in finals)
            roles[role] += 1
            roles_by_group[group][role] += 1
            tokens_by_role[role] += response["usage"]["total_tokens"]
            if any(d["tool_call_id"] in errors for d in calls):
                with_tool_error_by_role[role] += 1
            if attempt in finals:
                assert trial_id not in terminal_scores
                terminal_scores[trial_id] = trial["obligations"]["checker_and_additional_obligation_passed"]
        assert (set(decisions) | finals) <= seen

    assert sum(roles.values()) == 68 and sum(tokens_by_role.values()) == 1_003_728
    assert excluded == [["search-control", "attempt-11"]]
    result = {
        "source": str(SOURCE.relative_to(REPO)),
        "source_sha256": sha256(source_bytes).hexdigest(),
        "trials": len(analysis["trials"]),
        "responses": sum(roles.values()),
        "role_counts": roles,
        "role_counts_by_condition_group": dict(roles_by_group),
        "actual_total_tokens_by_role_including_input": tokens_by_role,
        "responses_containing_at_least_one_reported_tool_feedback_error_by_role": with_tool_error_by_role,
        "terminal_text_trial_passed_original_checker_and_predeclared_action_audit": terminal_scores,
        "excluded_without_provider_response": excluded,
        "scope": "Five selected development conditions, two harness variants per condition, one execution per variant. Queries and sensing describe information requested; sensing can update the blackboard. Tool feedback errors overlap roles. Scores describe whole trials. No role measures replaceability, marginal utility or sufficient evidence at an intermediate step.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
