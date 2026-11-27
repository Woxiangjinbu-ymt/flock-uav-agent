"""Snapshot references must survive history trimming and preserve failed actions and observer evidence."""
import json

from flockcue.recording.compact_receipts import compact_receipts
from posttraining.trajectories.replay_receipts import verify_contents


def pair(call_id, obstacles):
    call = {"id": call_id, "function": {"name": "move_to_and_sense", "arguments": "{}"}}
    payload = {"move_result": {"outcome": "blocked", "success": False, "partial_success": False},
               "nearby": {"drone_id": "a", "obstacles": obstacles}, "changes": {},
               "blackboard": {"targets": [], "obstacles": obstacles, "drones": []}}
    return [{"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call_id,
             "content": json.dumps(payload, indent=2) + "\n\n[public progress annotation]"}]


def test_receipt_deltas_and_window_rebase():
    rows = [{"id": str(i), "position": {"x": i}, "note": "observed"} for i in range(8)]
    updated = [{**rows[0], "position": {"x": 2}}, *rows[1:]]
    messages = pair("first", rows) + pair("again", rows) + pair("update", updated) + pair("remove", updated[:-1])
    messages += pair("reorder", list(reversed(updated[:-1])))
    projected = compact_receipts(messages)
    stats = verify_contents(messages, projected)
    assert stats["full_snapshots"] + stats["delta_snapshots"] == 5
    repeated = json.JSONDecoder().raw_decode(projected[3]["content"])[0]
    assert repeated["blackboard_delta"] == {"from_snapshot": 1, "changes": {}}
    modified = json.JSONDecoder().raw_decode(projected[5]["content"])[0]
    assert modified["blackboard_delta"]["changes"]["obstacles"]["upsert_by_id"] == [updated[0]]
    removed = json.JSONDecoder().raw_decode(projected[7]["content"])[0]
    assert removed["blackboard_delta"]["changes"] == {"obstacles": {"remove_ids": ["7"]}}
    reordered = json.JSONDecoder().raw_decode(projected[9]["content"])[0]
    assert reordered["blackboard_delta"]["changes"] == {"obstacles": {"order": list("6543210")}}
    suffix = compact_receipts(messages[2:])
    assert verify_contents(messages[2:], suffix)["full_snapshots"] >= 1
    assert json.JSONDecoder().raw_decode(suffix[1]["content"])[0]["blackboard"] == {
        "targets": [], "obstacles": rows, "drones": []}


def test_unrelated_tool_and_plain_error_remain_verbatim():
    unrelated = pair("unrelated", [])
    unrelated[0]["tool_calls"][0]["function"]["name"] = "get_weather"
    error = pair("error", [])
    error[-1]["content"] = "navigation failed before a receipt was available"
    messages = unrelated + error
    assert compact_receipts(messages) == messages
