"""Print the exact earlier-request audit for natural, information-only responses."""
import base64
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BATCH = ROOT / "docs/results/deepseek-cost-pilot-20261103"
INFO = {"list_drones", "get_weather", "sense_nearby_entities", "get_drone_status", "get_target_info", "get_mission_progress"}


def signature(decision):
    wire = decision["wire_arguments"].copy()
    # Ignore JSON serialization order and whitespace, retaining the input type.
    if isinstance(wire.get("input_json"), str):
        wire["input_json"] = ["encoded JSON object", json.loads(wire["input_json"])]
    return decision["name"], json.dumps(wire, sort_keys=True, ensure_ascii=False)


def first_public_receipts(archive, selected_ids):
    observed = {}
    for line in archive.open():
        row = json.loads(line)
        if row["kind"] != "http_request":
            continue
        data = row["data"]
        raw = base64.b64decode(data["body"]["base64"], validate=True)
        assert sha256(raw).hexdigest() == data["body"]["sha256"]
        for message in json.loads(raw)["messages"]:
            call_id = message.get("tool_call_id")
            if message["role"] == "tool" and call_id in selected_ids and call_id not in observed:
                result, _ = json.JSONDecoder().raw_decode(message["content"])
                observed[call_id] = result
    return observed


def comparison_value(tool, receipt):
    if tool == "get_mission_progress":
        return receipt["progress_percentage"]
    if tool == "list_drones":
        return {d["id"]: (d["status"], d["position"]) for d in receipt}
    if tool == "sense_nearby_entities":
        return {
            actor: {kind: sorted(e["id"] for e in entities[kind]) for kind in ("targets", "obstacles", "drones")}
            for actor, entities in receipt["per_drone"].items()
        }
    raise ValueError(f"Select a meaningful comparison field for repeat of {tool}")


def main():
    analysis = json.loads((BATCH / "analysis-01/analysis.json").read_bytes())
    groups = defaultdict(list)
    call_counts = Counter()
    progress_witness = []
    repeat_results = []
    normal_trials = 0
    normal_responses = 0
    for trial in analysis["trials"]:
        if trial["condition"] == "unknown_takeoff":
            continue
        normal_trials += 1
        io = trial["model_io"]
        archive = BATCH / "run-01/trials" / trial["trial_id"] / "policy/modelio" / io["file"]
        assert sha256(archive.read_bytes()).hexdigest() == io["sha256"]
        decisions = defaultdict(list)
        for action in io["decisions"]:
            decisions[action["attempt_id"]].append(action)
        seen = {}
        earlier_pairs = []
        names_by_attempt = {}
        for response in io["responses"]:
            if response["status_code"] != 200:
                continue
            normal_responses += 1
            attempt = response["attempt_id"]
            actions = decisions[attempt]
            names_by_attempt[attempt] = [a["name"] for a in actions]
            keys = [signature(a) for a in actions]
            if actions and all(a["name"] in INFO for a in actions):
                repeats = sum(key in seen for key in keys)
                for key, action in zip(keys, actions):
                    if key in seen:
                        earlier_pairs.append((seen[key], action))
                call_counts["repeated"] += repeats
                call_counts["first"] += len(keys) - repeats
                role = "all_first"
                if repeats:
                    role = "all_repeat" if repeats == len(keys) else "mixed"
                groups[role].append([trial["trial_id"], attempt])
            seen.update(zip(keys, actions))
        selected_ids = {a["tool_call_id"] for pair in earlier_pairs for a in pair}
        receipts = first_public_receipts(archive, selected_ids) if selected_ids else {}
        for previous, current in earlier_pairs:
            name = current["name"]
            previous_value = comparison_value(name, receipts[previous["tool_call_id"]])
            current_value = comparison_value(name, receipts[current["tool_call_id"]])
            repeat_results.append([trial["trial_id"], previous["attempt_id"], current["attempt_id"], name, previous_value != current_value])
        if trial["trial_id"] == "search-candidate":
            progress = [a for a in io["decisions"] if a["name"] == "get_mission_progress"]
            assert len({signature(a) for a in progress}) == 1
            progress_witness = {
                "trial": trial["trial_id"],
                "source_archive": str(archive.relative_to(ROOT)),
                "observed": [[a["attempt_id"], a["tool_call_id"], receipts[a["tool_call_id"]]["progress_percentage"]] for a in progress],
                "tools_by_response": names_by_attempt,
            }
    assert normal_trials == 8 and normal_responses == 52 and sum(map(len, groups.values())) == 26
    print(json.dumps({
        "scope": "Eight known development trials, four conditions plus paired harness configurations. Count actual model responses requesting only information. A repeat requires a matching tool and canonical wire arguments at a strictly earlier model turn in the same trial. Counts exclude within-batch duplicates and do not treat skill-internal observations as earlier model requests. Request count does not establish novelty, necessity or savings.",
        "all_normal_responses": normal_responses,
        "counts_by_response": {key: len(rows) for key, rows in groups.items()},
        "counts_by_tool_call": call_counts,
        "responses_by_class": groups,
        "changed_selected_public_field_count": sum(row[-1] for row in repeat_results),
        "comparison_fields": "Compare the latest same request using progress_percentage, aircraft IDs mapped to status and position, or per-aircraft sets of sensed target/obstacle/drone IDs. Timestamps and other metadata are ignored. A changed result can already be known from a different tool or bundled action.",
        "previous_and_current_repeat_comparisons": repeat_results,
        "repeated_progress_query_with_changing_public_response": progress_witness,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
