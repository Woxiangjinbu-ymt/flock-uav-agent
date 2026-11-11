import pytest

from flockcue.evidence.command_results import command_outcome, normalize_command_result


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "success"}, "completed"),
        ({"success": True}, "completed"),
        ({"status": "partial_success"}, "partial"),
        ({"status": "partial_success", "message": "Insufficient battery for waypoint 2"}, "partial"),
        ({"success": False, "partial_success": True, "completed_waypoints": 1}, "partial"),
        ({"success": True, "partial_success": True}, "partial"),
        ({"status": "error", "message": "Path to waypoint 1 blocked: wall"}, "blocked"),
        ({"status": "error", "message": "Waypoint 1 conflicts with obstacle: wall"}, "blocked"),
        ({"status": "error", "message": "Flight path intersects with polygon obstacle 'wall'"}, "blocked"),
        ({"success": False, "message": "Direct movement blocked"}, "blocked"),
        ({"status": "error", "reason_code": "path_blocked"}, "blocked"),
        ({"status": "error", "message": "blocked"}, "error"),
        ({"success": False}, "error"),
        ({"error": "Command rejected"}, "error"),
        ({"status": "error", "message": "Insufficient battery to complete path"}, "error"),
        ({"status": "error", "message": "Path blocked: insufficient battery"}, "error"),
        ({"status": "error", "message": "Waypoint 1 altitude exceeds maximum (50m)"}, "error"),
        ({"status": "error", "message": "Cannot move from current status: idle"}, "error"),
        ({"status": "error", "message": "Missing waypoints parameter"}, "error"),
        ({"status": "error", "message": "Invalid path blocked by input validation"}, "error"),
        ({}, "unknown"),
        (None, "unknown"),
        ([], "unknown"),
        ({"message": "Path blocked"}, "unknown"),
        ({"status": "pending", "success": True}, "unknown"),
        ({"success": "true"}, "unknown"),
        ({"success": 1}, "unknown"),
        ({"status": "success", "success": False}, "unknown"),
        ({"status": "success", "partial_success": True}, "unknown"),
        ({"status": "error", "success": True}, "unknown"),
        ({"status": "error", "partial_success": True}, "unknown"),
        ({"status": "partial_success", "partial_success": False}, "unknown"),
        ({"status": "success", "error": "Command failed"}, "unknown"),
        ({"success": True, "error": "Command failed"}, "unknown"),
        ({"status": "success", "unsuccessful_points_count": 1}, "unknown"),
        ({"success": True, "unsuccessful_points": [[4, 5, 6]]}, "unknown"),
        ({"outcome": "completed"}, "unknown"),
    ],
)
def test_command_outcome(result, expected):
    assert command_outcome(result) == expected


def test_normalization_is_additive_and_does_not_mutate_original():
    receipt = {
        "status": "partial_success",
        "success": False,
        "message": "Stopped before final waypoint",
        "successful_points_count": 1,
        "successful_points": [[1, 2, 3]],
        "unsuccessful_points_count": 1,
        "unsuccessful_points": [[4, 5, 6]],
    }

    normalized = normalize_command_result(receipt)

    assert normalized == {**receipt, "outcome": "partial"}
    assert normalized is not receipt
    assert "outcome" not in receipt
    assert normalize_command_result(normalized) == normalized


def test_normalization_preserves_missing_or_non_object_payload():
    assert normalize_command_result(None) is None
    assert normalize_command_result("unexpected response") == "unexpected response"


def test_normalization_does_not_trust_an_existing_outcome():
    assert normalize_command_result({"status": "error", "outcome": "completed"}) == {
        "status": "error", "outcome": "error",
    }
