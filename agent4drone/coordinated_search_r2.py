"""Public-receipt coordinated search policy for a bounded research pilot.

This module makes no API calls and has no access to scene files, target truth,
or task checkers. Its caller owns the deadline and may disable it entirely.
"""
from __future__ import annotations

import math
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from path_planning.path_finder import _obstacle_to_shape


GRID_STEP_M = 70.0
SCORE_CELL_STEP_M = 25.0
SEARCH_MARGIN_M = 200.0
SAFETY_BUFFER_M = 1.0
DISTANCE_SCALE_M = 250.0
MAX_ROUNDS = 16


def lattice(bounds: tuple[float, float, float, float], step: float) -> list[tuple[float, float]]:
    x0, x1, y0, y1 = bounds
    points = []
    x = x0 + step / 2
    while x <= x1:
        y = y0 + step / 2
        while y <= y1:
            points.append((round(x, 3), round(y, 3)))
            y += step
        x += step
    return points


class PublicSearchPolicy:
    def __init__(self, drones: list[dict], inherited_obstacles: list[dict]):
        if not drones:
            raise ValueError("A public fleet is required")
        ids = [str(item["id"]) for item in drones]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate drone IDs")
        self.ids = sorted(ids)
        self.xy = {str(item["id"]): (float(item["position"]["x"]),
                                       float(item["position"]["y"])) for item in drones}
        radii = {float(item["perceived_radius"]) for item in drones}
        if len(radii) != 1 or next(iter(radii)) <= 0:
            raise ValueError("This pilot requires one shared public sensing radius")
        self.radius = radii.pop()
        xs = [xy[0] for xy in self.xy.values()]
        ys = [xy[1] for xy in self.xy.values()]
        self.bounds = (max(0.0, min(xs) - SEARCH_MARGIN_M), max(xs) + SEARCH_MARGIN_M,
                       max(0.0, min(ys) - SEARCH_MARGIN_M), max(ys) + SEARCH_MARGIN_M)
        self.candidates = lattice(self.bounds, GRID_STEP_M)
        self.score_cells = lattice(self.bounds, SCORE_CELL_STEP_M)
        self.candidate_cover = [frozenset(i for i, cell in enumerate(self.score_cells)
                                          if math.dist(point, cell) <= self.radius)
                                for point in self.candidates]
        self.covered = {i for i, cell in enumerate(self.score_cells)
                        if any(math.dist(cell, xy) <= self.radius for xy in self.xy.values())}
        self.initial_covered_count = len(self.covered)
        self.known_obstacles = {str(item["id"]): item for item in inherited_obstacles}
        self.obstacle_sources: dict[str, dict] = {}
        self.selected: set[int] = set()
        self.tried_edges: set[tuple] = set()
        self.disabled: set[str] = set()
        self._refresh_blocked()

    def _refresh_blocked(self) -> None:
        shapes = [_obstacle_to_shape(item) for item in self.known_obstacles.values()]
        self.blocked = unary_union(shapes).buffer(SAFETY_BUFFER_M)

    def choose(self, actor: str, *, excluded_indices=()) -> tuple[int, tuple[float, float], float] | None:
        if actor in self.disabled:
            return None
        start = self.xy[actor]
        ranked = []
        for i, destination in enumerate(self.candidates):
            if i in excluded_indices or i in self.selected or (actor, start, i) in self.tried_edges:
                continue
            novel = len(self.candidate_cover[i] - self.covered)
            if not novel:
                continue
            travel = math.dist(start, destination)
            ranked.append((-novel / (1.0 + travel / DISTANCE_SCALE_M), travel, i))
        ranked.sort()
        for _, travel, i in ranked:
            destination = self.candidates[i]
            if self.blocked.intersects(Point(*destination)):
                continue
            if self.blocked.intersects(LineString([start, destination])):
                continue
            return i, destination, travel
        return None

    def accept(self, actor: str, index: int, outcome: str,
               actual_xy: tuple[float, float], nearby_obstacles: list[dict],
               *, round_number: int, sequence: int) -> list[str]:
        if outcome not in {"completed", "blocked"}:
            raise ValueError(f"Unsupported movement outcome: {outcome}")
        start = self.xy[actor]
        self.tried_edges.add((actor, start, index))
        if outcome == "completed":
            if math.dist(actual_xy, self.candidates[index]) > 1e-6:
                raise ValueError("Movement success did not reach the requested endpoint")
            self.xy[actor] = actual_xy
            self.selected.add(index)
            self.covered.update(self.candidate_cover[index])
        elif math.dist(actual_xy, start) > 1e-6:
            raise ValueError("Blocked movement changed the actual position")
        new_ids = []
        for item in nearby_obstacles:
            oid = str(item["id"])
            if oid in self.known_obstacles:
                continue
            self.known_obstacles[oid] = item
            self.obstacle_sources[oid] = {
                "round": round_number, "sequence": sequence, "observer": actor,
                "observed_at_xy": list(actual_xy), "receipt": "nearby_after_" + outcome,
            }
            new_ids.append(oid)
        if new_ids:
            self._refresh_blocked()
        return new_ids

    def disable(self, actor: str) -> None:
        self.disabled.add(actor)
