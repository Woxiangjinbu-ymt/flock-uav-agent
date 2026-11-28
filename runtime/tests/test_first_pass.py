"""The formal command may dispatch only in one empty, exclusively held stage."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import __main__ as cli
from runtime import executor, parallel


class FirstPassTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.addCleanup(executor.use_stage, executor.STAGE)
        self.stage = Path(workspace.name)

    def command(self, name):
        with (patch.object(sys, "argv", ["runtime", name, "--stage", str(self.stage)]),
              patch.object(executor, "verify_registration", return_value={}),
              patch.dict(os.environ)):
            return cli.main()

    def test_fresh_preflight_holds_the_same_lock_as_run(self):
        def probe(gateway):
            self.assertTrue(gateway)
            with self.assertRaisesRegex(ValueError, "active"):
                with parallel.first_pass_lock(self.stage):
                    self.fail("Concurrent execution admitted")
            return True

        with patch.object(executor, "preflight", side_effect=probe) as gateway:
            self.assertEqual(self.command("preflight"), 0)
        gateway.assert_called_once()

    def test_existing_attempt_blocks_both_commands_before_network(self):
        (self.stage / "runs/prior-attempt").mkdir(parents=True)
        with (patch.object(executor, "preflight") as gateway,
              patch.object(parallel, "run_attempt") as run):
            for name in ("preflight", "run"):
                with self.assertRaisesRegex(ValueError, "already contains"):
                    self.command(name)
            gateway.assert_not_called()
            run.assert_not_called()

    def test_collection_dispatches_each_new_training_scene_once(self):
        registration = executor.read_json(executor.COLLECTION_REGISTRATION)
        pilot = executor.read_json(executor.PILOT_REGISTRATION)
        training = set(executor.collection_train_roles())
        carried = set(registration["carried_seals"])
        with patch.object(executor, "PLAN", executor.COLLECTION_PLAN):
            scenes = executor.select_scenes(executor.canonical_scenes())
        selected = {scene["id"] for _, scene in scenes}
        self.assertEqual(len(training), 51)
        self.assertEqual(len(scenes), len(selected))
        self.assertEqual(carried, set(pilot["pilot_scenes"]))
        self.assertEqual(selected, training - carried)
        self.assertTrue(training.isdisjoint(pilot["additional_validation"]))
        self.assertEqual(sum(len(scene["tasks"]) for _, scene in scenes), 960)

    def test_supplement_replays_full_scenes_and_preserves_registered_prefixes(self):
        self.assertIn(executor.REPAIR_PLAN, executor.TEACHER_PLANS)
        repair = executor.read_json(executor.REPAIR_REGISTRATION)
        with patch.object(executor, "PLAN", executor.REPAIR_PLAN):
            scenes = executor.select_scenes(executor.canonical_scenes())
        plan = executor.make_plan(executor.REPAIR_PLAN, scenes)
        self.assertEqual((len(scenes), plan["expected_tasks"], plan["expected_checks"]), (2, 40, 243))
        kept = []
        for scene in plan["sessions"]:
            prefix = repair["prefixes"][scene["id"]]["scored_task_ids"]
            self.assertEqual(prefix, [task["id"] for task in scene["tasks"][:len(prefix)]])
            kept.extend(prefix)
        self.assertEqual(len(set(kept)), 5)

    def test_concurrent_preflight_is_rejected_before_probing(self):
        with parallel.first_pass_lock(self.stage), patch.object(executor, "preflight") as gateway:
            with self.assertRaisesRegex(ValueError, "active"):
                self.command("preflight")
            gateway.assert_not_called()

    def test_edited_registration_cannot_authorize_a_run(self):
        registration = self.stage / "registration.json"
        registration.write_text("{}\n")
        executor.seal_registration(self.stage)
        registration.write_text('{"changed": true}\n')
        executor.use_stage(self.stage)
        with patch.object(executor, "load_plan", return_value={}):
            with self.assertRaisesRegex(ValueError, "Git commit"):
                executor.verify_registration()


if __name__ == "__main__":
    unittest.main()
