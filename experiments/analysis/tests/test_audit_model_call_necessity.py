"""Meaningful audit guards: no future leakage, bad boundaries, scope, side effects."""
import copy
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "audit_model_call_necessity.py"
spec = importlib.util.spec_from_file_location("audit_model_call_necessity", SOURCE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

COMMAND = "Drone Drone 3 takes off to 14 meters altitude, then rotates to face heading 353 degrees and hovers."


def event(index, tool, status="completed", args=None, payload=None, scope="action_receipt"):
    return {"evidence_id": f"e{index}", "tool_call_id": f"call{index}", "tool_name": tool,
            "status": status, "args": args or {}, "payload": payload or {}, "scope": scope,
            "start_sequence": index * 2, "end_sequence": index * 2 + 1}


def fixture():
    events = [event(1, "list_drones", "observed", payload=[{"id": "d3", "name": "Drone 3"}], scope="observation"),
              event(2, "take_off", args={"drone_id": "d3", "altitude": 14}),
              event(3, "rotate", args={"drone_id": "d3", "heading": 353}),
              event(4, "hover", args={"drone_id": "d3"})]
    requests = [{"segment": 0, "message_sha256": [str(j) for j in range(2 + i * 2)],
                 "previous_prefix_preserved": bool(i)} for i in range(5)]
    return {"command": COMMAND, "output": "success", "success": True,
            "tool_chain": [{"tool": e["tool_name"]} for e in events],
            "task_progress": {"events": events, "task_evidence": {"model_requests": requests, "injections": []}}}


class AuditTests(unittest.TestCase):
    def test_prefix_continuation_and_confirmation_are_distinct(self):
        trace = fixture()
        before = copy.deepcopy(trace)
        rows = audit.audit_trace(trace)[0]
        self.assertEqual([r["label"] for r in rows], ["INITIAL_PLAN", "UNDETERMINED", "CONTINUE_CANDIDATE", "CONTINUE_CANDIDATE", "CONFIRM_CANDIDATE"])
        self.assertFalse(any(r["counterfactual_call_removal_verified"] for r in rows))
        self.assertEqual(trace, before)

    def test_outcomes_and_future_receipts_cannot_change_earlier_labels(self):
        trace = fixture()
        before = audit.audit_trace(trace)[0]
        trace.update(output="failed", success=False, hidden_checker={"passed": False})
        trace["task_progress"]["events"][-1]["status"] = "blocked"
        after = audit.audit_trace(trace)[0]
        self.assertEqual(before[:-1], after[:-1])
        self.assertEqual(after[-1]["label"], "REASSESS")

    def test_extra_task_clause_is_not_silently_dropped(self):
        trace = fixture()
        trace["command"] += " Take a photo after hovering."
        self.assertIsNone(audit.simple_plan(trace["command"]))
        self.assertEqual(audit.audit_trace(trace)[0][2]["label"], "UNDETERMINED")

    def test_wrong_actor_or_wrong_heading_never_confirms_plan(self):
        for key, value in (("drone_id", "other"), ("heading", 352)):
            trace = fixture()
            trace["task_progress"]["events"][2]["args"][key] = value
            self.assertEqual(audit.audit_trace(trace)[0][-1]["label"], "UNDETERMINED")

    def test_missing_or_overlapping_receipts_reject_alignment(self):
        for damage in ("missing", "overlap", "prefix", "anchor"):
            trace = fixture()
            if damage == "missing":
                trace["task_progress"]["events"].pop()
            elif damage == "overlap":
                trace["task_progress"]["events"][0]["end_sequence"] = 5
            elif damage == "prefix":
                trace["task_progress"]["task_evidence"]["model_requests"][2]["previous_prefix_preserved"] = False
            else:
                trace["task_progress"]["task_evidence"]["injections"] = [{"tool_call_id": "call4", "message_index": 2}]
            with self.assertRaises(ValueError, msg=damage):
                audit.align_requests(trace)

    def test_concurrent_order_within_batch_is_not_model_order(self):
        trace = fixture()
        trace["tool_chain"] = [{"tool": "take_off"}, {"tool": "list_drones"}, {"tool": "rotate"}, {"tool": "hover"}]
        requests = trace["task_progress"]["task_evidence"]["model_requests"]
        for r, n in zip(requests, [2, 5, 7, 9]):
            r["message_sha256"] = [str(j) for j in range(n)]
        requests.pop()
        groups, _, _, meta = audit.align_requests(trace)
        self.assertEqual(len(groups[1]), 2)
        self.assertEqual(meta["concurrent_tool_order_differences"], 1)

    def test_tail_is_not_retroactively_given_to_last_request(self):
        trace = fixture()
        trace["task_progress"]["events"].append(event(5, "move_to", "blocked"))
        trace["tool_chain"] = []
        rows, _, tail, _, _ = audit.audit_trace(trace)
        self.assertEqual(rows[-1]["label"], "CONFIRM_CANDIDATE")
        self.assertEqual([e["evidence_id"] for e in tail], ["e5"])

    def test_unknown_effect_is_observed_before_retry(self):
        e = event(1, "take_photo", "unknown", payload={"tool_exception": "request cancelled"})
        result = audit.classify_boundary(COMMAND, [e], [e], 1)
        self.assertEqual(result["label"], "OBSERVE_OR_REPAIR")
        self.assertEqual(result["failure_kinds"], ["unknown_effect"])

    def test_schema_failure_differs_from_failed_action(self):
        e = event(1, "move_to", "unknown", payload="Error invoking tool 'move_to': Input should be a valid string")
        self.assertEqual(audit.failure_kind(e), "argument_schema")
        e.update(status="partial", payload={"successful_points": [[1, 2, 3]], "unsuccessful_points": [[4, 5, 6]]})
        self.assertEqual(audit.classify_boundary(COMMAND, [e], [e], 1)["label"], "REASSESS")

    def test_historical_error_text_in_blackboard_does_not_invalidate_success(self):
        e = event(1, "navigate_to", payload={"blackboard": {"notes": "insufficient battery"}, "message": "arrived"})
        self.assertIsNone(audit.failure_kind(e))

    def test_completed_movement_with_unverified_arrival_needs_local_check(self):
        e = event(1, "navigate_to_and_sense", "error", payload={"navigation_result": {
            "arrival_verified": False, "final_position": {"x": 11, "y": 11, "z": 20},
            "last_move_result": {"outcome": "completed", "message": "path completed"}}})
        self.assertEqual(audit.failure_kind(e), "arrival_unverified")
        self.assertEqual(audit.classify_boundary(COMMAND, [e], [e], 1)["label"], "OBSERVE_OR_REPAIR")

    def test_missing_arguments_cannot_be_recovered_by_serialization_alone(self):
        e = event(1, "sense_nearby_entities", "error", payload="Error invoking tool: input_json: Field required")
        self.assertEqual(audit.failure_kind(e), "argument_missing")
        self.assertEqual(audit.classify_boundary(COMMAND, [e], [e], 1)["label"], "REASSESS")

    def test_repeated_failure_requires_preceding_batch_without_successful_motion(self):
        trace = fixture()
        ev = trace["task_progress"]["events"]
        for i in (1, 2):
            ev[i].update(tool_name="move_to", status="blocked", args={"drone_id": "d3", "x": 3})
        trace["tool_chain"] = []
        events = audit.audit_trace(trace)[1]
        self.assertFalse(events[1]["repeated_failure_without_recorded_motion"])
        self.assertTrue(events[2]["repeated_failure_without_recorded_motion"])


if __name__ == "__main__":
    unittest.main()
