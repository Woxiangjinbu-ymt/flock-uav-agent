#!/usr/bin/env python3
"""Show the route-template collision guard's behavior without a live API.

This is a code-path probe, not a reconstruction of historical generation.
Run with controller/.venv/bin/python.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "controller"))
import template_placeholders as placeholders  # noqa: E402


class NoCurrentSessionAPI:
    def api_check_point_collision(self, *args, **kwargs):
        return None


def main():
    source = "controller/template_placeholders.py"
    frozen = ROOT / "experiments/runs/20261003-143255-full-5c501118/source.zip"
    with ZipFile(frozen) as archive:
        assert archive.read(source) == (ROOT / source).read_bytes()
    original_api = placeholders.APIServer
    try:
        placeholders.APIServer = NoCurrentSessionAPI
        obstacle = {"id": "probe", "name": "Probe obstacle", "type": "circle",
                    "position": {"x": 100, "y": 100, "z": 0}, "radius": 50, "height": 0}
        returns_collision = placeholders.check_collision(100, 100, [obstacle])
    finally:
        placeholders.APIServer = original_api
    result = {
        "frozen_source_matches": True,
        "probe_point": [100, 100], "provided_obstacle": obstacle,
        "mock_api_response": None, "check_collision_result": returns_collision,
        "interpretation": "The supplied obstacle is not consulted if the live API gives no response; the guard fails open.",
        "limit": "Does not establish that the live API was unavailable when B0 scenes were generated.",
    }
    output = ROOT / "docs/audit/r3-disjoint-feasibility-20261009/generator-probe.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "check_collision_result": returns_collision}))


if __name__ == "__main__":
    main()
