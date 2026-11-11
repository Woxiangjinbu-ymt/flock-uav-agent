"""Synthetic-file tests only: no Agent, simulator, SDK, child, or network."""
from __future__ import annotations
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SOURCE = (Path(__file__).resolve().parents[2] / "scripts") / "audit_harness_natural_runner.py"
SPEC = importlib.util.spec_from_file_location("independent_natural_oracle", SOURCE)
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def blob(body):
    return {"base64": base64.b64encode(body).decode(), "length": len(body), "sha256": sha(body)}


class Synthetic:
    def __init__(self, root, case="skill_success"):
        self.run = root.resolve()
        self.case = case
        self.folder = self.run / "trials" / case
        self.policy = self.folder / "policy"
        self.archive_path = self.policy / "modelio" / "wire.jsonl"
        self.trial = {"trial_id": case, "scenario": case}
        self.archive, self.oracle, self.actions = [], [], []
        self.responses = []
        success, killed = case in ORACLE.SUCCESS, case in ORACLE.KILLS
        denied = case in {"http_limit", "usage_stop"}
        count = 5 if case == "raw_success" else 3
        self.event("archive_start", {})
        attempts = []
        for index in range(count):
            logical, aid = "model-" + str(index + 1), "wire-" + str(index + 1)
            request = json.dumps({"messages": [{"role": "user", "content": str(index)}]}).encode()
            usage = {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}
            if index == 2 and case in {"http_error", "usage_missing"}:
                usage = None
            response = json.dumps({"response": index, "usage": usage}).encode()
            self.responses.append(response)
            status = 500 if case == "http_error" and index == 2 else 200
            produced = not (index == 2 and (killed or denied))
            delivered = produced and not (index == 2 and case in {"request_timeout", "task_deadline", "cancel", "usage_missing"})
            self.event("model_start", {"logical_call_id": logical})
            self.event("http_request", {"attempt_id": aid, "logical_call_id": logical, "body": blob(request)})
            if not (index == 2 and denied):
                self.oracle.append({"kind": "delegated_request", "index": index, "at": index * 2,
                    "body_sha256": sha(request), "body_length": len(request)})
                attempt = {"attempt_id": index + 1, "delegated": True, "delivered_to_sdk": delivered,
                    "request_body_sha256": sha(request), "request_body_length": len(request), "usage": usage if produced else None}
                if produced:
                    self.oracle.append({"kind": "produced_response", "index": index, "at": index * 2 + 1,
                        "status_code": status, "body_sha256": sha(response), "body_length": len(response), "usage": usage})
                    if not delivered:
                        attempt["diagnostic_response" if case == "usage_missing" else "late_response"] = {
                            "status_code": status, "body": blob(response)}
                attempts.append(attempt)
            if delivered:
                self.event("http_response_start", {"attempt_id": aid, "logical_call_id": logical, "status_code": status})
                self.event("http_response_chunk", {"attempt_id": aid, "logical_call_id": logical, "chunk_index": 1, "body": blob(response)})
            if not (index == 2 and case == "watchdog_hang"):
                body = response if delivered else b""
                self.event("http_response_end", {"attempt_id": aid, "logical_call_id": logical, "length": len(body),
                    "sha256": sha(body), "chunk_count": int(delivered), "response_observed": delivered, "complete": delivered})
                self.event("model_end", {"logical_call_id": logical})
        if case != "watchdog_hang":
            self.event("archive_end", {"complete": success})
        commands = ["take_off", "rotate", "hover"] if success else ["take_off"]
        self.raw = [{"phase": "task", "action": False, "admin": False, "endpoint": "/drones",
            "origin": {"tool_call_id": "local-list"}, "response": [{"id": "actor-3", "name": "Drone 3"}]}]
        for index, command in enumerate(commands):
            action = {"phase": "task", "action": True, "admin": False, "endpoint": "/drones/actor-3/" + command,
                "committed": True, "response": {"status": "success", "drone_id": "actor-3"}, "request_id": index + 1,
                "params": {"altitude": 14.0} if index == 0 else {}}
            self.raw.append(action)
            self.actions.append(action)
        self.physical = [{"request_id": 1, "actor": "actor-3", "committed": True, "vertical_distance": 14,
            "history": {"event": "take_off", "start_position": {"z": 0}, "position": {"z": 14}}}]
        self.limits = {"attempts": attempts, "admitted_http_attempts": len(attempts), "delegated_http_attempts": len(attempts),
            "known_total_tokens": sum(a["usage"]["total_tokens"] for a in attempts if a["usage"]),
            "closed": not killed, "threads_still_alive": [3] if killed else [],
            "needs_process_termination": killed, "reusable": False}
        worker = {"limits": copy.deepcopy(self.limits), "entry": {"command_id": "command-1"},
            "agent": {"archive_info": {"path": str(self.archive_path), "complete": success},
                "archive_verify": {"complete": success}}}
        self.row = {"trial_id": case, "status": "incomplete" if killed else "completed", "worker": None if killed else worker,
            "score": None if killed else {}, "processes": [{"pid": 123, "exited": True,
                "returncode": -9 if killed else 0, "kill_reason": ORACLE.KILLS.get(case)}]}
        self.notice = {"pid": 123}

    def event(self, kind, data):
        self.archive.append({"sequence": len(self.archive) + 1, "command_id": "command-1", "episode_id": "episode-1",
            "archive_id": "archive-1", "kind": kind, "data": data})

    def write(self):
        files = {self.archive_path: self.archive, self.folder / "private/local-transport.jsonl": self.oracle,
            self.policy / "worldlogs/public-api.jsonl": self.raw, self.policy / "worldlogs/physical-events.jsonl": self.physical}
        for path, rows in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        (self.folder / "private/request-limits.json").write_text(json.dumps(self.limits))
        (self.folder / "termination-required.json").write_text(json.dumps(self.notice))

    def audit(self):
        self.write()
        return ORACLE.audit_trial(self.run, self.trial, self.row)

    def remove_events(self, predicate):
        self.archive[:] = [e for e in self.archive if not predicate(e)]
        for n, row in enumerate(self.archive, 1):
            row["sequence"] = n


