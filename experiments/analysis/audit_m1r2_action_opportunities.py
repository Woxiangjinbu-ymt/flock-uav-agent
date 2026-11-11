"""Read-only opportunity audit for the frozen M1-R2 fast100 attempt.

The row classification uses task text and public tool/event records. Frozen
checker leaves are attached only after those records are projected, as an
offline outcome explanation. This script never calls the server or a model.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAIR_FILE = ROOT / "docs/results/m1-revision2-fast100-20261007/paired-tasks.json"
RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
OUT = ROOT / "docs/audit/m1r2-action-opportunities-20261009/rows.json"
ACTION_MARKERS = ("move", "navigate", "take_off", "land")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_event(position):
    return position.get("evidence_id") if isinstance(position, dict) else None


def first_visible_clues(injections):
    """First evidence actually injected into a model message for each ref.

    Snapshot omissions are recorded separately by the caller. This does not
    assume that the final position evidence is the first localization.
    """
    first = {}
    for injection in injections:
        text = injection["text"]
        snapshot = json.loads(text[text.index("{"):])
        for item in snapshot["items"]:
            entry = first.setdefault(item["id"], {})
            for state, field in (("localized", "position"),
                                 ("reported_only", "report")):
                if item["knowledge_state"] != state or state in entry:
                    continue
                evidence = item.get(field) or {}
                entry[state] = {
                    "source": evidence.get("evidence_id"),
                    "message_index": injection["message_index"],
                }
    return first


def classify_failure(row, visits, refs, failed_checks):
    if row["m1r2_pass"]:
        return "passed_control"
    if any(c["endpoint"].endswith("/task_progress") for c in failed_checks):
        return "aggregate_progress_gap"
    if any(q["knowledge_state"] == "unknown" for q in visits):
        return "named_target_unlocalized"
    if ("coverage" in row["name"].lower()
            and any(q["relation"] == "unparsed_reference" for q in refs)):
        return "area_target_actor_gap"
    return "coordinate_route_or_formation_gap"


def main():
    pairs = read(PAIR_FILE)
    assert len(pairs) == 100
    rows = []
    for pair in pairs:
        trace_path = ROOT / pair["m1r2_trace"]
        trace = read(trace_path)
        assert trace["command"] and trace["task_progress"]["events"] is not None
        refs = trace["task_progress"]["task_evidence"]["requirements"]
        injections = trace["task_progress"]["task_evidence"]["injections"]
        visible_clues = first_visible_clues(injections)
        visits = [q for q in refs if q["relation"] == "visit"]
        events = trace["task_progress"]["events"]
        checks = read(RUN / pair["session_id"] / f"{pair['task_id']}.checks.json")
        failed = [
            {"endpoint": c["endpoint"], "parameters": c.get("parameters"),
             "actual": c.get("actual")}
            for c in checks if c.get("type") == "leaf" and c.get("result") is False
        ]
        action_actors = {
            event.get("args", {}).get("drone_id")
            for event in events
            if any(marker in event["tool_name"] for marker in ACTION_MARKERS)
            and event.get("args", {}).get("drone_id")
        }
        required_actors = {q["actor_id"] for q in visits if q.get("actor_id")}
        public_progress = trace["task_progress"]["task_evidence"].get(
            "progress_observations", [])
        row = {
            "session_id": pair["session_id"],
            "task_id": pair["task_id"],
            "name": pair["name"],
            "m0_pass": pair["m0_pass"],
            "r2_pass": pair["m1r2_pass"],
            "pair_vs_m0": pair["pair_vs_m0"],
            "r2_timeout": pair["m1r2_timeout"],
            "passed_checks": pair["m1r2_passed_checks"],
            "expected_checks": pair["expected_checks"],
            "command": trace["command"],
            "trace": pair["m1r2_trace"],
            "events": len(events),
            "tool_counts": dict(collections.Counter(e["tool_name"] for e in events)),
            "requirements": [
                {"target_name": q["target_name"], "relation": q["relation"],
                 "required_actor": q.get("required_actor"),
                 "actor_id": q.get("actor_id"),
                 "knowledge_state": q["knowledge_state"],
                 "execution_state": q["execution_state"],
                 "final_position_source": source_event(q.get("position")),
                 "first_model_visible_localization": visible_clues.get(q["id"], {}).get("localized"),
                 "first_model_visible_report": visible_clues.get(q["id"], {}).get("reported_only"),
                 "execution_evidence_id": q.get("execution_evidence_id"),
                 "report_source": (q.get("report") or {}).get("evidence_id")}
                for q in refs
            ],
            "action_actor_ids": sorted(action_actors),
            "required_actor_ids_for_explicit_visits": sorted(required_actors),
            "additional_action_actor_ids": sorted(action_actors - required_actors),
            "evidence_injection_count": len(injections),
            "evidence_snapshots_with_omissions": sum(
                bool(json.loads(item["text"][item["text"].index("{"):]).get("omitted"))
                for item in injections
            ),
            "public_progress_observations": len(public_progress),
            "last_public_progress_percent": public_progress[-1]["progress_percentage"] if public_progress else None,
            "failed_checks_offline_only": failed,
        }
        row["failure_group"] = classify_failure(pair, visits, refs, failed)
        rows.append(row)

    assert len(rows) == 100
    assert sum(r["r2_pass"] for r in rows) == 79
    assert collections.Counter(r["failure_group"] for r in rows if not r["r2_pass"]) == {
        "named_target_unlocalized": 6,
        "aggregate_progress_gap": 3,
        "area_target_actor_gap": 1,
        "coordinate_route_or_formation_gap": 11,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
