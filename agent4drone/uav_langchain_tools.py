"""
LangChain Tools for UAV Control
Wraps the UAV API client as LangChain tools using @tool decorator
All tools accept JSON string input for consistent parameter handling
"""
from langchain.tools import tool
from blackboard import PerceptionBlackboard
from uav_api_client import UAVAPIClient
from command_results import command_outcome
from path_planning import generate_coverage_path as plan_coverage_paths
from path_planning import find_path
from relative_movement import create_relative_movement_tool
import json
import math
import uuid
from typing import Optional


_COVERAGE_PLAN_CACHE = {}
_DEFAULT_NAVIGATION_HORIZON = 80.0
_DEFAULT_WAYPOINT_ARRIVAL_TOLERANCE = 1.0
_PATH_RESULT_FIELDS = [
    "successful_points_count",
    "successful_points",
    "unsuccessful_points_count",
    "unsuccessful_points",
]


def _position_xy(entity: dict) -> tuple[float, float]:
    position = entity.get("position") or {}
    try:
        return float(position["x"]), float(position["y"])
    except (KeyError, TypeError, ValueError) as exc:
        entity_id = entity.get("id", "unknown")
        raise ValueError(f"Entity {entity_id} is missing numeric position.x/position.y") from exc


def _position_xyz(entity: dict) -> tuple[float, float, float]:
    """Require observed coordinates; missing altitude is not ground level."""
    try:
        point = tuple(float(entity["position"][axis]) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Drone position must contain numeric x, y and z") from exc
    if not all(math.isfinite(value) for value in point):
        raise ValueError("Drone position coordinates must be finite")
    return point


def _float_param(params: dict, key: str, default: float) -> float:
    value = float(params[key]) if params.get(key) is not None else default
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and positive")
    return value


def _bool_param(params: dict, key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


def _blackboard_obstacles_for_planner(blackboard: PerceptionBlackboard) -> list[dict]:
    return blackboard.full().get("obstacles", [])


def _waypoints_with_altitude(
    path: list[tuple[float, float]],
    altitude: float,
) -> list[dict]:
    return [{"x": float(x), "y": float(y), "z": float(altitude)} for x, y in path]


def _target_to_planner_target(target: dict):
    target_type = target.get("type")
    if target_type == "circle":
        x, y = _position_xy(target)
        try:
            radius = float(target["radius"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Circle target {target.get('id')} is missing numeric radius") from exc
        return (x, y, radius)

    if target_type == "polygon":
        vertices = target.get("vertices") or []
        if len(vertices) < 3:
            raise ValueError(f"Polygon target {target.get('id')} must contain at least 3 vertices")
        planner_vertices = []
        for vertex in vertices:
            try:
                planner_vertices.append((float(vertex["x"]), float(vertex["y"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Polygon target {target.get('id')} has an invalid vertex") from exc
        return planner_vertices

    raise ValueError(
        f"Target {target.get('id')} has unsupported type {target_type!r}; expected 'circle' or 'polygon'"
    )


def _waypoints_from_path(path: list[tuple[float, float]]) -> list[dict]:
    return [{"x": float(x), "y": float(y)} for x, y in path]


def _waypoint_preview(waypoints: list[dict]) -> dict:
    return {
        "first_waypoint": waypoints[0] if waypoints else None,
        "last_waypoint": waypoints[-1] if waypoints else None,
        "waypoint_count": len(waypoints),
    }


def _coverage_execution_call(
    plan_id: str,
    drone_id: str,
    tool_name: str = "move_along_path",
    allow_partial_move: bool = True,
    detail: str = "summary",
) -> dict:
    """Return the actual LangChain argument shape, including the JSON string."""
    return {
        "tool": tool_name,
        "arguments": {
            "input_json": json.dumps({
                "drone_id": drone_id,
                "coverage_plan_id": plan_id,
                "allow_partial_move": allow_partial_move,
                "detail": detail,
            })
        },
    }


def _coverage_plan_error(
    message: str,
    requested_plan_id: str,
    drone_id: str,
    generated_plan_ids: list[str],
    tool_name: str,
    allow_partial_move: bool,
    detail: str,
) -> str:
    # A process-wide cache remains for compatibility. Recovery suggestions only
    # enumerate plans generated by this tool instance, never other commands.
    candidates = []
    for plan_id in generated_plan_ids:
        plan = _COVERAGE_PLAN_CACHE.get(plan_id)
        if not plan or not plan.get("paths", {}).get(drone_id):
            continue
        candidates.append({
            "coverage_plan_id": plan_id,
            "target": {
                "id": plan.get("target_id"),
                "name": plan.get("target_name"),
                "type": plan.get("target_type"),
            },
            "drone_id": drone_id,
            "execution_call": _coverage_execution_call(
                plan_id, drone_id, tool_name, allow_partial_move, detail,
            ),
        })
    return _json({
        "error": message,
        "requested_coverage_plan_id": requested_plan_id,
        "requested_drone_id": drone_id,
        "candidate_plans": candidates,
        "recovery": (
            "A target_id is not a coverage_plan_id. No movement was executed. "
            "Select a candidate only if its target matches the requested mission; "
            "use its execution_call.tool and execution_call.arguments as supplied. "
            "Candidates are limited to this tool instance and the requested drone. "
            "If none match, generate a new plan for the intended target and drone."
        ),
    })


def _normalize_waypoint(waypoint: dict) -> dict:
    source = waypoint.get("coordinates") if isinstance(waypoint.get("coordinates"), dict) else waypoint
    normalized = {
        "x": float(source["x"]),
        "y": float(source["y"]),
    }
    if "z" in source:
        normalized["z"] = float(source["z"])
    return normalized


def _normalize_waypoints(waypoints: list[dict]) -> list[dict]:
    return [_normalize_waypoint(waypoint) for waypoint in waypoints]


def _json(data) -> str:
    return json.dumps(data, indent=2)


def _detail_level(params: Optional[dict], default: str = "summary") -> str:
    if not isinstance(params, dict):
        return default
    detail = params.get("detail", default)
    if isinstance(detail, str):
        detail = detail.strip().lower()
    return "full" if detail == "full" else "summary"


def _compact_position(position) -> Optional[dict]:
    if not isinstance(position, dict):
        return None
    compact = {}
    for key in ("x", "y", "z"):
        if key in position:
            compact[key] = position[key]
    return compact or None


def _compact_drone(drone: dict) -> dict:
    compact = {
        "id": drone.get("id"),
        "name": drone.get("name"),
        "status": drone.get("status"),
        "position": _compact_position(drone.get("position")),
        "battery_level": drone.get("battery_level"),
        "task_radius": drone.get("task_radius"),
        "perceived_radius": drone.get("perceived_radius"),
    }
    return {key: value for key, value in compact.items() if value is not None}


def _compact_entity(entity: dict) -> dict:
    compact = {
        "id": entity.get("id"),
        "name": entity.get("name"),
        "type": entity.get("type"),
        "position": _compact_position(entity.get("position")),
        "radius": entity.get("radius"),
    }
    vertices = entity.get("vertices")
    if isinstance(vertices, list):
        compact["vertex_count"] = len(vertices)
    return {key: value for key, value in compact.items() if value is not None}


def _compact_nearby(nearby: dict) -> dict:
    targets = nearby.get("targets") or []
    obstacles = nearby.get("obstacles") or []
    drones = nearby.get("drones") or []
    return {
        "drone_id": nearby.get("drone_id"),
        "counts": {
            "targets": len(targets),
            "obstacles": len(obstacles),
            "drones": len(drones),
        },
        "targets": [_compact_entity(target) for target in targets],
        "obstacles": [_compact_entity(obstacle) for obstacle in obstacles],
        "drones": [_compact_drone(drone) for drone in drones],
    }


def _compact_changes(changes: dict) -> dict:
    return {
        key: values
        for key, values in changes.items()
        if values
    }


def _compact_move_result(result) -> dict:
    if not isinstance(result, dict):
        return {"result": result}
    keep_keys = [
        "outcome",
        "success",
        "partial_success",
        "status",
        "message",
        "error",
        "reason",
        "reason_code",
        "error_code",
        "blocked_reason",
        "command_id",
        "drone_id",
        "command",
        "position",
        "final_position",
        "completed_waypoints",
        "waypoint_count",
        "allow_partial_move",
    ]
    if result.get("command") == "move_along_path" or any(result.get(key) is not None for key in _PATH_RESULT_FIELDS):
        keep_keys.extend(_PATH_RESULT_FIELDS)
    compact = {key: result.get(key) for key in keep_keys if key in result}
    if "position" in compact:
        compact["position"] = _compact_position(compact["position"])
    if "final_position" in compact:
        compact["final_position"] = _compact_position(compact["final_position"])
    return {key: value for key, value in compact.items() if value is not None}


def _move_and_sense_response(
    client: UAVAPIClient,
    blackboard: PerceptionBlackboard,
    drone_id: str,
    move_result,
    detail: str,
) -> str:
    nearby = client.get_nearby_entities(drone_id)
    observation_step = blackboard.next_step()
    changes = blackboard.ingest_nearby(drone_id, nearby, observation_step)
    if detail == "full":
        response = {
            "drone_id": drone_id,
            "move_result": move_result,
            "nearby": nearby,
            "changes": changes,
            "blackboard": blackboard.full(),
        }
    else:
        response = {
            "drone_id": drone_id,
            "move_result": _compact_move_result(move_result),
            "nearby": _compact_nearby(nearby),
            "changes": _compact_changes(changes),
            "blackboard": blackboard.summary(),
        }
    return _json(response)


def _compact_navigation_response(response: dict) -> dict:
    compact = {
        key: response.get(key)
        for key in (
            "outcome",
            "success",
            "arrival_verified",
            "message",
            "drone_id",
            "final_position",
            "distance_remaining",
            "movement_strategy",
            "planned_waypoint_count",
            "steps_taken",
            "replans",
            "sensed_obstacle_ids",
        )
        if key in response
    }
    if "final_position" in compact:
        compact["final_position"] = _compact_position(compact["final_position"])
    if "last_move_result" in response:
        compact["last_move_result"] = _compact_move_result(response["last_move_result"])
    if "direct_move_result" in response:
        compact["direct_move_result"] = _compact_move_result(response["direct_move_result"])
    if response.get("steps"):
        compact["last_step"] = response["steps"][-1]
    return {key: value for key, value in compact.items() if value is not None}


def _navigate_and_sense_response(
    client: UAVAPIClient,
    blackboard: PerceptionBlackboard,
    drone_id: str,
    navigation_result: dict,
    detail: str,
) -> str:
    nearby = client.get_nearby_entities(drone_id)
    observation_step = blackboard.next_step()
    changes = blackboard.ingest_nearby(drone_id, nearby, observation_step)
    if detail == "full":
        response = {
            "drone_id": drone_id,
            "navigation_result": navigation_result,
            "nearby": nearby,
            "changes": changes,
            "blackboard": blackboard.full(),
        }
    else:
        response = {
            "drone_id": drone_id,
            "navigation_result": _compact_navigation_response(navigation_result),
            "nearby": _compact_nearby(nearby),
            "changes": _compact_changes(changes),
            "blackboard": blackboard.summary(),
        }
    return _json(response)


def _navigate_to_destination(
    client: UAVAPIClient,
    blackboard: PerceptionBlackboard,
    drone_id: str,
    destination: tuple[float, float],
    altitude: float,
    safety_buffer: float,
    tolerance: float,
) -> dict:
    requested = (float(destination[0]), float(destination[1]), float(altitude))
    if not all(math.isfinite(value) for value in requested):
        raise ValueError("Destination coordinates must be finite")
    for name, value in (("arrival_tolerance", tolerance), ("safety_buffer", safety_buffer)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")

    def navigation_result(status: dict, outcome: str, strategy: str, message: str, **extra) -> dict:
        distance = math.dist(_position_xyz(status), requested)
        arrived = distance <= tolerance
        success = outcome == "completed" and arrived
        # A completed command alone is insufficient evidence of arrival.
        if outcome == "completed" and not arrived:
            outcome = "error"
        return {
            "success": success,
            "outcome": outcome,
            "arrival_verified": arrived,
            "message": "Destination reached" if success else message,
            "drone_id": drone_id,
            "final_position": status.get("position"),
            "distance_remaining": distance,
            "movement_strategy": strategy,
            **extra,
        }

    status = client.get_drone_status(drone_id)
    if math.dist(_position_xyz(status), requested) <= tolerance:
        return navigation_result(status, "completed", "already_at_destination", "Destination reached")

    direct_result = client.move_to(drone_id, *requested)
    status = client.get_drone_status(drone_id)
    direct_outcome = command_outcome(direct_result)
    # Only a spatial blockage can be addressed by this one-shot route planner.
    # Partial execution, transport ambiguity, battery and input errors need an
    # informed next decision, not a replay of the requested movement.
    if direct_outcome != "blocked":
        return navigation_result(
            status,
            direct_outcome,
            "move_to",
            "Direct movement did not verify arrival at the requested coordinate",
            last_move_result=direct_result,
        )

    observation_step = blackboard.next_step()
    nearby = client.get_nearby_entities(drone_id)
    changes = blackboard.ingest_nearby(drone_id, nearby, observation_step)
    sensed_obstacle_ids = sorted(
        set(changes.get("new_obstacles", [])) | set(changes.get("updated_obstacles", []))
    )

    status = client.get_drone_status(drone_id)
    current = _position_xyz(status)[:2]
    try:
        route = find_path(
            current,
            requested[:2],
            known_obstacles=_blackboard_obstacles_for_planner(blackboard),
            safety_buffer=safety_buffer,
            max_segment_length=_DEFAULT_NAVIGATION_HORIZON,
        )
    except Exception as exc:
        return navigation_result(
            status,
            "blocked",
            "planned_fallback",
            f"Path planning failed after direct movement was blocked: {exc}",
            sensed_obstacle_ids=sensed_obstacle_ids,
            last_move_result=direct_result,
        )

    planned_waypoints = _waypoints_with_altitude(route[1:], requested[2])
    if not planned_waypoints:
        return navigation_result(
            status,
            "blocked",
            "planned_fallback",
            "Planner did not produce a fallback waypoint",
            sensed_obstacle_ids=sensed_obstacle_ids,
            last_move_result=direct_result,
        )

    fallback_result = client.move_along_path(
        drone_id,
        planned_waypoints,
        allow_partial_move=False,
    )
    status = client.get_drone_status(drone_id)
    return navigation_result(
        status,
        command_outcome(fallback_result),
        "planned_fallback",
        "Fallback movement did not verify arrival at the requested coordinate",
        sensed_obstacle_ids=sensed_obstacle_ids,
        planned_waypoint_count=len(planned_waypoints),
        direct_move_result=direct_result,
        last_move_result=fallback_result,
    )


def create_uav_tools(client: UAVAPIClient, blackboard: Optional[PerceptionBlackboard] = None) -> list:
    """
    Create all UAV control tools for LangChain agent using @tool decorator
    All tools that require parameters accept a JSON string input
    """

    if blackboard is None:
        blackboard = PerceptionBlackboard()

    move_within_distance = create_relative_movement_tool(
        client,
        lambda drone_id, destination, altitude, safety_buffer, tolerance: _navigate_to_destination(
            client, blackboard, drone_id, destination, altitude, safety_buffer, tolerance
        ),
    )

    # UAVControlAgent recreates tools for each command, so error recovery cannot
    # disclose candidate plans from previous commands or another tool instance.
    generated_coverage_plan_ids = []

    # ========== Information Gathering Tools (No Parameters) ==========

    @tool
    def list_drones() -> str:
        """List all available drones in the current session with their status, battery level, and position.
        Use this to see what drones are available before trying to control them.

        No input required."""
        try:
            drones = client.list_drones()
            return _json([_compact_drone(drone) for drone in drones])
        except Exception as e:
            return f"Error listing drones: {str(e)}"

    @tool
    def get_session_info() -> str:
        """Get current session information including task type, statistics, and status.
        Use this to understand what mission you need to complete.

        No input required."""
        try:
            session = client.get_current_session()
            return json.dumps(session, indent=2)
        except Exception as e:
            return f"Error getting session info: {str(e)}"

    @tool
    def get_weather() -> str:
        """Get current weather conditions including wind speed, visibility, and weather type.
        Check this before takeoff to ensure safe flying conditions.

        No input required."""
        try:
            weather = client.get_weather()
            return json.dumps(weather, indent=2)
        except Exception as e:
            return f"Error getting weather: {str(e)}"

    @tool
    def get_drone_status(input_json: str) -> str:
        """Get detailed status of a specific drone including position, battery, heading, and visited targets.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            status = client.get_drone_status(drone_id)
            return json.dumps(status, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error getting drone status: {str(e)}"

    @tool
    def get_target_info(input_json: str) -> str:
        """Get detailed information for one specific target after discovering its ID from get_nearby_entities.

        Input should be a JSON string with:
        - target_id: The ID of the target (required)

        Example: {{"target_id": "target-area-1"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            target_id = params.get("target_id")

            if not target_id:
                return "Error: target_id is required"

            target = client.get_target(target_id)
            return json.dumps(target, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"target_id\": \"target-area-1\"}}"
        except Exception as e:
            return f"Error getting target info: {str(e)}"

    @tool
    def get_obstacle_info(input_json: str) -> str:
        """Get detailed information for one specific obstacle after discovering its ID from get_nearby_entities.

        Input should be a JSON string with:
        - obstacle_id: The ID of the obstacle (required)

        Example: {{"obstacle_id": "obstacle-1"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            obstacle_id = params.get("obstacle_id")

            if not obstacle_id:
                return "Error: obstacle_id is required"

            obstacle = client.get_obstacle(obstacle_id)
            return json.dumps(obstacle, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"obstacle_id\": \"obstacle-1\"}}"
        except Exception as e:
            return f"Error getting obstacle info: {str(e)}"

    @tool
    def get_nearby_entities(input_json: str) -> str:
        """Get drones, targets, and obstacles near a specific drone (within its perception radius).

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            nearby = client.get_nearby_entities(drone_id)
            detail = _detail_level(params)
            return _json(nearby if detail == "full" else _compact_nearby(nearby))
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error getting nearby entities: {str(e)}"

    @tool
    def sense_nearby_entities(input_json: str) -> str:
        """Query local nearby entities for selected drones, update the shared perception blackboard, and return a compact environment summary.

        This tool respects local drone sensing by only calling each drone's nearby endpoint.

        Input should be a JSON string with:
        - drone_ids: Optional list of drone IDs. If omitted, all listed drones are sensed.
        - include_blackboard: Whether to include blackboard summary in the response (optional, default true)
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_ids": ["drone-001", "drone-002"], "detail": "summary"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            if params is None:
                params = {}
            drone_ids = params.get("drone_ids")
            include_blackboard = bool(params.get("include_blackboard", True))
            detail = _detail_level(params)

            if drone_ids is None:
                drone_ids = [drone.get("id") for drone in client.list_drones() if drone.get("id")]
            if not isinstance(drone_ids, list) or not drone_ids:
                return "Error: drone_ids must be a non-empty list when provided"
            if detail not in {"summary", "full"}:
                return "Error: detail must be either 'summary' or 'full'"

            per_drone = {}
            combined_changes = {
                "new_targets": [],
                "updated_targets": [],
                "new_obstacles": [],
                "updated_obstacles": [],
                "new_drones": [],
                "updated_drones": [],
            }
            observation_step = blackboard.next_step()
            for drone_id in drone_ids:
                if not drone_id:
                    continue
                nearby = client.get_nearby_entities(str(drone_id))
                per_drone[str(drone_id)] = nearby if detail == "full" else _compact_nearby(nearby)
                changes = blackboard.ingest_nearby(str(drone_id), nearby, observation_step)
                for key, values in changes.items():
                    combined_changes[key].extend(values)

            response = {
                "sensed_drone_ids": [str(drone_id) for drone_id in drone_ids if drone_id],
                "per_drone": per_drone,
                "changes": combined_changes if detail == "full" else _compact_changes(combined_changes),
            }
            if include_blackboard:
                response["blackboard"] = blackboard.full() if detail == "full" else blackboard.summary()
            return _json(response)
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"drone_ids\": [\"drone-001\"], \"detail\": \"summary\"}"
            )
        except Exception as e:
            return f"Error sensing nearby entities: {str(e)}"

    @tool
    def update_blackboard_notes(input_json: str) -> str:
        """Add or update an LLM-authored mission note for a known blackboard target or obstacle.

        Deterministic sensing owns factual fields; this tool only updates note and optional priority.

        Input should be a JSON string with:
        - entity_id: ID already present in the blackboard (required)
        - entity_kind: "target" or "obstacle" (required)
        - note: Concise mission-relevant note (required)
        - priority: Optional one of "low", "medium", "high"

        Example: {{"entity_id": "obstacle-1", "entity_kind": "obstacle", "note": "Avoid when routing east", "priority": "high"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            entity_id = params.get("entity_id")
            entity_kind = params.get("entity_kind")
            note = params.get("note")
            priority = params.get("priority")

            if not entity_id:
                return "Error: entity_id is required"
            if not entity_kind:
                return "Error: entity_kind is required"
            if not note:
                return "Error: note is required"
            if str(entity_kind).strip().lower() not in {"target", "targets", "obstacle", "obstacles"}:
                return "Error: entity_kind must be 'target' or 'obstacle'"

            entry = blackboard.update_note(entity_kind, str(entity_id), str(note), priority)
            return json.dumps(
                {
                    "updated": True,
                    "entity": blackboard.compact_entry(entry),
                },
                indent=2,
            )
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"entity_id\": \"target-1\", \"entity_kind\": \"target\", \"note\": \"...\"}"
            )
        except (KeyError, ValueError) as e:
            return f"Error updating blackboard note: {str(e)}"
        except Exception as e:
            return f"Error updating blackboard note: {str(e)}"

    @tool
    def generate_coverage_path(input_json: str) -> str:
        """Generate a multi-drone area coverage path plan for a circle or polygon target.
        The tool stores full 2D waypoint paths in a temporary plan cache and returns a compact coverage_plan_id.
        Use move_along_path with coverage_plan_id and each drone_id to execute the generated paths.
        Each drone assignment includes an execution_call with the tool name and
        exact arguments (input_json string) to copy into the next tool call.

        Input should be a JSON string with:
        - target_id: Circle or polygon target ID to cover (required)
        - drone_ids: List of drone IDs assigned to the area (required)

        Example: {{"target_id": "target-area-1", "drone_ids": ["drone-001", "drone-002"]}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            target_id = params.get("target_id")
            drone_ids = params.get("drone_ids")

            if not target_id:
                return "Error: target_id is required"
            if not isinstance(drone_ids, list) or not drone_ids:
                return "Error: drone_ids must be a non-empty list"

            target = client.get_target(target_id)
            planner_target = _target_to_planner_target(target)
            drones_by_id = {drone.get("id"): drone for drone in client.list_drones()}
            selected_drones = []
            for drone_id in drone_ids:
                drone = drones_by_id.get(drone_id)
                if drone is None:
                    drone = client.get_drone_status(drone_id)
                selected_drones.append(drone)

            starts = [_position_xy(drone) for drone in selected_drones]
            task_radii = [float(drone.get("task_radius", 0)) for drone in selected_drones]
            positive_task_radii = [radius for radius in task_radii if radius > 0]
            if not positive_task_radii:
                return "Error: selected drones must have positive task_radius values"
            task_radius = min(positive_task_radii)
            point_spacing = task_radius

            paths = plan_coverage_paths(
                starts=starts,
                target=planner_target,
                task_radius=task_radius,
                point_spacing=point_spacing,
            )
            plan_id = f"coverage-{uuid.uuid4().hex}"
            cached_paths = {
                drone_id: _waypoints_from_path(path)
                for drone_id, path in zip(drone_ids, paths)
            }
            _COVERAGE_PLAN_CACHE[plan_id] = {
                "target_id": target_id,
                "target_name": target.get("name"),
                "target_type": target.get("type"),
                "task_radius": task_radius,
                "paths": cached_paths,
            }
            generated_coverage_plan_ids.append(plan_id)

            response = {
                "coverage_plan_id": plan_id,
                "target": {
                    "id": target_id,
                    "name": target.get("name"),
                    "type": target.get("type"),
                },
                "task_radius": task_radius,
                "point_spacing": point_spacing,
                "drone_assignments": [
                    {
                        "drone_id": drone_id,
                        **_waypoint_preview(cached_paths[drone_id]),
                        "execution_call": _coverage_execution_call(plan_id, drone_id),
                    }
                    for drone_id in drone_ids
                ],
            }
            return json.dumps(response, indent=2)
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"target_id\": \"target-area-1\", \"drone_ids\": [\"drone-001\"]}"
            )
        except Exception as e:
            return f"Error generating coverage path: {str(e)}"

    @tool
    def land(input_json: str) -> str:
        """Command a drone to land at its current position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.land(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error during landing: {str(e)}"

    @tool
    def hover(input_json: str) -> str:
        """Command a drone to hover at its current position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - duration: Optional duration in seconds to hover (optional)

        Example: {{"drone_id": "drone-001", "duration": 5.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            duration = params.get('duration')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.hover(drone_id, duration)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error hovering: {str(e)}"

    @tool
    def return_home(input_json: str) -> str:
        """Command a drone to return to its home position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.return_home(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error returning home: {str(e)}"

    @tool
    def set_home(input_json: str) -> str:
        """Set the drone's current position as its new home position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.set_home(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error setting home: {str(e)}"

    @tool
    def calibrate(input_json: str) -> str:
        """Calibrate the drone's sensors.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.calibrate(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error calibrating: {str(e)}"

    @tool
    def take_photo(input_json: str) -> str:
        """Command a drone to take a photo.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.take_photo(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error taking photo: {str(e)}"

    # ========== Two Parameter Tools ==========

    @tool
    def take_off(input_json: str) -> str:
        """Command a drone to take off to a specified altitude.
        Drone must be on ground (idle or ready status).

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - altitude: Target altitude in meters (optional, default: 10.0)

        Example: {{"drone_id": "drone-001", "altitude": 15.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            altitude = params.get('altitude', 10.0)

            if not drone_id:
                return "Error: drone_id is required"

            result = client.take_off(drone_id, altitude)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"altitude\": 15.0}}"
        except Exception as e:
            return f"Error during takeoff: {str(e)}"

    @tool
    def change_altitude(input_json: str) -> str:
        """Change a drone's altitude while maintaining X/Y position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - altitude: Target altitude in meters (required)

        Example: {{"drone_id": "drone-001", "altitude": 20.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            altitude = params.get('altitude')

            if not drone_id:
                return "Error: drone_id is required"
            if altitude is None:
                return "Error: altitude is required"

            result = client.change_altitude(drone_id, altitude)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"altitude\": 20.0}}"
        except Exception as e:
            return f"Error changing altitude: {str(e)}"

    @tool
    def rotate(input_json: str) -> str:
        """Rotate a drone to face a specific direction.
        0=North, 90=East, 180=South, 270=West.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - heading: Target heading in degrees 0-360 (required)

        Example: {{"drone_id": "drone-001", "heading": 90.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            heading = params.get('heading')

            if not drone_id:
                return "Error: drone_id is required"
            if heading is None:
                return "Error: heading is required"

            result = client.rotate(drone_id, heading)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"heading\": 90.0}}"
        except Exception as e:
            return f"Error rotating: {str(e)}"

    @tool
    def send_message(input_json: str) -> str:
        """Send a message from one drone to another.

        Input should be a JSON string with:
        - drone_id: The ID of the sender drone (required)
        - target_drone_id: The ID of the recipient drone (required)
        - message: The message content (required)

        Example: {{"drone_id": "drone-001", "target_drone_id": "drone-002", "message": "Hello"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            target_drone_id = params.get('target_drone_id')
            message = params.get('message')

            if not drone_id:
                return "Error: drone_id is required"
            if not target_drone_id:
                return "Error: target_drone_id is required"
            if not message:
                return "Error: message is required"

            result = client.send_message(drone_id, target_drone_id, message)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"target_drone_id\": \"drone-002\", \"message\": \"...\"}}"
        except Exception as e:
            return f"Error sending message: {str(e)}"

    @tool
    def broadcast(input_json: str) -> str:
        """Broadcast a message from one drone to all other drones.

        Input should be a JSON string with:
        - drone_id: The ID of the sender drone (required)
        - message: The message content (required)

        Example: {{"drone_id": "drone-001", "message": "Alert"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            message = params.get('message')

            if not drone_id:
                return "Error: drone_id is required"
            if not message:
                return "Error: message is required"

            result = client.broadcast(drone_id, message)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"message\": \"...\"}}"
        except Exception as e:
            return f"Error broadcasting: {str(e)}"

    @tool
    def charge(input_json: str) -> str:
        """Command a drone to charge its battery.
        Drone must be landed at a charging station.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - charge_amount: Amount to charge in percent (required)

        Example: {{"drone_id": "drone-001", "charge_amount": 25.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            charge_amount = params.get('charge_amount')

            if not drone_id:
                return "Error: drone_id is required"
            if charge_amount is None:
                return "Error: charge_amount is required"

            result = client.charge(drone_id, charge_amount)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"charge_amount\": 25.0}}"
        except Exception as e:
            return f"Error charging: {str(e)}"

    @tool
    def move_towards(input_json: str) -> str:
        """Move a drone a specific distance in a direction.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - distance: Distance to move in meters (required)
        - heading: Heading direction in degrees 0-360 (optional, default: current heading)
        - dz: Vertical component in meters (optional)

        Example: {{"drone_id": "drone-001", "distance": 10.0, "heading": 90.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            distance = params.get('distance')
            heading = params.get('heading')
            dz = params.get('dz')

            if not drone_id:
                return "Error: drone_id is required"
            if distance is None:
                return "Error: distance is required"

            result = client.move_towards(drone_id, distance, heading, dz)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"distance\": 10.0}}"
        except Exception as e:
            return f"Error moving towards: {str(e)}"

    @tool
    def move_towards_and_sense(input_json: str) -> str:
        """Move a drone a specific distance in a direction, then sense nearby local entities and update the perception blackboard.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - distance: Distance to move in meters (required)
        - heading: Heading direction in degrees 0-360 (optional, default: current heading)
        - dz: Vertical component in meters (optional)
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_id": "drone-001", "distance": 10.0, "heading": 90.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            distance = params.get('distance')
            heading = params.get('heading')
            dz = params.get('dz')
            detail = _detail_level(params)

            if not drone_id:
                return "Error: drone_id is required"
            if distance is None:
                return "Error: distance is required"

            result = client.move_towards(drone_id, distance, heading, dz)
            return _move_and_sense_response(client, blackboard, drone_id, result, detail)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"distance\": 10.0}}"
        except Exception as e:
            return f"Error moving towards and sensing: {str(e)}"

    @tool
    def navigate_to(input_json: str) -> str:
        """Navigate one drone to an exact coordinate.

        This first tries a direct move_to. If an obstacle blocks it, it senses once, plans around known
        obstacles, and tries one move_along_path fallback with allow_partial_move=false. Other errors and
        partial results are returned without replay. Success requires a completed command and verified
        XYZ arrival; distance_remaining is the 3D distance. It does not repeatedly replan.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - x: Destination X coordinate in meters (required)
        - y: Destination Y coordinate in meters (required)
        - z: Optional destination altitude. If omitted, current altitude is preserved.
        - arrival_tolerance: Optional positive 3D arrival tolerance in meters (default 1.0)
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_id": "drone-001", "x": 100.0, "y": 50.0, "z": 10.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            if params is None:
                params = {}
            drone_id = params.get("drone_id")
            x = params.get("x")
            y = params.get("y")
            detail = _detail_level(params)

            if not drone_id:
                return "Error: drone_id is required"
            if x is None or y is None:
                return "Error: x and y coordinates are required"

            destination = (float(x), float(y))
            status = client.get_drone_status(drone_id)
            altitude = float(params.get("z")) if params.get("z") is not None else _position_xyz(status)[2]
            safety_buffer = _float_param(params, "safety_buffer", 1.1)
            tolerance = _float_param(params, "arrival_tolerance", _DEFAULT_WAYPOINT_ARRIVAL_TOLERANCE)

            def navigation_response(response: dict) -> str:
                return _json(response if detail == "full" else _compact_navigation_response(response))

            return navigation_response(
                _navigate_to_destination(
                    client,
                    blackboard,
                    drone_id,
                    destination,
                    altitude,
                    safety_buffer,
                    tolerance,
                )
            )
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"drone_id\": \"drone-001\", \"x\": 100, \"y\": 50}"
            )
        except Exception as e:
            return f"Error navigating to destination: {str(e)}"

    @tool
    def navigate_to_and_sense(input_json: str) -> str:
        """Navigate one drone to an exact coordinate, then sense nearby local entities and update the perception blackboard.

        This uses the same navigation behavior as navigate_to: it first tries direct move_to, and if direct
        movement is blocked by an obstacle, it senses once, plans around known obstacles, and tries one
        move_along_path fallback with allow_partial_move=false. Other errors and partial results are
        returned without replay. Arrival and distance_remaining use XYZ coordinates. After navigation
        returns, it senses nearby entities at the resulting location.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - x: Destination X coordinate in meters (required)
        - y: Destination Y coordinate in meters (required)
        - z: Optional destination altitude. If omitted, current altitude is preserved.
        - arrival_tolerance: Optional positive 3D arrival tolerance in meters (default 1.0)
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_id": "drone-001", "x": 100.0, "y": 50.0, "z": 10.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            if params is None:
                params = {}
            drone_id = params.get("drone_id")
            x = params.get("x")
            y = params.get("y")
            detail = _detail_level(params)

            if not drone_id:
                return "Error: drone_id is required"
            if x is None or y is None:
                return "Error: x and y coordinates are required"

            destination = (float(x), float(y))
            status = client.get_drone_status(drone_id)
            altitude = float(params.get("z")) if params.get("z") is not None else _position_xyz(status)[2]
            safety_buffer = _float_param(params, "safety_buffer", 1.1)
            tolerance = _float_param(params, "arrival_tolerance", _DEFAULT_WAYPOINT_ARRIVAL_TOLERANCE)

            result = _navigate_to_destination(
                client,
                blackboard,
                drone_id,
                destination,
                altitude,
                safety_buffer,
                tolerance,
            )
            return _navigate_and_sense_response(client, blackboard, drone_id, result, detail)
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"drone_id\": \"drone-001\", \"x\": 100, \"y\": 50}"
            )
        except Exception as e:
            return f"Error navigating to destination and sensing: {str(e)}"

    @tool
    def move_along_path(input_json: str) -> str:
        """Move a drone along a path of 2D or 3D waypoints, or execute a cached coverage path.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - waypoints: List of points with x/y and optional z coordinates (required unless coverage_plan_id is provided)
        - coverage_plan_id: Cached plan ID returned by generate_coverage_path (optional)
        - allow_partial_move: Whether the server may stop at the last reachable waypoint (optional, default true).
          If partial movement occurs, the response may report partial_success instead of success.
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_id": "drone-001", "coverage_plan_id": "coverage-abc"}}
        Example: {{"drone_id": "drone-001", "waypoints": [{{"x": 10, "y": 10}}, {{"x": 20, "y": 20}}]}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            coverage_plan_id = params.get('coverage_plan_id')
            waypoints = params.get('waypoints')
            allow_partial_move = _bool_param(params, 'allow_partial_move', True)
            detail = _detail_level(params)

            if not drone_id:
                return "Error: drone_id is required"

            if coverage_plan_id:
                plan = _COVERAGE_PLAN_CACHE.get(coverage_plan_id)
                if not plan:
                    return _coverage_plan_error(
                        f"coverage_plan_id {coverage_plan_id!r} was not found",
                        coverage_plan_id, drone_id, generated_coverage_plan_ids,
                        "move_along_path", allow_partial_move, detail,
                    )
                waypoints = plan.get("paths", {}).get(drone_id)
                if not waypoints:
                    return _coverage_plan_error(
                        f"coverage_plan_id {coverage_plan_id!r} has no cached path "
                        f"for drone_id {drone_id!r}",
                        coverage_plan_id, drone_id, generated_coverage_plan_ids,
                        "move_along_path", allow_partial_move, detail,
                    )

            if not waypoints:
                return "Error: waypoints list is required unless coverage_plan_id is provided"

            normalized_waypoints = _normalize_waypoints(waypoints)
            result = client.move_along_path(
                drone_id,
                normalized_waypoints,
                allow_partial_move=allow_partial_move,
            )
            return _json(result if detail == "full" else _compact_move_result(result))
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"drone_id\": \"drone-001\", \"waypoints\": [{\"x\": 10, \"y\": 10}]}"
            )
        except Exception as e:
            return f"Error moving along path: {str(e)}"

    @tool
    def move_along_path_and_sense(input_json: str) -> str:
        """Move a drone along a path or cached coverage path, then sense nearby local entities and update the perception blackboard.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - waypoints: List of points with x/y and optional z coordinates (required unless coverage_plan_id is provided)
        - coverage_plan_id: Cached plan ID returned by generate_coverage_path (optional)
        - allow_partial_move: Whether the server may stop at the last reachable waypoint (optional, default true).
          If partial movement occurs, the response may report partial_success instead of success.
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_id": "drone-001", "coverage_plan_id": "coverage-abc"}}
        Example: {{"drone_id": "drone-001", "waypoints": [{{"x": 10, "y": 10}}, {{"x": 20, "y": 20}}]}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            coverage_plan_id = params.get('coverage_plan_id')
            waypoints = params.get('waypoints')
            allow_partial_move = _bool_param(params, 'allow_partial_move', True)
            detail = _detail_level(params)

            if not drone_id:
                return "Error: drone_id is required"

            if coverage_plan_id:
                plan = _COVERAGE_PLAN_CACHE.get(coverage_plan_id)
                if not plan:
                    return _coverage_plan_error(
                        f"coverage_plan_id {coverage_plan_id!r} was not found",
                        coverage_plan_id, drone_id, generated_coverage_plan_ids,
                        "move_along_path_and_sense", allow_partial_move, detail,
                    )
                waypoints = plan.get("paths", {}).get(drone_id)
                if not waypoints:
                    return _coverage_plan_error(
                        f"coverage_plan_id {coverage_plan_id!r} has no cached path "
                        f"for drone_id {drone_id!r}",
                        coverage_plan_id, drone_id, generated_coverage_plan_ids,
                        "move_along_path_and_sense", allow_partial_move, detail,
                    )

            if not waypoints:
                return "Error: waypoints list is required unless coverage_plan_id is provided"

            normalized_waypoints = _normalize_waypoints(waypoints)
            result = client.move_along_path(
                drone_id,
                normalized_waypoints,
                allow_partial_move=allow_partial_move,
            )
            return _move_and_sense_response(client, blackboard, drone_id, result, detail)
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"drone_id\": \"drone-001\", \"waypoints\": [{\"x\": 10, \"y\": 10}]}"
            )
        except Exception as e:
            return f"Error moving along path and sensing: {str(e)}"

    # ========== Multi-Parameter Tools ==========

    @tool
    def move_to(input_json: str) -> str:
        """Move a drone to specific 3D coordinates (x, y, z).
        Always check for collisions first using check_path_collision.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - x: Target X coordinate in meters (required)
        - y: Target Y coordinate in meters (required)
        - z: Target Z coordinate (altitude) in meters (required)

        Example: {{"drone_id": "drone-001", "x": 100.0, "y": 50.0, "z": 20.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            x = params.get('x')
            y = params.get('y')
            z = params.get('z')

            if not drone_id:
                return "Error: drone_id is required"
            if x is None or y is None or z is None:
                return "Error: x, y, and z coordinates are required"

            result = client.move_to(drone_id, x, y, z)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"x\": 100.0, \"y\": 50.0, \"z\": 20.0}}"
        except Exception as e:
            return f"Error moving drone: {str(e)}"

    @tool
    def move_to_and_sense(input_json: str) -> str:
        """Move a drone to specific 3D coordinates (x, y, z), then sense nearby local entities and update the perception blackboard.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - x: Target X coordinate in meters (required)
        - y: Target Y coordinate in meters (required)
        - z: Target Z coordinate (altitude) in meters (required)
        - detail: "summary" or "full" response detail (optional, default "summary")

        Example: {{"drone_id": "drone-001", "x": 100.0, "y": 50.0, "z": 20.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            x = params.get('x')
            y = params.get('y')
            z = params.get('z')
            detail = _detail_level(params)

            if not drone_id:
                return "Error: drone_id is required"
            if x is None or y is None or z is None:
                return "Error: x, y, and z coordinates are required"

            result = client.move_to(drone_id, x, y, z)
            return _move_and_sense_response(client, blackboard, drone_id, result, detail)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"x\": 100.0, \"y\": 50.0, \"z\": 20.0}}"
        except Exception as e:
            return f"Error moving drone and sensing: {str(e)}"


    # Return all tools
    return [
        list_drones,
        get_drone_status,
        get_weather,
        sense_nearby_entities,
        get_nearby_entities,
        update_blackboard_notes,
        get_target_info,
        get_obstacle_info,
        generate_coverage_path,
        take_off,
        land,
        move_to,
        move_to_and_sense,
        navigate_to,
        navigate_to_and_sense,
        move_within_distance,
        move_along_path,
        move_along_path_and_sense,
        move_towards,
        move_towards_and_sense,
        change_altitude,
        hover,
        rotate,
        return_home,
        set_home,
        calibrate,
        take_photo,
        send_message,
        broadcast,
        charge,
    ]
