"""Materialize only authorized mechanism fixtures and verify them without a model."""
from __future__ import annotations
import sys
from pathlib import Path

if __name__ == "__main__" and Path(__file__).resolve().parent.name != "scripts":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import argparse
import copy
import json
import pickle

from harness_v2_world import ROOT, World, digest, sha, write_json
from harness_v2_cases import build_case, offline_run, checker


def prepare(study, plan):
    if (study / "manifest.json").exists():
        raise ValueError("Already frozen: fixture preparation is forbidden")
    fixtures = study / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    entries = json.loads((plan / "case_catalog.proposed.json").read_text())["mechanism"]
    rows = []
    for entry in entries:
        world = World()
        case = build_case(entry)
        world.import_session(case["scene"])
        snapshot = world.snapshot()
        timeline = world.bake_timeline()
        case["snapshot_sha256"] = sha(snapshot)
        case["state_sha256"] = world.state_digest()
        case["timeline_sha256"] = sha(timeline)
        seed = world.public_seed()
        case["public_startup_sha256"] = digest(seed)
        (fixtures / f"{case['case_id']}.pkl").write_bytes(snapshot)
        (fixtures / f"{case['case_id']}.timeline.pkl").write_bytes(timeline)
        write_json(fixtures / f"{case['case_id']}.json", case)
        pair = []
        for arm in ("applicability", "failure"):
            world.restore(snapshot)
            assert world.state_digest() == case["state_sha256"]
            assert digest(world.public_seed()) == case["public_startup_sha256"]
            world.restore(snapshot)
            result = offline_run(world, case, arm)
            pair.append(result)
            write_json(study / "offline-traces" / f"{case['case_id']}-{arm}.json", result)
        a, f = pair
        assert a["calls"] == f["calls"], case["case_id"] + ": scripted public receipts diverged"
        assert a["final"]["status"] == f["final"]["status"]
        assert a["final"]["reason"] == f["final"]["reason"]
        assert a["checker"] == f["checker"]
        assert a["calls"][:len(f["first_calls"])] == f["first_calls"]
        if case["family"].startswith("recoverable"):
            assert a["first"]["status"] == "completed" and f["first"]["status"] == "paused"
            assert a["checker"]["passed"]
            assert len(a["events"]) == case["event"]["count"]
        elif case["family"].startswith("valid"):
            assert a["first"]["status"] == f["first"]["status"] == "completed"
            assert a["checker"]["passed"]
        else:
            assert a["final"]["status"] == "needs_replan"
            assert not a["checker"]["passed"]
            if case["event"]["kind"] == "receipt_loss":
                assert len([c for c in a["calls"] if c["action"]]) == 1
                assert a["calls"][-1]["tool"] == "get_drone_status"
            if case["event"].get("count") == 17:
                assert a["final"]["reason"] == "local_repair_budget_exhausted"
                assert len(a["events"]) == 17
        # Prove every mutation, including private history and RNG, is discarded.
        world.server.session_controller.get_current_session_ref().request_history.append({"dirty": True})
        world.rng.random()
        world.id_rng.random()
        world.advance(37.25)
        world.restore(snapshot)
        assert world.state_digest() == case["state_sha256"]
        assert not checker(world, case)["passed"]
        rows.append({"case_id": case["case_id"], "passed": True,
                     "a_first": a["first"]["status"], "f_first": f["first"]["status"],
                     "final": a["final"]["status"], "reason": a["final"]["reason"],
                     "same_all_scripted_public_receipts": True, "events": len(a["events"])})
        print(json.dumps(rows[-1]), flush=True)
    blocks = [b for b in json.loads((plan / "schedule.proposed.json").read_text())["blocks"]
              if b["stage"] == "mechanism"]
    assert len(blocks) == 48
    for entry in entries:
        pair = sorted([b for b in blocks if b["case_id"] == entry["case_id"]], key=lambda b: b["repeat"])
        assert [t["arm"] for t in pair[0]["trials"]] == [t["arm"] for t in pair[1]["trials"]][::-1]
        assert pair[0]["worker"] != pair[1]["worker"]
    write_json(study / "schedule.json", {"stage": "mechanism_only", "workers": 2,
                "executions": 96, "blocks": blocks, "authorized": True})
    report = {"passed": True, "provider_calls": 0, "cases": 24, "offline_executions": 48,
              "rows": rows, "schedule_balanced": True, "full100_authorized": False}
    write_json(study / "fixture-preflight.json", report)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--study", type=Path, required=True)
    p.add_argument("--plan", type=Path, default=ROOT / "experiments/minimal-harness-v2-plan")
    args = p.parse_args()
    prepare(args.study, args.plan)
