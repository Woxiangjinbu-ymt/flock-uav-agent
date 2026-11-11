"""Read-only experiment audit; writes only this new report directory. No models."""
import collections
import difflib
import hashlib
import json
import math
from pathlib import Path

from relative_hint_probe import relative_contact_hint

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ATTEMPT = ROOT / "experiments/runs/20261006-214358-m1r-fast100-b4e9b6f3"
PREVIOUS = ROOT / "docs/results/m1-revision-fast100-20261006"
INPUTS = {}


def sha(data): return hashlib.sha256(data).hexdigest()


def raw(path):
    content = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = sha(content)
    return content


def read(path): return json.loads(raw(path))


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    suite = read(ATTEMPT / "suite-seal.json")["files"]
    actual = {str(p.relative_to(ATTEMPT)): sha(raw(p)) for p in ATTEMPT.rglob("*")
              if p.is_file() and p.name != "suite-seal.json"}
    assert actual == suite, "Original failed snapshot changed"
    preserved_reports = {}
    for dirname in ("m1-progress-fast100-20261006", "m1-revision-offline-20261006", "m1-revision-fast100-20261006"):
        directory = ROOT / "docs/results" / dirname
        hashes = read(directory / "artifact-sha256.json")
        for name, checksum in hashes.items():
            assert sha(raw(directory / name)) == checksum, (dirname, name)
        preserved_reports[dirname] = len(hashes)
    scene_checks = {}
    for scene in read(ATTEMPT / "plan.json")["sessions"]:
        sid = scene["id"]
        seal = read(ATTEMPT / sid / "complete.json")
        changed = [p for p, h in seal["files"].items() if sha(raw(ATTEMPT / sid / p)) != h]
        scene_checks[sid] = {"files": len(seal["files"]), "changed": changed}
    assert scene_checks["ef36e27a"]["changed"] == ["metrics/reproduction-metrics.jsonl"]
    assert sum(len(x["changed"]) for x in scene_checks.values()) == 1
    telemetry = raw(ATTEMPT / "ef36e27a/metrics/reproduction-metrics.jsonl")
    expected = read(ATTEMPT / "ef36e27a/complete.json")["files"]["metrics/reproduction-metrics.jsonl"]
    h, offset, found = hashlib.sha256(), 0, None
    for count, line in enumerate(telemetry.splitlines(keepends=True), 1):
        h.update(line); offset += len(line)
        if h.hexdigest() == expected:
            found = (count, offset); break
    assert found is not None
    suffix = telemetry[offset:]
    records = [json.loads(line) for line in suffix.splitlines()]
    assert len(records) == 1 and records[0]["event"] == "resource_sample"
    (OUT / "sealed-resource-prefix.jsonl").write_bytes(telemetry[:offset])
    (OUT / "late-resource-append.jsonl").write_bytes(suffix)
    write("seal-audit.json", {
        "original_run_accepted": False, "original_terminal_snapshot_unchanged": True,
        "terminal_files": len(suite), "scene_seals": scene_checks,
        "preserved_report_artifacts": preserved_reports,
        "prefix": {"bytes": offset, "lines": found[0], "sha256": expected, "matches_original_scene_seal": True},
        "suffix": {"bytes": len(suffix), "sha256": sha(suffix), "records": records},
        "recovery_scope": "derived copies only; no reseal, score change, resume or acceptance promotion",
    })
    pairs = read(PREVIOUS / "paired-tasks.json")
    cases = []
    for pair in pairs:
        if pair["pair_outcome_diagnostic"] != "L": continue
        case = {"pair": pair, "arms": {}}
        for arm in ("m0", "m1r"):
            trace = read(ROOT / pair[arm + "_trace"])
            directory = (ROOT / pair[arm + "_trace"]).parents[2]
            before = read(directory / (pair["task_id"] + ".before.json"))
            after = read(directory / (pair["task_id"] + ".after.json"))
            checks = read(directory / (pair["task_id"] + ".checks.json"))
            targets = {c["parameters"]["target_id"] for c in checks if c.get("parameters", {}).get("target_id")}
            selected = {}
            for label, snapshot in (("before", before), ("after", after)):
                selected[label] = [{k: t.get(k) for k in ("id", "name", "is_reached", "reached_by")}
                                   for t in snapshot["targets"] if not targets or t["id"] in targets]
            progress = trace.get("task_progress", {})
            evidence = progress.get("task_evidence", {})
            movements = [e for e in progress.get("events", []) if e["tool_name"].startswith(("move_", "navigate_"))]
            case["arms"][arm] = {
                "command": trace["command"], "command_id": trace["command_id"], "trace_status": trace["status"],
                "before_reached": sum(t["is_reached"] for t in before["targets"]),
                "after_reached": sum(t["is_reached"] for t in after["targets"]),
                "world_target_count_for_audit_only": len(before["targets"]),
                "checked_targets_for_audit_only": selected,
                "drone_name_map": {d["id"]: d["name"] for d in before["drones"]},
                "checks": checks, "output": trace["output"],
                "events": len(progress.get("events", [])), "toolchain_items": len(trace["tool_chain"]),
                "api_calls": len(trace["api_calls"]), "movement_status_counts": dict(collections.Counter(e["status"] for e in movements)),
                "requirements": evidence.get("requirements"), "injections": evidence.get("injections"),
            }
        cases.append(case)
    assert len(cases) == 3
    write("cases.json", cases)
    sequential = next(c for c in cases if c["pair"]["task_id"] == "c6e28e3b")
    last = read(ROOT / sequential["pair"]["m1r_trace"])
    matches, searched = [], 0
    def scan(value, source, evidence_id):
        if isinstance(value, dict):
            if value.get("name") in {"Fixed Target 55", "Fixed Target 31"}:
                matches.append([source, evidence_id, "entity", value["name"]])
            for child in value.values(): scan(child, source, evidence_id)
        elif isinstance(value, list):
            for child in value: scan(child, source, evidence_id)
        elif isinstance(value, str) and " - Reached target(s): " in value:
            for name in value.split(" - Reached target(s): ", 1)[1].split(", "):
                if name in {"Fixed Target 55", "Fixed Target 31"}:
                    matches.append([source, evidence_id, "contact_receipt", name])
    for path in sorted((ROOT / sequential["pair"]["m1r_trace"]).parent.glob("*.json")):
        tr = read(path)
        if tr["started_at"] > last["completed_at"]: continue
        searched += 1
        for event in tr["task_progress"]["events"]:
            scan(event["payload"], str(path.relative_to(ROOT)), event["evidence_id"])
    assert searched == 8 and not matches
    write("sequential-clue-audit.json", {"traces_through_current_task": searched,
          "scope": "saved observer entity names and positive contact receipts only; not all possible text",
          "searched_names": ["Fixed Target 55", "Fixed Target 31"], "matches": matches})
    prior = next(p for p in pairs if p["session_id"] == "35ce64d8" and p["task_id"] == "de294a89")
    trace = read(ROOT / prior["m1r_trace"])
    events = {e["evidence_id"]: e for e in trace["task_progress"]["events"]}
    hints = [relative_contact_hint(events[key], session_id=prior["session_id"], command_id=trace["command_id"])
             for key in ("e51", "e53")]
    assert all(hints)
    assert hints[0]["points"] == [[154.0, 115.0, 18.0], [154.0, 36.0, 18.0]]
    assert hints[1]["points"] == [[154.0, 36.0, 18.0], [154.0, 124.0, 18.0]]
    assert all(h["reported_names"] == ["Fixed Target 10", "Fixed Target 11"] for h in hints)
    sweep = next(c for c in cases if c["pair"]["task_id"] == "20742d37")
    refs = sweep["arms"]["m1r"]["requirements"]
    unresolved = [r for r in refs if r["knowledge_state"] == "reported_only"]
    assert len(unresolved) == 2 and all(r["report"]["localization_hint"] is None for r in unresolved)
    assert all(r["actor_id"] != hints[0]["source"]["actor_id"] for r in unresolved)
    # Ground truth is consulted ONLY AFTER hint extraction, for diagnostic
    # geometry. It is never passed to the candidate parser or agent.
    snapshot = read((ROOT / sweep["pair"]["m1r_trace"]).parents[2] / "20742d37.before.json")
    endpoint = hints[0]["points"][1]
    geometry = []
    for t in snapshot["targets"]:
        if t["name"] in hints[0]["reported_names"]:
            p = [t["position"][key] for key in ("x", "y", "z")]
            geometry.append({"name": t["name"], "audit_only_ground_truth": p,
                             "distance_from_e51_endpoint_3d_m": math.dist(endpoint, p)})
    assert all(x["distance_from_e51_endpoint_3d_m"] < 50 for x in geometry)
    write("relative-hint-replay.json", {
        "scope": "offline clue recovery only; no navigation/model replay or task-success claim",
        "source_trace": prior["m1r_trace"], "source_task": prior["task_id"],
        "original_events": [events[k] for k in ("e51", "e53")], "candidate_hints": hints,
        "new_search_hints_for_two_unresolved_assignments": True,
        "current_assignment_completed": False, "audit_only_geometry": geometry,
    })
    sources = ["scripts/reproduction_metrics.py", "scripts/reproduction_parallel.py", "scripts/tests/test_reproduction_metrics.py",
               "agent4drone/task_evidence.py", "agent4drone/uav_langchain_tools.py", "agent4drone/uav_api_client.py",
               "server/api/server.py", "server/controllers/drone_controller.py", "server/controllers/session_controller.py",
               "server/models/session.py"]
    patch = []
    for name in sources:
        current = raw(ROOT / name)
        frozen = raw(ATTEMPT / "runtime" / name)
        if name.startswith("scripts/"):
            patch.extend(difflib.unified_diff(frozen.decode().splitlines(True), current.decode().splitlines(True),
                         fromfile="frozen/" + name, tofile="workspace/" + name))
        else:
            assert current == frozen, "Agent/scoring code changed during seal-only repair"
    (OUT / "seal-fix.patch").write_text("".join(patch))
    write("input-sha256.json", INPUTS)
    print(json.dumps({"cases": len(cases), "hints": len(hints), "unchanged_attempt_files": len(suite),
                      "preserved_report_artifacts": preserved_reports, "input_hashes": len(INPUTS)}))


if __name__ == "__main__": main()
