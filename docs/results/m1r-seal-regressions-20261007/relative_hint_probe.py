"""Offline candidate only: derive a search hint from one observed receipt.

No world state, checker, task IDs or target coordinates are inputs. This is not
installed in M1-R and does not claim that a target is at a flight endpoint.
The narrow accepted format is the completed horizontal move_towards receipt
emitted by the frozen server. Unsupported formats stay unknown.
"""
import math
import re

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
RECEIPT = re.compile(
    rf"^Drone moved ({NUMBER})m to position "
    rf"\(({NUMBER}), ({NUMBER}), ({NUMBER})\), heading: ({NUMBER})° "
)


def relative_contact_hint(event, *, session_id, command_id):
    receipt = event.get("payload")
    args = event.get("args", {})
    if (event.get("tool_name") != "move_towards"
            or event.get("status") != "completed"
            or event.get("ordering") != "certain"
            or event.get("overlaps")
            or not isinstance(receipt, dict)
            or receipt.get("command") != "move_towards"
            or receipt.get("outcome") != "completed"
            or receipt.get("status") != "success"
            or receipt.get("error")
            or not args.get("drone_id")
            or receipt.get("drone_id") != args["drone_id"]):
        return None
    message = receipt.get("message", "")
    if not isinstance(message, str) or " - Reached target(s): " not in message:
        return None
    match = RECEIPT.match(message)
    if not match or "heading" not in args or any(k in args for k in ("dx", "dy", "azimuth", "elevation")):
        return None
    try:
        distance, x, y, z, heading = map(float, match.groups())
        requested_distance = float(args["distance"])
        requested_heading = float(args["heading"])
        dz = float(args.get("dz", 0))
    except (TypeError, ValueError, KeyError):
        return None
    values = (distance, x, y, z, heading, requested_distance, requested_heading, dz)
    angular_error = abs((requested_heading - heading + 180) % 360 - 180)
    if (not all(math.isfinite(v) for v in values) or distance <= 0 or dz != 0
            or abs(distance - requested_distance) > .011 or angular_error > .051):
        return None
    # Use the explicit request bearing (full precision), checked against the
    # rounded receipt bearing, and the observed successful distance/end point.
    angle = math.radians(requested_heading)
    start = [x - distance * math.sin(angle), y - distance * math.cos(angle), z]
    names = message.split(" - Reached target(s): ", 1)[1].split(", ")
    return {
        "kind": "receipt_derived_relative_segment",
        "points": [[round(v, 6) for v in start], [x, y, z]],
        "coordinate_rounding_m": .02,
        "reported_names": names,
        "source": {"session_id": session_id, "command_id": command_id,
                   "evidence_id": event["evidence_id"], "tool_call_id": event["tool_call_id"],
                   "actor_id": args["drone_id"], "message_path": ["message"]},
        "target_position": None,
        "current_assignment_completed": False,
        "meaning": "historical contact near this segment; search clue only; no endpoint/radius guarantee",
    }
