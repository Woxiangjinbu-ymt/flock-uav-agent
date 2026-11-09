"""Replay the Mac rehearsal from a materialized 29-task dev study using the public CLI."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD_RUN = ROOT / "outputs/flockcue-g1g2-mac-acceptance-20261108/dev-run"
G3 = ROOT / "docs/results/thinking-events-g3-20261108/run-01/g3.json"


def verify(study):
    def run(*args, success=True):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts"), "HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": ""}
        result = subprocess.run([sys.executable, "-m", "thinking_events", *map(str, args)], cwd=ROOT,
                                env=env, capture_output=True, text=True)
        if (result.returncode == 0) != success:
            raise ValueError(f"Unexpected exit from {args[0]}: {result.stderr}")

    def same(left, right):
        if left.read_bytes() != right.read_bytes():
            raise ValueError(f"Delivery differs: {right.name}")

    def read(path):
        return json.loads(path.read_bytes())

    with TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prepared, simulated = tmp / "prepared", tmp / "simulated"
        run("prepare-dev", "--study", study, "--output", prepared)
        for name in ("campaign.json", "campaign.sha256", "plan-slots.jsonl"):
            same(prepared / name, HERE / name)
        run("run-dev", "--run", prepared, "--g3", G3)
        same(prepared / "dev-readiness.json", HERE / "dev-readiness.json")
        run("simulate-paired", "--run", prepared, "--output", simulated)
        for path in simulated.rglob("*"):
            if path.is_file():
                same(path, HERE / path.relative_to(simulated))
        plan = [json.loads(line) for line in (prepared / "plan-slots.jsonl").read_bytes().splitlines()]
        counts = Counter((r["model"], r["arm"]) for r in plan)
        assert counts == {(m, a): 29 for m in ("Qwen3.5-2B", "Qwen3-4B") for a in ("on", "off")}
        assert len({r["scene_id"] for r in plan}) == 5 and len({r["task_id"] for r in plan}) == 29
        main = {r["slot_id"]: (r["task_id"], r["arm"]) for r in plan if r["model"] == "Qwen3.5-2B"}
        reports = sorted((HERE / "cases").iterdir())
        for case in reports:
            out = tmp / case.name
            run("report", "--run", prepared, "--results", case / "results.json", "--output", out)
            for name in ("gates.json", "REPORT_CN.md"):
                same(out / name, case / name)
            # Independently count the four matched cells directly from the saved synthetic checker replies.
            scores = {}
            for row in read(case / "results.json")["observations"]:
                if row["slot_id"] in main and row["status"] == "scored" and row["result"]["service_ok"]:
                    scores[main[row["slot_id"]]] = row["result"]["synthetic_checker"]["passed"]
            cells = Counter((scores[t, "on"], scores[t, "off"]) for t, a in scores if a == "on" and (t, "off") in scores)
            gate = read(out / "gates.json")
            model = gate["by_model"]["Qwen3.5-2B"]
            assert model["four_cells"] == {"both_success": cells[True, True], "only_on": cells[True, False],
                                          "only_off": cells[False, True], "both_failure": cells[False, False]}
            assert model["missing_pairs"] == 29 - sum(cells.values())
            assert gate["scope"] == "synthetic" and gate["eligible_for_collection"] is False
        example = read(HERE / "cases/pass/results.json")
        bad = tmp / "bad.json"
        for mismatch in ("wrong_plan", "duplicate_slot", "wrong_checker", "wrong_scope"):
            broken = json.loads(json.dumps(example))
            if mismatch == "wrong_plan":
                broken["plan_sha256"] = "0" * 64
            elif mismatch == "duplicate_slot":
                broken["observations"].append(broken["observations"][0])
            elif mismatch == "wrong_checker":
                broken["observations"][0]["result"]["passed"] = not broken["observations"][0]["result"]["passed"]
            else:
                broken["schema"] = "formal"
            bad.write_text(json.dumps(broken))
            run("report", "--run", prepared, "--results", bad, "--output", tmp / "invalid", success=False)
            assert not (tmp / "invalid").exists()
        slots = [json.loads(line)["status"] for line in (prepared / "slots.jsonl").read_bytes().splitlines()]
        assert Counter(slots) == {"unstarted": 116}
        return {"replayed_cases": len(reports), "four_arms_per_case": dict(sorted(
                    (f"{m}/{a}", n) for (m, a), n in counts.items())),
                "formal_slots_unstarted": len(slots), "invalid_inputs_rejected": 4,
                "plan_sha256": hashlib.sha256((prepared / "plan-slots.jsonl").read_bytes()).hexdigest(),
                "formal_G1": "unmeasured", "formal_G2": "unmeasured", "gpu_launches": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=OLD_RUN / "study")
    print(json.dumps(verify(parser.parse_args().study), ensure_ascii=False, indent=2))
