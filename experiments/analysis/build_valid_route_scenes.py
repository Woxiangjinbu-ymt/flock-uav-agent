#!/usr/bin/env python3
"""Deterministically construct two fresh, locally executable dual-route scenes.

The selection stream and feasibility rules are fixed in PROTOCOL_CN.md. No
model outcomes are read. Run with controller/.venv/bin/python.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "controller"))
sys.path.insert(0, str(ROOT / "server"))
from random_session_generator import RandomSessionGenerator  # noqa: E402
from config.battery_config import BatteryConfig  # noqa: E402
from controllers.obstacle_controller import ObstacleController  # noqa: E402
from models.drone import Drone  # noqa: E402
from models.environment import Environment  # noqa: E402
from models.session import Session  # noqa: E402
from models.task import Task  # noqa: E402

DEST = ROOT / "experiments/r3-valid-route-v1"
SEEDS = range(2026100901, 2026100913)
ACCEPT_COUNT = 2
ALTITUDE = 26.0
STAMP = 1800000000.0


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def stable_id(seed: int, kind: str, ordinal: int = 0) -> str:
    return digest(f"valid-route-v1:{seed}:{kind}:{ordinal}".encode())[:8]


def point(x: float, y: float, z: float = ALTITUDE) -> dict:
    return {"x": float(x), "y": float(y), "z": float(z)}


def length(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def legal(controller: ObstacleController, a: dict, b: dict) -> bool:
    if controller.check_point_collision(b["x"], b["y"], b["z"], margin=1.0)["result"]:
        return False
    return not controller.check_path_collision(
        a["x"], a["y"], b["x"], b["y"], a["z"], b["z"], safety_margin=1.0)["collision"]


def route_energy(drone: dict, route: list[dict], environment: dict) -> float:
    prev = point(drone["position"]["x"], drone["position"]["y"])
    total = BatteryConfig.calculate_movement_cost("take_off", drone["position"], prev,
                                                  environment=environment, drone_heading=drone["heading"])
    for destination in route:
        total += BatteryConfig.calculate_movement_cost("move_along_path", prev, destination,
                                                       environment=environment, drone_heading=drone["heading"])
        prev = destination
    total += BatteryConfig.calculate_movement_cost("land", prev,
                                                   point(prev["x"], prev["y"], 0),
                                                   environment=environment, drone_heading=drone["heading"])
    return total


def sample_route(rng: random.Random, drone: dict, controller: ObstacleController,
                 environment: dict) -> tuple[list[dict], dict] | None:
    start = point(drone["position"]["x"], drone["position"]["y"])
    if controller.check_point_collision(start["x"], start["y"], ALTITUDE, margin=1.0)["result"]:
        return None
    if drone["max_altitude"] <= ALTITUDE:
        return None
    for route_attempt in range(100):
        route = []
        current = start
        for ordinal in range(12):
            chosen = None
            for _ in range(500):
                candidate = point(rng.randint(50, 950), rng.randint(50, 950))
                distance = length(current, candidate)
                if not 120 <= distance <= 330:
                    continue
                if any(length(candidate, previous) < 60 for previous in route):
                    continue
                if not legal(controller, current, candidate):
                    continue
                if ordinal == 11 and controller.check_point_collision(
                        candidate["x"], candidate["y"], 0.0, margin=1.0)["result"]:
                    continue
                chosen = candidate
                break
            if chosen is None:
                break
            route.append(chosen)
            current = chosen
        if len(route) != 12:
            continue
        distance = sum(length(a, b) for a, b in zip([start, *route[:-1]], route))
        energy = route_energy(drone, route, environment)
        if 1800 <= distance <= 3400 and energy <= 70:
            return route, {"route_attempt": route_attempt + 1,
                           "horizontal_distance_m": round(distance, 2),
                           "estimated_battery_percent": round(energy, 2)}
    return None


def format_xyz(p: dict) -> str:
    return f"({int(p['x'])}, {int(p['y'])}, {int(p['z'])})"


def build_task(seed: int, drones: list[dict], routes: list[list[dict]]) -> dict:
    names = [drone["name"] for drone in drones]
    chunks = [f"Drones {names[0]} and {names[1]} perform a dual marathon flight at 26 meters."]
    checks, related = [], []
    for drone, route in zip(drones, routes):
        chunks.append(f"{drone['name']} route: fly to and start at {format_xyz(route[0])}, "
                      f"then waypoints {', '.join(format_xyz(p) for p in route[1:-1])}, "
                      f"then end {format_xyz(route[-1])} and land.")
        related.append({"endpoint": "/drones/{id}/command/take_off",
                        "parameters": {"id": drone["id"], "altitude": ALTITUDE}})
        for position in route:
            related.append({"endpoint": "/drones/{id}/command/move_to",
                            "parameters": {"id": drone["id"], **position}})
        related.append({"endpoint": "/drones/{id}/command/land", "parameters": {"id": drone["id"]}})
        for position in route[:-1]:
            checks.append({"endpoint": "/check/drone_has_visited_position",
                           "parameters": {"drone_id": drone["id"], **position, "tolerance": 3.0}})
        checks.append({"endpoint": "/check/drone_position",
                       "parameters": {"drone_id": drone["id"], "x": route[-1]["x"],
                                      "y": route[-1]["y"], "z": 0, "tolerance": 3.0}})
        checks.append({"endpoint": "/check/drone_on_ground",
                       "parameters": {"drone_id": drone["id"], "tolerance": 0.5}})
    task = Task(name=f"Validated Dual Marathon {seed}", content=" ".join(chunks),
                description="Prospectively generated executable two-UAV route",
                creator="research-validator", originated_from="valid-route-v1 protocol",
                difficulty="hard", related_apis=related,
                execution_check_apis={"logic": "and", "checks": checks},
                commands=["take_off", "move_to", "land"],
                task_id=stable_id(seed, "task"), created_at=STAMP, last_updated=STAMP)
    return task.to_dict()


def build_scene(seed: int) -> tuple[dict, dict] | tuple[None, str]:
    old_state = random.getstate()
    try:
        random.seed(seed)
        generated = RandomSessionGenerator().generate_session_data(
            drone_count=2, target_count=0, obstacle_count=10,
            area_width=1000, area_height=1000, task_type="others")
        env_payload = RandomSessionGenerator().generate_environment_payload(f"Valid Route {seed}")
    finally:
        random.setstate(old_state)
    if len(generated["drones"]) != 2 or len(generated["obstacles"]) != 10:
        return None, "entity_count"
    drones = []
    for index, payload in enumerate(generated["drones"]):
        record = {**payload, "id": stable_id(seed, "drone", index),
                  "created_at": STAMP, "last_updated": STAMP,
                  "battery_level": 100.0, "battery_volume": payload["battery_capacity"]}
        drones.append(Drone.from_dict(record).to_dict())
    controller = ObstacleController()
    for index, payload in enumerate(generated["obstacles"]):
        controller.add_obstacle({**payload, "id": stable_id(seed, "obstacle", index),
                                 "created_at": STAMP, "last_updated": STAMP})
    obstacles = controller.get_all_obstacles()
    environment = Environment(
        name=env_payload["name"], weather=env_payload["weather"],
        temperature=env_payload["temperature"], humidity=env_payload["humidity"],
        pressure=env_payload["pressure"], wind_speed=env_payload["wind_speed"],
        wind_direction=env_payload["wind_direction"], visibility=env_payload["visibility"],
        environment_id=stable_id(seed, "environment"), created_at=STAMP, last_updated=STAMP).to_dict()
    rng = random.Random(seed + 7000000)
    routes, route_metadata = [], []
    for drone in drones:
        sampled = sample_route(rng, drone, controller, environment)
        if sampled is None:
            return None, f"no_legal_route:{drone['name']}"
        route, metadata = sampled
        routes.append(route)
        route_metadata.append(metadata)
    task = build_task(seed, drones, routes)
    scene = {"id": stable_id(seed, "scene"), "name": f"Validated Dual Route {seed}",
             "description": "Fresh fixed-seed route mechanism challenge; original benchmark unchanged",
             "status": "inactive", "creator": "research-validator", "task_type": "others",
             "task_description": "Two-drone ordered coordinate route",
             "is_distance_3d": False, "canvas_width": 1000.0, "canvas_height": 1000.0,
             "created_at": STAMP, "last_updated": STAMP,
             "drones": drones, "targets": [], "obstacles": obstacles,
             "environment": environment, "tasks": [task],
             "history": {"command_history": [], "status_history": {}, "path_history": {},
                         "target_reaches": {}, "moving_target_tracking": {}, "area_coverage": {}}}
    parsed = Session.from_dict(scene)
    assert parsed.id == scene["id"] and len(parsed.tasks) == 1
    return scene, {"routes": route_metadata, "seed": seed,
                   "drone_ids": [drone["id"] for drone in drones]}


def main():
    if (DEST / "manifest.json").exists():
        raise ValueError("Already generated and frozen; refusing to resample")
    protocol = ROOT / "docs/audit/r3-valid-route-prospective-20261009/PROTOCOL_CN.md"
    with ZipFile(ROOT / "experiments/runs/20261003-143255-full-5c501118/source.zip") as archive:
        names = ("controller/random_session_generator.py", "server/controllers/obstacle_controller.py",
                 "server/config/battery_config.py", "server/models/session.py")
        assert all(archive.read(name) == (ROOT / name).read_bytes() for name in names)
    candidates = []
    accepted = []
    for seed in SEEDS:
        scene, info = build_scene(seed)
        if scene is None:
            candidates.append({"seed": seed, "status": "rejected", "reason": info})
            continue
        blob = (json.dumps(scene, ensure_ascii=False, indent=2) + "\n").encode()
        candidate = {"seed": seed, "status": "accepted", "scene_id": scene["id"],
                     "task_id": scene["tasks"][0]["id"], "sha256": digest(blob), **info}
        candidates.append(candidate)
        accepted.append((scene, blob, candidate))
        if len(accepted) == ACCEPT_COUNT:
            break
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "protocol_sha256": digest(protocol.read_bytes()),
                "seed_start": SEEDS.start, "seed_stop_exclusive": SEEDS.stop,
                "accepted_required": ACCEPT_COUNT, "candidates": candidates,
                "accepted": [candidate for _, _, candidate in accepted],
                "source_sha256": {name: digest((ROOT / name).read_bytes()) for name in names}}
    for scene, blob, _ in accepted:
        directory = DEST / "data"
        directory.mkdir(exist_ok=True)
        (directory / f"{scene['id']}.json").write_bytes(blob)
    (DEST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"accepted": [x[2]["seed"] for x in accepted],
                      "rejected": [x for x in candidates if x["status"] == "rejected"],
                      "model_calls": 0}, ensure_ascii=False))
    if len(accepted) != ACCEPT_COUNT:
        raise ValueError("Predeclared seed stream yielded fewer than two scenes")


if __name__ == "__main__":
    main()
