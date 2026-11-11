"""Quality witness privacy/identity boundaries; no provider or real simulation."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_task_dataset as quality


def recipe(steps, omit=0):
    return {"version": 1, "steps": steps, "negative_omit_step_index": omit,
            "constants": {"default_flight_altitude": 20, "coverage_spacing": 10}}


class PublicAPI:
    def __init__(self):
        self.calls = []
        self.drones = [{"id": "public-drone-1", "name": "Drone 1"}, {"id": "public-drone-2", "name": "Drone 2"}]
        self.targets = [{"id": "public-target-1", "name": "Fixed Target 1"}, {"id": "public-circle", "name": "Circle Target 1"}]
        self.details = {
            "public-target-1": {**self.targets[0], "type": "fixed", "position": {"x": 143, "y": 217, "z": 0}},
            "public-circle": {**self.targets[1], "type": "circle", "position": {"x": 313, "y": 419, "z": 0}, "radius": 12},
        }

    def __call__(self, method, endpoint, **kwargs):
        assert not kwargs.get("admin"), "Solver attempted evaluator access"
        assert not endpoint.startswith(("/check", "/sessions")), "Private endpoint entered solver"
        self.calls.append((method, endpoint, copy.deepcopy(kwargs)))
        if method == "GET" and endpoint == "/drones":
            return copy.deepcopy(self.drones)
        if method == "GET" and endpoint.endswith("/nearby"):
            return {"targets": copy.deepcopy(self.targets)}
        if method == "GET" and endpoint.startswith("/targets/"):
            return copy.deepcopy(self.details[endpoint.rsplit("/", 1)[-1]])
        if method == "POST" and "/command/" in endpoint:
            return {"status": "success"}
        raise AssertionError(f"Unexpected public request: {method} {endpoint}")


def posts(api):
    return [call for call in api.calls if call[0] == "POST"]


def test_visit_uses_observed_id_and_public_target_coordinates():
    api = PublicAPI()
    result = quality.execute_public_recipe("Drone 2 must visit Fixed Target 1.", recipe([
        {"op": "visit_target", "drone": "Drone 2", "target": "Fixed Target 1", "altitude": 20}]), api)
    assert posts(api) == [("POST", "/drones/public-drone-2/command/move_to", {"params": {"x": 143, "y": 217, "z": 20}})]
    assert ("GET", "/targets/public-target-1", {}) in api.calls
    assert result["public_target_ids"] == ["public-circle", "public-target-1"]
    assert api.calls.index(("GET", "/drones/public-drone-1/nearby", {})) < api.calls.index(("GET", "/targets/public-target-1", {}))


def test_unobserved_target_is_rejected_before_detail_or_action():
    api = PublicAPI()
    api.targets = []
    with pytest.raises(ValueError, match="not been publicly observed"):
        quality.execute_public_recipe("Drone 1 must visit Fixed Target 1.", recipe([
            {"op": "visit_target", "drone": "Drone 1", "target": "Fixed Target 1", "altitude": 20}]), api)
    assert not posts(api) and not any(call[1].startswith("/targets/") for call in api.calls)


@pytest.mark.parametrize("key,value", [("id", "other-target"), ("name", "Fixed Target 2")])
def test_detail_target_identity_must_match_public_discovery(key, value):
    api = PublicAPI()
    api.details["public-target-1"][key] = value
    with pytest.raises(ValueError, match="identity changed"):
        quality.execute_public_recipe("Drone 1 must visit Fixed Target 1.", recipe([
            {"op": "visit_target", "drone": "Drone 1", "target": "Fixed Target 1", "altitude": 20}]), api)
    assert not posts(api)


def test_duplicate_actor_name_cannot_choose_an_arbitrary_public_identity():
    api = PublicAPI()
    api.drones[1]["name"] = "Drone 1"
    with pytest.raises(ValueError, match="Ambiguous public actor"):
        quality.execute_public_recipe("Drone 1 must hover.", recipe([{"op": "hover", "drone": "Drone 1"}]), api)
    assert not posts(api)


def test_duplicate_target_name_cannot_choose_an_arbitrary_public_identity():
    api = PublicAPI()
    api.targets.append({"id": "another-target", "name": "Fixed Target 1"})
    with pytest.raises(ValueError, match="Ambiguous public target"):
        quality.execute_public_recipe("Drone 1 must visit Fixed Target 1.", recipe([
            {"op": "visit_target", "drone": "Drone 1", "target": "Fixed Target 1", "altitude": 20}]), api)
    assert not posts(api)


@pytest.mark.parametrize("position", [{"x": 1, "y": 5, "z": 3}, {"x": 4, "y": 2, "z": 6}, {"x": 999, "y": 2, "z": 3}])
def test_waypoint_must_be_a_whole_public_tuple_not_a_bag_of_numbers(position):
    api = PublicAPI()
    with pytest.raises(ValueError, match="Waypoint tuple absent"):
        quality.execute_public_recipe("Drone 1 must visit (1, 2, 3) and (4, 5, 6).", recipe([
            {"op": "move_to", "drone": "Drone 1", "position": position, "source": "command"}]), api)
    assert api.calls == []


def test_command_tuple_navigation_uses_public_actor_binding():
    api = PublicAPI()
    quality.execute_public_recipe("Drone 2 must visit (1, 2, 3).", recipe([
        {"op": "move_to", "drone": "Drone 2", "position": {"x": 1, "y": 2, "z": 3}, "source": "command"}]), api)
    assert posts(api)[0][1:] == ("/drones/public-drone-2/command/move_to", {"params": {"x": 1, "y": 2, "z": 3}})


@pytest.mark.parametrize("step", [
    {"op": "move_to", "drone": "Drone 1", "position": {"x": 1, "y": 2, "z": 3}, "source": "private_scene"},
    {"op": "visit_target", "drone": "Drone 1", "target": "Fixed Target 1", "altitude": 20, "position": {"x": 1, "y": 2, "z": 3}},
    {"op": "visit_target", "drone": "Drone 1", "target": "Fixed Target 1", "altitude": 20, "target_id": "private-id"},
    {"op": "hover", "drone": "Drone 1", "admin": True},
])
def test_private_witness_fields_fail_before_any_public_call(step):
    api = PublicAPI()
    with pytest.raises(ValueError):
        quality.execute_public_recipe("Drone 1 must visit Fixed Target 1 at (1, 2, 3).", recipe([step]), api)
    assert api.calls == []


def test_fleet_choice_still_uses_a_returned_public_actor():
    api = PublicAPI()
    quality.execute_public_recipe("Use any available drones to visit all non-waypoint targets.", recipe([
        {"op": "visit_target", "drone": "Drone 2", "target": "Fixed Target 1", "altitude": 20}]), api)
    assert posts(api)[0][1] == "/drones/public-drone-2/command/move_to"


def test_coverage_path_changes_only_with_public_geometry():
    public_paths = []
    for x, y, radius in ((313, 419, 12), (529, 631, 8)):
        api = PublicAPI()
        api.details["public-circle"].update(position={"x": x, "y": y, "z": 0}, radius=radius)
        quality.execute_public_recipe("Use the available fleet to cover Circle Target 1.", recipe([
            {"op": "cover_target", "drone": "Drone 1", "target": "Circle Target 1", "altitude": 20, "spacing": 10}]), api)
        action = posts(api)[0]
        assert action[1] == "/drones/public-drone-1/command/move_along_path"
        points = action[2]["json"]["waypoints"]
        assert action[2]["json"]["allow_partial_move"] is False
        assert min(p["x"] for p in points) == x - radius and max(p["x"] for p in points) == x + radius
        assert min(p["y"] for p in points) == y - radius and max(p["y"] for p in points) == y + radius
        assert {p["z"] for p in points} == {20}
        public_paths.append(points)
    assert public_paths[0] != public_paths[1]


@pytest.mark.parametrize("change", [{"type": "polygon"}, {"radius": 0}, {"radius": 101}])
def test_unbounded_or_unsupported_public_coverage_geometry_is_rejected(change):
    api = PublicAPI()
    api.details["public-circle"].update(change)
    with pytest.raises(ValueError, match="bounded public circles"):
        quality.execute_public_recipe("Use the available fleet to cover Circle Target 1.", recipe([
            {"op": "cover_target", "drone": "Drone 1", "target": "Circle Target 1", "altitude": 20, "spacing": 10}]), api)
    assert not posts(api)


def test_negative_control_omits_only_the_predeclared_step():
    steps = [{"op": "take_off", "drone": "Drone 1", "altitude": 20},
             {"op": "rotate", "drone": "Drone 1", "heading": 90},
             {"op": "hover", "drone": "Drone 1"}]
    positive, negative = PublicAPI(), PublicAPI()
    command = "Drone 1 must take off to 20 meters, face heading 90 degrees, and hover."
    quality.execute_public_recipe(command, recipe(steps, 1), positive)
    quality.execute_public_recipe(command, recipe(steps, 1), negative, negative=True)
    assert [row[1].rsplit("/", 1)[-1] for row in posts(positive)] == ["take_off", "rotate", "hover"]
    assert [row[1].rsplit("/", 1)[-1] for row in posts(negative)] == ["take_off", "hover"]


def test_unknown_action_or_missing_negative_control_fails_before_execution():
    with pytest.raises(ValueError):
        quality.verify_recipe("Drone 1 must hover.", recipe([{"op": "set_private_state", "drone": "Drone 1"}]))
    with pytest.raises(ValueError, match="negative control"):
        quality.verify_recipe("Drone 1 must hover.", recipe([{"op": "hover", "drone": "Drone 1"}], True))


def test_actor_matching_does_not_confuse_drone_1_with_drone_10():
    with pytest.raises(ValueError, match="actor"):
        quality.verify_recipe("Drone 10 must hover.", recipe([{"op": "hover", "drone": "Drone 1"}]))


@pytest.mark.parametrize("step,command", [
    ({"op": "rotate", "drone": "Drone 1", "heading": True}, "Drone 1 must rotate to 90 degrees."),
    ({"op": "take_off", "drone": "Drone 1", "altitude": True}, "Drone 1 must take off to 20 meters."),
    ({"op": "cover_target", "drone": "Drone 1", "target": "Circle Target 1", "altitude": 20, "spacing": True}, "Use the available fleet to cover Circle Target 1."),
])
def test_boolean_numeric_witness_values_are_rejected(step, command):
    with pytest.raises(ValueError):
        quality.verify_recipe(command, recipe([step]))


def test_scene_worker_restores_each_task_and_each_negative_control(monkeypatch, tmp_path):
    """A deliberately dirty positive run must not contaminate negative or next task."""
    state = {"dirty": False, "run_starts": [], "restores": 0, "results": None}
    scene = {"seed": 31, "scene_id": "s1"}
    tasks = [{"task_id": f"t{i}", "scene_id": "s1", "split": "train", "task_family": "F1",
              "command": "Drone 1 must hover.", "quality": {"solver_recipe": recipe([{"op": "hover", "drone": "Drone 1"}])},
              "original_task": {"execution_check_apis": {"endpoint": "/check/drone_has_hovered"}}} for i in range(2)]

    class World:
        def __init__(self, **kwargs):
            self.phase = "preparation"
            self.requests, self.physical_events = [], []

        def import_session(self, raw):
            pass

        def snapshot(self):
            return b"clean-initial-snapshot"

        def restore(self, blob, expected):
            assert blob == b"clean-initial-snapshot"
            assert expected == quality.sha(blob)
            state["dirty"] = False
            state["restores"] += 1

        def state_digest(self):
            return "dirty" if state["dirty"] else "initial-state"

        def public_seed(self):
            return {"state": "initial" if not state["dirty"] else "dirty"}

        def start(self, **kwargs):
            assert not state["dirty"]
            self.phase = "task"

        def freeze_score(self, **kwargs):
            return b"positive" if state["dirty"] else b"negative"

        def evaluate(self, ident, blob):
            return {"passed": blob == b"positive", "errors": [], "checks_expected": 1}

        def request(self, *args, **kwargs):
            raise AssertionError("Mock witness should not enter network")

    def execute(command, witness, request, *, negative):
        state["run_starts"].append({"negative": negative, "dirty": state["dirty"]})
        assert not state["dirty"]
        state["dirty"] = not negative
        return {"actions": []}

    monkeypatch.setitem(sys.modules, "harness_full100_world", SimpleNamespace(FullWorld=World))
    monkeypatch.setitem(sys.modules, "harness_natural_runner", SimpleNamespace(task_request_allowed=lambda *args: True))
    monkeypatch.setitem(sys.modules, "harness_v2_world", SimpleNamespace(digest=lambda obj: json.dumps(obj, sort_keys=True)))
    monkeypatch.setattr(quality, "verify_sources", lambda output: {"dataset": str(tmp_path / "dataset")})
    monkeypatch.setattr(quality, "load_dataset", lambda dataset: ({"tasks": tasks}, {"s1": (scene, {})}))
    monkeypatch.setattr(quality, "execute_public_recipe", execute)
    monkeypatch.setattr(quality, "write", lambda path, value: state.update(results=value))
    quality.scene_worker(tmp_path / "dataset", tmp_path / "audit", "s1")
    assert state["restores"] == 6  # initial checker plus independent positive/negative per task
    assert state["run_starts"] == [{"negative": value, "dirty": False} for value in (False, True, False, True)]
    assert state["results"]["passed"] is True and len(state["results"]["rows"]) == 2


@pytest.mark.parametrize("mutation", ["dataset", "run-manifest"])
def test_quality_run_rejects_input_version_change(monkeypatch, tmp_path, mutation):
    dataset, output = tmp_path / "dataset", tmp_path / "audit"
    quality.write(dataset / "manifest.json", {"version": 1})
    quality.write(output / "run-manifest.json", {"dataset": str(dataset), "sources": {},
                  "dataset_manifest_sha256": quality.sha((dataset / "manifest.json").read_bytes())})
    (output / "run-manifest.sha256").write_text(quality.sha((output / "run-manifest.json").read_bytes()))
    calls = []
    monkeypatch.setattr(quality, "load_dataset", lambda p: calls.append(p))
    quality.verify_sources(output)
    assert calls == [dataset]
    changed = dataset / "manifest.json" if mutation == "dataset" else output / "run-manifest.json"
    with changed.open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="changed"):
        quality.verify_sources(output)


def test_worker_rejects_other_dataset_before_simulator_import(monkeypatch, tmp_path):
    monkeypatch.setattr(quality, "verify_sources", lambda p: {"dataset": str(tmp_path / "first")})
    with pytest.raises(ValueError, match="Worker dataset path changed"):
        quality.scene_worker(tmp_path / "second", tmp_path / "audit", "s1")
