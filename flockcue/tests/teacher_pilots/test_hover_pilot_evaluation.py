"""Independent frozen-world semantics; no provider, policy, pickle or simulator."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(next(parent / "scripts" for parent in Path(__file__).resolve().parents if (parent / "scripts").is_dir())))
import hover_pilot_evaluation as scoring


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent scoring tests cannot access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def state(z=12, **changes):
    return {"id": scoring.ACTOR, "name": scoring.ACTOR_NAME, "status": "hovering", "speed": 0,
            "position": {"x": 660, "y": 734, "z": z}, **changes}


def action(ident, command="take_off", *, start=None, end=None, committed=True, actor=scoring.ACTOR):
    start = start or {"x": 660, "y": 734, "z": 0 if command == "take_off" else 12}
    end = end or {"x": 660, "y": 734, "z": 12}
    history = {"event": "hovering" if command == "hover" else command,
               "start_position": start, "position": end, "duration": 0}
    origin = {"kind": "model_tool", "tool": command, "tool_call_id": "call-" + str(ident)}
    row = {"request_id": ident, "phase": "task", "method": "POST", "admin": False,
           "endpoint": f"/drones/{actor}/command/{command}", "action": True,
           "params": {"altitude": 12} if command == "take_off" else {},
           "response": {"drone_id": actor, "command": command, "status": "success"},
           "committed": committed, "origin": origin, "history_events": [{"actor": actor, "history": history}]}
    event = {"event_id": f"{ident}:0", "request_id": ident, "phase": "task", "actor": actor,
             "history": deepcopy(history), "origin": deepcopy(origin), "committed": committed}
    return row, event


def evidence(*commands):
    pairs = [action(i + 1, cmd) for i, cmd in enumerate(commands or ("take_off",))]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def assess(condition="state_hover", *, final=None, public=None, physical=None):
    if public is None:
        public, physical = evidence()
    return scoring.evaluate_evidence(condition, state(0, status="idle"), final or state(), public, physical)


@pytest.mark.parametrize("condition", ["state_hover", "unknown_takeoff"])
def test_goal_truth_uses_actual_effect_and_final_state_without_any_policy_query(condition):
    public, physical = evidence()
    before = deepcopy((public, physical))
    result = assess(condition, public=public, physical=physical)
    assert result["task_completed"] is True and result["status"] == "satisfied"
    assert result["policy_status_query_required"] is False
    assert result["evidence_refs"] == ["1:0"]
    assert not any(r["method"] == "GET" for r in public)
    assert (public, physical) == before


def test_explicit_hover_requires_real_ordered_effect_and_state_alone_is_insufficient():
    assert assess("explicit_hover")["task_completed"] is False
    public, physical = evidence("take_off", "hover")
    result = assess("explicit_hover", public=public, physical=physical)
    assert result["task_completed"] is True and result["evidence_refs"] == ["1:0", "2:0"]
    public, physical = evidence("hover", "take_off")
    assert assess("explicit_hover", public=public, physical=physical)["task_completed"] is False


@pytest.mark.parametrize("damage", ["altitude", "ground", "moving_status", "speed", "xy_drift"])
def test_final_goal_state_failures_are_false_not_unknown(damage):
    final = state()
    if damage == "altitude": final["position"]["z"] = 13
    elif damage == "ground": final["position"]["z"] = 0
    elif damage == "moving_status": final["status"] = "moving"
    elif damage == "speed": final["speed"] = 1
    else: final["position"]["x"] += 1
    result = assess(final=final)
    assert result["task_completed"] is False and result["status"] == "not_satisfied"


@pytest.mark.parametrize("damage", ["wrong_actor", "wrong_command", "failed", "wrong_altitude",
                                      "bool_altitude", "partial", "uncommitted", "wrong_event"])
def test_attempt_or_receipt_alone_cannot_substitute_for_matching_actual_takeoff(damage):
    public, physical = evidence()
    row, event = public[0], physical[0]
    if damage == "wrong_actor": row["response"]["drone_id"] = "other"
    elif damage == "wrong_command": row["response"]["command"] = "hover"
    elif damage == "failed": row["response"]["status"] = "error"
    elif damage == "wrong_altitude": row["params"]["altitude"] = 11
    elif damage == "bool_altitude": row["params"]["altitude"] = True
    elif damage == "partial": row["response"]["partial_success"] = True
    elif damage == "uncommitted": row["committed"] = event["committed"] = False
    else:
        row["history_events"][0]["history"]["event"] = "hovering"
        event["history"]["event"] = "hovering"
    assert assess(public=public, physical=physical)["task_completed"] is False


def test_missing_takeoff_is_goal_failure_and_zero_history_needs_no_sidecar():
    result = assess(public=[], physical=[])
    assert result["task_completed"] is False
    assert "same_actor_takeoff_to_requested_altitude" in result["reasons"]


def test_travel_away_and_back_is_not_in_place_even_when_final_state_matches():
    public, physical = evidence()
    away = {"x": 700, "y": 734, "z": 12}
    for pair in (action(2, "move_to", end=away), action(3, "move_to", start=away)):
        public.append(pair[0]); physical.append(pair[1])
    result = assess(public=public, physical=physical)
    assert result["task_completed"] is False and result["intervening_travel_event_ids"] == ["2:0", "3:0"]


def test_other_actor_and_uncommitted_motion_do_not_change_frozen_goal_truth():
    public, physical = evidence()
    away = {"x": 700, "y": 734, "z": 12}
    for pair in (action(2, "move_to", end=away, actor="other"),
                 action(3, "move_to", end=away, committed=False)):
        public.append(pair[0]); physical.append(pair[1])
    assert assess(public=public, physical=physical)["task_completed"] is True


def test_duration_echo_and_instantaneous_hover_never_establish_elapsed_holding():
    public, physical = evidence("take_off", "hover")
    public[-1]["history_events"][0]["history"]["duration"] = 5
    physical[-1]["history"]["duration"] = 5
    result = assess("duration_hover", public=public, physical=physical)
    assert result["task_completed"] is None and result["status"] == "not_evaluable"
    assert result["reasons"] == [scoring.DURATION_REASON]
    assert result["elapsed_duration_verified"] is False


@pytest.mark.parametrize("damage", ["sidecar_missing", "event_unbound", "event_duplicate", "history_changed",
                                     "committed_changed", "request_duplicate", "position_nan"])
def test_log_inconsistency_is_integrity_error_not_a_fabricated_mission_failure(damage):
    public, physical = evidence()
    if damage == "sidecar_missing": physical = []
    elif damage == "event_unbound": physical[0]["request_id"] = 99
    elif damage == "event_duplicate": physical.append(deepcopy(physical[0]))
    elif damage == "history_changed": physical[0]["history"]["position"]["z"] = 100
    elif damage == "committed_changed": physical[0]["committed"] = False
    elif damage == "request_duplicate": public.append(deepcopy(public[0]))
    else:
        public[0]["history_events"][0]["history"]["position"]["z"] = float("nan")
        physical[0]["history"] = deepcopy(public[0]["history_events"][0]["history"])
    with pytest.raises(ValueError):
        assess(public=public, physical=physical)


@pytest.mark.parametrize("change", [{"id": "other"}, {"speed": float("nan")}, {"speed": False},
                                    {"position": {"x": 1, "y": 2}}, {"error": "unavailable"}])
def test_malformed_snapshot_is_not_scored_as_task_failure(change):
    with pytest.raises(ValueError):
        assess(final=state(**change))


class FrozenWorld:
    phase = "scoring"

    def __init__(self):
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return state()


def write_run(tmp_path):
    scene_file = "scenes/fixed.json"
    scene = tmp_path / "inputs/dataset" / scene_file
    scene.parent.mkdir(parents=True)
    scene.write_text(json.dumps({"drones": [state(0, status="idle")]}))
    folder = tmp_path / "trials/state-control"
    logs = folder / "policy/worldlogs"
    logs.mkdir(parents=True)
    public, physical = evidence()
    for name, rows in (("public-api.jsonl", public), ("physical-events.jsonl", physical)):
        (logs / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    fixture = {"task_id": scoring.TASK, "scene_id": "fb8971ec", "scene_file": scene_file}
    trial = {"task_id": scoring.TASK, "condition": "state_hover"}
    return folder, fixture, trial


def test_snapshot_adapter_queries_only_frozen_scoring_world_and_does_not_modify_policy(tmp_path):
    folder, fixture, trial = write_run(tmp_path)
    before = {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    world = FrozenWorld()
    result = scoring.evaluate_snapshot(world, fixture, trial, tmp_path, folder)
    assert result["task_completed"] is True
    assert world.calls == [(("GET", "/drones/3d229034"), {"admin": True})]
    assert {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    imports = [n for n in ast.walk(ast.parse(Path(scoring.__file__).read_text()))
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert all("uav_agent" not in ast.unparse(n) and "hover_completion" not in ast.unparse(n) for n in imports)


@pytest.mark.parametrize("damage", ["policy_phase", "task", "condition", "path_escape", "ambiguous_actor"])
def test_snapshot_adapter_rejects_out_of_scope_or_unbound_inputs(tmp_path, damage):
    folder, fixture, trial = write_run(tmp_path)
    world = FrozenWorld()
    if damage == "policy_phase": world.phase = "task"
    elif damage == "task": trial["task_id"] = "other"
    elif damage == "condition": trial["condition"] = "search"
    elif damage == "path_escape": fixture["scene_file"] = "../../outside.json"
    else:
        (tmp_path / "inputs/dataset" / fixture["scene_file"]).write_text(json.dumps({"drones": [state(0), state(0)]}))
    with pytest.raises(ValueError):
        scoring.evaluate_snapshot(world, fixture, trial, tmp_path, folder)
    assert world.calls == []


def no_activity(folder):
    for name in ("public-api.jsonl", "physical-events.jsonl"):
        (folder / "policy/worldlogs" / name).unlink()
    worker = {"physical_cost": {"api_requests": 0, "physical_history_events": 0}, "files": {}}
    (folder / "worker-result.json").write_text(json.dumps(worker))
    return worker


@pytest.mark.parametrize("condition,expected", [("duration_hover", None), ("state_hover", False)])
def test_zero_api_exit_with_sealed_absence_is_scoreable(tmp_path, condition, expected):
    folder, fixture, trial = write_run(tmp_path)
    trial["condition"] = condition
    no_activity(folder)
    result = scoring.evaluate_snapshot(FrozenWorld(), fixture, trial, tmp_path, folder)
    assert result["task_completed"] is expected
    if condition == "duration_hover":
        assert result["reasons"] == [scoring.DURATION_REASON]
    assert not (folder / "policy/worldlogs/public-api.jsonl").exists()


@pytest.mark.parametrize("damage", ["nonzero_api", "missing_api_count", "boolean_api_count",
                                     "sealed_public", "nonzero_events", "sealed_physical", "no_worker"])
def test_missing_logs_cannot_hide_expected_activity_or_disappear_from_sealed_files(tmp_path, damage):
    folder, fixture, trial = write_run(tmp_path)
    trial["condition"] = "duration_hover"
    worker = no_activity(folder)
    if damage == "nonzero_api": worker["physical_cost"]["api_requests"] = 1
    elif damage == "missing_api_count": worker["physical_cost"].pop("api_requests")
    elif damage == "boolean_api_count": worker["physical_cost"]["api_requests"] = False
    elif damage == "sealed_public": worker["files"]["policy/worldlogs/public-api.jsonl"] = "hash"
    elif damage == "nonzero_events": worker["physical_cost"]["physical_history_events"] = 1
    elif damage == "sealed_physical": worker["files"]["policy/worldlogs/physical-events.jsonl"] = "hash"
    if damage == "no_worker": (folder / "worker-result.json").unlink()
    else: (folder / "worker-result.json").write_text(json.dumps(worker))
    world = FrozenWorld()
    with pytest.raises(ValueError, match="Missing"):
        scoring.evaluate_snapshot(world, fixture, trial, tmp_path, folder)
    assert world.calls == []


def test_missing_physical_log_still_crosschecks_nonempty_public_history(tmp_path):
    folder, fixture, trial = write_run(tmp_path)
    (folder / "policy/worldlogs/physical-events.jsonl").unlink()
    (folder / "worker-result.json").write_text(json.dumps({
        "physical_cost": {"api_requests": 1, "physical_history_events": 0}, "files": {}}))
    with pytest.raises(ValueError, match="sidecar is incomplete"):
        scoring.evaluate_snapshot(FrozenWorld(), fixture, trial, tmp_path, folder)
