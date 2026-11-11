"""Only a successful public same-session reading above 95% can end a search."""
from types import SimpleNamespace

import pytest

from flockcue.harness.core import SkillExit
from flockcue.harness.skills import public_search
from flockcue.tools.blackboard import PerceptionBlackboard


def start():
    frame = SimpleNamespace(state={}, scene_id="test", scene_epoch=0, plan_id="plan")
    skill = public_search(frame, PerceptionBlackboard("test"), static_obstacle_contract=True)
    assert next(skill).tool == "get_mission_progress"
    return skill, frame


def receipt(progress):
    return {"session_id": "test", "source_endpoint": "/sessions/current",
            "scope": "public_session_aggregate_not_full_task_verdict",
            "progress_percentage": progress}


def test_threshold_requires_strictly_more_than_95():
    skill, frame = start()
    with pytest.raises(StopIteration):
        skill.send(receipt(96))
    assert frame.state["public_stop_condition"] == "session_progress_strictly_above_95"
    skill, _ = start()
    assert skill.send(receipt(95)).tool == "list_drones"


@pytest.mark.parametrize("bad", [{"partial_success": True}, {"session_id": "previous"}, {"status": "error"}])
def test_rejects_uncertain_or_out_of_scope_progress(bad):
    skill, _ = start()
    with pytest.raises(SkillExit, match="public_progress_unavailable"):
        skill.send({**receipt(100), **bad})
