"""Follow-up v9 only confirms completion near 95%; never hands off for stagnation."""
import pytest
from test_search_progress import PlateauWorld, many, no_network
from test_harness_shared_endpoint import make
from test_harness_observed_target import TargetWorld, make as target_runtime
from test_harness_flight_state import reject_without_effect
from test_minimal_harness import finish

V9 = "search_confirm_v9"


@pytest.mark.parametrize("cached", [0, 33, 94])
def test_below_95_has_no_extra_reads_and_preserves_full_exploration(many, cached):
    r,w,_ = make(PlateauWorld(lambda moves:cached,reaches=range(1,81)), revision=V9, scheduler="applicability")
    result,_ = finish(r,"public_search")
    assert result["reason"] == "round_limit_below_threshold" and w.moves == 80
    assert len([n for n,_ in w.calls if n=="get_current_session"]) == 9
    assert result["state"]["search_confirmation"]["extra_reads"] == []
    assert "search_progress" not in result["state"]


@pytest.mark.parametrize("cached,expected_moves", [(94,10),(95,1)])
def test_new_reach_only_confirms_early_after_cached_95(many,cached,expected_moves):
    r,w,_ = make(PlateauWorld(lambda moves:100 if moves else cached,reaches={1}), revision=V9,scheduler="applicability")
    result,_ = finish(r,"public_search")
    assert result["status"] == "completed" and w.moves == expected_moves
    assert len([n for n,_ in w.calls if n=="get_current_session"]) == 2
    assert len(result["state"]["search_confirmation"]["extra_reads"]) == (cached==95)
    reject_without_effect(r,w,r.frames[result["plan_id"]])


def test_reach_name_cannot_finish_at_exactly_95_or_repeat_extra_query(many):
    class SameName(PlateauWorld):
        def _call(self,name,**args):
            value=super()._call(name,**args)
            if name=="move_to":
                value["message"]="Drone moved to position (300,100,20) - Reached target(s): Fixed Target 1"
            return value
    r,w,_ = make(SameName(lambda moves:95),revision=V9,scheduler="applicability")
    result,_ = finish(r,"public_search")
    assert result["reason"]=="round_limit_below_threshold" and w.moves==80
    assert len([n for n,_ in w.calls if n=="get_current_session"])==10
    assert len(result["state"]["search_confirmation"]["extra_reads"])==1


@pytest.mark.parametrize("mode,reason", [("unknown","unknown_action_effect_observed_do_not_replay"),
    ("partial","search_move_partial"),("wrong_actor","wrong_actor_receipt"),("effect_mismatch","search_move_effect_mismatch")])
def test_original_effect_guards_still_precede_confirmation(mode,reason):
    r,w,_=target_runtime(TargetWorld(mode=mode),revision=V9,scheduler="applicability")
    result,_=finish(r,"public_search")
    assert result["reason"]==reason
    assert result["state"]["search_confirmation"]["extra_reads"]==[]
    reject_without_effect(r,w,r.frames[result["plan_id"]])


@pytest.mark.parametrize("patch", [{"session_id":"other"},{"scope":"full_task_verdict"},
    {"source_endpoint":"/private/check"},{"status":"error"},{"partial_success":True}])
def test_extra_confirmation_requires_successful_same_scope_receipt(many,patch):
    r,w,_=make(PlateauWorld(lambda moves:100 if moves else 95,reaches={1}),revision=V9,scheduler="applicability")
    original=r.invoke
    def invoke(call):
        value=original(call)
        return {**value,**patch} if call.tool=="get_mission_progress" and w.moves else value
    r.invoke=invoke
    result,_=finish(r,"public_search")
    assert result["reason"]=="public_progress_unavailable" and w.moves==1
    assert result["status"]=="needs_replan"
