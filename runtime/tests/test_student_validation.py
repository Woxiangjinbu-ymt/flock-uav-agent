import csv
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime import executor as rt, student_validation as sv
from posttraining.inference import output_budget


class CandidateValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(rt.use_stage, rt.STAGE)
        self.stage = Path(self.tmp.name) / "stage"
        rt.use_stage(self.stage)
        self.adapter = Path(self.tmp.name) / "checkpoint"
        self.adapter.mkdir()
        self.weights = b"candidate weight checksum fixture"
        config = dict(rt.read_json(sv.SFT_RESULT)["training"]["adapter"], weights_sha256=rt.digest(self.weights))
        rt.save_json(self.adapter/sv.checkpoint.CONFIG, config)
        (self.adapter/sv.checkpoint.WEIGHTS).write_bytes(self.weights)

    def test_new_plan_freezes_checkpoint_and_preserves_original_protocol(self):
        original = sv.build_plan()
        sv.prepare(self.stage, self.adapter)
        (self.adapter/sv.checkpoint.WEIGHTS).write_bytes(b"another checkpoint")
        plan = sv.validate_plan(self.stage, live=True)
        self.assertEqual(plan["sessions"], original["sessions"])
        self.assertEqual(plan["protocol"], original["protocol"])
        self.assertEqual(plan["student"]["sft_weights_sha256"], rt.digest(self.weights))
        self.assertTrue(plan["paired_uncapped"])
        self.assertIsNone(plan["student"]["budgets"]["episode_tokens"])
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            sv.prepare(self.stage, self.adapter)
        with patch.object(sv, "deployment_packages", return_value={}):
            with self.assertRaisesRegex(ValueError, "packages differ"):
                sv.run(self.stage, "sft", "http://127.0.0.1:9000/v1", None)

    def test_prepare_rejects_wrong_model_or_corrupt_weights(self):
        config = self.adapter/sv.checkpoint.CONFIG
        rt.save_json(config, dict(rt.read_json(config), base_revision="different"))
        with self.assertRaisesRegex(ValueError, "base revision"):
            sv.prepare(self.stage, self.adapter)
        (self.adapter/sv.checkpoint.WEIGHTS).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "adapter checksum"):
            sv.prepare(self.stage, self.adapter)
        self.assertFalse(self.stage.exists())

    def test_historical_replay_plan_remains_identical(self):
        sv.prepare(self.stage)
        self.assertIsNone(sv.validate_plan(self.stage, live=True)["student"]["budgets"]["episode_tokens"])
        rt.save_json(self.stage/"plan.json", sv.build_plan(uncapped=False))
        self.assertEqual(rt.digest((self.stage/"plan.json").read_bytes()), sv.REFERENCE["plan_sha256"])
        self.assertEqual(sv.validate_plan(self.stage)["student"]["budgets"]["episode_tokens"], 100000)

    def test_uncapped_plan_keeps_context_limit_and_pairs_two_live_arms(self):
        sv.prepare(self.stage, self.adapter)
        plan = sv.validate_plan(self.stage, live=True)
        original = sv.build_plan()
        self.assertEqual(plan["protocol"], original["protocol"])
        self.assertEqual(plan["sessions"], original["sessions"])
        self.assertEqual(plan["student"]["budgets"], {
            **original["student"]["budgets"], "episode_tokens": None})
        self.assertNotIn("historical_comparison", plan)
        self.assertEqual(output_budget(30720, 200000), (2048, None))
        self.assertEqual(output_budget(30721, 100000, None), (0, "context_length"))
        self.assertEqual(output_budget(30720, 100000, 100000), (0, "token_budget"))

    def test_larger_context_only_changes_paired_window(self):
        sv.prepare(self.stage, self.adapter, context_tokens=65536)
        plan = sv.validate_plan(self.stage, live=True)
        reference = sv.candidate_plan(rt.read_json(self.adapter/sv.checkpoint.CONFIG), sv.source_hashes(), uncapped=True)
        reference["student"]["budgets"]["context_tokens"] = 65536
        self.assertEqual(plan, reference)
        self.assertEqual(output_budget(63488, 200000, None, 65536), (2048, None))
        self.assertEqual(output_budget(63489, 200000, None, 65536), (0, "context_length"))

    def test_4096_generation_reserves_requested_space_in_paired_window(self):
        sv.prepare(self.stage, self.adapter, context_tokens=65536, generation_tokens=4096)
        plan = sv.validate_plan(self.stage, live=True)
        self.assertEqual(plan["student"]["budgets"], {
            "episode_tokens": None, "context_tokens": 65536, "generation_tokens": 4096})
        self.assertEqual(output_budget(61440, 200000, None, 65536, 4096), (4096, None))
        self.assertEqual(output_budget(61441, 200000, None, 65536, 4096), (0, "context_length"))

    def test_report_compares_complete_paired_rows_only(self):
        sv.prepare(self.stage, self.adapter)
        archive = rt.ROOT/"datasets/results/sft/budget-validation/32k-2048"
        sources = rt.read_json(archive/"sources.json")
        config = rt.read_json(self.adapter/sv.checkpoint.CONFIG)
        rt.save_json(self.stage/"plan.json", sv.candidate_plan(config, sources, uncapped=False))
        rt.save_json(self.stage/"sources.json", sources)
        shutil.copyfile(sv.FIRST_PAIRS, self.stage/"historical-pairs.csv")
        plan = sv.validate_plan(self.stage)
        with sv.FIRST_PAIRS.open(newline="") as stream:
            history = list(csv.DictReader(stream))
        scores = {r["task_id"]: {"pass": False, "checker": r["sft_checker"] == "True",
                                  "passed_checks": int(r["sft_passed_checks"]), "stop_reason": r["sft_failure_reason"]}
                  for r in history}
        gained = next(r for r in history if r["sft_checker"] == "True")
        scores[gained["task_id"]].update({"pass": True, "stop_reason": "completed"})
        with patch.object(sv, "scored_tasks", return_value=scores), patch("builtins.print"):
            summary = sv.report_candidate(self.stage, plan)
            self.assertEqual((summary["tasks"], summary["formal_passes"], summary["original_checker_passes"]), (60, 1, 6))
            self.assertEqual(summary["paired_comparison"]["sft"]["pass"]["gained"], 1)
            self.assertEqual(summary["passed_leaf_checks"], 87)
            self.assertEqual(summary["stop_reasons"]["completed"], 1)
            self.assertEqual(len(list(csv.DictReader((self.stage/"pairs.csv").read_text().splitlines()))), 60)
            del scores[gained["task_id"]]
            with self.assertRaisesRegex(ValueError, "exact registered tasks"):
                sv.report_candidate(self.stage, plan)
