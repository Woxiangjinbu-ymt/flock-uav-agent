"""Rebuild the current plan and replay the 11/09 delivery with its sealed CLI.

Use --archive to locate a restored copy of the original 11/09 dev-run.
All regenerated manifests, journals and reports are written in a temporary directory.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD_RUN = ROOT / "outputs/flockcue-g1g2-mac-acceptance-20261108/dev-run"
ARCHIVE = ROOT / "outputs/flockcue-g1g2-paired-mac-20261109/dev-run"
G3 = ROOT / "docs/results/thinking-events-g3-20261108/run-01/g3.json"


def _copy_inventory(source, destination, inventory):
    for name, expected in inventory.items():
        origin = source / name
        if Path(name).is_absolute() or ".." in Path(name).parts or not origin.resolve().is_relative_to(source.resolve()):
            raise ValueError(f"Invalid archive path: {name}")
        raw = origin.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Historical file differs: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def verify(study, archive=ARCHIVE):
    def run(*args, source=ROOT, success=True):
        env = {**os.environ, "PYTHONPATH": str(source / "scripts"), "PYTHONDONTWRITEBYTECODE": "1",
               "HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": ""}
        result = subprocess.run([sys.executable, "-m", "thinking_events", *map(str, args)], cwd=source,
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
        current, prepared, simulated = tmp / "current", tmp / "historical", tmp / "simulated"
        run("prepare-dev", "--study", Path(study).resolve(), "--output", current)
        same(current / "plan-slots.jsonl", HERE / "plan-slots.jsonl")
        raw = (HERE / "campaign.json").read_bytes()
        original_sha = hashlib.sha256(raw).hexdigest()
        if (original_sha != (HERE / "campaign.sha256").read_text().strip()
                or original_sha != read(HERE / "dev-readiness.json")["campaign_sha256"]):
            raise ValueError("Historical campaign seal differs")
        campaign = json.loads(raw)
        prepared.mkdir()
        for name in ("campaign.json", "campaign.sha256", "plan-slots.jsonl"):
            same(archive / name, HERE / name)
            shutil.copyfile(archive / name, prepared / name)
        _copy_inventory(archive / "source", prepared / "source", campaign["sources"])
        _copy_inventory(current / "study", prepared / "study", campaign["inputs"])
        # Use the original journal and let the historical entry point validate its plan, statuses and cost.
        for name in ("slots.jsonl", "cost-ledger.json"):
            shutil.copyfile(archive / name, prepared / name)
        # The historical reporting CLI locates G3 relative to its own package root.
        # Give it a separate checked tree so the worker snapshot inventory stays exact.
        cli = tmp / "historical-cli"
        shutil.copytree(prepared / "source", cli)
        relative_g3 = G3.relative_to(ROOT)
        _copy_inventory(ROOT, cli, {str(relative_g3): campaign["g3_reference_sha256"]})
        _copy_inventory(G3.parent, cli / relative_g3.parent, read(cli / relative_g3)["inputs_sha256"])
        run("run-dev", "--run", prepared, "--g3", G3, source=cli)
        same(prepared / "dev-readiness.json", HERE / "dev-readiness.json")
        expected_files = {Path("summary.json"), Path("REPORT_CN.md")}
        expected_files.update(p.relative_to(HERE) for p in (HERE / "cases").rglob("*") if p.is_file())
        for source, campaign_dir, output in ((cli, prepared, simulated), (ROOT, current, tmp / "current-sim")):
            run("simulate-paired", "--run", campaign_dir, "--output", output, source=source)
            actual_files = {p.relative_to(output) for p in output.rglob("*") if p.is_file()}
            if actual_files != expected_files:
                raise ValueError("Simulated delivery files differ from the archive")
            for name in expected_files:
                same(output / name, HERE / name)
        plan = [json.loads(line) for line in (prepared / "plan-slots.jsonl").read_bytes().splitlines()]
        counts = Counter((r["model"], r["arm"]) for r in plan)
        assert counts == {(m, a): 29 for m in ("Qwen3.5-2B", "Qwen3-4B") for a in ("on", "off")}
        assert len({r["scene_id"] for r in plan}) == 5 and len({r["task_id"] for r in plan}) == 29
        main = {r["slot_id"]: (r["task_id"], r["arm"]) for r in plan if r["model"] == "Qwen3.5-2B"}
        reports = sorted((HERE / "cases").iterdir())
        for case in reports:
            out = tmp / case.name
            run("report", "--run", prepared, "--results", case / "results.json", "--output", out, source=cli)
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
            for source, campaign_dir in ((cli, prepared), (ROOT, current)):
                run("report", "--run", campaign_dir, "--results", bad, "--output", tmp / "invalid", source=source,
                    success=False)
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
    parser.add_argument("--archive", type=Path, default=ARCHIVE, help="sealed original 11/09 dev-run")
    args = parser.parse_args()
    print(json.dumps(verify(args.study, args.archive), ensure_ascii=False, indent=2))
