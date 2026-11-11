"""Optional relative-distance action built on the existing B1 navigator.

This tool verifies only the requested pairwise relation. It does not judge a
whole mission or read task checks/progress. Session metadata selects 2D/3D only.
"""
import json
import math

from langchain.tools import tool


def _number(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else " and nonnegative" if nonnegative else ""))
    return number


def _position(client, drone_id):
    state = client.get_drone_status(drone_id)
    if not isinstance(state, dict) or state.get("id", drone_id) != drone_id:
        raise ValueError(f"Status response does not identify drone {drone_id}")
    position = state.get("position")
    if not isinstance(position, dict) or any(axis not in position for axis in "xyz"):
        raise ValueError(f"Drone {drone_id} must have observed x, y and z")
    return tuple(_number(position[axis], f"position.{axis}") for axis in "xyz")


def _point(position):
    return dict(zip("xyz", position))


def _distance(first, second, mode):
    value = math.dist(first[:2], second[:2]) if mode == "2d" else math.dist(first, second)
    if not math.isfinite(value):
        raise ValueError("Distance exceeds finite numeric range")
    return value


def create_relative_movement_tool(client, navigate):
    """Bind navigate(drone_id, destination_xy, altitude, safety_buffer, tolerance)."""

    @tool
    def move_within_distance(input_json: str) -> str:
        """Move one drone until its final distance to another is at most max_distance.

        Input JSON: drone_id, reference_drone_id, max_distance (positive meters).
        max_distance is the FINAL pairwise distance, NOT a travel distance.
        distance_mode optionally selects 'session' (default, public session
        is_distance_3d; missing flag means 2D), '2d' (XY) or '3d' (XYZ).
        Optional z selects the moving drone's altitude only if movement is needed;
        omitted z preserves its current height. Never takes off automatically or
        changes the reference drone. Optional safety_buffer defaults to 1.1.
        Chooses a small interior distance margin (at most 0.1m or 1% of the bound),
        invokes B1 navigation once, then measures fresh positions of both drones.
        Already-satisfied relations do not move. An impossible height difference,
        blocked/partial navigation, or moving reference is reported without retry.
        success reports the observed distance relation; navigation_result separately
        preserves command completion, including partial movement.
        This verifies only this distance relation, not overall mission completion.
        """
        result = {"success": False, "outcome": "error", "within_distance": None,
                  "distance_before": None, "distance_after": None, "navigation_result": None,
                  "navigation_attempted": False}
        navigation_attempted = False
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            if not isinstance(params, dict):
                raise ValueError("Input must be a JSON object")
            for key in ("drone_id", "reference_drone_id"):
                if not isinstance(params.get(key), str) or not params[key].strip():
                    raise ValueError(f"{key} must be a nonempty drone ID")
            drone_id, reference_id = params["drone_id"], params["reference_drone_id"]
            if drone_id == reference_id:
                raise ValueError("drone_id and reference_drone_id must differ")
            maximum = _number(params.get("max_distance"), "max_distance", positive=True)
            safety = _number(params.get("safety_buffer", 1.1), "safety_buffer", positive=True)
            explicit_z = _number(params["z"], "z", nonnegative=True) if "z" in params else None
            requested_mode = params.get("distance_mode", "session")
            if requested_mode not in ("session", "2d", "3d"):
                raise ValueError("distance_mode must be session, 2d or 3d")
            mode = requested_mode
            if mode == "session":
                session = client.get_current_session()
                if not isinstance(session, dict):
                    raise ValueError("Session metadata is not an object")
                is_3d = session.get("is_distance_3d", False)
                if not isinstance(is_3d, bool):
                    raise ValueError("Session is_distance_3d must be boolean")
                mode = "3d" if is_3d else "2d"
            moving = _position(client, drone_id)
            reference = _position(client, reference_id)
            before = _distance(moving, reference, mode)
            altitude = moving[2] if explicit_z is None else explicit_z
            result.update(drone_id=drone_id, reference_drone_id=reference_id,
                          requested_distance_mode=requested_mode, distance_mode=mode,
                          max_distance=maximum, distance_before=before,
                          drone_position_before=_point(moving), reference_position_before=_point(reference),
                          requested_altitude=altitude)
            if before <= maximum:
                result.update(success=True, outcome="completed", within_distance=True,
                              distance_after=before, drone_position_after=_point(moving),
                              reference_position=_point(reference), reference_position_after=_point(reference),
                              reference_moved=False, message="Already within the requested distance; no movement")
                return json.dumps(result, allow_nan=False)

            vertical = abs(altitude - reference[2]) if mode == "3d" else 0.0
            if vertical > maximum:
                result.update(outcome="blocked", within_distance=False, distance_after=before,
                              drone_position_after=_point(moving), reference_position=_point(reference),
                              reference_position_after=_point(reference), reference_moved=False,
                              reason="altitude_separation_exceeds_max_distance",
                              message="At the selected altitude the 3D bound is impossible. Supply a different explicit z only if the mission permits it; no altitude change or takeoff was issued.")
                return json.dumps(result, allow_nan=False)

            public_margin = min(0.1, maximum * 0.01)
            margin = min(public_margin, (maximum - vertical) / 2) if mode == "3d" else public_margin
            inner_radius = maximum - margin
            ratio = vertical / inner_radius if inner_radius else 0.0
            horizontal_radius = inner_radius * math.sqrt(max(0.0, (1 - ratio) * (1 + ratio)))
            horizontal_before = _distance(moving, reference, "2d")
            scale = min(1.0, horizontal_radius / horizontal_before) if horizontal_before else 0.0
            destination = (reference[0] + (moving[0] - reference[0]) * scale,
                           reference[1] + (moving[1] - reference[1]) * scale)
            if not all(math.isfinite(value) for value in destination):
                raise ValueError("Destination exceeds finite numeric range")
            travel = _distance(moving, (*destination, altitude), "3d")
            # dz == bound has just one feasible XY point and no interior margin.
            # Use a small navigation tolerance there, and still check the actual
            # final relation strictly rather than accepting the navigator's claim.
            tolerance = min(margin / 4 if margin > 0 else min(public_margin / 4, 1e-6), travel / 4)
            if tolerance <= 0:
                raise ValueError("Requested correction is below representable navigation precision")
            result.update(destination={"x": destination[0], "y": destination[1], "z": altitude},
                          safety_buffer=safety, distance_margin=margin, navigation_tolerance=tolerance,
                          boundary_only=vertical == maximum)
            navigation_attempted = True
            result["navigation_attempted"] = True
            result["navigation_result"] = navigate(drone_id, destination, altitude, safety, tolerance)
            # Both reads must be new: the reference may move during navigation.
            after_position = _position(client, drone_id)
            after_reference = _position(client, reference_id)
            after = _distance(after_position, after_reference, mode)
            within = after <= maximum
            navigation = result["navigation_result"]
            outcome = navigation.get("outcome", "unknown") if isinstance(navigation, dict) else "unknown"
            # The selected endpoint is an implementation detail. A partial path
            # may already satisfy the user's distance bound; do not ask for more
            # movement solely to finish that path. Keep its receipt separately.
            result.update(success=within, within_distance=within, distance_after=after,
                          drone_position_after=_point(after_position), reference_position=_point(after_reference),
                          reference_position_after=_point(after_reference), reference_moved=after_reference != reference,
                          outcome="completed" if within else "error" if outcome == "completed" else outcome,
                          message="Observed distance relation satisfied; navigation_result separately reports path execution" if within else
                          "Observed distance is still above the requested bound; no automatic repeat")
        except Exception as exc:
            result.update(success=False, outcome="unknown" if navigation_attempted else "error",
                          message=(f"Navigation was attempted; final distance is unverified: {exc}. "
                                   "Inspect the retained receipt and fresh state before deciding another action."
                                   if navigation_attempted else str(exc)))
        return json.dumps(result, allow_nan=False)

    return move_within_distance
