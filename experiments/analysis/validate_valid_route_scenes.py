#!/usr/bin/env python3
"""Zero-model execution audit of the frozen valid-route scenes.

Run with server/.venv/bin/python. This script imports each frozen scene into
the unmodified FastAPI application and records original command/check results.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from api.server import ROLE_SECRETS, UserRole, app  # noqa: E402

DEST = ROOT / "experiments/r3-valid-route-v1"


def assert_success(response, label: str, *, command: bool = False) -> dict:
    body = response.json()
    if response.status_code not in (200, 201) or (command and body.get("status") != "success"):
        raise RuntimeError(f"{label}: HTTP {response.status_code}: {body}")
    return body


def routes_from_task(scene: dict) -> dict[str, list[dict]]:
    routes: dict[str, list[dict]] = {}
    for api in scene["tasks"][0]["related_apis"]:
        if api["endpoint"].endswith("/command/move_to"):
            params = api["parameters"]
            routes.setdefault(params["id"], []).append(
                {axis: params[axis] for axis in ("x", "y", "z")}
            )
    if len(routes) != 2 or any(len(points) != 12 for points in routes.values()):
        raise ValueError("Expected two 12-point routes in task related_apis")
    return routes


def main() -> None:
    manifest = json.loads((DEST / "manifest.json").read_text())
    if len(manifest["accepted"]) != 2:
        raise ValueError("Expected exactly two frozen scenes")
    protocol = ROOT / "docs/audit/r3-valid-route-prospective-20261009/PROTOCOL_CN.md"
    if hashlib.sha256(protocol.read_bytes()).hexdigest() != manifest["protocol_sha256"]:
        raise ValueError("Protocol changed after scene generation")
    headers = {"X-API-Key": ROLE_SECRETS[UserRole.ADMIN]}
    results = []
    with TestClient(app) as client:
        for record in manifest["accepted"]:
            raw = (DEST / "data" / f"{record['scene_id']}.json").read_bytes()
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError(f"Frozen scene hash changed: {record['scene_id']}")
            scene = json.loads(raw)
            session_id = scene["id"]
            task_id = scene["tasks"][0]["id"]
            trace = {"seed": record["seed"], "scene_id": session_id, "task_id": task_id,
                     "commands": [], "check": None, "passed": False}
            try:
                assert_success(client.post(f"/sessions/{session_id}", headers=headers, json=scene), "import")
                assert_success(client.post(f"/sessions/{session_id}/set-current", headers=headers), "set-current")
                landed = assert_success(client.post("/drones/land_all", headers=headers), "prepare-land")
                if landed.get("drones_blocked") != 0 or landed.get("total_drones") != 2:
                    raise RuntimeError(f"Runner preparation cannot land all drones: {landed}")
                charged = assert_success(client.post("/drones/charge_all", headers=headers), "prepare-charge")
                if charged.get("total_drones") != 2:
                    raise RuntimeError(f"Runner preparation cannot charge all drones: {charged}")
                routes = routes_from_task(scene)
                for drone in scene["drones"]:
                    drone_id = drone["id"]
                    takeoff = assert_success(client.post(
                        f"/drones/{drone_id}/command/take_off",
                        params={"altitude": 26.0}, headers=headers), f"takeoff:{drone_id}", command=True)
                    trace["commands"].append({"drone_id": drone_id, "command": "take_off",
                                              "status": takeoff["status"]})
                    path = assert_success(client.post(
                        f"/drones/{drone_id}/command/move_along_path",
                        json={"waypoints": routes[drone_id], "allow_partial_move": False},
                        headers=headers), f"path:{drone_id}", command=True)
                    trace["commands"].append({"drone_id": drone_id, "command": "move_along_path",
                                              "status": path["status"],
                                              "successful_points_count": path.get("successful_points_count")})
                    if path.get("successful_points_count") != 12:
                        raise RuntimeError(f"Expected 12 successful route points: {path}")
                    land = assert_success(client.post(f"/drones/{drone_id}/command/land",
                                                      headers=headers), f"land:{drone_id}", command=True)
                    trace["commands"].append({"drone_id": drone_id, "command": "land",
                                              "status": land["status"]})
                check = assert_success(client.get(
                    f"/sessions/current/tasks/{task_id}/check", headers=headers), "task-check")
                trace["check"] = {"result": check["result"], "details": check.get("details")}
                trace["passed"] = bool(check["result"])
                if not trace["passed"]:
                    raise RuntimeError("Original checker did not pass all leaves")
            except Exception as exc:
                trace["error"] = f"{type(exc).__name__}: {exc}"
                results.append(trace)
                break  # Stop at first failure; never replace the frozen scene.
            results.append(trace)
    output = {"schema": 1, "model_calls": 0, "all_passed": len(results) == 2 and all(
        item["passed"] for item in results), "results": results}
    path = DEST / "server-dry-run.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"all_passed": output["all_passed"], "scenes": [
        {"seed": item["seed"], "passed": item["passed"], "error": item.get("error")}
        for item in results]}, ensure_ascii=False))
    if not output["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
