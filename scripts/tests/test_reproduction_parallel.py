"""Scene executor integration tests. Real local HTTP; deterministic agent, no LLM.

Run with MULTIUAV_LIVE_TEST=1 to enable child processes/local listening ports.
"""
import copy
from contextlib import contextmanager
import _thread
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reproduction as r
import reproduction_parallel as parallel


class RecoveryBoundaryTests(unittest.TestCase):
    def test_provider_failure_detection_ignores_recovered_retries_and_agent_errors(self):
        import json
        events = [
            {"event": "http_end", "job_id": "job", "status_code": 429},
            {"event": "http_end", "job_id": "job", "status_code": 200},
            {"event": "llm_end", "job_id": "job", "run_id": "retry-ok"},
            {"event": "llm_error", "job_id": "job", "run_id": "cancel", "error_type": "ValueError"},
            {"event": "llm_error", "job_id": "other", "run_id": "other-error", "status_code": 402},
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "metrics.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            self.assertEqual(parallel.terminal_provider_errors(path, "job"), [])
            events += [{"event": "llm_error", "job_id": "job", "run_id": "quota", "status_code": 402},
                       {"event": "llm_error", "job_id": "job", "run_id": "network", "error_type": "APIConnectionError"}]
            path.write_text("\n".join(json.dumps(e) for e in events))
            self.assertEqual([e["run_id"] for e in parallel.terminal_provider_errors(path, "job")], ["quota", "network"])

    def test_transient_terminal_status_waits_for_completion_timestamp(self):
        class Fake:
            calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                return {"status": "completed", "completed_at": None if self.calls == 1 else r.datetime.now().isoformat()}

        api = Fake()
        result = r.wait_job(api, "test", 1, .001, lambda _: None, cutoff_at=time.time() + 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(api.calls, 2)

    def test_sealed_scene_recovered_when_coordinator_did_not_record_completion(self):
        plan = r.load_plan("smoke")
        selected = plan["sessions"][0]
        rows = r.initial_results(plan)
        for row in rows:
            row.update(status="scored", passed=False, task_pass=False, agent_timed_out=False,
                       world_quiescent=True, continuation_safe=True)
            for leaf in row["leaves"]:
                leaf.update(status="scored", result=False)
        with tempfile.TemporaryDirectory() as td:
            attempt = Path(td)
            directory = attempt / selected["id"]
            directory.mkdir()
            r.save_json(directory / "complete.json", {"fixture": True})
            manifest = {"status": "running", "scenes": {selected["id"]: {
                "status": "running", "generation": 1, "directory": selected["id"], "history": []}}}
            with patch.object(parallel, "verify_scene", return_value=rows) as verify, \
                    patch.object(parallel, "ensure_stopped"):
                recovered = parallel.recover_records(r, attempt, plan, manifest)
            verify.assert_called_once()
            self.assertEqual(recovered, {selected["id"]: rows})
            self.assertEqual(manifest["scenes"][selected["id"]]["status"], "completed")
            self.assertIn("seal_sha256", manifest["scenes"][selected["id"]])


@unittest.skipUnless(os.environ.get("MULTIUAV_LIVE_TEST") == "1", "opt in to local HTTP/process tests")
class LiveParallelHarnessTests(unittest.TestCase):
    @contextmanager
    def fixture(self, modes=None, fail_scene=None, scene_count=2):
        plan = copy.deepcopy(r.load_plan("pilot"))
        source_scenes = r.load_plan("full")["sessions"] if scene_count > len(plan["sessions"]) else plan["sessions"]
        plan["sessions"] = copy.deepcopy(source_scenes[:scene_count])
        for scene in plan["sessions"]:
            scene["tasks"] = scene["tasks"][:2]
        plan["expected_tasks"] = sum(len(s["tasks"]) for s in plan["sessions"])
        plan["expected_checks"] = sum(len(t["leaves"]) for s in plan["sessions"] for t in s["tasks"])
        plan["protocol"].update(timeout_seconds=2, poll_seconds=.025,
                                worker_stop_seconds=.2 if "stuck" in (modes or {}).values() else 5,
                                land_wait_seconds=0, charge_wait_seconds=0, between_tasks_seconds=0)
        settings, config, _ = r.selected_config()
        real_child, real_read = r.Child, r.read_json
        children = []
        modes = modes or {}
        state = {"fail_scene": fail_scene, "plan": plan, "children": children, "modes": modes,
                 "active_scenes": set(), "max_active_scenes": 0, "scene_starts": [], "scene_stops": [],
                 "parallel_count": scene_count}
        lifecycle_lock = threading.Lock()

        def child(argv, cwd, log, env):
            argv = list(argv)
            sid = next(s["id"] for s in plan["sessions"] if s["id"] in Path(log).parts)
            if "reproduction_agent.py" in argv[1]:
                if state["fail_scene"] == sid:
                    raise RuntimeError("TEST infrastructure failure before agent startup")
                argv[1] = str(Path(__file__).with_name("fake_parallel_agent_service.py"))
                env = {**env, "HARNESS_TEST_MODE": modes.get(sid, "success"),
                       "HARNESS_TEST_SCENE": sid, "HARNESS_TEST_DIRECTORY": str(state["runs"]),
                       "HARNESS_TEST_PARALLEL_COUNT": str(state["parallel_count"])}
            result = real_child(argv, cwd, log, env)
            children.append(result)
            if "reproduction_server.py" in argv[1]:
                with lifecycle_lock:
                    state["active_scenes"].add(sid)
                    state["max_active_scenes"] = max(state["max_active_scenes"], len(state["active_scenes"]))
                    state["scene_starts"].append((sid, time.monotonic()))
                stop = result.stop

                def tracked_stop():
                    stop()
                    if result.process.poll() is not None:
                        with lifecycle_lock:
                            if sid in state["active_scenes"]:
                                state["active_scenes"].remove(sid)
                                state["scene_stops"].append((sid, time.monotonic()))

                result.stop = tracked_stop
            return result

        def read(path):
            if Path(path).name == "preflight.local.json":
                return {"gateway_verified": True, "configuration": r.public_settings(settings), "TEST_FIXTURE": True}
            return real_read(path)

        with tempfile.TemporaryDirectory(prefix="multiuav-parallel-test-") as td:
            state["runs"] = Path(td)
            with patch.object(r, "RUNS", Path(td)), patch.object(r, "load_plan", return_value=plan), \
                    patch.object(r, "selected_config", return_value=(settings, config, "fake-local-test-key")), \
                    patch.object(r, "read_json", side_effect=read), patch.object(r, "Child", side_effect=child):
                try:
                    yield state
                finally:
                    for proc in children:
                        proc.stop()
            self.assertTrue(all(c.process.poll() is not None for c in children))

    def attempt(self, state):
        return next(p for p in state["runs"].iterdir() if p.is_dir())

    def scene_directory(self, attempt, sid):
        manifest = r.read_json(attempt / "manifest.json")
        return attempt / manifest["scenes"][sid]["directory"]

    def assert_scene_evidence(self, attempt, selected):
        directory = self.scene_directory(attempt, selected["id"])
        self.assertTrue((directory / "complete.json").exists())
        resources = r.read_json(directory / "performance.json")["resources"]
        self.assertGreater(resources["samples"], 0)
        self.assertEqual(resources["errors"], 0)
        self.assertGreater(resources["peak_sampled_tree_rss_kib"], 0)
        intervals = []
        for index, task in enumerate(selected["tasks"], 1):
            prefix = directory / task["id"]
            window = r.read_json(str(prefix) + ".window.json")
            barrier = r.read_json(str(prefix) + ".barrier.json")
            agent_state = r.read_json(str(prefix) + ".agent-state.json")
            self.assertTrue(barrier["quiescent"])
            self.assertEqual(barrier["inflight"], 0)
            self.assertEqual(agent_state["blackboards"]["fixture"]["_step"], index)
            self.assertTrue(Path(str(prefix) + ".after.json").exists())
            self.assertTrue(Path(str(prefix) + ".checks.json").exists())
            intervals.append((window["opened_at"], barrier["closed_at"]))
        self.assertLessEqual(intervals[0][1], intervals[1][0])
        return intervals

    def test_parallel_scenes_preserve_order_and_own_blackboards(self):
        modes = {s["id"]: "parallel" for s in r.load_plan("pilot")["sessions"][:2]}
        with self.fixture(modes=modes) as state:
            state["plan"]["protocol"]["timeout_seconds"] = 20
            self.assertTrue(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            rows = r.read_json(attempt / "results.json")
            expected_order = [(s["id"], t["id"]) for s in state["plan"]["sessions"] for t in s["tasks"]]
            self.assertEqual([(x["session_id"], x["task_id"]) for x in rows], expected_order)
            self.assertTrue(r.read_json(attempt / "summary.json")["valid_complete_run"])
            intervals = [self.assert_scene_evidence(attempt, s) for s in state["plan"]["sessions"]]
            self.assertLess(max(x[0][0] for x in intervals), min(x[-1][1] for x in intervals))
            self.assertEqual(len(state["children"]), 4)
            for path in (attempt / "configuration.json", attempt / "agent-settings.json"):
                self.assertNotIn("fake-local-test-key", path.read_text())

    def test_queue_refills_two_workers_until_four_scenes_complete(self):
        with self.fixture(scene_count=4) as state:
            self.assertTrue(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            expected = [(s["id"], t["id"]) for s in state["plan"]["sessions"] for t in s["tasks"]]
            rows = r.read_json(attempt / "results.json")
            self.assertEqual([(x["session_id"], x["task_id"]) for x in rows], expected)
            self.assertEqual(len(state["children"]), 8)
            self.assertEqual(len(state["scene_starts"]), 4)
            self.assertEqual(len(state["scene_stops"]), 4)
            self.assertEqual(state["max_active_scenes"], 2)
            self.assertFalse(state["active_scenes"])
            starts = sorted(t for _, t in state["scene_starts"])
            stops = sorted(t for _, t in state["scene_stops"])
            self.assertLess(stops[0], starts[2])
            self.assertLess(stops[1], starts[3])
            for selected in state["plan"]["sessions"]:
                self.assert_scene_evidence(attempt, selected)
            self.assertTrue(r.read_json(attempt / "summary.json")["valid_complete_run"])

    def test_four_workers_execute_four_isolated_scenes_concurrently(self):
        modes = {s["id"]: "parallel" for s in r.load_plan("full")["sessions"][:4]}
        with self.fixture(modes=modes, scene_count=4) as state:
            state["plan"]["protocol"]["timeout_seconds"] = 20
            self.assertTrue(r.run_attempt("pilot", workers=4))
            attempt = self.attempt(state)
            self.assertEqual(len(state["children"]), 8)
            self.assertEqual(state["max_active_scenes"], 4)
            intervals = [self.assert_scene_evidence(attempt, s) for s in state["plan"]["sessions"]]
            self.assertLess(max(x[0][0] for x in intervals), min(x[0][1] for x in intervals))
            expected = [(s["id"], t["id"]) for s in state["plan"]["sessions"] for t in s["tasks"]]
            rows = r.read_json(attempt / "results.json")
            self.assertEqual([(x["session_id"], x["task_id"]) for x in rows], expected)
            self.assertTrue(r.read_json(attempt / "summary.json")["valid_complete_run"])

    def test_saved_summary_rejects_modified_frozen_configuration(self):
        with self.fixture() as state:
            self.assertTrue(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            self.assertTrue(parallel.saved_summary(r, attempt)["valid_complete_run"])
            configuration = attempt / "configuration.json"
            configuration.write_text(configuration.read_text() + "\n")
            with self.assertRaises(ValueError):
                parallel.saved_summary(r, attempt)

    def test_timeout_is_scored_and_other_scene_continues(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(modes={sid: "timeout"}) as state:
            self.assertTrue(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            rows = r.read_json(attempt / "results.json")
            self.assertEqual([x["status"] for x in rows], ["scored"] * 4)
            self.assertEqual([x["agent_timed_out"] for x in rows], [True, False, False, False])
            self.assertTrue(all(x["continuation_safe"] for x in rows))
            for selected in state["plan"]["sessions"]:
                self.assert_scene_evidence(attempt, selected)
                self.assertNotIn("LATE_ACTION_ATTEMPT", (self.scene_directory(attempt, selected["id"]) / "agent.log").read_text())

    def test_provider_outage_stops_dispatch_invalidates_and_can_resume(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(modes={sid: "provider_error"}, scene_count=3) as state:
            self.assertFalse(r.run_attempt("pilot", workers=1))
            attempt = self.attempt(state)
            manifest = r.read_json(attempt / "manifest.json")
            first = self.scene_directory(attempt, sid)
            rows = r.read_json(first / "results.json")
            self.assertEqual(rows[0]["status"], "invalid")
            self.assertIsNone(rows[0]["task_pass"])
            self.assertEqual(rows[0]["provider_errors"][0]["status_code"], 402)
            self.assertTrue((first / (rows[0]["task_id"] + ".checks.json")).exists())
            self.assertFalse((first / "complete.json").exists())
            self.assertEqual(rows[1]["status"], "not_run")
            self.assertTrue(all(v["status"] == "pending" for k, v in manifest["scenes"].items() if k != sid))
            self.assertIsNone(r.read_json(attempt / "summary.json")["metrics"])
            self.assertEqual(r.read_json(attempt / "summary.json")["scored_checks"], 0)
            state["modes"].clear()
            self.assertTrue(r.resume_attempt(attempt))
            self.assertEqual(r.read_json(attempt / "manifest.json")["scenes"][sid]["generation"], 2)

    def test_failed_scene_does_not_abort_others_and_resume_retains_complete_scene(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(fail_scene=sid) as state:
            self.assertFalse(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            other = state["plan"]["sessions"][1]["id"]
            completed = self.scene_directory(attempt, other)
            original_complete = (completed / "complete.json").read_bytes()
            old_directory = self.scene_directory(attempt, sid)
            old_files = {p.relative_to(old_directory): p.read_bytes() for p in old_directory.rglob("*") if p.is_file()}
            rows = r.read_json(attempt / "results.json")
            self.assertEqual([x["status"] for x in rows if x["session_id"] == other], ["scored", "scored"])
            self.assertFalse(r.read_json(attempt / "summary.json")["valid_complete_run"])
            children_before = len(state["children"])
            state["fail_scene"] = None
            self.assertTrue(r.resume_attempt(attempt))
            self.assertEqual(len(state["children"]), children_before + 2)
            self.assertEqual((completed / "complete.json").read_bytes(), original_complete)
            self.assertNotEqual(self.scene_directory(attempt, sid), old_directory)
            for relative, original in old_files.items():
                self.assertEqual((old_directory / relative).read_bytes(), original)
            manifest = r.read_json(attempt / "manifest.json")
            self.assertEqual(manifest["scenes"][sid]["generation"], 2)
            self.assertEqual(manifest["scenes"][other]["generation"], 1)
            self.assertTrue(r.read_json(attempt / "summary.json")["valid_complete_run"])

    def test_stuck_worker_preserves_score_and_only_blocks_its_own_scene(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(modes={sid: "stuck"}) as state:
            self.assertFalse(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            rows = r.read_json(attempt / "results.json")
            first_scene = [x for x in rows if x["session_id"] == sid]
            self.assertEqual([x["status"] for x in first_scene], ["scored", "not_run"])
            self.assertTrue(first_scene[0]["agent_timed_out"])
            self.assertFalse(first_scene[0]["continuation_safe"])
            other = state["plan"]["sessions"][1]
            self.assertEqual([x["status"] for x in rows if x["session_id"] != sid], ["scored", "scored"])
            self.assert_scene_evidence(attempt, other)
            self.assertIsNone(r.read_json(attempt / "summary.json")["metrics"])

    def test_resume_rejects_modified_completed_evidence_without_starting_children(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(fail_scene=sid) as state:
            self.assertFalse(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            other = state["plan"]["sessions"][1]
            directory = self.scene_directory(attempt, other["id"])
            self.assertTrue((directory / "complete.json").exists())
            artifact = directory / (other["tasks"][0]["id"] + ".after.json")
            artifact.write_text(artifact.read_text() + "\n")
            children_before = len(state["children"])
            state["fail_scene"] = None
            with self.assertRaises(ValueError):
                r.resume_attempt(attempt)
            self.assertEqual(len(state["children"]), children_before)

    def test_resume_rejects_missing_completed_evidence(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(fail_scene=sid) as state:
            self.assertFalse(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            other = state["plan"]["sessions"][1]
            directory = self.scene_directory(attempt, other["id"])
            (directory / (other["tasks"][0]["id"] + ".checks.json")).unlink()
            with self.assertRaises(ValueError):
                r.resume_attempt(attempt)

    def test_unpublished_scene_seal_is_recovered_without_replaying_tasks(self):
        with self.fixture() as state:
            self.assertTrue(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            sid = state["plan"]["sessions"][0]["id"]
            directory = self.scene_directory(attempt, sid)
            marker = directory / "complete.json"
            marker.unlink()
            child_count = len(state["children"])
            original_files = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
            # An acknowledged marker disappearing means evidence loss; it must
            # not be treated as the crash-before-publication window below.
            with self.assertRaises(ValueError):
                r.resume_attempt(attempt)
            self.assertEqual(len(state["children"]), child_count)
            manifest = r.read_json(attempt / "manifest.json")
            manifest["status"] = "running"
            record = manifest["scenes"][sid]
            record["status"] = "running"
            record.pop("seal_sha256", None)
            r.save_json(attempt / "manifest.json", manifest)
            self.assertTrue(r.resume_attempt(attempt))
            self.assertEqual(len(state["children"]), child_count)
            self.assertTrue(marker.exists())
            for relative, original in original_files.items():
                self.assertEqual((directory / relative).read_bytes(), original)
            self.assertEqual(r.read_json(attempt / "manifest.json")["scenes"][sid]["generation"], 1)
            self.assertTrue(r.read_json(attempt / "summary.json")["valid_complete_run"])

    def test_resume_rejects_executor_configuration_and_source_drift(self):
        sid = r.load_plan("pilot")["sessions"][0]["id"]
        with self.fixture(fail_scene=sid) as state:
            self.assertFalse(r.run_attempt("pilot", workers=2))
            attempt = self.attempt(state)
            before = len(state["children"])
            with self.assertRaises(ValueError):
                r.resume_attempt(attempt, workers=1)
            hashes = r.read_json(attempt / "source_hashes.json")
            with patch.object(r, "current_source_hashes", return_value={**hashes, "test_source_drift.py": "changed"}):
                with self.assertRaises(ValueError):
                    r.resume_attempt(attempt)
            settings, config, key = r.selected_config()
            changed_settings = copy.deepcopy(settings)
            changed_settings["provider_configs"][changed_settings["selected_provider"]]["default_model"] = "changed-model"
            with patch.object(r, "selected_config", return_value=(changed_settings, config, key)):
                with self.assertRaises(ValueError):
                    r.resume_attempt(attempt)
            self.assertEqual(len(state["children"]), before)

    def test_interrupt_stops_children_and_resume_reuses_completed_scene(self):
        ids = [s["id"] for s in r.load_plan("pilot")["sessions"][:2]]
        modes = {ids[1]: "interrupt"}
        with self.fixture(modes=modes) as state:
            state["plan"]["protocol"]["timeout_seconds"] = 30
            done, interrupted = threading.Event(), threading.Event()

            def interrupt_after_checkpoint():
                deadline = time.monotonic() + 30
                while not done.wait(.025) and time.monotonic() < deadline:
                    attempts = [p for p in state["runs"].iterdir() if p.is_dir()]
                    if not attempts:
                        continue
                    attempt = attempts[0]
                    complete = attempt / ids[0] / "complete.json"
                    running = list((attempt / ids[1]).glob("*.window.json"))
                    if complete.exists() and running:
                        interrupted.set()
                        _thread.interrupt_main()
                        return

            watcher = threading.Thread(target=interrupt_after_checkpoint, daemon=True)
            watcher.start()
            try:
                try:
                    self.assertFalse(r.run_attempt("pilot", workers=2))
                except KeyboardInterrupt:
                    self.fail("Runner did not handle interrupt and checkpoint cleanly")
            finally:
                done.set()
                watcher.join(timeout=2)
            self.assertTrue(interrupted.is_set(), "Fixture never reached the checkpoint/active-job overlap")
            self.assertTrue(all(c.process.poll() is not None for c in state["children"]))
            attempt = self.attempt(state)
            complete = attempt / ids[0] / "complete.json"
            original_complete = complete.read_bytes()
            original_directory = self.scene_directory(attempt, ids[1])
            child_count = len(state["children"])
            modes[ids[1]] = "success"
            self.assertTrue(r.resume_attempt(attempt))
            self.assertEqual(len(state["children"]), child_count + 2)
            self.assertEqual(complete.read_bytes(), original_complete)
            self.assertNotEqual(self.scene_directory(attempt, ids[1]), original_directory)
            self.assertTrue(r.read_json(attempt / "summary.json")["valid_complete_run"])


if __name__ == "__main__":
    unittest.main()
