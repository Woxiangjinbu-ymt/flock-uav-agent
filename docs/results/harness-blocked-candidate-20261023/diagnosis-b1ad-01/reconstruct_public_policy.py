"""Replay frozen search generator using frozen public skill responses only.

Does not instantiate a simulator, import a provider, or execute any public API.
The original generator must request the entire recorded call stream exactly.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
TARGETS = {
    "01e3ece4": {"destination": (875.0, 455.0), "obstacle_name": "Circle Obstacle 3", "requests": [311, 434]},
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert sha((ROOT / name).read_bytes()) == digest, name
    network_attempts = []

    def deny(*args, **kwargs):
        network_attempts.append("blocked_connection")
        raise RuntimeError("Network forbidden in public trace reconstruction")

    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    source = ROOT / "source/agent4drone"
    sys.path.insert(0, str(source))
    import harness_skills
    from harness_core import PublicCall, Checkpoint, SkillExit
    from blackboard import PerceptionBlackboard
    from coordinated_search_r2 import PublicSearchPolicy
    from shapely.geometry import Point, LineString
    from path_planning.path_finder import _obstacle_facts

    # Use the exact frozen public-progress projection without importing its
    # unrelated model middleware dependencies. This function only needs math.
    tree = ast.parse((source / "task_evidence_r2.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "public_progress")
    namespace = {"math": math}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "frozen-public-progress", "exec"), namespace)
    public_progress = namespace["public_progress"]
    rows = [json.loads(line) for line in (ROOT / "inputs/skill-public-api.jsonl").read_text().splitlines()]
    assert all(r["phase"] == "task" and not r["admin"] and r["origin"]["kind"] == "skill" for r in rows)
    start = json.loads((ROOT / "inputs/start.json").read_text())
    expected = json.loads((ROOT / "inputs/expected-public-terminal.json").read_text())
    context = {"row_index": 0, "last_row": None, "last_move": None}
    choices, accepts, policies, transitions = [], [], [], []

    def candidate_view(policy, actor, index):
        destination = policy.candidates[index]
        start_xy = policy.xy[actor]
        name = TARGETS[actor]["obstacle_name"] if actor in TARGETS else None
        known = [{"id": oid, "name": _obstacle_facts(item).get("name")}
                 for oid, item in policy.known_obstacles.items()]
        facts = {
            "actor": actor, "start_xy": start_xy, "candidate_index": index, "destination": destination,
            "disabled": actor in policy.disabled, "selected": index in policy.selected,
            "current_tried_key": [actor, start_xy, index],
            "current_key_present": (actor, start_xy, index) in policy.tried_edges,
            "same_actor_candidate_tried_keys": sorted([list(key) for key in policy.tried_edges if key[0] == actor and key[2] == index]),
            "novel_score_cells": len(policy.candidate_cover[index] - policy.covered),
            "blocked_destination": bool(policy.blocked.intersects(Point(*destination))),
            "blocked_path": bool(policy.blocked.intersects(LineString([start_xy, destination]))),
            "known_obstacle_count": len(known), "named_obstacle": name,
            "named_obstacle_known_ids": sorted(item["id"] for item in known if item["name"] == name),
            "blocked_geometry_sha256": sha(policy.blocked.wkb),
        }
        facts["eligible"] = (not facts["disabled"] and not facts["selected"] and not facts["current_key_present"]
                             and facts["novel_score_cells"] > 0 and not facts["blocked_destination"] and not facts["blocked_path"])
        return facts

    class TracedPolicy(PublicSearchPolicy):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.replay_round, self.previous_actor = 1, None
            self.target_indices = {actor: self.candidates.index(t["destination"]) for actor, t in TARGETS.items()}
            policies.append(self)

        def choose(self, actor):
            if self.previous_actor is not None and actor <= self.previous_actor:
                self.replay_round += 1
            self.previous_actor = actor
            before = {"choice_number": len(choices) + 1, "round": self.replay_round, "actor": actor,
                      "public_status_request_id": context["last_row"]["request_id"],
                      "public_status": context["last_row"]["response"],
                      "start_xy": self.xy[actor], "selected": sorted(self.selected),
                      "tried_edges": sorted([list(key) for key in self.tried_edges]),
                      "known_obstacles": [{"id": oid, "name": _obstacle_facts(item).get("name")}
                                          for oid, item in self.known_obstacles.items()]}
            if actor in self.target_indices:
                before["watched_candidate"] = candidate_view(self, actor, self.target_indices[actor])
            choice = super().choose(actor)
            before["choice"] = choice
            if choice:
                before["chosen_candidate_state"] = candidate_view(self, actor, choice[0])
            choices.append(before)
            return choice

        def accept(self, actor, index, outcome, actual_xy, nearby_obstacles, *, round_number, sequence):
            assert round_number == self.replay_round
            record = {"sequence": sequence, "round": round_number, "actor": actor, "candidate_index": index,
                      "move_request_id": context["last_move"]["request_id"],
                      "move_receipt": context["last_move"]["response"],
                      "outcome": outcome, "actual_xy": actual_xy,
                      "nearby_request_id": context["last_row"]["request_id"],
                      "nearby_obstacle_ids": [o["id"] for o in nearby_obstacles],
                      "before": candidate_view(self, actor, index)}
            new_ids = super().accept(actor, index, outcome, actual_xy, nearby_obstacles,
                                     round_number=round_number, sequence=sequence)
            record.update(after=candidate_view(self, actor, index), new_obstacle_ids=new_ids)
            accepts.append(record)
            for watched_actor, watched_index in self.target_indices.items():
                transitions.append({"after_move_request_id": record["move_request_id"], "sequence": sequence,
                                    "changed_actor": actor, **candidate_view(self, watched_actor, watched_index)})
            return new_ids

    harness_skills.PublicSearchPolicy = TracedPolicy
    board = PerceptionBlackboard(start["scene_id"])
    runtime = SimpleNamespace(command=start["command"], task_id=start["trial_id"],
                              scene_id=start["scene_id"], scene_epoch=0, deadline=500.0, revision="flight_state_v3")
    frame, generator = harness_skills.make_skill_factory(board)("public_search", start["trial_id"] + ":p1", runtime)
    send_value = None
    reason = None
    iterations = 0
    try:
        while True:
            iterations += 1
            assert iterations <= 10000
            item = generator.send(send_value)
            send_value = None
            if isinstance(item, Checkpoint):
                frame.units += 1
                continue
            assert isinstance(item, PublicCall), type(item)
            row = rows[context["row_index"]]
            assert row["origin"]["tool"] == item.tool, (row["request_id"], row["origin"]["tool"], item.tool)
            assert bool(row["action"]) == item.action
            args = dict(item.args)
            actor = args.pop("drone_id", None)
            if actor:
                assert row["endpoint"].split("/")[2] == actor
            assert (row["params"] or {}) == args, (row["request_id"], row["params"], args)
            send_value = (public_progress(row["response"], board.session_id) if item.tool == "get_mission_progress"
                          else row["response"])
            context["row_index"] += 1
            context["last_row"] = row
            if item.tool == "move_to":
                context["last_move"] = row
                choices[-1]["move_request_id"] = row["request_id"]
            frame.evidence_refs.append("public-request-" + str(row["request_id"]))
            frame.api_calls += 1
    except SkillExit as stop:
        reason = stop.reason
    except StopIteration:
        reason = "skill_public_stop_condition"
    assert context["row_index"] == len(rows), (context["row_index"], len(rows))
    actual = {"reason": reason, "cursor": frame.cursor, "repairs": frame.repairs,
              "api_calls": frame.api_calls, "units": frame.units}
    assert actual == expected, (actual, expected)
    assert len(policies) == 1 and not network_attempts
    write("choice-trace.json", choices)
    write("accept-trace.json", accepts)
    write("watched-candidate-transitions.json", transitions)
    pairs = {}
    for actor, target in TARGETS.items():
        pairs[actor] = {"target": target,
            "choices": [x for x in choices if x["actor"] == actor],
            "repeated_failures": [x for x in accepts if x["move_request_id"] in target["requests"]],
            "actor_moves": [x for x in accepts if x["actor"] == actor],
            "eligibility_transitions": [x for x in transitions if x["actor"] == actor]}
    write("target-pairs.json", pairs)
    summary = {"status": "exact_public_call_replay_verified", "public_calls": len(rows), "choices": len(choices),
               "moves": len(accepts), "terminal": actual, "simulator_calls": 0, "model_calls": 0,
               "network_attempts": network_attempts, "all_frozen_hashes_unchanged": True,
               "target_candidate_indices": policies[0].target_indices,
               "known_obstacles_final": [{"id": oid, "name": _obstacle_facts(item).get("name")}
                                         for oid, item in policies[0].known_obstacles.items()]}
    for name, digest in manifest["files"].items():
        assert sha((ROOT / name).read_bytes()) == digest, name
    write("summary.json", summary)
    write("seal.json", {"files": {str(p.relative_to(ROOT)): sha(p.read_bytes())
                                 for p in ROOT.rglob("*") if p.is_file()}})
    print(json.dumps({k: v for k, v in summary.items() if k != "known_obstacles_final"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
