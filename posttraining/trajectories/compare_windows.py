"""Compare the conditional 48K teacher pilot with its registered 32K task pairs."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path

from posttraining.inference import EPISODE_TOKEN_LIMIT, MAX_GENERATION
from runtime.executor import FALLBACK_REGISTRATION, PILOT_REGISTRATION, digest, read_json


def prefix_budget(turns):
    """Measure the complete teacher decisions a student could reproduce in order."""
    spent = budget_turns = 0
    first_stop = None
    for row in turns:
        prompt, target = row["window"].get("tokens"), row.get("target_tokens")
        if prompt is None or target is None:
            break
        if spent + prompt + target > EPISODE_TOKEN_LIMIT:
            break
        spent += prompt + target
        budget_turns += 1
    admitted = [r for r in turns if r["shadow_reachable"]]
    count = len(admitted)
    if admitted != turns[:count]:
        raise ValueError("Reachable decisions must form a continuous prefix")
    used = sum(r["window"]["tokens"] + r["target_tokens"] for r in admitted)
    if count < len(turns):
        row = turns[count]
        prompt, target = row["window"].get("tokens"), row.get("target_tokens")
        remaining = EPISODE_TOKEN_LIMIT - used
        if row["window"].get("error"):
            first_stop = "local_context"
        elif prompt is None or target is None or row["wire_issues"]:
            first_stop = "unverified_decision"
        elif prompt >= remaining:
            first_stop = "episode_prompt"
        elif min(MAX_GENERATION, remaining - prompt) < target:
            first_stop = "generation_limit" if MAX_GENERATION <= remaining - prompt else "episode_generation"
        else:
            first_stop = row["projection"]
    return {"reproducible_turns": count, "prefix_tools": sum(len(r["tools"]) for r in admitted),
            "prefix_tokens": used, "first_stop": first_stop or "", "budget_only_turns": budget_turns}


def compare(attempt, inputs):
    reg = read_json(FALLBACK_REGISTRATION)
    reference = PILOT_REGISTRATION.with_name("pairs.csv")
    plan = read_json(attempt / "plan.json")
    if (digest(reference.read_bytes()) != reg["paired_reference_sha256"]
            or digest((attempt / "plan.json").read_bytes()) != read_json(PILOT_REGISTRATION)["plan_sha256"]
            or read_json(attempt / "configuration.json").get("fallback_prompt_limit") != reg["fallback_prompt_tokens"]):
        raise ValueError("Expected the registered 32K reference and 48K fallback plan")
    with reference.open(newline="") as stream:
        previous = {r["task"]: r for r in csv.DictReader(stream)}
    for scene in plan["sessions"]:
        sid = scene["id"]
        folder = inputs / sid
        by_task = defaultdict(list)
        with (folder / "decisions.jsonl").open() as stream:
            for turn in map(json.loads, stream):
                by_task[turn["task_id"]].append(turn)
        episodes = read_json(folder / "episodes.json")
        scores = read_json(attempt / sid / "results.json")
        for task, episode, score in zip(scene["tasks"], episodes, scores, strict=True):
            tid = task["id"]
            prior = previous.pop(tid)
            if (tid != episode["task_id"] or tid != score["task_id"] or sid != prior["scene"]
                    or episode["scene_id"] != sid or episode["task_index"] != int(prior["position"])):
                raise ValueError("Task, scene or task order differs between arms")
            turns = by_task[tid]
            fallback = [r["turn"] for r in turns if r["window"].get("fallback_prompt_limit") and r["attempts"]]
            cohort = "other"
            if tid in reg["blocked_regressions"]:
                cohort = "blocked_regression"
            elif tid in reg["other_regressions"]:
                cohort = "omission"
            prefix = prefix_budget(turns)
            yield {"scene": sid, "task": tid, "position": prior["position"], "cohort": cohort,
                   "native_pass": prior["native_pass"], "ordinary_pass": prior["pilot_pass"],
                   "fallback_pass": int(episode["formal_pass"]), "initial_pass": int(episode["initial_pass"]),
                   "passed_checks": sum(bool(c["result"]) for c in score["leaves"]), "checks": len(score["leaves"]),
                   "complete_archive": int(episode["archive_complete"]), "turns": len(turns),
                   "fallback_turns": len(fallback), "first_fallback_turn": fallback[0] if fallback else "",
                   "local_rejections": sum(bool(r["window"].get("error")) for r in turns),
                   **prefix, "full_replayable": int(episode["archive_complete"] and
                                                      prefix["reproducible_turns"] == len(turns))}
    if previous:
        raise ValueError("Unpaired reference tasks")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = list(compare(args.attempt, args.inputs))
    with args.output.open("x", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
