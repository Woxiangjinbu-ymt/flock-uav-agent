"""Check transport hashes, then reproduce the A100 paired report on a CPU."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
OUTPUTS = ROOT / "outputs/sense-example-20261111"
HISTORICAL_RUN = ROOT / "outputs/flockcue-stage0-20261110/server/run"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_manifest(root, manifest):
    rows = manifest.read_text().splitlines()
    if not rows:
        raise ValueError(f"Empty digest inventory {manifest}")
    seen = set()
    for row in rows:
        digest, name = row.split("  ", 1)
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or path in seen or sha256(path) != digest:
            raise ValueError(f"Invalid inventory entry in {manifest}: {name}")
        seen.add(path)
    return len(seen)


def verify_online_events(work, pairs_file):
    # Compare the renderer's live decisions with the Mac replay of the generation IDs.
    plan = json.loads((work / "plan.json").read_bytes())
    pairs = {pair["pair_index"]: pair for pair in map(json.loads, pairs_file.read_text().splitlines())}
    observations = 0
    for slot in plan["slots"]:
        path = work / "receipts" / (slot["slot_key"] + ".json")
        receipt = json.loads(path.read_bytes()) if path.exists() else {}
        score = None
        if receipt.get("status") == "completed":
            response = receipt["response"]
            calls = (response["message"] or {}).get("tool_calls", [])
            score = int(response["status"] == "ok" and
                        any(call["function"]["name"] == "sense_nearby_entities" for call in calls))
        elif receipt.get("status") == "timeout":
            score = 0
        if score != pairs[slot["pair_index"]][slot["condition"]]["score"]:
            raise ValueError(f"Live and replayed primary events differ for {slot['slot_key']}")
        observations += score is not None
    return observations


def verify(archive, mac_prepared, model_path, report_dir):
    transferred = check_manifest(archive, archive / "payload.sha256")
    historical = check_manifest(HISTORICAL_RUN, archive / "verification/stage0-before.sha256")
    prepared = sha256(mac_prepared / "plan.json")
    if sha256(archive / "run/plan.json") != prepared:
        raise ValueError("Mac and A100 produced different experiment plans")
    subprocess.run([sys.executable, str(ROOT / "scripts/diagnose_sense_example.py"), "report",
                    "--work", str(archive / "run"), "--model-path", str(model_path),
                    "--output", str(report_dir)], check=True, stdout=sys.stderr)
    result_files = {}
    for name in ("pairs.jsonl", "summary.json", "REPORT_CN.md"):
        digest = sha256(report_dir / name)
        if sha256(archive / "report" / name) != digest:
            raise ValueError(f"Mac and A100 disagreed on {name}")
        result_files[name] = digest
    return {"transport_files_verified": transferred, "stage0_files_verified": historical,
            "payload_sha256": sha256(archive / "payload.sha256"),
            "mac_and_server_plan_sha256": prepared, "mac_and_server_reports_sha256": result_files,
            "live_and_mac_primary_events_compared": verify_online_events(archive / "run", report_dir / "pairs.jsonl")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=OUTPUTS / "server")
    parser.add_argument("--mac-prepared", type=Path, default=OUTPUTS / "mac-prepared")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, help="defaults to a sibling of the transferred report")
    args = parser.parse_args()
    result = verify(args.archive, args.mac_prepared, args.model_path, args.report_dir or args.archive / "mac-report")
    print(json.dumps(result, ensure_ascii=False, indent=2))