class TestNaturalOracle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def sample(self, case="skill_success"):
        return Synthetic(self.root, case)

    def assert_rejected(self, fixture):
        checks = fixture.audit()
        self.assertTrue(checks)
        self.assertFalse(all(checks.values()), checks)

    def test_fixed_twelve_case_shapes(self):
        for case in sorted(ORACLE.SUCCESS | ORACLE.ERRORS | set(ORACLE.KILLS)):
            with self.subTest(case=case):
                checks = self.sample(case).audit()
                self.assertTrue(all(checks.values()), checks)

    def test_hung_drain_requires_closed_partial_response(self):
        f = self.sample("hung_drain")
        self.assertTrue(f.audit()["undelivered_response_2_absent_from_archive"])
        f.remove_events(lambda e: e["kind"] == "http_response_end" and e["data"]["attempt_id"] == "wire-3")
        self.assert_rejected(f)

    def test_watchdog_requires_open_archive(self):
        f = self.sample("watchdog_hang")
        self.assertTrue(all(f.audit().values()))
        f.event("archive_end", {"complete": False})
        self.assert_rejected(f)

    def test_request_bytes_disagree_with_oracle(self):
        f = self.sample(); f.oracle[0]["body_sha256"] = "0" * 64
        self.assert_rejected(f)

    def test_response_bytes_disagree_with_oracle(self):
        f = self.sample(); f.oracle[1]["body_sha256"] = "0" * 64
        self.assert_rejected(f)

    def test_missing_response_chunk(self):
        f = self.sample(); f.remove_events(lambda e: e["kind"] == "http_response_chunk" and e["data"]["attempt_id"] == "wire-1")
        self.assert_rejected(f)

    def test_corrupt_base64_body(self):
        f = self.sample(); next(e for e in f.archive if e["kind"] == "http_request")["data"]["body"]["base64"] = "not valid"
        self.assert_rejected(f)

    def test_request_body_length_is_bool_rejected(self):
        f = self.sample(); next(e for e in f.archive if e["kind"] == "http_request")["data"]["body"]["length"] = True
        self.assert_rejected(f)

    def test_controller_request_mismatch(self):
        f = self.sample(); f.limits["attempts"][0]["request_body_sha256"] = "1" * 64
        f.row["worker"]["limits"] = copy.deepcopy(f.limits)
        self.assert_rejected(f)

    def test_worker_controller_snapshot_mismatch(self):
        f = self.sample(); f.row["worker"]["limits"]["known_total_tokens"] += 1
        self.assert_rejected(f)

    def test_duplicate_oracle_response(self):
        f = self.sample(); f.oracle.append(copy.deepcopy(f.oracle[1]))
        self.assert_rejected(f)

    def test_oracle_response_precedes_own_request(self):
        f = self.sample(); f.oracle[1]["at"] = -1
        self.assert_rejected(f)

    def test_wrong_archive_command_owner(self):
        f = self.sample(); f.row["worker"]["entry"]["command_id"] = "another"
        self.assert_rejected(f)

    def test_false_complete_on_matrix_error(self):
        f = self.sample("http_error")
        f.archive[-1]["data"]["complete"] = True
        f.row["worker"]["agent"]["archive_info"]["complete"] = True
        f.row["worker"]["agent"]["archive_verify"]["complete"] = True
        self.assert_rejected(f)

    def test_kill_without_third_real_dispatch(self):
        f = self.sample("watchdog_hang"); f.oracle = f.oracle[:-1]
        self.assert_rejected(f)

    def test_kill_without_physical_takeoff(self):
        f = self.sample("watchdog_hang"); f.physical = []
        self.assert_rejected(f)

    def test_kill_must_not_be_scored(self):
        f = self.sample("hung_drain"); f.row["score"] = {"success": False}
        self.assert_rejected(f)

    def test_kill_pid_notice_mismatch(self):
        f = self.sample("hung_drain"); f.notice["pid"] = 456
        self.assert_rejected(f)

    def test_kill_diagnostic_must_still_be_unresolved(self):
        f = self.sample("hung_drain"); f.limits["threads_still_alive"] = []
        self.assert_rejected(f)

    def test_late_body_must_be_private_and_exact(self):
        f = self.sample("request_timeout")
        f.limits["attempts"][2]["late_response"]["body"] = blob(b"wrong")
        f.row["worker"]["limits"] = copy.deepcopy(f.limits)
        self.assert_rejected(f)

    def test_late_body_copied_into_policy(self):
        f = self.sample("request_timeout"); f.write()
        (f.policy / "leaked.txt").write_bytes(f.responses[2])
        self.assert_rejected(f)

    def test_late_body_base64_copied_into_policy(self):
        f = self.sample("cancel"); f.write()
        (f.policy / "leaked.txt").write_bytes(base64.b64encode(f.responses[2]))
        self.assert_rejected(f)

    def test_undelivered_response_cannot_be_claimed_delivered(self):
        f = self.sample("usage_missing"); f.limits["attempts"][2]["delivered_to_sdk"] = True
        f.row["worker"]["limits"] = copy.deepcopy(f.limits)
        self.assert_rejected(f)

    def test_usage_sum_wrong(self):
        f = self.sample(); f.limits["known_total_tokens"] += 1
        f.row["worker"]["limits"] = copy.deepcopy(f.limits)
        self.assert_rejected(f)

    def test_usage_cannot_be_synthesized_for_http_error(self):
        f = self.sample("http_error"); f.limits["attempts"][2]["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        f.row["worker"]["limits"] = copy.deepcopy(f.limits)
        self.assert_rejected(f)

    def test_known_usage_rejects_bool(self):
        f = self.sample(); f.limits["attempts"][0]["usage"]["prompt_tokens"] = True
        f.row["worker"]["limits"] = copy.deepcopy(f.limits)
        self.assert_rejected(f)

    def test_hidden_task_admin_request(self):
        f = self.sample(); f.raw[0]["admin"] = True
        self.assert_rejected(f)

    def test_partial_takeoff_not_successful_action(self):
        f = self.sample("watchdog_hang"); f.actions[0]["response"]["status"] = "partial_success"
        self.assert_rejected(f)

    def test_mixed_archive_identity(self):
        f = self.sample(); f.archive[-1]["episode_id"] = "other"
        self.assert_rejected(f)

    def test_duplicate_logical_call(self):
        f = self.sample(); starts = [e for e in f.archive if e["kind"] == "model_start"]
        starts[1]["data"]["logical_call_id"] = starts[0]["data"]["logical_call_id"]
        self.assert_rejected(f)

    def test_path_escape_trial(self):
        f = self.sample(); f.trial["trial_id"] = "../skill_success"
        self.assert_rejected(f)

    def test_truncated_jsonl(self):
        f = self.sample(); f.write()
        f.archive_path.write_bytes(f.archive_path.read_bytes().rstrip(b"\n"))
        checks = ORACLE.audit_trial(f.run, f.trial, f.row)
        self.assertFalse(all(checks.values()))

    def test_symlink_archive_rejected(self):
        f = self.sample(); f.write()
        target = self.root / "outside.jsonl"; target.write_bytes(f.archive_path.read_bytes())
        f.archive_path.unlink(); f.archive_path.symlink_to(target)
        self.assertFalse(all(ORACLE.audit_trial(f.run, f.trial, f.row).values()))


if __name__ == "__main__":
    unittest.main()
