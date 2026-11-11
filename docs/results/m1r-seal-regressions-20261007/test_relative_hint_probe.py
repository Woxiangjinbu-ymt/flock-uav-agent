import copy
import unittest

from relative_hint_probe import relative_contact_hint


class ReceiptHintTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "tool_name": "move_towards", "status": "completed", "ordering": "certain", "overlaps": [],
            "evidence_id": "e7", "tool_call_id": "tool7",
            "args": {"drone_id": "actor-a", "distance": 40, "heading": 90},
            "payload": {"command": "move_towards", "outcome": "completed", "status": "success",
                        "drone_id": "actor-a", "message": "Drone moved 40.00m to position (70.00, 90.00, 20.00), heading: 90.0° (battery: healthy) - Reached target(s): Arbitrary Target"},
        }

    def probe(self, event=None):
        return relative_contact_hint(event or self.event, session_id="session-a", command_id="prior-command")

    def test_extracts_segment_without_promoting_location_or_completion(self):
        hint = self.probe()
        self.assertEqual(hint["points"], [[30, 90, 20], [70, 90, 20]])
        self.assertEqual(hint["source"]["command_id"], "prior-command")
        self.assertEqual(hint["reported_names"], ["Arbitrary Target"])
        self.assertIsNone(hint["target_position"])
        self.assertFalse(hint["current_assignment_completed"])

    def test_rejects_failure_partial_conflict_and_actor_mismatch(self):
        changes = [("status", "blocked"), ("ordering", "uncertain"), ("overlaps", ["e8"]),
                   ("payload.outcome", "partial"), ("payload.status", "error"),
                   ("payload.drone_id", "actor-b"), ("payload.error", "contradiction")]
        for key, value in changes:
            with self.subTest(key=key):
                event = copy.deepcopy(self.event)
                obj = event
                path = key.split(".")
                for p in path[:-1]: obj = obj[p]
                obj[path[-1]] = value
                self.assertIsNone(self.probe(event))

    def test_rejects_unconfirmed_direction_distance_and_vertical_move(self):
        for key, value in [("heading", 0), ("distance", 400), ("distance", float("nan")), ("dz", 3), ("azimuth", 90)]:
            with self.subTest(key=key):
                event = copy.deepcopy(self.event)
                event["args"][key] = value
                self.assertIsNone(self.probe(event))

    def test_rejects_free_text_or_missing_contact(self):
        for message in ["Probably reached Arbitrary Target at (70, 90, 20)",
                        self.event["payload"]["message"].split(" - Reached")[0]]:
            event = copy.deepcopy(self.event)
            event["payload"]["message"] = message
            self.assertIsNone(self.probe(event))


if __name__ == "__main__": unittest.main()
