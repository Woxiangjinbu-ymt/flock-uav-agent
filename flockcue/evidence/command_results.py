"""Interpret command receipts without confusing HTTP success with execution.

The server uses ``status``; older clients and test doubles use boolean
``success`` / ``partial_success``. Keep those fields intact and derive a small
outcome vocabulary. Missing or contradictory evidence never proves completion.
"""

import re
from typing import Any


_BLOCKAGE_REASONS = {
    "obstacle_blocked", "path_blocked", "obstacle_collision", "collision",
}
_BLOCKAGE_MESSAGE = re.compile(
    r"\b(?:path|movement|motion|route|corridor)\b[^.\n]*\bblocked\b"
    r"|\b(?:conflicts?|intersects?|collid\w*|blocked)\b[^.\n]*\bobstacle\b",
    re.IGNORECASE,
)
_NON_GEOMETRIC_FAILURE = re.compile(
    r"\bbattery\b|\bcurrent status\b|\b(?:invalid|missing|required)\b"
    r"|\baltitude\b[^.\n]*\b(?:maximum|negative|valid)\b",
    re.IGNORECASE,
)


def _is_geometry_blocked(result: dict) -> bool:
    """Recognize explicit obstacle/path rejection, never generic failure."""
    reasons = [result.get(key) for key in ("reason", "reason_code", "error_code", "blocked_reason")]
    messages = [result.get("message"), result.get("error"), *reasons]
    text = " ".join(value for value in messages if isinstance(value, str))
    if _NON_GEOMETRIC_FAILURE.search(text):
        return False
    return any(
        isinstance(reason, str) and reason.strip().lower() in _BLOCKAGE_REASONS
        for reason in reasons
    ) or bool(_BLOCKAGE_MESSAGE.search(text))


def command_outcome(result: Any) -> str:
    """Return completed, partial, blocked, error, or unknown from raw evidence.

    ``partial_success`` is evidence of progress, not arrival. A failed command
    is ``blocked`` only when its reason explicitly identifies path geometry;
    battery, input and drone-state failures must not trigger obstacle recovery.
    A previously derived ``outcome`` field is deliberately not trusted.
    """
    if not isinstance(result, dict):
        return "unknown"

    status = result.get("status")
    if isinstance(status, str):
        status = status.strip().lower()
    if status not in (None, "success", "partial_success", "error"):
        return "unknown"

    success = result.get("success")
    partial = result.get("partial_success")
    if success is not None and not isinstance(success, bool):
        return "unknown"
    if partial is not None and not isinstance(partial, bool):
        return "unknown"

    has_error = bool(result.get("error"))
    if success is True and has_error:
        return "unknown"
    if status == "success" and (success is False or partial is True or has_error):
        return "unknown"
    if status == "error" and (success is True or partial is True):
        return "unknown"
    if status == "partial_success" and (partial is False or has_error):
        return "unknown"

    if status == "partial_success" or partial is True:
        return "unknown" if has_error else "partial"
    if status == "error" or success is False or has_error:
        return "blocked" if _is_geometry_blocked(result) else "error"
    if status == "success" or success is True:
        remaining = result.get("unsuccessful_points_count")
        if (isinstance(remaining, (int, float)) and remaining > 0) or result.get("unsuccessful_points"):
            return "unknown"
        return "completed"
    return "unknown"


def normalize_command_result(result: Any) -> Any:
    """Add the derived outcome to a copied receipt; preserve non-object data."""
    if not isinstance(result, dict):
        return result
    return {**result, "outcome": command_outcome(result)}
