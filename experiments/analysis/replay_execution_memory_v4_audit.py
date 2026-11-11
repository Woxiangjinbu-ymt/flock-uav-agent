#!/usr/bin/env python3
"""Replay sealed replies after an audit-only length repair; never call a model."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent4drone"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def replay(study, output):
    study, output = study.resolve(), output.resolve()
    seal_path = output / "pre-repair-seal.json"
    seal = read(seal_path)
    if not seal["pre_repair_integrity_passed"]:
        raise ValueError("Original campaign must pass integrity before repair")
    mismatches = [name for name, expected in seal["files"].items() if sha(ROOT / name) != expected]
    if mismatches:
        raise ValueError(f"Sealed original files changed: {mismatches}")
    manifest = read(study / "manifest.json")
    changed_sources = [name for name, expected in manifest["sources"].items() if sha(ROOT / name) != expected]
    allowed = {"agent4drone/execution_memory_v4.py", "agent4drone/tests/test_execution_memory_v4.py"}
    if set(changed_sources) != allowed:
        raise ValueError(f"Unexpected post-study edits: {changed_sources}")
    original_path = study / "frozen-source/agent4drone/execution_memory_v4.py"
    current_path = ROOT / "agent4drone/execution_memory_v4.py"
    old_source = original_path.read_text()
    expected = old_source.replace('len(plan["reason"]) <= 240', 'len(plan["reason"]) <= 1024')
    if old_source == expected or current_path.read_text() != expected:
        raise ValueError("Only the planned reason length limit may change")
    spec = importlib.util.spec_from_file_location("memory_v4_frozen_audit", original_path)
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    from execution_memory_v4 import audit_query_decision

    totals = {arm: {version: Counter() for version in ("original", "repaired")} for arm in ("v2", "v3", "v4")}
    rows, inputs = [], {str(seal_path): sha(seal_path), str(current_path): sha(current_path),
                       str(original_path): sha(original_path), str(Path(__file__).resolve()): sha(Path(__file__))}
    for trial in read(study / "schedule.json"):
        label = f"{trial['case_id']}-r{trial['repeat']}-{trial['arm']}"
        folder = study / "trials" / label
        for saved in read(folder / "decision-audit.json"):
            ordinal = saved["ordinal"]
            reply_path = folder / f"response-{ordinal}-original.json"
            capture_path = folder / f"request-{ordinal}-decision-context.json"
            reply, capture = read(reply_path), read(capture_path)
            for path in (reply_path, capture_path, folder / "decision-audit.json"):
                inputs[str(path)] = sha(path)
            original = frozen.audit_query_decision(capture["context"], reply["content"], reply["tool_calls"])
            if original != saved["audit"]:
                raise ValueError(f"Frozen audit does not reproduce original: {label}/{ordinal}")
            repaired = audit_query_decision(capture["context"], reply["content"], reply["tool_calls"])
            same = original == repaired
            if original["parse_status"] != "invalid" and not same:
                raise ValueError(f"An existing valid/missing audit changed: {label}/{ordinal}")
            if not same and not (original["parse_status"] == "invalid" and repaired["parse_status"] == "valid"):
                raise ValueError(f"Unexpected audit difference: {label}/{ordinal}")
            for version, audit in (("original", original), ("repaired", repaired)):
                counter = totals[trial["arm"]][version]
                counter["responses"] += 1
                counter["parse_" + audit["parse_status"]] += 1
                for declaration in audit["declarations"]:
                    counter["declarations"] += 1
                    counter["structured_" + declaration["observed_change_support"]["status"]] += 1
            rows.append({"trial": label, "ordinal": ordinal, "unchanged": same,
                         "original": original, "repaired": repaired})
    changed = [row for row in rows if not row["unchanged"]]
    result = {
        "provider_calls": 0, "executed_tools": 0, "replayed_responses": len(rows),
        "scope": "Post-study audit parsing only. Original actions, success, cost and invalid rates remain unchanged.",
        "original_files_unchanged": True, "sealed_file_count": len(seal["files"]),
        "original_audits_reproduced_exactly": True, "existing_valid_and_missing_audits_unchanged": True,
        "only_execution_source_change": "planned_action.reason length 240 -> 1024; prompt and tool control unchanged",
        "changed_current_sources": changed_sources, "current_source_check_matches_original_manifest": False,
        "original_archived_execution_sources_unchanged": True,
        "totals": totals, "changed_responses": [{"trial": row["trial"], "ordinal": row["ordinal"]} for row in changed],
        "free_text_factually_verified": False, "rows": rows, "input_sha256": inputs,
    }
    (output / "audit-replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return {"replayed_responses": len(rows), "changed_audits": len(changed), "provider_calls": 0,
            "sealed_originals_unchanged": True, "totals": totals}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.study, args.output), ensure_ascii=False))
