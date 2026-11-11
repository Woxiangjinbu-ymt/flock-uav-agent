"""Check the local FlockCue baseline or replay its two scripted hover cases."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import general_agent
from validate_harness_episode import check_hashes


def read(path):
    return json.loads(path.read_bytes())


def checked_reference(lock):
    campaign = ROOT / lock["reference_campaign"]
    check_hashes(campaign, {"campaign-seal.json": lock["campaign_seal_sha256"]})
    sealed = read(campaign / "campaign-seal.json")["files"]
    consumed = ("runs/action/manifest.json", "runs/action/summary.json")
    check_hashes(campaign, {path: sealed[path] for path in consumed})
    check_hashes(ROOT, lock["dependency_files"])
    check_hashes(ROOT, {lock["profile"]: sealed["selection-source/" + lock["profile"]]})
    reference = read(campaign / "runs/action/manifest.json")
    if platform.python_version() != reference["python"].split()[0]:
        raise ValueError("Use Python " + reference["python"].split()[0] + " from the project environment")
    return reference


def prepare_locked(lock, reference, output, *, current_layout=False):
    study = ROOT / lock["reference_campaign"] / "studies/action"
    source_hashes = dict(reference["sources"])
    sealed = {}
    # Legacy handoffs are self-contained copies with the original flat source
    # layout. In this checkout verify both sides of every relocated runner.
    registry_path = Path(__file__).with_name("layout-source-lock.json")
    if registry_path.is_file():
        from flockcue.history import sealed_source, source_registry
        relocated = source_registry()["sources"]
        for name in source_hashes.keys() & relocated.keys():
            if source_hashes[name] != relocated[name]["before_sha256"]:
                raise ValueError("Layout lineage differs from the original campaign: " + name)
            if current_layout:
                source_hashes[name] = relocated[name]["after_sha256"]
            else:
                sealed[name] = sealed_source(ROOT, name, source_hashes[name])
    reference = {**reference, "sources": source_hashes}
    kwargs = {"sealed_sources": sealed} if sealed else {}
    current = general_agent.prepare(ROOT / lock["profile"], study, ROOT / lock["cases"], output, **kwargs)
    # Host paths and Python build metadata vary between checkouts. Compare all
    # other manifest fields, including complete source/input maps and budgets.
    host_fields = {"workspace", "study", "origin_study", "python_executable", "python"}
    for key in sorted((set(current) | set(reference)) - host_fields):
        if key not in current or key not in reference or current[key] != reference[key]:
            detail = key
            if key in {"sources", "inputs"}:
                old, new = reference.get(key, {}), current.get(key, {})
                different = {
                    "added": sorted(new.keys() - old.keys()),
                    "removed": sorted(old.keys() - new.keys()),
                    "changed": sorted(name for name in old.keys() & new.keys() if old[name] != new[name]),
                }
                detail += " " + json.dumps(different)
            raise ValueError("Current harness differs from the frozen reference: " + detail)
    general_agent.load_manifest(output)


def validate_output(output):
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new run directory: " + str(output))
    output = output.resolve()
    if output.is_relative_to(ROOT):
        if output.parent != ROOT / "experiments/runs" and not output.is_relative_to(ROOT / "tmp"):
            raise ValueError("Inside the repository use experiments/runs/<new-name> or tmp/<new-name>")
    for parent in output.parents:
        if (parent / "campaign-seal.json").exists() or (parent / ".attempt.lock").exists():
            raise ValueError("Output would modify an existing run: " + str(parent))
    return output


def outcomes(summary):
    if (summary["status"] != "completed" or summary["worker_count"] != 2
            or summary["scorer_count"] != 2 or summary["provider_calls"] != 0):
        raise ValueError("The two local episodes did not complete without provider calls")
    return [{
        "trial": row["trial_id"],
        "public_completion": row["public_evidence"]["completion_assessment"]["status"],
        "completion_claim_accepted": row["public_evidence"]["completion_assessment"]["completion_claim_accepted"],
        "original_checker_passed": row["independent_evaluation"]["passed"],
        "archive_complete": row["run"]["archive_complete"],
    } for row in summary["trials"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check sealed sources, inputs and dependency files")
    smoke = commands.add_parser("smoke", help="replay both cases locally using the locked harness")
    smoke.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = validate_output(args.output) if args.command == "smoke" else None
    lock = read(Path(__file__).with_name("baseline.lock.json"))
    reference = checked_reference(lock)
    with TemporaryDirectory(prefix="uav-harness-check-") as scratch:
        prepare_locked(lock, reference, Path(scratch) / "check", current_layout=__package__ == "flockcue")
    result = {"baseline": reference["agent_profile"]["profile_id"], "profile_sha256": reference["profile_sha256"],
              "source_files": len(reference["sources"]), "provider_calls": 0,
              "source_layout": "current" if __package__ == "flockcue" else "published_flat"}
    if output is not None:
        prepare_locked(lock, reference, output, current_layout=__package__ == "flockcue")
        # The existing 'run' command calls run_prepared. Launch the sealed
        # coordinator directly, leaving one machine-readable result on stdout.
        subprocess.run(
            [sys.executable, str(output / "source/scripts/general_agent.py"), "run", "--run", str(output)],
            check=True, stdout=subprocess.PIPE,
        )
        verified = general_agent.verify(output)
        expected = outcomes(read(ROOT / lock["reference_campaign"] / "runs/action/summary.json"))
        actual = outcomes(read(output / "summary.json"))
        if actual != expected:
            raise ValueError("New smoke outcomes differ from the sealed two-case reference")
        result.update(output=str(output), verification=verified, outcomes=actual)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
