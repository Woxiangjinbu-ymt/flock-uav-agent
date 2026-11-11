#!/usr/bin/env python3
"""Plot the sole online worker activation from public sensing receipts.

The true target marker is added only after all public sample points are frozen;
it is for diagnostic evaluation, never planner input.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "docs/results/m1r2-observation-worker-prefix-20261009/summary.json"
GEOMETRY = ROOT / "docs/audit/observation-worker-next-step-20261009/geometry.json"
RUN = ROOT / "experiments/runs/20261009-144145-worker-prefix-worker-e35843d6"
SCENE = ROOT / "experiments/m1-revision2-v1/frozen/data/scenes/Target_Assignment_Moderate_1_35ce64d8.json"
OUTPUT = ROOT / "docs/audit/unknown-target-search-space-20261009/coverage_case_22e1e1c5.png"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    summary = read(SUMMARY)
    row = next(item for item in summary["arms"]["worker"]["tasks"]
               if item["task_id"] == "22e1e1c5")
    geometry = read(GEOMETRY)["actual_worker"]
    trace_path = next(RUN.rglob(f"*{row['command_id']}*completed.json"))
    trace = read(trace_path)
    scene = read(SCENE)
    target = next(item for item in scene["targets"] if item["name"] == "Fixed Target 9")
    end = row["worker_runs"][0]["trigger"]["trigger_sequence"]
    events = trace["task_progress"]["events"]
    listed = next(item["payload"] for item in events if item["tool_name"] == "list_drones")
    drones = {item["id"]: item for item in listed}
    current = {actor: (item["position"]["x"], item["position"]["y"])
               for actor, item in drones.items()}
    main_samples, fleet_samples = [], []
    for event in events:
        if event["end_sequence"] is None or event["end_sequence"] > end:
            continue
        payload = event["payload"]
        if isinstance(payload, dict) and isinstance(payload.get("per_drone"), dict):
            for actor in payload.get("sensed_drone_ids", []):
                fleet_samples.append((*current[actor], drones[actor]["perceived_radius"]))
        elif event["tool_name"] == "navigate_to_and_sense":
            point = payload.get("navigation_result", {}).get("final_position")
            actor = event["args"].get("drone_id")
            if actor in drones and isinstance(point, dict):
                current[actor] = (point["x"], point["y"])
                main_samples.append((point["x"], point["y"], drones[actor]["perceived_radius"]))

    fig, ax = plt.subplots(figsize=(11, 8), dpi=180)
    for x, y, radius in fleet_samples:
        ax.add_patch(Circle((x, y), radius, color="#3b82f6", alpha=.075, linewidth=0))
    for x, y, radius in main_samples:
        ax.add_patch(Circle((x, y), radius, color="#0f766e", alpha=.13, linewidth=0))
    for point in geometry["points"]:
        ax.add_patch(Circle((point["x"], point["y"]), 50,
                            color="#f59e0b", alpha=.20, linewidth=0))
    ax.add_patch(Rectangle((0, 0), scene["canvas_width"], scene["canvas_height"],
                           fill=False, edgecolor="#334155", linewidth=1.5, linestyle="--"))
    ax.scatter([x for x, _, _ in fleet_samples], [y for _, y, _ in fleet_samples],
               s=11, color="#3b82f6", label="Public fleet sensing")
    ax.scatter([x for x, _, _ in main_samples], [y for _, y, _ in main_samples],
               s=24, color="#0f766e", label="Main sensing endpoints")
    ax.scatter([p["x"] for p in geometry["points"]],
               [p["y"] for p in geometry["points"]],
               s=60, marker="s", color="#d97706", label="Worker endpoints")
    ax.scatter([target["position"]["x"]], [target["position"]["y"]],
               s=180, marker="*", color="#dc2626", edgecolors="white", linewidths=.8,
               label="Missing target (evaluation only)", zorder=10)
    ax.set(xlim=(-50, 1100), ylim=(-50, 980), xlabel="X (m)", ylabel="Y (m)",
           title="Observed sensing footprints at the worker trigger: task 22e1e1c5")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=.15)
    ax.legend(loc="upper right", framealpha=.95, fontsize=9)
    fig.text(.5, .02, "Target position was added after route selection for offline evaluation. Dashed box is the nominal canvas, not a hard world boundary.",
             ha="center", fontsize=8, color="#475569")
    fig.tight_layout(rect=(0, .045, 1, 1))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=180)
    plt.close(fig)
    print(OUTPUT)


if __name__ == "__main__":
    main()
