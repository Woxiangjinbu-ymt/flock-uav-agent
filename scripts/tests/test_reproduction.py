"""Safety/denominator tests, plus opt-in real local HTTP integration (no external LLM)."""
import copy
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reproduction as r


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.plan = r.load_plan("smoke")

    def test_backend_selection_is_explicit_and_credentials_do_not_cross_providers(self):
        settings = {"selected_provider": "bailian_qwen_flash", "share_blackboard_by_session": True,
                    "toolchain_json_recording": True, "uav_api_key": "", "provider_configs": {}}
        for provider, (base, model, _) in r.SUPPORTED_BACKENDS.items():
            settings["provider_configs"][provider] = {"type": "openai-compatible", "base_url": base,
                                                     "default_model": model, "api_key": ""}
        with patch.object(r, "read_json", return_value=settings), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-only"}, clear=True):
            self.assertIsNone(r.selected_config()[2])
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "qwen-only"}):
                self.assertEqual(r.selected_config()[2], "qwen-only")
            settings["selected_provider"] = "deepseek_official"
            self.assertEqual(r.selected_config()[2], "deepseek-only")
            settings["provider_configs"]["deepseek_official"]["default_model"] = "qwen3.8-flash"
            with self.assertRaisesRegex(ValueError, "default_model"):
                r.selected_config()

    def test_backend_rejects_endpoint_mismatch(self):
        settings = {"selected_provider": "bailian_qwen_flash", "provider_configs": {
            "bailian_qwen_flash": {"type": "openai-compatible", "base_url": r.GATEWAY}}}
        with patch.object(r, "read_json", return_value=settings):
            with self.assertRaisesRegex(ValueError, "official endpoint"):
                r.selected_config()

    def test_canonical_full_counts_and_scene_prefixes(self):
        full, pilot = r.load_plan("full"), r.load_plan("pilot")
        self.assertEqual((len(full["sessions"]), full["expected_tasks"], full["expected_checks"]), (75, 1500, 9396))
        self.assertEqual((len(pilot["sessions"]), pilot["expected_tasks"], pilot["expected_checks"]), (3, 60, 345))
        self.assertTrue(all(len(s["tasks"]) == 20 for s in pilot["sessions"]))

    def test_plan_timeout_matches_upstream_gui_initial_value(self):
        tree = ast.parse((r.ROOT / "controller/check_ui/agent_checker.py").read_text())
        assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Attribute) and target.attr == "timeout_var" for target in node.targets)]
        self.assertEqual(len(assignments), 1)
        ui_seconds = int(next(kw.value.value for kw in assignments[0].value.keywords if kw.arg == "value"))
        for name in ("smoke", "pilot", "full"):
            self.assertEqual(r.load_plan(name)["protocol"]["timeout_seconds"], ui_seconds)
            self.assertEqual(r.load_plan(name)["protocol"]["id"], r.PROTOCOL_ID)

    def test_legacy_plan_migration_preserves_bytes_and_is_idempotent(self):
        for name, legacy in r.legacy_plans(self.plan):
            with self.subTest(protocol=name), tempfile.TemporaryDirectory() as td, patch.object(r, "PLANS", Path(td)):
                path = Path(td) / "smoke.json"
                r.save_json(path, legacy)
                original = path.read_bytes()
                r.persist_plan(self.plan)
                archive = Path(td) / "archive" / name / "smoke.json"
                self.assertEqual(archive.read_bytes(), original)
                self.assertEqual(r.read_json(path), self.plan)
                r.persist_plan(self.plan)
                self.assertEqual(archive.read_bytes(), original)

    def test_migration_refuses_unrelated_protocol_changes(self):
        legacy = dict(r.legacy_plans(self.plan))["agent4drone-bupt-v1-300s"]
        legacy["protocol"].update(timeout_seconds=300, poll_seconds=1)
        with tempfile.TemporaryDirectory() as td, patch.object(r, "PLANS", Path(td)):
            path = Path(td) / "smoke.json"
            r.save_json(path, legacy)
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "modified protocol"):
                r.persist_plan(self.plan)
            self.assertEqual(path.read_bytes(), original)

    def test_all_planned_leaf_rows_exist_before_execution(self):
        rows = r.initial_results(r.load_plan("full"))
        self.assertEqual(len(rows), 1500)
        self.assertEqual(sum(len(x["leaves"]) for x in rows), 9396)
        self.assertTrue(all(l["result"] is None for x in rows for l in x["leaves"]))

    def test_missing_rows_do_not_generate_metrics(self):
        rows = r.initial_results(self.plan)
        result = r.summarize(self.plan, rows, "completed")
        self.assertFalse(result["valid_complete_run"])
        self.assertIsNone(result["metrics"])
        with self.assertRaises(ValueError):
            r.summarize(self.plan, [], "completed")

    def test_duplicate_task_rows_rejected(self):
        rows = r.initial_results(self.plan)
        with self.assertRaises(ValueError):
            r.summarize(self.plan, rows + rows, "completed")

    def test_replaced_leaf_definition_rejected(self):
        rows = r.initial_results(self.plan)
        rows[0]["leaves"][0]["definition"]["expect"] = False
        with self.assertRaisesRegex(ValueError, "leaf"):
            r.summarize(self.plan, rows, "completed")

    def test_metrics_use_correct_denominators_and_tree_pass(self):
        # OR tree may pass with one failed leaf; Avg Check and Global Check differ.
        plan = copy.deepcopy(self.plan)
        t = copy.deepcopy(plan["sessions"][0]["tasks"][0])
        t["id"] = "test-second"
        t["leaves"] = t["leaves"][:1]
        plan["sessions"][0]["tasks"].append(t)
        plan.update(expected_tasks=2, expected_checks=3)
        rows = r.initial_results(plan)
        for row, scores in zip(rows, [[True, False], [False]]):
            row.update(status="scored", passed=any(scores), task_pass=any(scores), agent_timed_out=False,
                       world_quiescent=True, continuation_safe=True)
            for leaf, passed in zip(row["leaves"], scores):
                leaf.update(status="scored", result=passed)
        result = r.summarize(plan, rows, "completed")
        self.assertTrue(result["valid_complete_run"])
        self.assertEqual(result["metrics"], {"passed_tasks": 1, "task_pass": .5, "avg_check": .25,
                                            "global_check": 1/3, "total_failed": .5})
        self.assertIsNone(r.summarize(plan, rows, "aborted")["metrics"])
        rows[0]["continuation_safe"] = False
        self.assertIsNone(r.summarize(plan, rows, "completed")["metrics"])
        rows[0]["continuation_safe"] = True
        rows[0]["world_quiescent"] = False
        self.assertIsNone(r.summarize(plan, rows, "completed")["metrics"])

    def test_missing_leaf_result_is_incomplete(self):
        rows = r.initial_results(self.plan)
        rows[0].update(status="scored", passed=True)
        for leaf in rows[0]["leaves"]:
            leaf["status"] = "scored"
        self.assertFalse(r.summarize(self.plan, rows, "completed")["valid_complete_run"])

    def test_upstream_evaluator_visits_all_branches_and_false_expectation(self):
        calls = []
        class Fake:
            def api_check_execution(self, endpoint, parameters, show_error=False):
                calls.append((endpoint, parameters))
                return {"result": False}
        tree = {"logic": "or", "checks": [{"endpoint": "/check/one", "expect": False},
                                           {"endpoint": "/check/two", "expect": True}]}
        passed, details = r.upstream_evaluate(Fake(), tree, "scene")
        self.assertTrue(passed)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], {"session_id": "scene"})
        self.assertEqual([x["result"] for x in details if x["type"] == "leaf"], [True, False])

    def test_upstream_endpoint_error_remains_visible(self):
        class Fake:
            def api_check_execution(self, *args, **kwargs):
                raise TimeoutError("unavailable")
        passed, details = r.upstream_evaluate(Fake(), {"endpoint": "/check/one"}, "scene")
        self.assertFalse(passed)
        self.assertIn("error", details[0])

    def test_timeout_does_not_call_cancel_or_submit_next_job(self):
        calls = []
        class Fake:
            def call(self, method, path, **kwargs):
                calls.append((method, path))
                return {"status": "running"}
        with self.assertRaises(TimeoutError):
            r.wait_job(Fake(), "one", .015, .01, lambda _: None)
        self.assertTrue(all(call == ("GET", "/agent/jobs/one") for call in calls))

    def test_cancelled_is_not_a_quiescent_terminal_result(self):
        class Fake:
            def call(self, *args, **kwargs):
                return {"status": "cancelled", "completed_at": "now"}
        with self.assertRaises(ValueError):
            r.wait_job(Fake(), "one", 1, 0, lambda _: None)

    def test_completed_false_success_still_returned_for_environment_grading(self):
        class Fake:
            def call(self, *args, **kwargs):
                return {"status": "completed", "completed_at": "now", "result": {"success": False}}
        self.assertFalse(r.wait_job(Fake(), "one", 1, 0, lambda _: None)["result"]["success"])

    def test_landing_blocked_aborts_before_charge(self):
        calls = []
        class Fake:
            def call(self, method, path, **kwargs):
                calls.append(path)
                return {"drones_blocked": 1, "total_drones": 1}
        scene = {"id": "s", "drones": [{"id": "d"}]}
        with patch.object(r, "current_snapshot", return_value=scene):
            with self.assertRaisesRegex(ValueError, "Landing"):
                r.prepare_task(Fake(), scene, self.plan["protocol"])
        self.assertNotIn("/drones/charge_all", calls)

    def test_force_reap_ignores_forged_cancel_status(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "ready"
            code = "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(%r).touch(); time.sleep(60)" % str(marker)
            child = r.Child([sys.executable, "-c", code], r.ROOT, Path(td) / "child.log", os.environ.copy())
            try:
                import time
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists())
                child.stop()
                self.assertIsNotNone(child.process.returncode)
            finally:
                child.stop()

    def test_server_admission_drain_and_stale_requests(self):
        result = subprocess.run([str(r.ROOT / "server/.venv/bin/python"),
                                 str(Path(__file__).with_name("gate_probe.py"))],
                                cwd=r.ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_finished_before_deadline_is_not_timeout_when_polled_late(self):
        class Fake:
            def call(self, *args, **kwargs):
                return {"status": "completed", "completed_at": "2026-10-02T12:00:00"}
        cutoff = r.datetime.fromisoformat("2026-10-02T12:00:01").timestamp()
        self.assertEqual(r.wait_job(Fake(), "one", 0, 0, lambda _: None, cutoff)["status"], "completed")
        with self.assertRaises(r.AgentDeadlineExceeded):
            r.wait_job(Fake(), "one", 0, 0, lambda _: None, cutoff - 2)


@unittest.skipUnless(os.environ.get("MULTIUAV_LIVE_TEST") == "1", "opt in to local HTTP/process tests")
class LiveHarnessTests(unittest.TestCase):
    def run_fixture(self, mode):
        plan = copy.deepcopy(r.load_plan("smoke"))
        if mode.startswith("timeout"):
            second = copy.deepcopy(plan["sessions"][0]["tasks"][0])
            scene = r.read_json(r.DATA / plan["sessions"][0]["file"])
            t = scene["tasks"][1]
            second.update(id=t["id"], name=t["name"], leaves=r.leaves(t["execution_check_apis"]))
            plan["sessions"][0]["tasks"].append(second)
            plan["expected_tasks"] = 2
            plan["expected_checks"] += len(second["leaves"])
        plan["protocol"].update(timeout_seconds=2 if mode.startswith("timeout") else 30, poll_seconds=.05,
                                 worker_stop_seconds=.2 if mode == "timeout_stuck" else 5,
                                 land_wait_seconds=0, charge_wait_seconds=0, between_tasks_seconds=0)
        settings, config, _ = r.selected_config()
        real_child = r.Child
        children = []

        def child(argv, cwd, log, env):
            argv = list(argv)
            if "reproduction_agent.py" in argv[1]:
                argv[1] = str(Path(__file__).with_name("fake_agent_service.py"))
                env = {**env, "HARNESS_TEST_MODE": mode}
            c = real_child(argv, cwd, log, env)
            children.append(c)
            return c

        real_read = r.read_json
        def read(path):
            if Path(path).name == "preflight.local.json":
                return {"gateway_verified": True, "configuration": r.public_settings(settings), "TEST_FIXTURE": True}
            return real_read(path)

        with tempfile.TemporaryDirectory(prefix="multiuav-harness-test-") as td:
            with patch.object(r, "RUNS", Path(td)), patch.object(r, "load_plan", return_value=plan), \
                    patch.object(r, "selected_config", return_value=(settings, config, "fake-local-test-key")), \
                    patch.object(r, "read_json", side_effect=read), patch.object(r, "Child", side_effect=child):
                success = r.run_attempt("smoke")
            attempt = next(p for p in Path(td).iterdir() if p.is_dir())
            rows = real_read(attempt / "results.json")
            summary = real_read(attempt / "summary.json")
            self.assertTrue(all(c.process.poll() is not None for c in children))
            self.assertNotIn("fake-local-test-key", (attempt / "configuration.json").read_text())
            self.assertNotIn("fake-local-test-key", (attempt / "agent-settings.json").read_text())
            first = rows[0]
            prefix = attempt / first["session_id"] / first["task_id"]
            self.assertTrue(real_read(str(prefix) + ".barrier.json")["quiescent"])
            self.assertTrue(Path(str(prefix) + ".after.json").exists())
            self.assertTrue(Path(str(prefix) + ".checks.json").exists())
            if mode == "timeout_stuck":
                self.assertFalse(success)
                self.assertEqual([x["status"] for x in rows], ["scored", "not_run"])
                self.assertTrue(first["agent_timed_out"])
                self.assertTrue(first["task_pass"])
                self.assertFalse(first["continuation_safe"])
                self.assertIsNone(summary["metrics"])
                self.assertFalse((attempt / rows[1]["session_id"] / (rows[1]["task_id"] + ".job.json")).exists())
            elif mode.startswith("timeout"):
                self.assertTrue(success)
                self.assertTrue(summary["valid_complete_run"])
                self.assertEqual([x["status"] for x in rows], ["scored", "scored"])
                self.assertTrue(first["agent_timed_out"])
                self.assertTrue(first["continuation_safe"])
                self.assertEqual(first["agent_final_status"], "cancelled")
                self.assertNotIn("LATE_ACTION_ATTEMPT", (prefix.parent / "agent.log").read_text())
                self.assertFalse(rows[1]["agent_timed_out"])
                expected_checks = {"timeout_success": 2, "timeout_partial": 1, "timeout_empty": 0}[mode]
                self.assertEqual(sum(leaf["result"] for leaf in first["leaves"]), expected_checks)
                self.assertEqual(first["task_pass"], expected_checks == 2)
                second_state = real_read(attempt / rows[1]["session_id"] / (rows[1]["task_id"] + ".agent-state.json"))
                self.assertEqual(second_state["blackboards"]["fixture"]["_step"], 2)
                self.assertEqual(summary["agent_timed_out_tasks"], 1)
                self.assertEqual(summary["timed_out_but_passed_tasks"], int(expected_checks == 2))
            else:
                self.assertTrue(success)
                self.assertTrue(summary["valid_complete_run"])
                self.assertEqual(summary["metrics"]["passed_tasks"], 1)
                self.assertEqual(summary["metrics"]["global_check"], 1)

    def test_real_server_upstream_service_and_checker(self):
        self.run_fixture("success")

    def test_timeout_goal_met_is_scored_pass_and_continues(self):
        self.run_fixture("timeout_success")

    def test_timeout_partial_progress_is_scored_and_continues(self):
        self.run_fixture("timeout_partial")

    def test_timeout_no_progress_is_scored_failure_and_continues(self):
        self.run_fixture("timeout_empty")

    def test_unstoppable_worker_preserves_score_but_blocks_next_task(self):
        self.run_fixture("timeout_stuck")


if __name__ == "__main__":
    unittest.main()
