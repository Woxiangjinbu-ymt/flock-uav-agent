"""Research protocol controls must resist silent scope and attribution changes."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("freeze_harness_validation", ROOT / "scripts/freeze_harness_validation.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def plan():
    return json.loads((ROOT / module.PLAN).read_text())


def test_plan_preserves_all_tasks_and_counterbalances_each_pair(plan):
    slots = module.natural_slots(plan)
    assert len(slots) == len({s["slot_id"] for s in slots}) == 72
    for task in plan["natural_task_ids"]:
        rows = [s for s in slots if s["task_id"] == task]
        assert len(rows) == 4
        assert [s["arm"] for s in rows[:2]] == list(reversed([s["arm"] for s in rows[2:]]))
        assert {s["execution_status"] for s in rows} == {"not_authorized_not_run"}


def test_scheduler_arms_hold_all_other_capabilities_fixed(plan):
    arms = module.mechanism_arms(plan)
    left, right = (copy.deepcopy(arms[name]) for name in ("failure", "applicability"))
    assert left.pop("scheduler") == "failure"
    assert right.pop("scheduler") == "applicability"
    assert left == right and left["execution_contract"] == "task_execution_state_v1"


@pytest.mark.parametrize("key,value", [
    ("online_authorized_executions", 1), ("online_authorized_executions", False),
    ("teacher_collection", True), ("training", True), ("natural_repetitions", 3),
    ("model_identity", "implicitly-selected-provider"), ("paid_batch_budget", "2.00"),
    ("zero_opportunity_rate", 0), ("system_comparison_kind", "pure_scheduler_ablation"),
    ("guarded_macro", "novel_algorithm"),
    ("development_study_manifest_sha256", "0" * 64),
])
def test_no_implicit_online_authority_or_changed_research_interpretation(plan, key, value):
    plan[key] = value
    with pytest.raises(ValueError):
        module.validate_plan(plan)


@pytest.mark.parametrize("change", ["drop_task", "duplicate_task", "drop_cost", "drop_negative", "memory_v7"])
def test_cannot_drop_denominators_costs_or_negative_controls(plan, change):
    if change == "drop_task":
        plan["natural_task_ids"].pop()
    elif change == "duplicate_task":
        plan["natural_task_ids"][-1] = plan["natural_task_ids"][0]
    elif change == "drop_cost":
        plan["secondary_metrics"].remove("physical_action_attempts")
    elif change == "drop_negative":
        plan["fault_categories"].remove("nontransferable_failure")
    else:
        plan["candidate"]["execution_memory"] = "execution_memory_v7"
    with pytest.raises(ValueError):
        module.validate_plan(plan)


def test_duplicate_json_fields_are_rejected(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"online_authorized_executions": 0, "online_authorized_executions": 1}')
    with pytest.raises(ValueError, match="Duplicate"):
        module.read(path)


def test_review_packet_detects_manifest_and_document_changes(tmp_path, monkeypatch):
    documents = {"docs/method.md": b"finite method contract"}
    packet = {"documents": {"docs/method.md": module.sha(documents["docs/method.md"])},
        "source_sha256": {"agent.py": "original"}}
    monkeypatch.setattr(module, "build_packet", lambda root=ROOT: (copy.deepcopy(packet), documents))
    output = tmp_path / "frozen"
    module.prepare(output)
    assert module.verify(output) == packet
    with pytest.raises(FileExistsError):
        module.prepare(output)
    packet["source_sha256"]["agent.py"] = "changed"
    with pytest.raises(ValueError, match="differs"):
        module.verify(output)
    packet["source_sha256"]["agent.py"] = "original"
    (output / "documents/docs/method.md").write_bytes(b"altered claim")
    with pytest.raises(ValueError, match="specification changed"):
        module.verify(output)
