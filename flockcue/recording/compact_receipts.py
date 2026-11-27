"""Lossless, model-visible encoding of repeated sensing and movement receipts."""
import json


TOOLS = {
    "sense_nearby_entities", "get_nearby_entities", "move_to", "move_towards", "move_along_path",
    "navigate_to", "move_to_and_sense", "move_towards_and_sense", "move_along_path_and_sense",
    "navigate_to_and_sense",
}
CATEGORIES = {"targets", "obstacles", "drones"}


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def snapshot_index(board):
    if not isinstance(board, dict) or set(board) != CATEGORIES:
        return None
    result = {}
    for group, rows in board.items():
        if not isinstance(rows, list):
            return None
        entries = {r["id"]: packed(r) for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)}
        if len(entries) != len(rows):
            return None
        result[group] = entries
    return result


def changes(board, current, previous):
    delta = {}
    for group, entries in current.items():
        old = previous[group]
        change = {}
        removed = [key for key in old if key not in entries]
        updates = [r for r in board[group] if old.get(r["id"]) != entries[r["id"]]]
        if removed:
            change["remove_ids"] = removed
        if updates:
            change["upsert_by_id"] = updates
        order = [key for key in old if key in entries] + [key for key in entries if key not in old]
        if order != list(entries):
            change["order"] = list(entries)
        if change:
            delta[group] = change
    return delta


def compact_receipts(messages):
    """Start a new base in each visible window; every delta refers to an earlier receipt."""
    owners = {}
    previous = None
    snapshot = 0
    result = []
    for message in messages:
        result.append(message)
        if message["role"] == "assistant":
            owners.update((c["id"], c["function"]["name"]) for c in message.get("tool_calls", []))
        if message["role"] != "tool" or owners.get(message["tool_call_id"]) not in TOOLS:
            continue
        content = message["content"]
        if not isinstance(content, str):
            continue
        try:
            value, end = json.JSONDecoder().raw_decode(content)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        board = value.get("blackboard")
        current = snapshot_index(board)
        if current is not None and "blackboard_snapshot" not in value and "blackboard_delta" not in value:
            snapshot += 1
            value["blackboard_snapshot"] = snapshot
            if previous is not None:
                delta = {"from_snapshot": snapshot - 1, "changes": changes(board, current, previous)}
                if len(packed(delta)) < len(packed(board)):
                    del value["blackboard"]
                    value["blackboard_delta"] = delta
            previous = current
        result[-1] = {**message, "content": packed(value) + content[end:]}
    return result
